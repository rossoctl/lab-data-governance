"""``driver.load_priors`` and the window: which payloads of other traces reach a
trace's fold, through which entities, holding what — and that what crosses counts in
the summaries of the legs it reaches (``leg_inherited_labels``), never changing the
conversation's own edges."""

from __future__ import annotations

import dataclasses
import datetime as dt

import psycopg
import pytest

from data_governance import db
from data_governance.label_lineage import find
from data_governance.processors.data_lineage import memory
from data_governance.processors.label_lineage import driver
from tests.label_lineage.fixtures import egress, legs_to, trace_input
from tests.processors.label_lineage.seed import seed, seed_clone, seed_trace

OFF = driver.Config(find.exact, "exact")
ON = driver.Config(find.exact, "exact", window_hours=2.0)
T2 = "t2-clone-of-r4"
HOUR = dt.timedelta(hours=1)


def _q(dsn: str, sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql, params).fetchall()


def _priors(trace_id: str, window: float):
    with db.transaction() as tx:
        trace = driver.load_trace(tx, trace_id)
        return driver.load_priors(tx, trace, window)


def _two_traces(dsn: str, delta: dt.timedelta = HOUR) -> dict:
    seed(dsn, "R4")
    return seed_clone(dsn, "R4", trace_id=T2, delta=delta, seq_offset=10_000)


def test_the_window_bounds_which_traces_are_priors(configured_db: str) -> None:
    _two_traces(configured_db, delta=HOUR)
    t1 = trace_input("R4").trace_id
    assert {p.at.trace_id for p in _priors(T2, 2.0)} == {t1}
    assert _priors(T2, 0.5) == ()
    assert _priors(T2, 0.0) == ()
    assert _priors(t1, 2.0) == ()  # the clone is LATER: never a prior of the original


def test_priors_come_through_stateful_entities_only_and_name_the_entity(configured_db: str) -> None:
    _two_traces(configured_db)
    with db.transaction() as tx:
        trace = driver.load_trace(tx, T2)
    stateful = {e.id for e in trace.entities.values() if memory.accumulates(e) or memory.is_entity_source(e)}
    assert stateful
    priors = _priors(T2, 2.0)
    assert priors and {p.entity_id for p in priors} <= stateful
    by_ix = {i["id"]: i for i in _rows_interactions(configured_db)}
    for p in priors:
        ix = by_ix[p.at.interaction_id]
        assert p.entity_id in (ix["caller"], ix["callee"])
        assert p.occurred_at is not None and p.occurrences


def _rows_interactions(dsn: str) -> list[dict]:
    return [{"id": r[0], "caller": r[1], "callee": r[2]}
            for r in _q(dsn, "SELECT id, caller_entity_id, callee_entity_id FROM interactions")]


def test_a_prior_payload_holds_its_own_findings_and_what_reached_it(configured_db: str) -> None:
    """Once T1 is derived, its egress leg holds the record's units reached from the
    store read; as a prior of T2 that leg contributes them with their T1 origin."""
    _two_traces(configured_db)
    t = trace_input("R4")
    driver.drain(0, OFF)  # derives both traces, no priors
    leg = egress("R4", t)
    (prior,) = [p for p in _priors(T2, 2.0) if p.at.leg == (leg.interaction_id, "request") and p.entity_id == leg.caller_entity_id]
    assert prior.at.trace_id == t.trace_id
    units = {(o.unit.entity_type, o.unit.value.canonical, o.unit.origin.trace_id, o.unit.origin.leg) for o in prior.occurrences}
    (store_read,) = [x for x in legs_to("R4", t, "lab-store", "response") if t.findings[x.payload_hash]]
    assert ("SSN", "123-45-6789", t.trace_id, (store_read.interaction_id, "response")) in units


def test_the_rebuilt_occurrence_is_the_stored_edge(configured_db: str) -> None:
    _two_traces(configured_db)
    driver.drain(0, OFF)
    t = trace_input("R4")
    leg = egress("R4", t)
    priors = [p for p in _priors(T2, 2.0) if p.at.leg == (leg.interaction_id, "request")]
    assert {p.entity_id for p in priors} == {leg.caller_entity_id, leg.callee_entity_id}  # the agent and the partner tool
    assert len({p.occurrences for p in priors}) == 1  # the same payload holds the same occurrences, whoever holds it
    prior = priors[0]
    rows = _q(configured_db, "SELECT unit_value, matched_start, matched_end, held_from, held_to, match_kind, score "
                             "FROM payload_label_edges WHERE to_interaction_id = %s AND to_leg_type = 'request'",
              (leg.interaction_id,))
    reached = {(o.unit.value.canonical, *o.span, o.held_from, o.held_to, o.match_kind, o.score)
               for o in prior.occurrences if o.source is None and o.at.leg == prior.at.leg
               and o.unit.origin.leg != prior.at.leg}
    assert {(r[0], r[1], r[2], r[3], r[4], r[5], float(r[6])) for r in rows} == reached


_LEGS_SQL = ("SELECT interaction_id, leg_type::text, inherited_tags_whole, max_inherited_level, origin_count, "
             "edge_count FROM leg_inherited_labels WHERE trace_id = %s")


def test_with_the_window_on_the_cross_edges_are_written_and_the_legs_they_reach_count_them(configured_db: str) -> None:
    _two_traces(configured_db)
    driver.drain(0, OFF)
    legs_off = {(r[0], r[1]): r for r in _q(configured_db, _LEGS_SQL, (T2,))}
    own_off = _q(configured_db, "SELECT edge_id FROM payload_label_edges WHERE trace_id = %s ORDER BY 1", (T2,))
    assert _q(configured_db, "SELECT count(*) FROM payload_label_edges WHERE from_trace_id IS NOT NULL") == [(0,)]
    with psycopg.connect(configured_db) as conn:
        for table in ("payload_label_edges", "leg_inherited_labels", "label_lineage_trace_status",
                      "risk_recompute_requests", "processor_state"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    driver.drain(0, ON)
    t1 = trace_input("R4").trace_id
    crossing = _q(configured_db, "SELECT from_trace_id, origin_trace_id FROM payload_label_edges "
                                 "WHERE trace_id = %s AND from_trace_id IS NOT NULL", (T2,))
    assert crossing and {r[0] for r in crossing} == {t1} and {r[1] for r in crossing} == {t1}
    assert _q(configured_db, "SELECT count(*) FROM payload_label_edges WHERE trace_id = %s AND from_trace_id IS NOT NULL",
              (t1,)) == [(0,)]
    foreign = _q(configured_db, "SELECT count(*) FROM payload_label_edges WHERE trace_id = %s AND origin_trace_id = %s",
                 (T2, t1))
    assert foreign[0][0] > len(crossing)  # the foreign origin travels on inside T2
    reached = {(r[0], r[1]) for r in _q(configured_db, "SELECT to_interaction_id, to_leg_type::text FROM payload_label_edges "
                                                       "WHERE trace_id = %s AND origin_trace_id IS NOT NULL", (T2,))}
    legs_on = {(r[0], r[1]): r for r in _q(configured_db, _LEGS_SQL, (T2,))}
    assert reached and legs_on.keys() == legs_off.keys()
    for key in reached:  # a label carried in from T1 is inherited like one of T2's own
        assert legs_on[key][4] > legs_off[key][4] and legs_on[key][5] > legs_off[key][5]
    assert all(legs_on[key] == legs_off[key] for key in legs_off.keys() - reached)
    own_on = _q(configured_db, "SELECT edge_id FROM payload_label_edges WHERE trace_id = %s AND origin_trace_id IS NULL "
                               "ORDER BY 1", (T2,))
    assert own_on == own_off
    requests_on = {r[0] for r in _q(configured_db, "SELECT interaction_id FROM risk_recompute_requests")}
    assert {ix for ix, _ in reached} <= requests_on
    status = _q(configured_db, "SELECT edge_count FROM label_lineage_trace_status WHERE trace_id = %s", (T2,))
    assert status[0][0] == len(own_on) + foreign[0][0]


def test_draining_twice_with_the_window_on_writes_the_same_rows(configured_db: str) -> None:
    _two_traces(configured_db)
    driver.drain(0, ON)
    snap = lambda: (_q(configured_db, "SELECT edge_id, from_trace_id, origin_trace_id FROM payload_label_edges ORDER BY 1"),
                    _q(configured_db, "SELECT * FROM leg_inherited_labels ORDER BY 1, 2"))
    first = snap()
    assert any(r[1] for r in first[0])
    driver.drain(0, ON)
    assert snap() == first


@pytest.mark.parametrize("raw, expected", [(None, 0.0), ("", 0.0), ("0", 0.0), ("24", 24.0), (" 1.5 ", 1.5)])
def test_the_window_parses_hours(raw: str | None, expected: float) -> None:
    assert driver.parse_window(raw) == expected


@pytest.mark.parametrize("raw", ["-1", "day", "1h", "nan", "inf", "1e400"])
def test_a_bad_window_is_refused_with_its_name(raw: str) -> None:
    with pytest.raises(ValueError, match="LABEL_CROSS_TRACE_WINDOW_HOURS"):
        driver.parse_window(raw)


def test_a_prior_leg_offers_each_unit_once_as_its_own_derivation_read_it(configured_db: str) -> None:
    """In T1 the record arrives at the agent in the root
    request and the agent writes it to the store: the write request's own finding
    was CLAIMED by the root's unit when T1 was derived (one origin, the root).
    Loaded as a prior, the write request offers that one unit, not a second
    origin of its own."""
    from tests.label_lineage import test_priors as hb

    root = {"payload": {"save": {"ssn": hb.S}}}
    t1 = hb._trace("T1", [
        ("t1-ua", "request", "u", "a", root),
        ("t1-store", "request", "a", "store", hb.W),
        ("t1-store", "response", "a", "store", {"written": True}),
    ], hb.T0, classified={hb._hash(root), hb._hash(hb.W)})
    seed_trace(configured_db, t1, seq_start=1)
    seed_trace(configured_db, hb.t2(), seq_start=100)
    driver.drain(0, driver.Config(find.exact, "exact", window_hours=0))
    with db.transaction() as tx:
        trace = driver.load_trace(tx, "T2")
        priors = driver.load_priors(tx, trace, 2.0)
    (at_write,) = [p for p in priors if p.at.leg == hb.T1_WRITE and p.entity_id == "store"]
    assert [(o.unit.origin.leg, o.unit.value.canonical) for o in at_write.occurrences] == [(("t1-ua", "request"), hb.S)]


def test_a_prior_leg_rewritten_in_place_offers_no_edges_stored_at_its_old_payload(configured_db: str) -> None:
    """Edges into a leg are read at the leg's CURRENT payload
    hash (as the risk engine reads them); a leg rewritten in place since its last
    derivation offers only its new payload's own findings until the stale arm
    re-derives it."""
    from tests.label_lineage import test_priors as hb

    seed_trace(configured_db, hb.t1(), seq_start=1)
    seed_trace(configured_db, hb.t2(), seq_start=100)
    driver.drain(0, driver.Config(find.exact, "exact", window_hours=0))
    # T2's store read holds S reached from nowhere in T2 (the store is memoryless
    # inside a trace); give T2's egress an edge, then rewrite the egress's payload.
    egress_hash = next(h for h, c in hb.t2().payloads.items() if c == hb.E)
    assert _q(configured_db, "SELECT count(*) FROM payload_label_edges WHERE to_payload_hash = %s", (egress_hash,))[0][0] >= 1
    new_hash, new_content = "h-rewritten", {"payload": {"note": "nothing"}}
    with psycopg.connect(configured_db) as conn:
        conn.execute("INSERT INTO interaction_payloads (content_hash, content, byte_size) VALUES (%s, %s, 1)",
                     (new_hash, psycopg.types.json.Jsonb(new_content)))
        conn.execute("UPDATE interaction_legs SET payload_hash = %s WHERE interaction_id = %s AND leg_type = 'request'",
                     (new_hash, hb.T2_EGRESS[0]))
        conn.execute("INSERT INTO payload_classifications (content_hash, sensitivity_level, regulatory_tags, "
                     "contains_identity_bundle, is_personalized, primary_domain, findings, model_version) "
                     "VALUES (%s, 'PUBLIC', '{}', false, false, 'test', '[]', 1)", (new_hash,))
        conn.commit()
    later = hb._trace("T3", [("t3-store", "request", "a", "store", {"key": "k"}),
                             ("t3-store", "response", "a", "store", hb.P)],
                      hb.T0 + dt.timedelta(minutes=20), classified=set())
    seed_trace(configured_db, later, seq_start=200)
    with db.transaction() as tx:
        trace = driver.load_trace(tx, "T3")
        priors = driver.load_priors(tx, trace, 2.0)
    at_egress = [p for p in priors if p.at.leg == hb.T2_EGRESS]
    assert at_egress == []  # the rewritten payload holds nothing; the old edges are not offered


def test_an_entity_that_keeps_no_state_carries_nothing_between_conversations(configured_db: str) -> None:
    """The user ``u`` (kind ``user``) is on T1's root
    request with the agent; it is no carrier. An LLM is the same case: a memoryless
    entity shared by every tenant must never link two conversations."""
    from data_governance.processors.data_lineage.traversal import Entity
    from tests.label_lineage import test_priors as hb

    seed_trace(configured_db, hb.t1(), seq_start=1)
    t2 = hb.t2()
    seed_trace(configured_db, t2, seq_start=100)
    with db.transaction() as tx:
        trace = driver.load_trace(tx, "T2")
        assert trace.entities["u"].kind == "user"
        priors = driver.load_priors(tx, trace, 2.0)
    assert priors and "u" not in {p.entity_id for p in priors}
    assert driver.stateful_entity_ids(trace) == ["a", "partner", "store"]
    llm = {**trace.entities, "llm": Entity(id="llm", natural_key="llm:shared", kind="llm")}
    assert "llm" not in driver.stateful_entity_ids(dataclasses.replace(trace, entities=llm))
