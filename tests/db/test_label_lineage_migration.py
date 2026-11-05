"""Tests for migration 0024 — the label-lineage tables.

Like the sibling migration tests these run the real chain against a real Postgres
(testcontainers) and assert what it produces: the four tables, their keys and
CHECKs, the indexes the processor and the risk engine read through, and a
downgrade that removes them and an upgrade that brings them back.
"""

from __future__ import annotations

import psycopg
import pytest

TABLES = ("payload_label_edges", "leg_inherited_labels", "label_lineage_trace_status",
          "risk_recompute_requests")


def _tables(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
    return {r[0] for r in rows}


def _columns(dsn: str, table: str) -> dict[str, tuple[str, str]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
    return {name: (dt, nullable) for name, dt, nullable in rows}


def _indexes(dsn: str, table: str) -> dict[str, str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s", (table,)
        ).fetchall()
    return dict(rows)


_EDGE = {
    "edge_id": "e1", "trace_id": "T", "to_interaction_id": "ix-out", "to_leg_type": "request",
    "to_payload_hash": "h-out", "from_interaction_id": "ix-in", "from_leg_type": "response",
    "from_payload_hash": "h-in", "origin_interaction_id": "ix-in", "origin_leg_type": "response",
    "origin_payload_hash": "h-in", "unit_start": 10, "unit_end": 21, "unit_text": "123",
    "unit_value": "123-45-6789", "entity_type": "SSN", "regulatory_tags": ["PII"],
    "sensitivity_level": "RESTRICTED", "matched_text": "123-45-6789", "matched_start": 5,
    "matched_end": 16, "matcher": "exact", "match_kind": "whole", "score": 1, "held_from": 0,
    "held_to": 11, "seq": 7,
}


def _insert_edge(conn: psycopg.Connection, **overrides) -> None:
    row = {**_EDGE, **overrides}
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    conn.execute(f"INSERT INTO payload_label_edges ({cols}) VALUES ({marks})", list(row.values()))


def test_the_four_tables_exist(migrated_dsn: str) -> None:
    assert set(TABLES) <= _tables(migrated_dsn)


def test_the_edge_carries_both_occurrences_and_the_nullable_seams(migrated_dsn: str) -> None:
    cols = _columns(migrated_dsn, "payload_label_edges")
    for nullable in ("from_trace_id", "origin_trace_id", "matched_text", "matched_start", "matched_end"):
        assert cols[nullable][1] == "YES", nullable
    for required in ("edge_id", "trace_id", "to_payload_hash", "from_payload_hash", "origin_payload_hash",
                     "unit_start", "unit_end", "unit_value", "entity_type", "matcher", "match_kind",
                     "score", "held_from", "held_to", "recombined", "seq"):
        assert cols[required][1] == "NO", required
    assert cols["score"][0] == "numeric" and cols["regulatory_tags"][0] == "ARRAY"


def test_a_whole_edge_writes_and_the_same_id_twice_is_refused(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        _insert_edge(conn)
        (recombined, from_trace) = conn.execute(
            "SELECT recombined, from_trace_id FROM payload_label_edges").fetchone()
        assert recombined is False and from_trace is None
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert_edge(conn)


@pytest.mark.parametrize("kind", ["whole", "normalized", "structural", "fragment", "semantic"])
def test_every_declared_match_kind_is_accepted(migrated_dsn: str, kind: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        _insert_edge(conn, match_kind=kind)
        conn.rollback()


@pytest.mark.parametrize("overrides", [
    {"match_kind": "fuzzy"},
    {"score": 1.5},
    {"held_from": 5, "held_to": 2},
    {"matched_start": None},
])
def test_the_checks_refuse_a_malformed_edge(migrated_dsn: str, overrides: dict) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_edge(conn, **overrides)


def test_a_span_less_edge_is_allowed(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        _insert_edge(conn, matched_text=None, matched_start=None, matched_end=None, match_kind="semantic",
                     score=0.8)
        conn.rollback()


def test_the_status_check_takes_complete_or_partial_only(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO label_lineage_trace_status (trace_id, status, unclassified_legs, "
            "unanchored_findings, derived_legs, edge_count, matcher) VALUES ('T', 'partial', 1, 0, 3, 2, 'exact')")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO label_lineage_trace_status (trace_id, status, unclassified_legs, "
                "unanchored_findings, derived_legs, edge_count, matcher) VALUES ('U', 'unknown', 0, 0, 0, 0, 'exact')")


def test_a_leg_row_defaults_to_nothing_inherited(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO leg_inherited_labels (interaction_id, leg_type, trace_id, payload_hash, seq, "
            "matcher, classified) VALUES ('ix', 'request', 'T', 'h', 1, 'exact', true)")
        row = conn.execute(
            "SELECT inherited_tags_whole, inherited_tags_fragment, max_inherited_level, origin_count, "
            "recombined, recombined_units, edge_count FROM leg_inherited_labels").fetchone()
    assert row == ([], [], None, 0, False, [], 0)


def test_the_read_paths_are_indexed(migrated_dsn: str) -> None:
    edges = " ".join(_indexes(migrated_dsn, "payload_label_edges").values())
    assert "(trace_id)" in edges
    assert "(to_interaction_id, to_leg_type)" in edges
    assert "(origin_payload_hash, unit_start, unit_end)" in edges
    assert "(trace_id)" in " ".join(_indexes(migrated_dsn, "leg_inherited_labels").values())


def test_downgrade_drops_the_tables_and_upgrade_restores_them(pg_dsn: str, monkeypatch) -> None:
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    assert set(TABLES) <= _tables(pg_dsn)
    command.downgrade(cfg, "0023_trace_risk_seq")
    assert not (set(TABLES) & _tables(pg_dsn))
    command.upgrade(cfg, "head")
    assert set(TABLES) <= _tables(pg_dsn)


def test_0024_follows_0023_in_the_chain() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    rev = script.get_revision("0024_label_lineage")
    assert rev.down_revision == "0023_trace_risk_seq"
