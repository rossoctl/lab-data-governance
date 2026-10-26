"""Migration 0025 — ``label_lineage_dirty_traces`` (slice 2 of label lineage, #273),
and nothing else: one row per trace to derive again because a trace it read
through the cross-conversation priors changed."""

from __future__ import annotations

import psycopg

from tests.db.test_label_lineage_migration import _columns, _tables

TABLE = "label_lineage_dirty_traces"


def test_the_table_exists_with_its_three_columns(migrated_dsn: str) -> None:
    assert TABLE in _tables(migrated_dsn)
    cols = _columns(migrated_dsn, TABLE)
    assert set(cols) == {"trace_id", "requested_at", "reason"}
    assert cols["trace_id"][0] == "text" and cols["reason"][1] == "NO"


def test_one_row_per_trace_and_a_default_time(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(f"INSERT INTO {TABLE} (trace_id, reason) VALUES ('t', 'source changed')")
        conn.execute(f"INSERT INTO {TABLE} (trace_id, reason) VALUES ('t', 'again') "
                     "ON CONFLICT (trace_id) DO UPDATE SET reason = EXCLUDED.reason")
        rows = conn.execute(f"SELECT trace_id, reason, requested_at IS NOT NULL FROM {TABLE}").fetchall()
    assert rows == [("t", "again", True)]


def test_0025_adds_no_other_table_and_follows_0024(pg_dsn: str, monkeypatch) -> None:
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "0024_label_lineage")
    before = _tables(pg_dsn)
    command.upgrade(cfg, "head")
    assert _tables(pg_dsn) - before == {TABLE}
    command.downgrade(cfg, "0024_label_lineage")
    assert _tables(pg_dsn) == before
    command.upgrade(cfg, "head")
    assert TABLE in _tables(pg_dsn)
