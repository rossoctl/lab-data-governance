"""``processors/label_lineage/store``: deterministic keys, upserts that converge, a
trace-scoped delete by set membership."""

from __future__ import annotations

import dataclasses

import psycopg

from data_governance import db
from data_governance.label_lineage import find, fold
from data_governance.processors.label_lineage import store
from tests.label_lineage.fixtures import egress, legs_to, trace_input


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
