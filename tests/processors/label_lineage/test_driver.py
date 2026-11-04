"""``processors/label_lineage/driver``: the readiness-gated drain over a replayed lab
trace in a migrated database — gating, whole-trace re-derivation, idempotence, the
stale arm, and recompute requests written only when a summary changed."""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

from data_governance import db, retrieval
from data_governance.label_lineage import find
from data_governance.processors.data_lineage import driver as data_lineage
from data_governance.processors.label_lineage import driver
from tests.label_lineage.fixtures import egress, legs_to, trace_input
from tests.processors.label_lineage.seed import classify, seed

OFF = driver.Config(find.exact, "exact")


def _q(dsn: str, sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql, params).fetchall()


def _snapshot(dsn: str) -> tuple:
    return (
        _q(dsn, "SELECT edge_id, to_payload_hash, unit_value, matched_start, seq FROM payload_label_edges ORDER BY 1"),
        _q(dsn, "SELECT interaction_id, leg_type::text, payload_hash, inherited_tags_whole, edge_count "
                "FROM leg_inherited_labels ORDER BY 1, 2"),
        _q(dsn, "SELECT trace_id, status, derived_legs, edge_count FROM label_lineage_trace_status"),
    )


def _store_read(row: str):
    t = trace_input(row)
    (leg,) = [x for x in legs_to(row, t, "lab-store", "response") if t.findings[x.payload_hash]]
    return t, leg


def _max_seq(dsn: str) -> int:
    return _q(dsn, "SELECT max(seq) FROM interaction_legs")[0][0]


def test_a_full_drain_derives_every_payload_bearing_leg(configured_db: str) -> None:
    seed(configured_db, "R4")
    cursor = driver.drain(0, OFF)
    assert cursor == _max_seq(configured_db)
    t = trace_input("R4")
    with_payload = {(x.interaction_id, x.leg_type) for x in t.legs if x.payload_hash}
    rows = {(r[0], r[1]) for r in _q(configured_db, "SELECT interaction_id, leg_type::text FROM leg_inherited_labels")}
    assert rows == with_payload
    assert _q(configured_db, "SELECT status FROM label_lineage_trace_status") == [("complete",)]
    leg = egress("R4", t)
    assert ("SSN", "123-45-6789") in set(_q(
        configured_db, "SELECT entity_type, unit_value FROM payload_label_edges WHERE to_interaction_id = %s",
        (leg.interaction_id,)))
    assert _q(configured_db, "SELECT last_processed_seq FROM processor_state WHERE processor_name = 'label_lineage'") == [
        (cursor,)]


def test_an_unclassified_leg_holds_the_cursor_until_it_is_classified(configured_db: str) -> None:
    t, store_read = _store_read("R4")
    d = seed(configured_db, "R4", skip_classification=frozenset({store_read.payload_hash}))
    cursor = driver.drain(0, OFF)
    assert cursor < store_read.seq
    assert _q(configured_db, "SELECT status FROM label_lineage_trace_status") == [("partial",)]
    with psycopg.connect(configured_db) as conn:
        classify(conn, next(c for c in d["classifications"] if c["content_hash"] == store_read.payload_hash))
        conn.commit()
    assert driver.drain(cursor, OFF) == _max_seq(configured_db)
    assert _q(configured_db, "SELECT status FROM label_lineage_trace_status") == [("complete",)]


def test_a_leg_whose_interaction_is_not_visible_is_not_ready(configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        conn.execute("INSERT INTO interaction_legs (interaction_id, leg_type, seq, payload_hash) "
                     "VALUES ('orphan', 'request', 1, NULL)")
        conn.commit()
    assert driver.drain(0, OFF) == 0


def test_draining_twice_writes_the_same_rows(configured_db: str) -> None:
    seed(configured_db, "R2")
    driver.drain(0, OFF)
    first = _snapshot(configured_db)
    driver.drain(0, OFF)  # every leg delivered again, every derivation repeated
    assert _snapshot(configured_db) == first and first[0]


def test_the_stale_arm_rederives_a_leg_rewritten_under_the_cursor(configured_db: str) -> None:
    """A leg rewritten in place keeps its seq; the cursor has passed it, so only
    the stale arm sees the new payload."""
    seed(configured_db, "R4")
    cursor = driver.drain(0, OFF)
    t, store_read = _store_read("R4")
    request = next(x for x in t.legs if x.interaction_id == store_read.interaction_id and x.leg_type == "request")
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE interaction_legs SET payload_hash = %s WHERE interaction_id = %s AND leg_type = 'response'",
                     (request.payload_hash, store_read.interaction_id))
        conn.commit()
    assert driver.drain(cursor, OFF) == cursor
    assert _q(configured_db, "SELECT payload_hash FROM leg_inherited_labels WHERE interaction_id = %s "
                             "AND leg_type = 'response'", (store_read.interaction_id,)) == [(request.payload_hash,)]
    leg = egress("R4", t)
    origins = {r[0] for r in _q(configured_db, "SELECT origin_payload_hash FROM payload_label_edges "
                                               "WHERE to_interaction_id = %s", (leg.interaction_id,))}
    assert store_read.payload_hash not in origins


def test_endpoint_correction_replays_labels_after_both_cursors_advanced(configured_db: str) -> None:
    seed(configured_db, "R4")
    t = trace_input("R4")
    leg = egress("R4", t)
    cursor = driver.drain(0, OFF)
    request = next(x for x in retrieval.get_leg_labels(t.trace_id, leg.interaction_id).legs
                   if x.leg_type == "request")
    assert request.carried is not None and "PII" in request.carried.regulatory_tags

    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, detected_from, original_seq) "
            "VALUES ('other-agent', 'agent', 'agent:other', 'Other agent', 'observed', 1)"
        )
        conn.execute("UPDATE interactions SET caller_entity_id = 'other-agent' WHERE id = %s",
                     (leg.interaction_id,))

    # The data-lineage consumer has its own correction progress. Draining it
    # first must not consume label lineage's replay obligation.
    data_lineage.drain(0)
    assert driver.drain(cursor, OFF) == cursor
    corrected = next(x for x in retrieval.get_leg_labels(t.trace_id, leg.interaction_id).legs
                     if x.leg_type == "request")
    assert corrected.carried is None


def test_time_correction_replays_labels_without_changing_seq_or_payload(configured_db: str) -> None:
    seed(configured_db, "R4")
    t = trace_input("R4")
    leg = egress("R4", t)
    cursor = driver.drain(0, OFF)
    request = next(x for x in retrieval.get_leg_labels(t.trace_id, leg.interaction_id).legs
                   if x.leg_type == "request")
    assert request.carried is not None
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE interaction_legs SET occurred_at = occurred_at - interval '1 hour' "
                     "WHERE interaction_id = %s AND leg_type = 'request'", (leg.interaction_id,))

    assert driver.drain(cursor, OFF) == cursor
    corrected = next(x for x in retrieval.get_leg_labels(t.trace_id, leg.interaction_id).legs
                     if x.leg_type == "request")
    assert corrected.seq == request.seq and corrected.payload_hash == request.payload_hash
    assert corrected.carried is None


def test_parent_correction_reorders_labels_after_the_cursor_advanced(configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        for entity_id, kind in (("u", "user"), ("a", "agent"), ("t", "tool")):
            conn.execute(
                "INSERT INTO entities (id, kind, natural_key, display_name, detected_from, original_seq) "
                "VALUES (%s, %s, %s, %s, 'observed', 1)",
                (entity_id, kind, f"{kind}:{entity_id}", entity_id),
            )
        for interaction_id, caller, callee, payload_hash, seq, occurred_at in (
            ("parent", "u", "a", "origin", 20, "2026-10-15T12:00:02Z"),
            ("child", "a", "t", "forwarded", 10, "2026-10-15T12:00:01Z"),
        ):
            conn.execute(
                "INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, summary) "
                "VALUES (%s, 'parent-correction', %s, %s, 's')", (interaction_id, caller, callee),
            )
            conn.execute(
                "INSERT INTO interaction_payloads (content_hash, content, byte_size) VALUES (%s, %s, 11)",
                (payload_hash, Jsonb("123-45-6789")),
            )
            conn.execute(
                "INSERT INTO interaction_legs (interaction_id, leg_type, seq, occurred_at, payload_hash) "
                "VALUES (%s, 'request', %s, %s, %s)", (interaction_id, seq, occurred_at, payload_hash),
            )
            classify(conn, {
                "content_hash": payload_hash, "sensitivity_level": "RESTRICTED" if seq == 20 else "PUBLIC",
                "regulatory_tags": ["PII"] if seq == 20 else [], "contains_identity_bundle": False,
                "is_personalized": False, "primary_domain": "unknown", "model_version": 1,
                "findings": [{"entity_type": "SSN", "regulatory_tags": ["PII"],
                              "sensitivity_level": "RESTRICTED", "category": "person.govID",
                              "start": 0, "end": 11, "text": "123-45-6789"}] if seq == 20 else [],
            })
    cursor = driver.drain(0, OFF)
    (before,) = retrieval.get_leg_labels("parent-correction", "child").legs
    assert before.carried is None
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE interactions SET parent_interaction_id = 'parent' WHERE id = 'child'")

    assert driver.drain(cursor, OFF) == cursor == 20
    (after,) = retrieval.get_leg_labels("parent-correction", "child").legs
    assert after.seq == before.seq == 10 and after.payload_hash == before.payload_hash
    assert after.carried is not None
    assert after.carried.regulatory_tags == ["PII"]
    assert {origin.origin_interaction_id for label in after.labels for origin in label.carried_in} == {"parent"}


def test_deleting_an_origin_replays_the_retained_legs(configured_db: str) -> None:
    seed(configured_db, "R4")
    t, origin = _store_read("R4")
    leg = egress("R4", t)
    cursor = driver.drain(0, OFF)

    def origin_ids() -> set[str]:
        return {o.origin_interaction_id
                for x in retrieval.get_leg_labels(t.trace_id, leg.interaction_id).legs
                for label in x.labels for o in label.carried_in}

    assert origin.interaction_id in origin_ids()
    with psycopg.connect(configured_db) as conn:
        conn.execute("DELETE FROM interaction_legs WHERE interaction_id = %s", (origin.interaction_id,))
        conn.execute("DELETE FROM interactions WHERE id = %s", (origin.interaction_id,))

    assert driver.drain(cursor, OFF) == cursor
    assert origin.interaction_id not in origin_ids()
    assert retrieval.get_leg_labels(t.trace_id, leg.interaction_id).lineage_status == "complete"


def test_trace_move_excludes_old_trace_labels_while_its_cleanup_is_pending(configured_db: str, monkeypatch) -> None:
    from data_governance.risk import config
    from data_governance.risk.engine.compute import compute_interaction_risk
    from tests.risk.engine.test_compute import _FakeOpaClient, _decision

    monkeypatch.setattr(config, "inherited_labels_mode", lambda: "on")
    seed(configured_db, "R4")
    t = trace_input("R4")
    leg = egress("R4", t)
    driver.drain(0, OFF)
    opa = _FakeOpaClient([_decision()])
    compute_interaction_risk(leg.interaction_id, opa_client=opa)
    assert "inherited" in retrieval.get_interaction_risk(leg.interaction_id).classification_summary["request"]

    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE interactions SET trace_id = 'new-trace' WHERE id = %s", (leg.interaction_id,))
    # Re-derive the destination before the old trace's independent cleanup runs.
    with db.transaction() as tx:
        driver.derive_and_persist(tx, "new-trace", OFF)
    request = next(x for x in retrieval.get_leg_labels("new-trace", leg.interaction_id).legs
                   if x.leg_type == "request")
    assert request.carried is None
    compute_interaction_risk(leg.interaction_id, opa_client=opa)
    current = retrieval.get_interaction_risk(leg.interaction_id)
    assert current.trace_id == "new-trace" and current.version == 2
    assert "inherited" not in current.classification_summary["request"]


def test_an_empty_trace_loses_its_persisted_label_coverage(configured_db: str) -> None:
    empty_version = retrieval.get_trace_version("empty-trace")
    seed(configured_db, "R4")
    trace_id = trace_input("R4").trace_id
    cursor = driver.drain(0, OFF)
    assert ";lab=complete" in retrieval.get_trace_version(trace_id)
    with psycopg.connect(configured_db) as conn:
        conn.execute("DELETE FROM interaction_legs WHERE interaction_id IN "
                     "(SELECT id FROM interactions WHERE trace_id = %s)", (trace_id,))
        conn.execute("DELETE FROM interactions WHERE trace_id = %s", (trace_id,))

    assert driver.drain(cursor, OFF) == cursor
    assert retrieval.get_trace_version(trace_id) == empty_version


def test_the_stale_arm_rederives_a_leg_with_no_row(configured_db: str) -> None:
    seed(configured_db, "R4")
    cursor = driver.drain(0, OFF)
    with psycopg.connect(configured_db) as conn:
        conn.execute("DELETE FROM leg_inherited_labels WHERE edge_count > 0 AND leg_type = 'request'")
        conn.commit()
    before = _q(configured_db, "SELECT count(*) FROM leg_inherited_labels")[0][0]
    driver.drain(cursor, OFF)
    assert _q(configured_db, "SELECT count(*) FROM leg_inherited_labels")[0][0] > before


def test_an_unchanged_trace_is_not_rederived_by_the_stale_arm(configured_db: str) -> None:
    seed(configured_db, "R4")
    cursor = driver.drain(0, OFF)
    assert driver._redrive_stale(cursor, OFF) == 0
    other = driver.Config(find.exact, "another")
    assert driver._redrive_stale(cursor, other) == 1  # a different matcher makes every row stale


def test_a_recompute_request_is_written_only_for_a_changed_summary(configured_db: str) -> None:
    """The egress leg's seq is lower than the store read's, so it is delivered,
    and may be decided, while the store read still waits for its classification.
    When the store read is classified and derived, the egress's inherited summary
    changes: that, and only that, asks for a recompute. The processor writes the
    request whatever the risk flag says; leg-ready decides what to do with it."""
    t, store_read = _store_read("R4")
    d = seed(configured_db, "R4", skip_classification=frozenset({store_read.payload_hash}))
    leg = egress("R4", t)
    cursor = driver.drain(0, OFF)
    assert leg.seq <= cursor < store_read.seq
    first = {r[0] for r in _q(configured_db,
                            "SELECT interaction_id FROM risk_recompute_requests WHERE labels_pending")}
    with_edges = {r[0] for r in _q(configured_db, "SELECT interaction_id FROM leg_inherited_labels WHERE edge_count > 0")}
    assert first == with_edges  # first rows with edges: a decision may have been made without them
    with psycopg.connect(configured_db) as conn:
        conn.execute("DELETE FROM risk_recompute_requests")
        conn.commit()
    driver.drain(0, OFF)  # derived again, nothing changed
    assert _q(configured_db, "SELECT count(*) FROM risk_recompute_requests") == [(0,)]
    with psycopg.connect(configured_db) as conn:
        classify(conn, next(c for c in d["classifications"] if c["content_hash"] == store_read.payload_hash))
        conn.commit()
    driver.drain(cursor, OFF)
    requested = {r[0] for r in _q(configured_db, "SELECT interaction_id FROM risk_recompute_requests")}
    assert leg.interaction_id in requested
    assert store_read.interaction_id not in requested


def test_one_run_derives_each_trace_once(configured_db: str, monkeypatch) -> None:
    seed(configured_db, "R4")
    seed(configured_db, "R8")
    calls: list[str] = []
    real = driver.derive_and_persist
    monkeypatch.setattr(driver, "derive_and_persist", lambda tx, trace_id, config: calls.append(trace_id) or real(tx, trace_id, config))
    assert driver.drain(0, OFF) == _max_seq(configured_db)
    assert sorted(calls) == sorted({trace_input("R4").trace_id, trace_input("R8").trace_id})


def test_a_derivation_wakes_leg_ready(configured_db: str) -> None:
    """With the risk flag on, the label row is what makes a leg ready: the write
    notifies leg-ready's channel instead of leaving it to the poll backstop."""
    seed(configured_db, "R4")
    with psycopg.connect(configured_db, autocommit=True) as listener:
        listener.execute(f"LISTEN {driver.NOTIFY_CHANNEL}")
        driver.drain(0, OFF)
        got = list(listener.notifies(timeout=2, stop_after=1))
    assert got and all(n.channel == driver.NOTIFY_CHANNEL for n in got)
