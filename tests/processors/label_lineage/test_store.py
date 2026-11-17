"""``processors/label_lineage/store``: deterministic keys, upserts that converge, a
trace-scoped delete by set membership."""

from __future__ import annotations

import dataclasses
import datetime as dt

import psycopg

from data_governance import db
from data_governance.label_lineage import cross, find, fold
from data_governance.processors.label_lineage import store
from tests.label_lineage.fixtures import egress, legs_to, trace_input
from tests.label_lineage.test_cross import SSN, T0, _first, _holdings, _second


def _derive(row: str, **replace) -> fold.Derivation:
    t = trace_input(row)
    return fold.derive(dataclasses.replace(t, **replace) if replace else t, find.exact)


def _rows(dsn: str, sql: str) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql).fetchall()


def test_edge_ids_are_deterministic_and_distinct() -> None:
    a, b = _derive("R4"), _derive("R4")
    ids = [store.edge_id(e) for e in a.edges]
    assert ids == [store.edge_id(e) for e in b.edges]
    assert len(set(ids)) == len(ids)
    t = trace_input("R4")
    reversed_ids = {store.edge_id(e) for e in fold.derive(dataclasses.replace(t, legs=t.legs[::-1]), find.exact).edges}
    assert reversed_ids == set(ids)


def test_writing_one_derivation_twice_is_one_set_of_rows(configured_db: str) -> None:
    d = _derive("R4")
    for _ in range(2):
        with db.transaction() as tx:
            store.write(tx, d, "exact")
    assert _rows(configured_db, "SELECT count(*) FROM payload_label_edges") == [(len(d.edges),)]
    assert _rows(configured_db, "SELECT count(*) FROM leg_inherited_labels") == [(len(d.legs),)]
    (status,) = _rows(configured_db, "SELECT status, derived_legs, edge_count, matcher FROM label_lineage_trace_status")
    assert status == ("complete", len(d.legs), len(d.edges), "exact")


def test_an_edge_row_is_the_edge_flattened(configured_db: str) -> None:
    d = _derive("R4")
    with db.transaction() as tx:
        store.write(tx, d, "exact")
    t = trace_input("R4")
    leg = egress("R4", t)
    (row,) = _rows(
        configured_db,
        "SELECT unit_value, unit_text, match_kind, score, held_from, held_to, matched_text, from_trace_id, "
        "origin_trace_id, recombined, origin_leg_type::text FROM payload_label_edges "
        f"WHERE to_interaction_id = '{leg.interaction_id}' AND entity_type = 'SSN' "
        "AND from_leg_type = 'response' AND origin_leg_type = 'response' AND from_payload_hash = origin_payload_hash",
    )
    assert row[:3] == ("123-45-6789", "123", "whole")
    assert float(row[3]) == 1.0 and row[4:7] == (0, 11, "123-45-6789")
    assert row[7:] == (None, None, False, "response")


def test_a_shorter_derivation_deletes_exactly_the_rows_it_no_longer_produces(configured_db: str) -> None:
    full = _derive("R4")
    other = _derive("R2")
    with db.transaction() as tx:
        store.write(tx, full, "exact")
        store.write(tx, other, "exact")
    t = trace_input("R4")
    (store_read,) = [x for x in legs_to("R4", t, "lab-store", "response") if t.findings[x.payload_hash]]
    shorter = _derive("R4", findings={h: f for h, f in t.findings.items() if h != store_read.payload_hash})
    with db.transaction() as tx:
        store.write(tx, shorter, "exact")
    kept = {r[0] for r in _rows(configured_db, f"SELECT edge_id FROM payload_label_edges WHERE trace_id = '{t.trace_id}'")}
    assert kept == {store.edge_id(e) for e in shorter.edges} and len(kept) < len(full.edges)
    assert _rows(configured_db, f"SELECT count(*) FROM payload_label_edges WHERE trace_id = '{other.trace_id}'") == [
        (len(other.edges),)]
    (status,) = _rows(configured_db, f"SELECT status FROM label_lineage_trace_status WHERE trace_id = '{t.trace_id}'")
    assert status == ("partial",)


def test_a_first_row_with_edges_counts_as_changed(configured_db: str) -> None:
    """A leg derived for the first time may already have been decided without its
    labels (the processor deployed later, or leg-ready's cursor ran ahead): a new
    row with edges is a change; a new row with none is not."""
    d = _derive("R4")
    with db.transaction() as tx:
        changed = store.write(tx, d, "exact")
    assert changed == {key[0] for key, leg in d.legs.items() if leg.edge_count > 0}
    assert any(leg.edge_count == 0 for leg in d.legs.values())


def test_write_reports_the_interactions_whose_summary_changed(configured_db: str) -> None:
    t = trace_input("R4")
    with db.transaction() as tx:
        store.write(tx, _derive("R4"), "exact")
    with db.transaction() as tx:
        assert store.write(tx, _derive("R4"), "exact") == set()
    (store_read,) = [x for x in legs_to("R4", t, "lab-store", "response") if t.findings[x.payload_hash]]
    without = {h: f for h, f in t.findings.items() if h != store_read.payload_hash}
    with db.transaction() as tx:
        changed = store.write(tx, _derive("R4", findings=without), "exact")
    assert egress("R4", t).interaction_id in changed
    assert store_read.interaction_id not in changed  # its own inherited summary did not move


# --- across conversations: occurrences and crossings ---------------------------------------------------------------

def _crossing() -> tuple[fold.Derivation, fold.Derivation]:
    first = fold.derive(_first(), find.exact, crossing=cross.Crossing())
    return first, fold.derive(_second(first.holdings), find.exact, crossing=cross.Crossing())


def test_a_crossing_is_an_edge_row_with_its_level_beside_it(configured_db: str) -> None:
    first, second = _crossing()
    with db.transaction() as tx:
        store.write(tx, first, "exact")
        store.write(tx, second, "exact")
    rows = _rows(configured_db,
                 "SELECT e.entity_type, e.from_trace_id, e.origin_trace_id, c.level, c.conversations, "
                 "o.trace_id, o.evidence FROM payload_label_edges e JOIN label_crossings c ON c.edge_id = e.edge_id "
                 "JOIN label_occurrences o ON o.occurrence_id = c.occurrence_id ORDER BY 1")
    assert rows == [("DOB", "T1", "T1", "corroborated", 1, "T1", "classified"),
                    ("SSN", "T1", "T1", "corroborated", 1, "T1", "classified")]
    assert _rows(configured_db, "SELECT count(*) FROM payload_label_edges WHERE trace_id = 'T2' "
                                "AND origin_trace_id = 'T1' AND from_trace_id IS NULL") == [(2,)]


def test_an_occurrence_row_carries_its_value_core_record_and_interval(configured_db: str) -> None:
    import hashlib
    first, _ = _crossing()
    with db.transaction() as tx:
        store.write(tx, first, "exact")
    rows = _rows(configured_db, "SELECT value_text, core_hash, word_count, record_start IS NOT NULL, entity_type, "
                                "valid_to - valid_from FROM label_occurrences ORDER BY 1")
    sha = lambda s: hashlib.sha256(s.encode()).hexdigest()
    assert [r[:5] for r in rows] == [("123-45-6789", sha("123-45-6789"), 3, True, "SSN"),
                                     ("1961-03-14", sha("1961-03-14"), 3, True, "DOB")]
    assert {r[5].total_seconds() for r in rows} == {900}


def test_deriving_again_replaces_the_rows_and_without_the_switch_stores_nothing(configured_db: str) -> None:
    first, second = _crossing()
    for _ in range(2):
        with db.transaction() as tx:
            store.write(tx, first, "exact")
            store.write(tx, second, "exact")
    assert _rows(configured_db, "SELECT trace_id, count(*) FROM label_occurrences GROUP BY 1") == [("T1", 2)]
    assert _rows(configured_db, "SELECT count(*) FROM label_crossings") == [(2,)]
    with db.transaction() as tx:
        store.write(tx, fold.derive(_second(), find.exact), "exact")
        store.write(tx, fold.derive(_first(), find.exact), "exact")
    assert _rows(configured_db, "SELECT (SELECT count(*) FROM label_crossings), "
                                "(SELECT count(*) FROM label_occurrences)") == [(0, 0)]


def test_a_common_value_is_a_crossing_row_without_an_edge(configured_db: str) -> None:
    held = [h for i in range(4) for h in _holdings(f"P{i}", {"ssn": SSN}, ("SSN", SSN),
                                                    at=T0 + dt.timedelta(seconds=i))]
    d = fold.derive(_second(held), find.exact, crossing=cross.Crossing())
    with db.transaction() as tx:
        store.write(tx, d, "exact")
    assert _rows(configured_db, "SELECT leg_type::text, conversations, edge_id, level FROM label_crossings") == [
        ("response", 4, None, None)]


def test_every_edge_that_rests_on_a_crossing_has_its_carried_row(configured_db: str) -> None:
    first, second = _crossing()
    for _ in range(2):
        with db.transaction() as tx:
            store.write(tx, first, "exact")
            store.write(tx, second, "exact")
    rows = _rows(configured_db,
                 "SELECT e.to_interaction_id, e.to_leg_type::text, e.entity_type, c.trace_id, c.level, c.ambiguous "
                 "FROM label_crossed_edges c JOIN payload_label_edges e ON e.edge_id = c.edge_id ORDER BY 1, 2, 3")
    assert rows == [("2", "response", "DOB", "T2", "corroborated", False),
                    ("2", "response", "SSN", "T2", "corroborated", False),
                    ("3", "request", "DOB", "T2", "corroborated", False),
                    ("3", "request", "SSN", "T2", "corroborated", False)]
    with db.transaction() as tx:
        store.write(tx, fold.derive(_second(), find.exact), "exact")
    assert _rows(configured_db, "SELECT count(*) FROM label_crossed_edges") == [(0,)]


def test_a_changed_level_alone_is_a_change(configured_db: str) -> None:
    """The same edges and summaries, the level they were carried with changed: the decisions on those legs read
    the level, so their interactions are recomputed."""
    first, second = _crossing()
    weaker = [dataclasses.replace(h, evidence="crossed", level=cross.VALUE_ONLY) for h in first.holdings]
    again = fold.derive(_second(weaker), find.exact, crossing=cross.Crossing())
    assert {store.edge_id(e) for e in again.edges} == {store.edge_id(e) for e in second.edges}
    assert again.legs == second.legs
    with db.transaction() as tx:
        store.write(tx, second, "exact")
    with db.transaction() as tx:
        assert store.write(tx, second, "exact") == set()
        assert store.write(tx, again, "exact") == {"2", "3"}


# --- the correction and the limits ------------------------------------------------------------------

def _write_and_correct(d: fold.Derivation) -> list[str]:
    with db.transaction() as tx:
        before = store.occurrences(tx, d.trace_id)
        store.write(tx, d, "exact")
        return store.correct(tx, d.trace_id, before)


def _queued(dsn: str) -> list[str]:
    return [r[0] for r in _rows(dsn, "SELECT trace_id FROM label_lineage_dirty_traces ORDER BY 1")]


def test_what_a_persistent_entity_sends_is_recorded_and_replaced(configured_db: str) -> None:
    first, second = _crossing()
    for _ in range(2):
        with db.transaction() as tx:
            store.write(tx, first, "exact")
            store.write(tx, second, "exact")
    assert _rows(configured_db, "SELECT trace_id, interaction_id, leg_type::text, entity_id FROM label_sends "
                                "ORDER BY 1") == [("T1", "1", "response", "v"), ("T2", "2", "response", "v")]
    with db.transaction() as tx:
        store.write(tx, fold.derive(_second(), find.exact), "exact")
    assert _rows(configured_db, "SELECT trace_id FROM label_sends") == [("T1",)]


def test_an_added_occurrence_queues_the_conversation_its_entity_sends_in_inside_the_interval(configured_db) -> None:
    first, second = _crossing()
    late = fold.derive(dataclasses.replace(_second(), trace_id="T3", legs=[
        dataclasses.replace(x, interaction_id=f"late-{x.interaction_id}",
                            occurred_at=x.occurred_at + cross.Crossing().retention) for x in _second().legs]),
        find.exact, crossing=cross.Crossing())
    _write_and_correct(fold.derive(_second(), find.exact, crossing=cross.Crossing()))   # derived before T1 arrived
    _write_and_correct(late)                                                        # after T1's interval
    assert _write_and_correct(first) == ["T2"]          # T1's own send inside the interval is not queued: own
    assert _queued(configured_db) == ["T2"]


def test_a_send_is_corrected_until_the_last_instant_of_the_interval_and_not_at_its_end(configured_db) -> None:
    first, _ = _crossing()
    retention = cross.Crossing().retention

    def sent_at(trace_id: str, t: dt.datetime) -> fold.Derivation:
        return fold.derive(dataclasses.replace(_second(), trace_id=trace_id, legs=[
            dataclasses.replace(x, interaction_id=f"{trace_id}-{x.interaction_id}", occurred_at=t)
            for x in _second().legs]), find.exact, crossing=cross.Crossing())

    _write_and_correct(sent_at("T5", T0 + retention - dt.timedelta(seconds=1)))
    _write_and_correct(sent_at("T6", T0 + retention))
    assert _write_and_correct(first) == ["T5"]


def test_an_unchanged_derivation_queues_nothing_and_a_removed_or_changed_occurrence_queues_again(
        configured_db: str) -> None:
    first, second = _crossing()
    _write_and_correct(second)
    _write_and_correct(first)
    with psycopg.connect(configured_db) as conn:
        conn.execute("DELETE FROM label_lineage_dirty_traces")
    assert _write_and_correct(first) == []
    weaker = dataclasses.replace(first, holdings=tuple(
        dataclasses.replace(h, evidence="crossed", level=cross.VALUE_ONLY) for h in first.holdings))
    assert _write_and_correct(weaker) == ["T2"]
    assert _write_and_correct(fold.derive(_first(), find.exact)) == ["T2"]      # without the switch: removed
    assert _write_and_correct(fold.derive(_first(), find.exact)) == []


def test_a_cut_is_a_crossing_row_naming_its_limit_and_is_replaced(configured_db: str) -> None:
    first, _ = _crossing()
    t = dataclasses.replace(_second(first.holdings), held_elsewhere={("2", "response"): 2})
    cut = fold.derive(t, find.exact, crossing=cross.Crossing(holdings_limit=1))
    with db.transaction() as tx:
        store.write(tx, cut, "exact")
    assert _rows(configured_db, "SELECT interaction_id, leg_type::text, cut, value_hash, edge_id "
                                "FROM label_crossings") == [("2", "response", "holdings_per_entity", None, None)]
    with db.transaction() as tx:
        store.write(tx, fold.derive(_second(first.holdings), find.exact, crossing=cross.Crossing()), "exact")
    assert _rows(configured_db, "SELECT count(*) FROM label_crossings WHERE cut IS NOT NULL") == [(0,)]
