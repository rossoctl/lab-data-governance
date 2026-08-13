"""Tests for the interaction policy-decision migration (issue #101).

Migration 0013 adds ``interaction_policy_decisions`` — the persistent record of
each OPA decision made for an interaction, keyed by
``(interaction_id, version)`` and append-only/versioned like
``interaction_risk_records`` (0012). Its purpose is to give "not yet
evaluated" a durable meaning: the interaction risk engine (#101) computes an
``evidence_fingerprint`` from the interaction's current legs/classifications/
spans and only calls OPA (inserting a new decision version) when that
fingerprint differs from the latest stored decision's — otherwise it reuses
the cached decision.

FK-free (0004 convention), TEXT for OPA-sourced enum-shaped columns
(``risk_level``/``enforcement_type``, ADR-0014), ``NUMERIC(4,3)`` for
``confidence`` (same reasoning as 0012's ``overall_confidence`` — a bounded
[0, 1] probability to three decimal places). No NOTIFY trigger is added on
this table in this issue — nothing consumes a decision-written channel yet
(see the module docstring in the migration itself).

Like the sibling migration tests, these run the real migration chain against
a real Postgres (testcontainers) and assert what it produces.
"""

from __future__ import annotations

import psycopg
import pytest


# --- helpers (mirrors tests/db/test_das_risk_tables_migration.py) -----------


def _columns(dsn: str, table: str) -> dict[str, dict[str, object]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default, "
            "numeric_precision, numeric_scale "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
    return {
        name: {
            "data_type": dt,
            "is_nullable": nn,
            "column_default": default,
            "numeric_precision": precision,
            "numeric_scale": scale,
        }
        for name, dt, nn, default, precision, scale in rows
    }


def _primary_key_columns(dsn: str, table: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT a.attname "
            "FROM pg_index i "
            "JOIN pg_attribute a "
            "  ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = %s::regclass AND i.indisprimary",
            (table,),
        ).fetchall()
    return sorted(r[0] for r in rows)


def _index_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_class WHERE relname = %s AND relkind = 'i'",
            (name,),
        ).fetchone()
    return row is not None


def _index_is_unique(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT ix.indisunique FROM pg_index ix "
            "JOIN pg_class c ON c.oid = ix.indexrelid "
            "WHERE c.relname = %s",
            (name,),
        ).fetchone()
    assert row is not None, name
    return bool(row[0])


def _fk_constraints(dsn: str, table: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = %s::regclass AND contype = 'f'",
            (table,),
        ).fetchall()
    return [r[0] for r in rows]


def _insert_min_decision(
    conn: psycopg.Connection,
    *,
    interaction_id: str,
    version: int = 1,
    fingerprint: str = "fp1",
) -> None:
    conn.execute(
        """
        INSERT INTO interaction_policy_decisions (
            interaction_id, version, evaluated_at, risk_level,
            evidence_fingerprint
        ) VALUES (%s, %s, now(), 'low', %s)
        """,
        (interaction_id, version, fingerprint),
    )


# --- structure ----------------------------------------------------------------


def test_table_exists_with_expected_columns(migrated_dsn: str) -> None:
    cols = _columns(migrated_dsn, "interaction_policy_decisions")
    assert cols, "interaction_policy_decisions table must exist"
    for name in (
        "decision_id",
        "interaction_id",
        "version",
        "evaluated_at",
        "risk_level",
        "enforcement_type",
        "allowed_actions",
        "explanation",
        "triggered_rules",
        "confidence",
        "policy_version",
        "evidence_fingerprint",
    ):
        assert name in cols, f"missing column {name!r}"
    assert cols["decision_id"]["data_type"] == "uuid"
    assert cols["decision_id"]["column_default"] == "gen_random_uuid()"
    assert cols["allowed_actions"]["data_type"] == "ARRAY"
    assert cols["triggered_rules"]["data_type"] == "ARRAY"
    # NUMERIC(4,3), not DOUBLE PRECISION/FLOAT — same reasoning as 0012's
    # overall_confidence: a bounded [0, 1] probability to 3dp.
    assert cols["confidence"]["data_type"] == "numeric"
    assert cols["confidence"]["numeric_precision"] == 4
    assert cols["confidence"]["numeric_scale"] == 3
    assert cols["evidence_fingerprint"]["is_nullable"] == "NO"
    assert cols["risk_level"]["is_nullable"] == "NO"
    assert cols["interaction_id"]["is_nullable"] == "NO"
    assert cols["version"]["is_nullable"] == "NO"
    assert cols["evaluated_at"]["is_nullable"] == "NO"


def test_primary_key(migrated_dsn: str) -> None:
    assert _primary_key_columns(migrated_dsn, "interaction_policy_decisions") == [
        "decision_id"
    ]


def test_no_foreign_keys(migrated_dsn: str) -> None:
    """FK-free per the 0004 derived-table convention."""
    assert _fk_constraints(migrated_dsn, "interaction_policy_decisions") == []


def test_indexes_exist(migrated_dsn: str) -> None:
    assert _index_exists(migrated_dsn, "interaction_policy_decisions_version_uq")
    assert _index_is_unique(
        migrated_dsn, "interaction_policy_decisions_version_uq"
    )
    assert _index_exists(migrated_dsn, "interaction_policy_decisions_latest_idx")


def test_interaction_id_version_unique_constraint_rejects_duplicate(
    migrated_dsn: str,
) -> None:
    """Corner case: the same (interaction_id, version) cannot be written
    twice — a decision version is an immutable snapshot."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        _insert_min_decision(conn, interaction_id="ix1", version=1)
        with conn.transaction(), pytest.raises(psycopg.errors.UniqueViolation):
            _insert_min_decision(conn, interaction_id="ix1", version=1)


def test_interaction_id_allows_multiple_versions(migrated_dsn: str) -> None:
    """A re-evaluated interaction inserts a new decision version rather than
    mutating the previous one — the normal recompute case."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        _insert_min_decision(conn, interaction_id="ix2", version=1, fingerprint="fp1")
        _insert_min_decision(conn, interaction_id="ix2", version=2, fingerprint="fp2")
        (count,) = conn.execute(
            "SELECT count(*) FROM interaction_policy_decisions "
            "WHERE interaction_id = %s",
            ("ix2",),
        ).fetchone()
    assert count == 2


def test_confidence_rejects_out_of_range_value(migrated_dsn: str) -> None:
    """NUMERIC(4,3) caps confidence at 4 total digits / 3 after the decimal
    point — an out-of-range value must be rejected at the DB layer."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        with conn.transaction(), pytest.raises(psycopg.errors.NumericValueOutOfRange):
            conn.execute(
                "INSERT INTO interaction_policy_decisions ("
                "interaction_id, version, evaluated_at, risk_level, "
                "evidence_fingerprint, confidence"
                ") VALUES (%s, 1, now(), 'low', 'fp', 12.345)",
                ("ix-oor",),
            )


def test_optional_columns_default_to_empty_array_not_null(migrated_dsn: str) -> None:
    """allowed_actions/triggered_rules default to '{}' — matching the 0012
    convention for TEXT[] columns — so absent evidence reads as an empty
    array, not NULL."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        _insert_min_decision(conn, interaction_id="ix3")
        row = conn.execute(
            "SELECT allowed_actions, triggered_rules, enforcement_type, "
            "explanation, confidence, policy_version "
            "FROM interaction_policy_decisions WHERE interaction_id = 'ix3'"
        ).fetchone()
    allowed_actions, triggered_rules, enforcement_type, explanation, confidence, policy_version = row
    assert allowed_actions == []
    assert triggered_rules == []
    assert enforcement_type is None
    assert explanation is None
    assert confidence is None
    assert policy_version is None


# --- migration chain -----------------------------------------------------------


def test_0017_is_applied_in_the_chain(migrated_dsn: str) -> None:
    """This revision (renumbered from 0013 to 0017 — see its own docstring)
    is the current head, but the assertion here is deliberately only
    reachability. The head assertion lives solely in
    ``test_latest_migration.py`` — the one canonical place — so a new
    revision landing on top only has to edit that file, not this one."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    walked = {rev.revision for rev in script.walk_revisions()}
    assert "0017_policy_decisions" in walked


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade(0016) -> upgrade cleanly removes and re-adds
    interaction_policy_decisions. Downgrading past this revision must not
    disturb interaction_risk_records (0016) or anything below it."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    assert _columns(pg_dsn, "interaction_policy_decisions")

    command.downgrade(cfg, "0016_das_risk_tables")
    assert not _columns(pg_dsn, "interaction_policy_decisions")
    assert _columns(pg_dsn, "interaction_risk_records"), (
        "downgrading past 0013 must not disturb interaction_risk_records"
    )

    command.upgrade(cfg, "head")
    assert _columns(pg_dsn, "interaction_policy_decisions")
