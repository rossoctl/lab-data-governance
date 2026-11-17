"""Tests for migration 0031 — what persistent entities received, and the crossings.

The real chain against a real Postgres: the three tables, their CHECKs, the indexes the
derivation loads and replaces through, no change to the 0024 tables, and a downgrade
that removes them and an upgrade that brings them back.
"""

from __future__ import annotations

import psycopg
import pytest

TABLES = ("label_occurrences", "label_crossings", "label_crossed_edges")

_OCCURRENCE = {
    "occurrence_id": "o1", "holder_entity_id": "v", "trace_id": "T1", "interaction_id": "ix", "leg_type": "request",
    "payload_hash": "h", "value_text": "123-45-6789", "value_hash": "vh", "core_hash": "ch", "word_count": 3,
    "span_start": 9, "span_end": 20, "record_start": 0, "record_end": 22, "origin_trace_id": "T1",
    "origin_interaction_id": "ix", "origin_leg_type": "request", "origin_payload_hash": "h", "unit_start": 9,
    "unit_end": 20, "unit_text": "123-45-6789", "unit_value": "123-45-6789", "entity_type": "SSN",
    "regulatory_tags": ["PII"], "sensitivity_level": "RESTRICTED", "held_from": 0, "held_to": 11,
    "label_basis": "classified", "level": None,
    "valid_from": "2026-11-13T09:00:00Z", "valid_to": "2026-11-13T09:15:00Z",
}
_CARRIED = {"edge_id": "e1", "trace_id": "T2", "level": "corroborated", "ambiguous": True}
_CROSSING = {"crossing_id": "c1", "trace_id": "T2", "interaction_id": "ix2", "leg_type": "response",
             "value_hash": "vh", "conversations": 1, "edge_id": "e1", "occurrence_id": "o1", "level": "value_only"}


def _insert(conn: psycopg.Connection, table: str, row: dict, **overrides) -> None:
    row = {**row, **overrides}
    conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})",
                 list(row.values()))


def _tables(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        return {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'").fetchall()}


def test_the_tables_take_an_occurrence_a_crossing_a_capped_value_and_a_carried_level(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        _insert(conn, "label_occurrences", _OCCURRENCE)
        _insert(conn, "label_occurrences", _OCCURRENCE, occurrence_id="o2", label_basis="crossed", level="corroborated",
                record_start=None, record_end=None)
        _insert(conn, "label_crossings", _CROSSING)
        _insert(conn, "label_crossings", _CROSSING, crossing_id="c2", edge_id=None, occurrence_id=None, level=None,
                conversations=4)
        _insert(conn, "label_crossed_edges", _CARRIED)


@pytest.mark.parametrize("table, overrides", [
    ("label_occurrences", {"label_basis": "guessed"}),
    ("label_occurrences", {"level": "value_only"}),                     # a level without a crossed basis
    ("label_occurrences", {"label_basis": "crossed"}),                  # crossed without a level
    ("label_occurrences", {"valid_to": "2026-11-13T09:00:00Z"}),        # an empty interval
    ("label_occurrences", {"record_end": None}),
    ("label_crossings", {"level": None}),                               # an edge without a level
    ("label_crossings", {"occurrence_id": None}),
    ("label_crossings", {"level": "strong"}),
    ("label_crossings", {"conversations": -1}),
    ("label_crossed_edges", {"level": "certain"}),                      # certain is never stored: no row
])
def test_the_checks_refuse_a_malformed_row(migrated_dsn: str, table: str, overrides: dict) -> None:
    row = {"label_occurrences": _OCCURRENCE, "label_crossings": _CROSSING, "label_crossed_edges": _CARRIED}[table]
    with psycopg.connect(migrated_dsn) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(conn, table, row, **overrides)


def test_one_edge_has_one_crossing_row(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        _insert(conn, "label_crossings", _CROSSING)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert(conn, "label_crossings", _CROSSING, crossing_id="c2")


def test_the_load_and_the_replace_are_indexed(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        defs = " ".join(r[0] for r in conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = ANY(%s)", (list(TABLES),)).fetchall())
    for cols in ("(holder_entity_id, core_hash, valid_to)", "(holder_entity_id, valid_to) INCLUDE (word_count, valid_from, trace_id)",
                 "(trace_id)"):
        assert cols in defs, cols
    assert "value_hash)" not in defs and "first_word" not in defs
    assert "label_crossings_occurrence_idx" not in defs       # nothing reads crossings by their occurrence


def test_downgrade_drops_the_tables_and_upgrade_restores_them(pg_dsn: str, monkeypatch) -> None:
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    assert set(TABLES) <= _tables(pg_dsn)
    command.downgrade(cfg, "0030_label_edges_holding_index")
    assert not (set(TABLES) & _tables(pg_dsn)) and "payload_label_edges" in _tables(pg_dsn)
    command.upgrade(cfg, "head")
    assert set(TABLES) <= _tables(pg_dsn)


def test_0031_follows_0030_in_the_chain() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_revision("0031_label_crossings").down_revision == "0030_label_edges_holding_index"
