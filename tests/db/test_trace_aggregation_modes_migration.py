"""Tests for migration 0018 — ``trace_risk_records.enforcement_aggregation_mode``.

Pairs with the already-existing ``risk_compounding_mode`` column (added by
``0016_das_risk_tables``, never populated until this issue's write-path
follow-up): together they let a stored trace risk record say which
risk-level/enforcement-type rollup mode produced it. Both stay nullable
``TEXT`` with no default and no backfill — the derived-table convention this
repo already follows (``0011_drop_leg_original_seq``'s docstring: "always
rebuildable by cursor replay, so no back-fill is needed").
"""

from __future__ import annotations

import psycopg

TABLE = "trace_risk_records"


def _columns(dsn: str, table: str) -> dict[str, dict[str, object]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
    return {
        name: {"data_type": dt, "is_nullable": nn, "column_default": d}
        for name, dt, nn, d in rows
    }


# --- shape at head -----------------------------------------------------------


def test_enforcement_aggregation_mode_column_exists(migrated_dsn: str) -> None:
    cols = _columns(migrated_dsn, TABLE)
    assert "enforcement_aggregation_mode" in cols
    col = cols["enforcement_aggregation_mode"]
    assert col["data_type"] == "text"
    assert col["is_nullable"] == "YES"
    assert col["column_default"] is None


def test_risk_compounding_mode_is_unchanged_by_this_migration(
    migrated_dsn: str,
) -> None:
    col = _columns(migrated_dsn, TABLE)["risk_compounding_mode"]
    assert col["data_type"] == "text"
    assert col["is_nullable"] == "YES"


# --- migration chain ---------------------------------------------------------


def test_revision_is_in_the_chain(migrated_dsn: str) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    walked = {rev.revision for rev in script.walk_revisions()}
    assert "0018_trace_aggregation_modes" in walked


# --- reversibility ------------------------------------------------------------


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    assert "enforcement_aggregation_mode" in _columns(pg_dsn, TABLE)

    command.downgrade(cfg, "0017_policy_decisions")
    assert "enforcement_aggregation_mode" not in _columns(pg_dsn, TABLE)

    command.upgrade(cfg, "head")
    assert "enforcement_aggregation_mode" in _columns(pg_dsn, TABLE)
