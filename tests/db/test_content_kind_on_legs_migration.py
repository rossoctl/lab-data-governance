"""Tests for migration 0022 — ``content_kind`` moves from the payload row to the leg.

``interaction_payloads`` is one row per distinct bytes, referenced from every
leg that carries them; the content kind is the role the bytes played on one
leg, so it belongs to ``interaction_legs`` (issue #286). It stays TEXT
(ADR-0014) and is nullable (a plain-http leg has no semantic body kind).

Like the sibling migration tests these run the real migration chain against a
real Postgres (testcontainers) and assert what it produces, not how.
"""

from __future__ import annotations

import psycopg


def _columns(dsn: str, table: str) -> dict[str, dict[str, object]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
    return {name: {"data_type": dt, "is_nullable": nn} for name, dt, nn in rows}


def test_content_kind_is_on_the_leg_and_not_on_the_payload(migrated_dsn: str) -> None:
    assert _columns(migrated_dsn, "interaction_legs")["content_kind"] == {
        "data_type": "text",
        "is_nullable": "YES",
    }
    assert "content_kind" not in _columns(migrated_dsn, "interaction_payloads")


def test_leg_content_kind_is_text_and_accepts_a_future_kind(migrated_dsn: str) -> None:
    """A new content kind is a code change, not a migration (ADR-0014) — an
    unknown value writes fine, and NULL is a plain-http leg."""
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, "
            "summary) VALUES ('ix', 't', 'a', 'b', 'a -> b')"
        )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, payload_hash, "
            "content_kind) VALUES ('ix', 'request', 'h', 'some_future_kind'), "
            "('ix', 'response', NULL, NULL)"
        )
        rows = dict(
            conn.execute(
                "SELECT leg_type::text, content_kind FROM interaction_legs"
            ).fetchall()
        )
        conn.rollback()
    assert rows == {"request": "some_future_kind", "response": None}


def test_0022_is_applied_in_the_chain() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    walked = {rev.revision for rev in script.walk_revisions()}
    assert "0022_content_kind_on_legs" in walked


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade(0020) -> upgrade moves the column back and forth;
    rows written before the downgrade survive it with the open-world kind."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    assert "content_kind" in _columns(pg_dsn, "interaction_legs")
    assert "content_kind" not in _columns(pg_dsn, "interaction_payloads")
    with psycopg.connect(pg_dsn) as conn:
        conn.execute(
            "INSERT INTO interaction_payloads (content_hash, content, byte_size) "
            "VALUES ('h', '{}'::jsonb, 2)"
        )
        conn.commit()

    command.downgrade(cfg, "0020_entity_namespace")
    assert "content_kind" not in _columns(pg_dsn, "interaction_legs")
    assert _columns(pg_dsn, "interaction_payloads")["content_kind"] == {
        "data_type": "text",
        "is_nullable": "NO",
    }
    with psycopg.connect(pg_dsn) as conn:
        assert conn.execute(
            "SELECT content_kind FROM interaction_payloads WHERE content_hash = 'h'"
        ).fetchone() == ("unknown",)

    command.upgrade(cfg, "head")
    assert "content_kind" in _columns(pg_dsn, "interaction_legs")
    assert "content_kind" not in _columns(pg_dsn, "interaction_payloads")
