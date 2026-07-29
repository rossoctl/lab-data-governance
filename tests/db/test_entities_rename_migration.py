"""Tests for migration 0013 — ``lineage_metadata.entity_path`` renamed to ``entities``.

The human-owned spec (``docs/data_lineage_alg.md`` "Lineage metadata") redefined
the third element of the metadata triple as an **unordered set**: "the set of
entities - through which entities the data passed through / Note: this is
unordered. In case an order is needed - it will need to be derived from the trace
using an API." The column name had to follow — ``entity_path`` promised a
*sequence* a consumer could legitimately read hop-by-hop, which the derivation
never guaranteed and the spec now explicitly disclaims.

What carries design weight here:

- **The old name is gone, the new one is present.** A stale ``entity_path`` left
  alongside ``entities`` would leave two readings of one fact — and the old name is
  precisely the one that invites the forbidden reading.
- **It is a RENAME, so the rows survive.** The values were always just entity
  natural keys; only their contract changed. Data loss would be an absurd price for
  a naming fix, and the round-trip below proves the values come through.
- **Everything else about the column is untouched** — still ``TEXT[]``, still NOT
  NULL, still defaulting to ``'{}'``. Postgres has no set type, so the array stays
  the representation; what changed is that its order means nothing.
- **Reversible.** The downgrade renames back, so rolling back to 0012 leaves a
  schema 0012-era code can still read.
"""

from __future__ import annotations

import psycopg

TABLE = "lineage_metadata"


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


def test_entities_column_replaces_entity_path(migrated_dsn: str) -> None:
    """The rename is complete: exactly one of the two names exists. Keeping the old
    one around would preserve the very name that asserts an order the spec says the
    field does not carry."""
    cols = _columns(migrated_dsn, TABLE)
    assert "entities" in cols
    assert "entity_path" not in cols


def test_entities_keeps_the_column_contract_it_had(migrated_dsn: str) -> None:
    """Only the NAME changed. Still ``TEXT[]`` (Postgres has no set type, so the
    array is the representation — its order is not meaning), still NOT NULL so an
    origin's *empty* set stays a real value distinguishable from the absent row,
    still defaulting to ``'{}'`` so that empty set is the natural construction."""
    col = _columns(migrated_dsn, TABLE)["entities"]
    assert col["data_type"] == "ARRAY"
    assert col["is_nullable"] == "NO"
    assert "{}" in str(col["column_default"])


# --- migration chain ---------------------------------------------------------


def test_revision_is_in_the_chain(migrated_dsn: str) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    walked = {rev.revision for rev in script.walk_revisions()}
    assert "0013_lineage_entities_rename" in walked


def test_head_is_0013(migrated_dsn: str) -> None:
    """Applying the chain to head lands on this revision — it is the current head
    (chained after 0012). This assertion travels with whichever revision is head; it
    moved here from ``test_lineage_trace_status_migration.py`` when 0013 landed, the
    same way that file inherited it from 0011's test."""
    with psycopg.connect(migrated_dsn) as conn:
        (version,) = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert version == "0013_lineage_entities_rename"


# --- reversibility, with the data intact ------------------------------------


def test_downgrade_restores_the_old_name_and_upgrade_renames_back(
    pg_dsn: str, monkeypatch
) -> None:
    """upgrade -> downgrade(0012) -> upgrade round-trips the NAME while carrying the
    ROWS through untouched. A rename that quietly dropped a governance claim's
    contents would be a far worse bug than the misleading name it fixed."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute(
            f"INSERT INTO {TABLE} (interaction_id, leg_type, data_sources, "
            "source_transformations, entities, payload_hash, seq) "
            "VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)",
            (
                "ix1",
                "request",
                ["user:alice"],
                '{"user:alice": []}',
                ["agent:a", "llm:x"],
                "h1",
                1,
            ),
        )

    command.downgrade(cfg, "0012_lineage_trace_status")
    cols = _columns(pg_dsn, TABLE)
    assert "entity_path" in cols and "entities" not in cols
    with psycopg.connect(pg_dsn) as conn:
        (values,) = conn.execute(
            f"SELECT entity_path FROM {TABLE} WHERE interaction_id = 'ix1'"
        ).fetchone()
    assert sorted(values) == ["agent:a", "llm:x"], "the rename must not touch rows"

    command.upgrade(cfg, "head")
    cols = _columns(pg_dsn, TABLE)
    assert "entities" in cols and "entity_path" not in cols
    with psycopg.connect(pg_dsn) as conn:
        (values,) = conn.execute(
            f"SELECT entities FROM {TABLE} WHERE interaction_id = 'ix1'"
        ).fetchone()
    assert sorted(values) == ["agent:a", "llm:x"]
