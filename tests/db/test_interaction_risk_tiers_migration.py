"""Tests for migration 0033 — the tier that decided each risk record version, and the late finding.

The real chain against a real Postgres: the table takes one row per record version and refuses a tier outside
0..2, a second row for one version and a row for no version; ``interaction_risk_records`` is not altered; a
downgrade removes the table and an upgrade brings it back.
"""

from __future__ import annotations

import psycopg
import pytest

_RECORD = (
    "INSERT INTO interaction_risk_records (interaction_id, trace_id, caller_entity_id, callee_entity_id, version, "
    "computed_at, risk_level, enforcement_type, policy_event_count) "
    "VALUES ('ix', 't', 'a', 'b', %s, now(), 'low', 'allow', 0)"
)
_TIER = "INSERT INTO interaction_risk_tiers (interaction_id, version, tier, late) VALUES (%s, %s, %s, %s)"


def _columns(dsn: str, table: str) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position", (table,)).fetchall()


def test_one_row_per_record_version(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(_RECORD, (1,))
        conn.execute(_RECORD, (2,))
        conn.execute(_TIER, ("ix", 1, 1, False))
        conn.execute(_TIER, ("ix", 2, 2, True))
        assert conn.execute("SELECT version, tier, late FROM interaction_risk_tiers ORDER BY version").fetchall() == [
            (1, 1, False), (2, 2, True)]
    assert _columns(migrated_dsn, "interaction_risk_tiers") == [
        ("interaction_id", "text", "NO"), ("version", "integer", "NO"), ("tier", "smallint", "NO"),
        ("late", "boolean", "NO")]


@pytest.mark.parametrize("row, error", [
    (("ix", 1, 3, False), psycopg.errors.CheckViolation),
    (("ix", 1, -1, False), psycopg.errors.CheckViolation),
    (("ix", 1, 0, None), psycopg.errors.NotNullViolation),
    (("ix", 2, 0, False), psycopg.errors.ForeignKeyViolation),       # a version that was never written
    (("other", 1, 0, False), psycopg.errors.ForeignKeyViolation),
])
def test_the_constraints_refuse_a_malformed_row(migrated_dsn: str, row: tuple, error) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(_RECORD, (1,))
        with pytest.raises(error):
            conn.execute(_TIER, row)


def test_a_deleted_record_takes_its_tier_rows_with_it(migrated_dsn: str) -> None:
    """So a trace-scoped delete of records (demos/risk/rogue/drop_traces.sh) needs no line for this table."""
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(_RECORD, (1,))
        conn.execute(_RECORD, (2,))
        conn.execute(_TIER, ("ix", 1, 0, False))
        conn.execute(_TIER, ("ix", 2, 0, True))
        conn.execute("DELETE FROM interaction_risk_records WHERE trace_id NOT IN ('kept')")
        assert conn.execute("SELECT count(*) FROM interaction_risk_tiers").fetchone()[0] == 0


def test_one_version_has_one_row(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(_RECORD, (1,))
        conn.execute(_TIER, ("ix", 1, 0, False))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(_TIER, ("ix", 1, 1, False))


def test_downgrade_drops_the_table_and_leaves_the_records_and_upgrade_restores_it(pg_dsn: str, monkeypatch) -> None:
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    records = _columns(pg_dsn, "interaction_risk_records")
    command.downgrade(cfg, "0032_label_corrections")
    assert _columns(pg_dsn, "interaction_risk_tiers") == []
    assert _columns(pg_dsn, "interaction_risk_records") == records
    command.upgrade(cfg, "head")
    assert len(_columns(pg_dsn, "interaction_risk_tiers")) == 4
    assert _columns(pg_dsn, "interaction_risk_records") == records


def test_0033_follows_0032_in_the_chain() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_revision("0033_interaction_risk_tiers").down_revision == "0032_label_corrections"
