"""Tests for the payload_classifications migration (issue #77).

Migration 0008 adds ``payload_classifications`` — the one-row-per-Payload store
for the data-governance **Classification** verdict (CONTEXT.md; ADR-0024). It is
the table P-classification writes and the payload read surface joins to expose
the nullable ``classification`` field.

Shape (per CONTEXT.md **Classification**): keyed on ``content_hash`` (PK; one
verdict per content-addressed payload, dedup inherited from the payload), the
document-level verdict columns, the **Findings** inline as ``findings JSONB``,
and a monotonic ``model_version INTEGER`` (starts at 1) as the future
re-classification hook. Write-once — no ``seq``/finalization analogue (ADR-0024).

Like the sibling migration tests these run the real migration chain against a
real Postgres (testcontainers) and assert what it produces, not how — the test
does not care whether the DDL is one ``op.execute`` or twenty.
"""

from __future__ import annotations

import psycopg


# --- helpers -----------------------------------------------------------------


def _columns(dsn: str, table: str) -> dict[str, dict[str, object]]:
    """Map column-name -> {data_type, is_nullable}."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
    return {name: {"data_type": dt, "is_nullable": nn} for name, dt, nn in rows}


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


# --- structure ---------------------------------------------------------------


def test_table_exists_keyed_on_content_hash(migrated_dsn: str) -> None:
    """``payload_classifications`` exists, PK on ``content_hash`` — one verdict
    per content-addressed Payload (ADR-0024)."""
    cols = _columns(migrated_dsn, "payload_classifications")
    assert cols, "payload_classifications table must exist"
    assert "content_hash" in cols
    assert _primary_key_columns(migrated_dsn, "payload_classifications") == [
        "content_hash"
    ]


def test_document_level_verdict_columns_exist(migrated_dsn: str) -> None:
    """The document-level Classification shape (CONTEXT.md): sensitivity_level,
    regulatory_tags, contains_identity_bundle, is_personalized, primary_domain."""
    cols = _columns(migrated_dsn, "payload_classifications")
    for name in (
        "sensitivity_level",
        "regulatory_tags",
        "contains_identity_bundle",
        "is_personalized",
        "primary_domain",
    ):
        assert name in cols, f"missing verdict column {name!r}"


def test_findings_is_jsonb_and_model_version_is_integer(migrated_dsn: str) -> None:
    """Findings are stored inline as JSONB (ADR-0024); model_version is a
    monotonic integer hook (starts at 1)."""
    cols = _columns(migrated_dsn, "payload_classifications")
    assert cols["findings"]["data_type"] == "jsonb"
    assert cols["model_version"]["data_type"] == "integer"


# --- migration chain ---------------------------------------------------------


def test_head_is_0010(migrated_dsn: str) -> None:
    """Applying the chain to head lands on the current head revision (0010 —
    the ``dg_entity_ready`` notify trigger, ADR-0027, chained after 0009). The
    head assertion lives here (rather than in each revision's own test) so a new
    revision moves exactly one line; the classification store this file covers is
    asserted structurally by the tests above regardless of the head."""
    with psycopg.connect(migrated_dsn) as conn:
        (version,) = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert version == "0010_entity_ready_notify"


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade(0007) -> upgrade cleanly removes and re-adds the
    payload_classifications table. Downgrade stops one revision below this one —
    the payload stream (0007) and everything below it stays put. Drives Alembic
    through the project's own config builder (the migrate CLI's)."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    # Up to head: the classification store is present.
    command.upgrade(cfg, "head")
    assert _columns(pg_dsn, "payload_classifications")

    # Down one revision to 0007: the table is gone, but the payload stream
    # (interaction_payloads.seq, from 0007) and everything below stays.
    command.downgrade(cfg, "0007_payloads_cursorable_stream")
    assert not _columns(pg_dsn, "payload_classifications")
    assert "seq" in _columns(pg_dsn, "interaction_payloads"), (
        "downgrading past 0008 must not disturb the 0007 payload stream"
    )

    # Back up to head: the table returns.
    command.upgrade(cfg, "head")
    assert _columns(pg_dsn, "payload_classifications")
