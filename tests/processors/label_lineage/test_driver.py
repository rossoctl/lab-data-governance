"""``processors/label_lineage/driver``: the readiness-gated drain over a replayed lab
trace in a migrated database — gating, whole-trace re-derivation, idempotence, the
stale arm, and recompute requests written only when a summary changed."""

from __future__ import annotations

import json
from collections import Counter

import psycopg
import pytest
from psycopg.types.json import Jsonb

from data_governance import db, retrieval
from data_governance.label_lineage import find, fold
from data_governance.processors.data_lineage import memory
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


def _edge_rows(dsn: str) -> Counter:
    return Counter(_q(dsn, "SELECT from_interaction_id, from_leg_type::text, to_interaction_id, to_leg_type::text, "
                           "entity_type, unit_value FROM payload_label_edges"))


def _edges_of(derivation) -> Counter:
    return Counter((*e.from_key, *e.to_key, e.unit.entity_type, e.unit.value.canonical) for e in derivation.edges)


def test_the_declared_scope_reaches_the_running_derivation(configured_db: str, monkeypatch) -> None:
    """R5's store declared ``conversation``: the processor, configured from its environment, pools the store's
    calls and writes the edges the fold draws under that declaration, not the ones it draws by kind."""
    store = "tool:lineage-lab/lab-store"
    t = trace_input("R5")
    declared, by_kind = (_edges_of(fold.derive(t, find.exact, {store: "conversation"})),
                         _edges_of(fold.derive(t, find.exact)))
    assert declared != by_kind                     # the declaration means something on this trace
    seed(configured_db, "R5")
    monkeypatch.setenv(memory.SCOPES_ENV_VAR, json.dumps({store: "conversation"}))
    driver.drain(0, driver.configured())
    assert _edge_rows(configured_db) == declared


# --- across conversations ------------------------------------------------------------------------------------------

def _seed_input(dsn: str, t: fold.TraceInput) -> None:
    """Write a hand-built trace (``tests.label_lineage.test_cross``) as the upstream processors would have."""
    with psycopg.connect(dsn) as conn:
        for e in t.entities.values():
            conn.execute("INSERT INTO entities (id, kind, natural_key, display_name, detected_from, original_seq) "
                         "VALUES (%s, %s, %s, %s, 'observed', 1) ON CONFLICT DO NOTHING",
                         (e.id, e.kind, e.natural_key, e.id))
        for leg in t.legs:
            conn.execute("INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, summary) "
                         "VALUES (%s, %s, %s, %s, '') ON CONFLICT DO NOTHING",
                         (leg.interaction_id, t.trace_id, leg.caller_entity_id, leg.callee_entity_id))
            content = t.payloads[leg.payload_hash]
            conn.execute("INSERT INTO interaction_payloads (content_hash, content, byte_size) VALUES (%s, %s, %s) "
                         "ON CONFLICT DO NOTHING", (leg.payload_hash, Jsonb(content), len(json.dumps(content))))
            classify(conn, {"content_hash": leg.payload_hash, "sensitivity_level": "RESTRICTED", "regulatory_tags": [],
                            "contains_identity_bundle": False, "is_personalized": False, "primary_domain": None,
                            "findings": t.findings.get(leg.payload_hash, []), "model_version": 1})
        seq = _q(dsn, "SELECT coalesce(max(seq), 0) FROM interaction_legs")[0][0]
        for n, leg in enumerate(t.legs, start=1):
            conn.execute("INSERT INTO interaction_legs (interaction_id, leg_type, seq, occurred_at, payload_hash, error) "
                         "VALUES (%s, %s, %s, %s, %s, false)",
                         (leg.interaction_id, leg.leg_type, seq + n, leg.occurred_at, leg.payload_hash))


def _crossings(dsn: str) -> list[tuple]:
    return _q(dsn, "SELECT e.entity_type, e.from_trace_id, c.level FROM payload_label_edges e "
                   "JOIN label_crossings c ON c.edge_id = e.edge_id ORDER BY 1")


def test_the_cross_switch_reaches_the_running_derivation(configured_db: str, monkeypatch) -> None:
    from data_governance.label_lineage import cross
    from tests.label_lineage.test_cross import _first, _second
    monkeypatch.setenv(cross.SWITCH_ENV_VAR, "on")
    monkeypatch.delenv("RISK_INHERITED_LABELS", raising=False)
    _seed_input(configured_db, _first())
    cursor = driver.drain(0, driver.configured())
    _seed_input(configured_db, _second())
    driver.drain(cursor, driver.configured())
    assert _crossings(configured_db) == [("DOB", "T1", "corroborated"), ("SSN", "T1", "corroborated")]


def test_the_load_reads_only_the_holdings_whose_value_is_in_a_payload_the_senders_send(configured_db: str) -> None:
    from data_governance.label_lineage import cross
    from tests.label_lineage.test_cross import _first, _second
    config = driver.Config(find.exact, "exact", crossing=cross.Crossing())
    _seed_input(configured_db, _first())
    driver.drain(0, config)
    _seed_input(configured_db, _second())

    def priors(content: object) -> list[str]:
        payloads = {h: (content if c and "subject" in c else c) for h, c in t.payloads.items()}
        return sorted(h.value for h in driver.load_priors(tx, "T2", list(t.legs), t.entities, payloads, config))

    with db.transaction() as tx:
        t = driver.load_trace(tx, "T2", config)
        assert sorted(h.value for h in t.priors) == ["123-45-6789", "1961-03-14"]
        assert priors({"nothing": "here"}) == []
        # the first words of both held values, neither value: nothing is loaded
        assert priors({"subject": "123-45-0000", "since": "1961-04-14"}) == []
        assert priors({"subject": "123-45-6789", "since": "1961-04-14"}) == ["123-45-6789"]


@pytest.mark.parametrize("risk", [None, "off", "on", "all", "a typo"])
@pytest.mark.parametrize("switch, on", [(None, False), ("off", False), ("on", True)])
def test_the_cross_switch_alone_governs_label_lineage(monkeypatch, switch, on, risk) -> None:
    """Label lineage reads no risk configuration: the risk flag, whatever it says (even a value leg-ready refuses),
    changes nothing here."""
    for k, v in (("LABEL_CROSS_CONVERSATION", switch), ("RISK_INHERITED_LABELS", risk)):
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    assert (driver.configured().crossing is not None) is on


def test_with_inherited_labels_off_the_cross_switch_still_registers_and_draws(configured_db: str, monkeypatch) -> None:
    from tests.label_lineage.test_cross import _first, _second
    monkeypatch.setenv("LABEL_CROSS_CONVERSATION", "on")
    monkeypatch.setenv("RISK_INHERITED_LABELS", "off")
    _seed_input(configured_db, _first())
    cursor = driver.drain(0, driver.configured())
    _seed_input(configured_db, _second())
    driver.drain(cursor, driver.configured())
    assert _crossings(configured_db) == [("DOB", "T1", "corroborated"), ("SSN", "T1", "corroborated")]


def test_an_invalid_cross_configuration_stops_the_processor_at_startup(monkeypatch) -> None:
    from data_governance.processors.label_lineage import __main__ as entry
    monkeypatch.setenv("DATABASE_URL", "postgres://unused")
    monkeypatch.setenv("LABEL_CROSS_CONVERSATION", "yes")
    assert entry.main() == 2
    monkeypatch.setenv("LABEL_CROSS_CONVERSATION", "on")
    monkeypatch.setenv("LABEL_CROSS_COMMON_LIMIT", "-1")
    assert entry.main() == 2


def test_one_run_derives_its_conversations_in_event_order(configured_db: str) -> None:
    """Both conversations in one delivered run, the earlier one with the id that sorts last: it is derived first,
    so the later one's crossings see what it registered."""
    import dataclasses

    from data_governance.label_lineage import cross
    from tests.label_lineage.test_cross import _first, _second
    _seed_input(configured_db, dataclasses.replace(_first(), trace_id="Z1"))
    _seed_input(configured_db, _second())
    driver.drain(0, driver.Config(find.exact, "exact", crossing=cross.Crossing()))
    assert _crossings(configured_db) == [("DOB", "Z1", "corroborated"), ("SSN", "Z1", "corroborated")]


# ("Z2", "A2"): the destination sorts first, so the trigger queues it first and the replay derives it first.
@pytest.mark.parametrize("old, new", [("Z2", "A2"), ("A2", "Z2")], ids=["destination-first", "source-first"])
def test_holdings_follow_their_receiving_interactions_to_a_new_trace(configured_db: str, old: str, new: str) -> None:
    """The membership correction queues both traces; in either order the holdings end under the trace that now
    owns the receiving leg, and a later conversation still crosses with them."""
    import dataclasses
    import datetime as dt

    from data_governance.label_lineage import cross
    from data_governance.processors.data_lineage.traversal import Entity
    from tests.label_lineage.test_cross import DOB, ENTITIES, SSN, T0, _first, _second, _trace
    config = driver.Config(find.exact, "exact", {"agent:lab/a": "persistent"}, crossing=cross.Crossing())
    _seed_input(configured_db, dataclasses.replace(_first(), trace_id="Z1"))
    cursor = driver.drain(0, config)
    _seed_input(configured_db, dataclasses.replace(_second(), trace_id=old))   # v returns SSN and DOB to a
    cursor = driver.drain(cursor, config)
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE interactions SET trace_id = %s WHERE trace_id = %s", (new, old))
    assert [r[0] for r in _q(configured_db, "SELECT trace_id FROM label_lineage_dirty_traces "
                                            "ORDER BY generation")] == sorted([old, new])
    driver.drain(cursor, config)
    assert _q(configured_db, "SELECT trace_id, value_text, evidence FROM label_occurrences "
                             "WHERE entity_id = 'a' ORDER BY value_text") == [(new, SSN, "crossed"),
                                                                             (new, DOB, "crossed")]
    assert _q(configured_db, "SELECT to_interaction_id, from_trace_id, count(*) FROM payload_label_edges "
                             "WHERE trace_id = %s GROUP BY 1, 2 ORDER BY 1", (new,)) == [("2", "Z1", 2), ("3", None, 2)]
    _seed_input(configured_db, _trace("X3", [("4", "request", "u", "a", {"q": "again"}, []),
                                             ("5", "request", "a", "t", {"subject": SSN, "since": DOB}, [])],
                                      T0 + dt.timedelta(minutes=8),
                                      {**ENTITIES, "u": Entity("u", "user:alice", "user")}))
    driver.drain(cursor, config)
    assert _q(configured_db, "SELECT e.entity_type, e.from_trace_id, c.level FROM payload_label_edges e JOIN "
                             "label_crossings c ON c.edge_id = e.edge_id WHERE e.trace_id = 'X3' ORDER BY 1") == [
        ("DOB", new, "corroborated"), ("SSN", new, "corroborated")]


@pytest.mark.parametrize("old, new", [("Z2", "A2"), ("A2", "Z2")], ids=["destination-first", "source-first"])
def test_a_crossing_follows_its_holding_to_a_new_trace(configured_db: str, old: str, new: str) -> None:
    """A conversation crossed with holdings whose interactions then move to another trace. Derived again, its
    crossing edges name the trace that owns the holdings now (the edge's id does not change, so the upsert has to
    carry it), and the read reaches the origin through that trace."""
    import dataclasses
    import datetime as dt

    from data_governance.label_lineage import cross
    from data_governance.processors.data_lineage.traversal import Entity
    from tests.label_lineage.test_cross import DOB, ENTITIES, SSN, T0, _first, _second, _trace
    config = driver.Config(find.exact, "exact", {"agent:lab/a": "persistent"}, crossing=cross.Crossing())
    _seed_input(configured_db, dataclasses.replace(_first(), trace_id="Z1"))
    cursor = driver.drain(0, config)
    _seed_input(configured_db, dataclasses.replace(_second(), trace_id=old))   # v returns SSN and DOB to a
    cursor = driver.drain(cursor, config)
    _seed_input(configured_db, _trace("X3", [("4", "request", "u", "a", {"q": "again"}, []),
                                             ("5", "request", "a", "t", {"subject": SSN, "since": DOB}, [])],
                                      T0 + dt.timedelta(minutes=8),
                                      {**ENTITIES, "u": Entity("u", "user:alice", "user")}))
    cursor = driver.drain(cursor, config)                                       # X3 crosses before the move
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE interactions SET trace_id = %s WHERE trace_id = %s", (new, old))
    driver.drain(cursor, config)
    with psycopg.connect(configured_db) as conn:
        conn.execute("SELECT dg_queue_label_trace('X3')")                       # X3 is derived again
    driver.drain(cursor, config)
    assert _q(configured_db, "SELECT e.entity_type, e.from_trace_id FROM payload_label_edges e JOIN label_crossings c "
                             "ON c.edge_id = e.edge_id WHERE e.trace_id = 'X3' ORDER BY 1") == [("DOB", new), ("SSN", new)]
    (leg,) = retrieval.labels.get_leg_labels("X3", "5").legs
    assert [[(h.trace_id, h.role) for h in chain] for label in leg.labels for o in label.carried_in
            for chain in o.chains] == [[("Z1", "origin"), (new, "hop"), ("X3", "here")]] * 2


@pytest.mark.parametrize("old, new", [("Z5", "A5"), ("A5", "Z5")], ids=["destination-first", "source-first"])
def test_a_moved_conversation_does_not_cross_with_itself(configured_db: str, old: str, new: str) -> None:
    """One conversation in which nothing crosses: u gives a an SSN and a sends it to t. Its interactions move to
    another trace, and it is derived there while its holdings still name the old one. They are its own, not another
    conversation's: it draws the one edge it drew before, and no crossing."""
    from data_governance.label_lineage import cross
    from data_governance.processors.data_lineage.traversal import Entity
    from tests.label_lineage.test_cross import ENTITIES, SSN, T0, _trace
    config = driver.Config(find.exact, "exact", {"agent:lab/a": "persistent"}, crossing=cross.Crossing())
    _seed_input(configured_db, _trace(old, [("0", "request", "u", "a", {"ssn": SSN}, [("SSN", SSN)]),
                                            ("1", "request", "a", "t", {"subject": SSN}, [])], T0,
                                      {**ENTITIES, "u": Entity("u", "user:alice", "user")}))
    cursor = driver.drain(0, config)
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE interactions SET trace_id = %s WHERE trace_id = %s", (new, old))
    driver.drain(cursor, config)
    assert _q(configured_db, "SELECT trace_id, from_trace_id, origin_trace_id FROM payload_label_edges") == [
        (new, None, None)]
    assert _q(configured_db, "SELECT (SELECT count(*) FROM label_crossings), "
                             "(SELECT count(*) FROM label_crossed_edges)") == [(0, 0)]


@pytest.mark.parametrize("old, new", [("Z6", "A6"), ("A6", "Z6")], ids=["destination-first", "source-first"])
def test_a_conversation_crosses_with_a_holding_that_moved_out_of_it(configured_db: str, old: str, new: str) -> None:
    """One conversation: u gives a an SSN and its date of birth in one record, and a sends both to t. The receiving
    interaction then moves to another trace. What a received there is another conversation's now, so the send
    crosses with it, whichever of the two traces is derived first: the holding's row still names the old trace
    when the old trace is derived first."""
    from data_governance.label_lineage import cross
    from data_governance.processors.data_lineage.traversal import Entity
    from tests.label_lineage.test_cross import DOB, ENTITIES, SSN, T0, _trace
    config = driver.Config(find.exact, "exact", {"agent:lab/a": "persistent"}, crossing=cross.Crossing())
    _seed_input(configured_db, _trace(old, [("0", "request", "u", "a", {"ssn": SSN, "dob": DOB},
                                             [("SSN", SSN), ("DOB", DOB)]),
                                            ("1", "request", "a", "t", {"subject": SSN, "since": DOB}, [])], T0,
                                      {**ENTITIES, "u": Entity("u", "user:alice", "user")}))
    cursor = driver.drain(0, config)
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE interactions SET trace_id = %s WHERE id = '0'", (new,))
    driver.drain(cursor, config)
    assert _q(configured_db, "SELECT e.trace_id, e.entity_type, e.from_trace_id, c.level FROM payload_label_edges e "
                             "JOIN label_crossings c ON c.edge_id = e.edge_id ORDER BY 2") == [
        (old, "DOB", new, "corroborated"), (old, "SSN", new, "corroborated")]


CROSS_TABLES = ("label_occurrences", "label_crossings", "label_crossed_edges")


def test_a_trace_that_loses_every_leg_loses_its_cross_conversation_rows(configured_db: str) -> None:
    from data_governance.label_lineage import cross
    from tests.label_lineage.test_cross import _first, _second
    config = driver.Config(find.exact, "exact", crossing=cross.Crossing())
    _seed_input(configured_db, _first())
    cursor = driver.drain(0, config)
    _seed_input(configured_db, _second())
    cursor = driver.drain(cursor, config)
    assert all(_q(configured_db, f"SELECT count(*) FROM {t} WHERE trace_id IN ('T1', 'T2')")[0][0]
               for t in CROSS_TABLES)
    with psycopg.connect(configured_db) as conn:
        for trace_id in ("T1", "T2"):
            conn.execute("DELETE FROM interaction_legs WHERE interaction_id IN "
                         "(SELECT id FROM interactions WHERE trace_id = %s)", (trace_id,))
            conn.execute("DELETE FROM interactions WHERE trace_id = %s", (trace_id,))
            conn.execute("SELECT dg_queue_label_trace(%s)", (trace_id,))
    driver.drain(cursor, config)
    assert {t: _q(configured_db, f"SELECT count(*) FROM {t}")[0][0] for t in CROSS_TABLES} == dict.fromkeys(
        CROSS_TABLES, 0)


def test_a_long_value_held_only_in_this_conversation_does_not_raise_the_cost(configured_db: str,
                                                                             monkeypatch) -> None:
    """K comes from other conversations only: derived again, a conversation whose partner received a 30-word value
    in it still hashes runs as long as what another conversation gave the partner (one SSN, three words)."""
    import datetime as dt

    from data_governance.label_lineage import cross
    from tests.label_lineage.test_cross import SSN, T0, _trace
    config = driver.Config(find.exact, "exact", crossing=cross.Crossing())
    other = "987-65-4321"
    long_value = " ".join(f"street{i}" for i in range(30))
    _seed_input(configured_db, _trace("T9", [("9", "request", "t", "v", {"ssn": other}, [("SSN", other)])],
                                      T0 - dt.timedelta(minutes=1)))
    _seed_input(configured_db, _trace("T1", [("1", "request", "t", "v", {"addr": long_value, "ssn": SSN},
                                              [("ADDRESS", long_value), ("SSN", SSN)]),
                                             ("1", "response", "t", "v", {"subject": other}, [])], T0))
    cursor = driver.drain(0, config)
    assert _q(configured_db, "SELECT max(word_count) FROM label_occurrences WHERE trace_id = 'T1'")[0][0] == 30
    seen = []
    real = cross.window_hashes
    monkeypatch.setattr(cross, "window_hashes", lambda text, longest: seen.append(longest) or real(text, longest))
    with psycopg.connect(configured_db) as conn:
        conn.execute("SELECT dg_queue_label_trace('T1')")
    driver.drain(cursor, config)
    assert seen and set(seen) == {3}                                   # T9's SSN, not T1's own address
    assert [c[:2] for c in _crossings(configured_db)] == [("SSN", "T9")]


def test_a_conversation_derived_again_never_loads_its_own_earlier_holdings(configured_db: str) -> None:
    """A crossing inside one conversation, then the receiving leg rewritten without the value and derived again
    by the stale arm: what the conversation itself registered before is not a prior, so the crossing goes. Another
    conversation gave the partner a value of the same shape, so the load does run for the partner's sends."""
    import datetime as dt

    from data_governance.label_lineage import cross
    from tests.label_lineage.test_cross import SSN, T0, _trace
    config = driver.Config(find.exact, "exact", crossing=cross.Crossing())
    _seed_input(configured_db, _trace("T9", [("9", "request", "t", "v", {"ssn": "987-65-4321"},
                                              [("SSN", "987-65-4321")])], T0 - dt.timedelta(minutes=1)))
    _seed_input(configured_db, _trace("T1", [("1", "request", "t", "v", {"ssn": SSN}, [("SSN", SSN)]),
                                             ("1", "response", "t", "v", {"subject": SSN}, []),
                                             ("2", "request", "t", "v", {"q": "again"}, []),
                                             ("2", "response", "t", "v", {"subject": SSN}, [])], T0))
    cursor = driver.drain(0, config)
    assert [c[0] for c in _crossings(configured_db)] == ["SSN"]
    other = "000-00-0000"
    with psycopg.connect(configured_db) as conn:
        conn.execute("INSERT INTO interaction_payloads (content_hash, content, byte_size) VALUES ('h-new', %s, 20)",
                     (Jsonb({"ssn": other}),))
        classify(conn, {"content_hash": "h-new", "sensitivity_level": "RESTRICTED", "regulatory_tags": [],
                        "contains_identity_bundle": False, "is_personalized": False, "primary_domain": None,
                        "findings": [{"entity_type": "SSN", "text": other, "start": 9, "end": 20,
                                      "regulatory_tags": ["PII"], "sensitivity_level": "RESTRICTED"}],
                        "model_version": 1})
        conn.execute("UPDATE interaction_legs SET payload_hash = 'h-new' "
                     "WHERE interaction_id = '1' AND leg_type = 'request'")
    driver.drain(cursor, config)
    assert _q(configured_db, "SELECT value_text FROM label_occurrences WHERE trace_id = 'T1'") == [(other,)]
    assert _crossings(configured_db) == []


def test_the_longest_held_value_counts_only_while_it_can_still_be_sent(configured_db: str, monkeypatch) -> None:
    """K, the most words the load hashes runs of, comes from the occurrences valid for the sends: a long value
    the sender held a month ago does not raise the cost of what it sends now."""
    import datetime as dt

    from data_governance.label_lineage import cross
    from tests.label_lineage.test_cross import SSN, T0, _first, _second, _trace
    config = driver.Config(find.exact, "exact", crossing=cross.Crossing())
    long_value = " ".join(f"street{i}" for i in range(30))
    _seed_input(configured_db, _trace("T0", [("0", "request", "a", "v", {"addr": long_value, "ssn": SSN},
                                              [("ADDRESS", long_value), ("SSN", SSN)]),
                                             ("0", "response", "a", "v", {"ok": True}, [])],
                                      T0 - dt.timedelta(days=30)))
    _seed_input(configured_db, _first())
    cursor = driver.drain(0, config)
    assert _q(configured_db, "SELECT max(word_count) FROM label_occurrences")[0][0] == 30
    seen = []
    real = cross.window_hashes
    monkeypatch.setattr(cross, "window_hashes", lambda text, longest: seen.append(longest) or real(text, longest))
    _seed_input(configured_db, _second())
    driver.drain(cursor, config)
    (t1_longest,) = _q(configured_db, "SELECT max(word_count) FROM label_occurrences WHERE trace_id = 'T1'")[0]
    assert seen and set(seen) == {t1_longest} and t1_longest < 30     # T1's SSN and DOB, not T0's address
    assert [c[:2] for c in _crossings(configured_db)] == [("DOB", "T1"), ("SSN", "T1")]
