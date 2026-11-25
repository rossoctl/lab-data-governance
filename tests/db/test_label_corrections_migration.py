"""Tests for migration 0032 — what a correction reads, and the cut a limit leaves.

The real chain against a real Postgres: ``label_sends`` and its indexes, the ``cut``
row of ``label_crossings`` and its CHECKs, the holdings count served by 0031's index,
and a downgrade that leaves exactly the schema of 0031 and an upgrade that brings 0032
back.
"""

from __future__ import annotations

import datetime as dt

import psycopg
import pytest

from data_governance.processors.label_lineage import driver

T = dt.datetime(2026, 11, 13, 9, 5, tzinfo=dt.timezone.utc)
_CROSSING = {"crossing_id": "c1", "trace_id": "T2", "interaction_id": "ix2", "leg_type": "response",
             "value_hash": "vh", "conversations": 1, "edge_id": "e1", "occurrence_id": "o1", "level": "value_only"}
_CUT = {"crossing_id": "c3", "trace_id": "T2", "interaction_id": "ix2", "leg_type": "response", "cut": "payload_bytes"}
_SEND = {"interaction_id": "ix2", "leg_type": "response", "entity_id": "v", "trace_id": "T2",
         "occurred_at": "2026-11-13T09:05:00Z"}


def _insert(conn: psycopg.Connection, table: str, row: dict, **overrides) -> None:
    row = {**row, **overrides}
    conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})",
                 list(row.values()))


def _schema(dsn: str) -> tuple[list[tuple], list[str], list[str], set[str]]:
    """``label_crossings``' columns and CHECKs, the indexes of the 0031 tables, and the public tables."""
    with psycopg.connect(dsn) as conn:
        columns = conn.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'label_crossings' ORDER BY ordinal_position").fetchall()
        checks = sorted(r[0] for r in conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'label_crossings'::regclass "
            "AND contype = 'c'").fetchall())
        indexes = sorted(r[0] for r in conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = ANY(%s)",
            (["label_occurrences", "label_crossings", "label_crossed_edges"],)).fetchall())
        tables = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'").fetchall()}
    return columns, checks, indexes, tables


def test_the_tables_take_a_cut_and_a_send(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        _insert(conn, "label_crossings", _CROSSING)
        _insert(conn, "label_crossings", _CROSSING, crossing_id="c2", edge_id=None, occurrence_id=None, level=None,
                conversations=4)
        _insert(conn, "label_crossings", _CUT)
        _insert(conn, "label_sends", _SEND)


@pytest.mark.parametrize("row, overrides", [
    ("crossing", {"value_hash": None}),                         # neither a value nor a cut
    ("crossing", {"conversations": None}),
    ("crossing", {"cut": "payload_bytes"}),                     # a cut that names a value and an edge
    ("crossing", {"edge_id": None, "occurrence_id": None, "level": None, "cut": "holdings_per_entity"}),
    ("cut", {"cut": "patience"}),
])
def test_the_checks_refuse_a_malformed_row(migrated_dsn: str, row: str, overrides: dict) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(conn, "label_crossings", {"crossing": _CROSSING, "cut": _CUT}[row], **overrides)


def test_one_leg_has_one_send_row(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        _insert(conn, "label_sends", _SEND)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert(conn, "label_sends", _SEND, trace_id="T3")


def test_the_correction_and_the_holdings_count_are_indexed(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        defs = " ".join(r[0] for r in conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = ANY(%s)",
            (["label_occurrences", "label_sends"],)).fetchall())
        conn.execute("SET enable_seqscan = off")     # an empty table: ask whether an index can serve the count
        plan = " ".join(r[0] for r in conn.execute("EXPLAIN " + driver._HELD_SQL, ("e", "T1", T, T, 2)).fetchall())
    for cols in ("label_sends_entity_idx ON public.label_sends USING btree (entity_id, occurred_at)",
                 "label_sends_trace_idx ON public.label_sends USING btree (trace_id)"):
        assert cols in defs, cols
    assert "label_occurrences_held_idx" not in defs                 # 0031's index serves the holdings count
    assert "Index Only Scan using label_occurrences_valid_idx" in plan, plan


def test_downgrade_leaves_the_0031_schema_and_upgrade_restores_0032(pg_dsn: str, monkeypatch) -> None:
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "0031_label_crossings")
    at_0031 = _schema(pg_dsn)
    command.upgrade(cfg, "head")
    at_head = _schema(pg_dsn)
    assert "label_sends" in at_head[3] and ("cut", "text", "YES") in at_head[0]
    with psycopg.connect(pg_dsn) as conn:                       # a cut row does not stop the downgrade
        _insert(conn, "label_crossings", _CUT)
        _insert(conn, "label_crossings", _CROSSING)
    command.downgrade(cfg, "0031_label_crossings")
    assert _schema(pg_dsn) == at_0031
    with psycopg.connect(pg_dsn) as conn:
        assert conn.execute("SELECT crossing_id FROM label_crossings").fetchall() == [("c1",)]
    command.upgrade(cfg, "head")
    assert _schema(pg_dsn) == at_head


def test_the_upgrade_queues_every_derived_conversation(pg_dsn: str, monkeypatch) -> None:
    """Derived before 0032, a conversation has no send rows and 0031's rule on its rows: the upgrade queues each,
    one already queued included (its generation moves, so an acknowledgment taken before cannot drop it)."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "0031_label_crossings")
    with psycopg.connect(pg_dsn) as conn:
        for trace in ("T1", "T2", "T3"):
            conn.execute("INSERT INTO label_lineage_trace_status (trace_id, status, unclassified_legs, "
                         "unanchored_findings, derived_legs, edge_count, matcher) "
                         "VALUES (%s, 'complete', 0, 0, 1, 0, 'exact')", (trace,))
        (queued_before,), = conn.execute("INSERT INTO label_lineage_dirty_traces (trace_id) VALUES ('T3') "
                                         "RETURNING generation").fetchall()
    command.upgrade(cfg, "head")
    with psycopg.connect(pg_dsn) as conn:
        queued = dict(conn.execute("SELECT trace_id, generation FROM label_lineage_dirty_traces").fetchall())
    assert sorted(queued) == ["T1", "T2", "T3"]
    assert queued["T3"] > queued_before


def test_0032_follows_0031_in_the_chain() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_revision("0032_label_corrections").down_revision == "0031_label_crossings"
