"""S7: a trace derived from another trace's edges is derived again when that trace
changes — ``label_lineage_dirty_traces``, ``queue_dependents`` and the dirty arm of
the drain. Two traces that read each other converge; nothing is queued when
nothing changed."""

from __future__ import annotations

import datetime as dt

import psycopg
import pytest

from data_governance.label_lineage import find
from data_governance.processors.label_lineage import driver
from tests.label_lineage.fixtures import legs_to, trace_input
from tests.processors.label_lineage.seed import classify, seed, seed_clone, seed_trace

ON = driver.Config(find.exact, "exact", window_hours=2.0)
T2 = "t2-clone-of-r4"


def _q(dsn: str, sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql, params).fetchall()


def _dirty(dsn: str) -> list[str]:
    return [r[0] for r in _q(dsn, "SELECT trace_id FROM label_lineage_dirty_traces ORDER BY requested_at")]


def _store_read_hash(row: str = "R4") -> str:
    t = trace_input(row)
    (leg,) = [x for x in legs_to(row, t, "lab-store", "response") if t.findings[x.payload_hash]]
    return leg.payload_hash


def _count_derivations(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = driver.derive_and_persist
    monkeypatch.setattr(driver, "derive_and_persist",
                        lambda tx, trace_id, config: calls.append(trace_id) or real(tx, trace_id, config))
    return calls


def test_a_changed_source_trace_queues_the_traces_that_read_it(configured_db: str, monkeypatch) -> None:
    """T1's store read (a payload T2's clone shares) is classified only after both
    traces were derived (the cursor held before it). The next wake delivers it and
    re-derives T1; its edges change, which queues T2 (T2 read T1 as a prior). The
    dirty arm then re-derives T2 and clears the row. (The same wake re-derives T2
    for its own copy of the leg too, so the dirty pass changes nothing more: the
    queue is the mechanism under test, the shared payload is the fixture's shape.)"""
    t1 = trace_input("R4").trace_id
    h = _store_read_hash()
    d1 = seed(configured_db, "R4", skip_classification=frozenset({h}))
    seed_clone(configured_db, "R4", trace_id=T2, delta=dt.timedelta(hours=1), seq_offset=10_000,
               skip_classification=frozenset({h}))
    cursor = driver.drain(0, ON)
    store_read = _q(configured_db, "SELECT l.interaction_id FROM interaction_legs l JOIN interactions i ON i.id = l.interaction_id "
                                   "WHERE l.payload_hash = %s AND i.trace_id = %s", (h, t1))[0][0]
    from_store_read = lambda: _q(configured_db, "SELECT count(*) FROM payload_label_edges WHERE trace_id = %s "
                                                "AND origin_trace_id = %s AND origin_interaction_id = %s AND origin_leg_type = 'response'",
                                 (T2, t1, store_read))[0][0]
    assert from_store_read() == 0  # T1's store read is unclassified: it is no origin yet
    assert _dirty(configured_db) == []
    with psycopg.connect(configured_db) as conn:
        classify(conn, next(c for c in d1["classifications"] if c["content_hash"] == h))
        conn.commit()
    queued: dict[str, int] = {}
    real = driver.queue_dependents

    def spy(tx, trace, window):
        queued[trace.trace_id] = real(tx, trace, window)
        return queued[trace.trace_id]

    monkeypatch.setattr(driver, "queue_dependents", spy)
    driver.drain(cursor, ON)
    assert queued.get(t1) == 1  # T1 changed; T2 read it, so T2 was queued ...
    assert _dirty(configured_db) == []  # ... and re-derived in the same wake, its row gone
    assert from_store_read() > 0


def test_nothing_is_queued_when_nothing_changed(configured_db: str, monkeypatch) -> None:
    seed(configured_db, "R4")
    seed_clone(configured_db, "R4", trace_id=T2, delta=dt.timedelta(hours=1), seq_offset=10_000)
    cursor = driver.drain(0, ON)
    assert _dirty(configured_db) == []
    calls = _count_derivations(monkeypatch)
    driver.drain(cursor, ON)
    assert calls == [] and _dirty(configured_db) == []


def test_queue_dependents_mirrors_the_priors_bounds(configured_db: str) -> None:
    t1 = trace_input("R4").trace_id
    seed(configured_db, "R4")
    seed_clone(configured_db, "R4", trace_id="later-in", delta=dt.timedelta(hours=1), seq_offset=10_000)
    seed_clone(configured_db, "R4", trace_id="later-out", delta=dt.timedelta(hours=5), seq_offset=20_000)
    seed_clone(configured_db, "R4", trace_id="earlier", delta=-dt.timedelta(hours=1), seq_offset=30_000)
    from data_governance import db

    with db.transaction() as tx:
        trace = driver.load_trace(tx, t1)
        queued = driver.queue_dependents(tx, trace, 2.0)
    assert queued == 1 and _dirty(configured_db) == ["later-in"]
    with db.transaction() as tx:
        assert driver.queue_dependents(tx, trace, 0.0) == 0


def test_two_traces_that_read_each_other_converge(configured_db: str, monkeypatch) -> None:
    """The clone's legs interleave with the original's (50 ms later): each trace is
    a prior of the other. Derived in either order, the drain settles with the dirty
    table empty and the same rows; the derivation count is recorded."""
    t1 = trace_input("R4").trace_id
    seed(configured_db, "R4")
    seed_clone(configured_db, "R4", trace_id=T2, delta=dt.timedelta(milliseconds=50), seq_offset=10_000)
    calls = _count_derivations(monkeypatch)
    cursor = driver.drain(0, ON)
    first_pass = list(calls)
    assert _dirty(configured_db) == []
    snap = lambda: (_q(configured_db, "SELECT edge_id, from_trace_id, origin_trace_id FROM payload_label_edges ORDER BY 1"),
                    _q(configured_db, "SELECT * FROM leg_inherited_labels ORDER BY 1, 2"))
    settled = snap()
    both = {r[0] for r in _q(configured_db, "SELECT DISTINCT trace_id FROM payload_label_edges WHERE from_trace_id IS NOT NULL")}
    assert both == {t1, T2}  # each read the other
    calls.clear()
    driver.drain(cursor, ON)
    assert calls == [] and snap() == settled
    # the other order: T2's legs first (a cursor already past them re-delivers nothing, so derive by hand)
    with psycopg.connect(configured_db) as conn:
        for table in ("payload_label_edges", "leg_inherited_labels", "label_lineage_trace_status",
                      "risk_recompute_requests", "processor_state", "label_lineage_dirty_traces"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    from data_governance import db

    calls.clear()
    for trace_id in (T2, t1):
        with db.transaction() as tx:
            driver.derive_and_persist(tx, trace_id, ON)
    driver.drain(0, ON)
    assert _dirty(configured_db) == []
    assert snap() == settled
    print(f"\nconvergence: first pass derived {first_pass}; reverse order derived {calls} before settling")


def test_the_dirty_row_outlives_a_failed_derivation(configured_db: str, monkeypatch) -> None:
    seed(configured_db, "R4")
    with psycopg.connect(configured_db) as conn:
        conn.execute("INSERT INTO label_lineage_dirty_traces (trace_id, reason) VALUES (%s, 'test')",
                     (trace_input("R4").trace_id,))
        conn.commit()
    real = driver.derive_and_persist

    def boom(tx, trace_id, config):
        raise RuntimeError("derivation failed")

    monkeypatch.setattr(driver, "derive_and_persist", boom)
    with pytest.raises(RuntimeError):
        driver.drain(0, ON)
    assert len(_dirty(configured_db)) == 1
    monkeypatch.setattr(driver, "derive_and_persist", real)
    driver.drain(0, ON)
    assert _dirty(configured_db) == []


def test_a_source_trace_that_changes_without_its_edges_changing_still_queues_its_readers(configured_db: str) -> None:
    """T1 is one write request holding the record and
    nothing else: it has no edges before or after its origin leg is classified.
    T2 (later on the wire, earlier in the table) read T1 as a prior while that leg
    was unclassified. The classification must reach T2: what priors read of T1 (a
    leg's classification) changed, edges or not — and the result equals a
    derivation from scratch on the same committed state."""
    from tests.label_lineage import test_priors as hb

    t1 = hb.t1()
    t2 = hb.t2()
    write_hash = next(h for h, f in t1.findings.items() if f)
    seed_trace(configured_db, t2, seq_start=1)
    seed_trace(configured_db, t1, seq_start=100, skip_classification=frozenset({write_hash}))
    crossing = lambda: _q(configured_db, "SELECT count(*) FROM payload_label_edges WHERE trace_id = 'T2' "
                                         "AND from_trace_id = 'T1'")[0][0]
    cursor = driver.drain(0, ON)
    assert crossing() == 0 and _dirty(configured_db) == []
    with psycopg.connect(configured_db) as conn:
        conn.execute("INSERT INTO payload_classifications (content_hash, sensitivity_level, regulatory_tags, "
                     "contains_identity_bundle, is_personalized, primary_domain, findings, model_version) "
                     "VALUES (%s, 'RESTRICTED', %s, false, false, 'test', %s, 1)",
                     (write_hash, ["PII"], psycopg.types.json.Jsonb(list(t1.findings[write_hash]))))
        conn.commit()
    driver.drain(cursor, ON)
    assert _q(configured_db, "SELECT count(*) FROM payload_label_edges WHERE trace_id = 'T1'") == [(0,)]
    by_arrival = crossing()
    assert by_arrival >= 1 and _dirty(configured_db) == []
    with psycopg.connect(configured_db) as conn:
        for table in ("payload_label_edges", "leg_inherited_labels", "label_lineage_trace_status",
                      "risk_recompute_requests", "processor_state", "label_lineage_dirty_traces"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    driver.drain(0, ON)
    assert crossing() == by_arrival


def test_a_source_trace_derived_late_queues_the_risk_recompute_of_what_it_reaches(configured_db: str) -> None:
    """Late arrival. T2 read the record alone (the read and the egress found
    nothing) and was derived while T1's write was still unclassified: nothing
    reached T2, no risk recompute is asked for. Then T1's classification lands.
    The dirty arm re-derives T2, the record crosses in at the read and travels on
    to the egress, the legs count it, and both interactions are queued for risk —
    the verdict computed before T1 was known is computed again."""
    from tests.label_lineage import test_priors as hb

    t1 = hb.t1()
    t2 = hb.t2(read_classified=False)
    write_hash = next(h for h, f in t1.findings.items() if f)
    seed_trace(configured_db, t2, seq_start=1)
    seed_trace(configured_db, t1, seq_start=100, skip_classification=frozenset({write_hash}))
    requested = lambda: {r[0] for r in _q(configured_db, "SELECT interaction_id FROM risk_recompute_requests")}
    cursor = driver.drain(0, ON)
    assert requested() == set() and _dirty(configured_db) == []
    with psycopg.connect(configured_db) as conn:
        conn.execute("INSERT INTO payload_classifications (content_hash, sensitivity_level, regulatory_tags, "
                     "contains_identity_bundle, is_personalized, primary_domain, findings, model_version) "
                     "VALUES (%s, 'RESTRICTED', %s, false, false, 'test', %s, 1)",
                     (write_hash, ["PII"], psycopg.types.json.Jsonb(list(t1.findings[write_hash]))))
        conn.commit()
    driver.drain(cursor, ON)
    assert requested() == {"t2-store", "t2-partner"} and _dirty(configured_db) == []
    egress = _q(configured_db, "SELECT inherited_tags_whole, origin_count FROM leg_inherited_labels "
                               "WHERE interaction_id = 't2-partner' AND leg_type = 'request'")
    assert egress == [(["PII"], 1)]


def test_a_queued_trace_that_lost_its_legs_leaves_the_queue(configured_db: str) -> None:
    """A trace re-keyed away by the interactions processor (no legs left) must not
    sit on the queue forever, re-read every round."""
    with psycopg.connect(configured_db) as conn:
        conn.execute("INSERT INTO label_lineage_dirty_traces (trace_id, reason) VALUES ('gone', 'test')")
        conn.commit()
    assert driver._redrive_dirty(ON) == 1
    assert _dirty(configured_db) == []
