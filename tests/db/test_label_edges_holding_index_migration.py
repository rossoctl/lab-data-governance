"""Migration 0030: the label edges' to-index gains the payload hash (the holding
an edge enters), so the labels read looks edges up by holding."""

from __future__ import annotations

import psycopg


def _to_index(dsn: str) -> str:
    with psycopg.connect(dsn) as conn:
        (definition,) = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'payload_label_edges_to_idx'").fetchone()
    return definition


def test_the_to_index_names_the_holding(migrated_dsn: str) -> None:
    assert _to_index(migrated_dsn).endswith("(to_interaction_id, to_leg_type, to_payload_hash)")


def test_downgrade_restores_the_two_column_index_and_upgrade_returns(pg_dsn: str, monkeypatch) -> None:
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0029_hash_evidence_fingerprints")
    assert _to_index(pg_dsn).endswith("(to_interaction_id, to_leg_type)")
    command.upgrade(cfg, "head")
    assert _to_index(pg_dsn).endswith("(to_interaction_id, to_leg_type, to_payload_hash)")


def test_0030_follows_0029_in_the_chain() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_revision("0030_label_edges_holding_index").down_revision == "0029_hash_evidence_fingerprints"
