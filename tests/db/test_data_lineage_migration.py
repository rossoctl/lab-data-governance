"""Tests for the ``lineage_metadata`` derived-table migration (issue #117).

Migration 0011 adds the store for intra-trace **data lineage** (ADR-0027): one
row per interaction leg that received lineage, holding the metadata triple.

The two assertions that carry design weight:

- **PK is ``(interaction_id, leg_type)``, not ``payload_hash``** (D5). Lineage is
  position-dependent while a content hash is not: payloads are content-addressed
  and deduped, so identical bytes at different positions have completely
  different lineage and would collide on a hash key.
- **``payload_hash`` is indexed but not unique** — a secondary index for the
  deferred reverse lookup ("where did this content come from / go").

Like the sibling migration tests these run the real migration chain against a
real Postgres (testcontainers) and assert what it produces, not how.
"""

from __future__ import annotations

import psycopg
import pytest

TABLE = "lineage_metadata"


# --- helpers (same shape as test_interactions_migration.py) -------------------


def _table_exists(dsn: str, table: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchone()
    return row is not None


def _columns(dsn: str, table: str) -> dict[str, dict[str, object]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
    return {name: {"data_type": dt, "is_nullable": nn} for name, dt, nn in rows}


def _column_udt(dsn: str, table: str, column: str) -> str | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
            (table, column),
        ).fetchone()
    return row[0] if row else None


def _pk_columns(dsn: str, table: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT a.attname
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
            WHERE c.contype = 'p' AND t.relname = %s
            ORDER BY array_position(c.conkey, a.attnum)
            """,
            (table,),
        ).fetchall()
    return [r[0] for r in rows]


def _unique_column_sets(dsn: str, table: str) -> set[tuple[str, ...]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT array_agg(a.attname ORDER BY array_position(c.conkey, a.attnum))
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
            WHERE c.contype IN ('p', 'u') AND t.relname = %s
            GROUP BY c.oid
            """,
            (table,),
        ).fetchall()
    return {tuple(cols) for (cols,) in rows}


def _indexes(dsn: str, table: str) -> dict[str, str]:
    """index name -> its CREATE INDEX definition."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = %s",
            (table,),
        ).fetchall()
    return dict(rows)


def _foreign_keys(dsn: str, table: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT c.conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "WHERE c.contype = 'f' AND t.relname = %s",
            (table,),
        ).fetchall()
    return [r[0] for r in rows]


# --- shape -------------------------------------------------------------------


def test_table_exists(migrated_dsn: str) -> None:
    assert _table_exists(migrated_dsn, TABLE)


def test_pk_is_interaction_id_and_leg_type(migrated_dsn: str) -> None:
    """ADR-0027 D5: the canonical key is the interaction **leg** — the grain at
    which a lineage fact is unique."""
    assert _pk_columns(migrated_dsn, TABLE) == ["interaction_id", "leg_type"]


def test_payload_hash_is_not_part_of_any_key(migrated_dsn: str) -> None:
    """D5's negative: keying on the content hash would collide distinct lineage
    facts that happen to share bytes."""
    for cols in _unique_column_sets(migrated_dsn, TABLE):
        assert "payload_hash" not in cols, cols


def test_leg_type_is_the_leg_type_enum(migrated_dsn: str) -> None:
    """Same structural ENUM as ``interaction_legs`` (ADR-0014/0025), so the key
    cannot hold a leg type the legs table could not."""
    assert _column_udt(migrated_dsn, TABLE, "leg_type") == "leg_type"


def test_has_the_metadata_triple_and_seq(migrated_dsn: str) -> None:
    """The lineage metadata is the spec's triple: the set of data sources, the
    ``data_source -> set<transformation>`` map, and the ordered entity list —
    plus the row's own ``seq`` and the secondary-index ``payload_hash``."""
    cols = _columns(migrated_dsn, TABLE)
    assert set(cols) == {
        "interaction_id",
        "leg_type",
        "data_sources",
        "source_transformations",
        "entity_path",
        "payload_hash",
        "seq",
    }


def test_metadata_column_types(migrated_dsn: str) -> None:
    """``data_sources`` / ``entity_path`` are TEXT[] (a set and an ordered list of
    natural keys); ``source_transformations`` is JSONB (a map to a set, which no
    array type expresses)."""
    cols = _columns(migrated_dsn, TABLE)
    assert cols["data_sources"]["data_type"] == "ARRAY"
    assert cols["entity_path"]["data_type"] == "ARRAY"
    assert cols["source_transformations"]["data_type"] == "jsonb"
    assert cols["seq"]["data_type"] == "bigint"


def test_metadata_columns_are_not_null(migrated_dsn: str) -> None:
    """An origin has an empty entity path and a single-key map — a real empty
    value, never NULL. NULL would be indistinguishable from "not yet derived",
    and absence of a row already carries that meaning."""
    cols = _columns(migrated_dsn, TABLE)
    for name in ("data_sources", "source_transformations", "entity_path", "seq"):
        assert cols[name]["is_nullable"] == "NO", name


def test_payload_hash_has_a_secondary_index(migrated_dsn: str) -> None:
    """D5: kept as a **secondary index** to support the deferred reverse lookup.
    Non-unique — the same content legitimately appears on many legs."""
    idx = _indexes(migrated_dsn, TABLE)
    hash_indexes = {
        name: d for name, d in idx.items() if "payload_hash" in d
    }
    assert hash_indexes, f"no index on payload_hash; have {sorted(idx)}"
    assert all("UNIQUE" not in d for d in hash_indexes.values()), hash_indexes


def test_has_a_seq_cursor_index(migrated_dsn: str) -> None:
    """Own ``seq`` means a future consumer can cursor this table, so it gets the
    ADR-0007 cursor-pagination index like every other stream."""
    idx = _indexes(migrated_dsn, TABLE)
    assert any(
        "(seq)" in d.replace(" ", "") or "(seq " in d
        for name, d in idx.items()
        if name not in {f"{TABLE}_pkey"}
    ), sorted(idx.items())


def test_is_fk_free(migrated_dsn: str) -> None:
    """Matches the FK-free derived schema (migration 0004 / ADR-0002/0005): the
    processor writes idempotently and derived tables are always rebuildable."""
    assert _foreign_keys(migrated_dsn, TABLE) == []


# --- behaviour ---------------------------------------------------------------


def _insert(conn: psycopg.Connection, ix: str, leg: str, payload_hash: str) -> None:
    conn.execute(
        f"INSERT INTO {TABLE} (interaction_id, leg_type, data_sources, "
        "source_transformations, entity_path, payload_hash, seq) "
        "VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)",
        (ix, leg, ["user:alice"], '{"user:alice": []}', [], payload_hash, 1),
    )


def test_two_legs_of_one_interaction_coexist(migrated_dsn: str) -> None:
    """The request and response of one call are distinct lineage facts."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        _insert(conn, "ix1", "request", "h1")
        _insert(conn, "ix1", "response", "h2")
        (n,) = conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()
    assert n == 2


def test_same_payload_hash_on_two_legs_is_allowed(migrated_dsn: str) -> None:
    """The concrete D5 scenario: an entity echoes its input verbatim, so both
    legs carry the same content hash with different lineage. A hash key would
    reject the second row (or, worse, overwrite the first)."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        _insert(conn, "ix1", "request", "same")
        _insert(conn, "ix1", "response", "same")
        (n,) = conn.execute(
            f"SELECT count(*) FROM {TABLE} WHERE payload_hash = 'same'"
        ).fetchone()
    assert n == 2


def test_duplicate_leg_is_rejected_by_the_pk(migrated_dsn: str) -> None:
    """The PK is what makes the processor's upsert-based idempotency work."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        _insert(conn, "ix1", "request", "h1")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert(conn, "ix1", "request", "h1")


def test_out_of_set_leg_type_is_rejected(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            _insert(conn, "ix1", "sideways", "h1")


# --- migration chain / downgrade round-trip ----------------------------------


def test_revision_is_in_the_chain(migrated_dsn: str) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    walked = {rev.revision for rev in script.walk_revisions()}
    assert "0011_lineage_metadata" in walked


def test_head_is_0011(migrated_dsn: str) -> None:
    """Applying the chain to head lands on this revision — it is the current
    head (chained after 0010). This assertion travels with whichever revision is
    head; it moved here from ``test_legs_notify_trigger.py`` when 0011 landed,
    the same way that file inherited it from 0008's test."""
    with psycopg.connect(migrated_dsn) as conn:
        (version,) = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert version == "0011_lineage_metadata"


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade(0010) -> upgrade drops and re-adds the table, leaving
    ``interaction_legs`` (and the ``leg_type`` ENUM it shares) intact."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    assert _table_exists(pg_dsn, TABLE)

    command.downgrade(cfg, "0010_legs_notify_trigger")
    assert not _table_exists(pg_dsn, TABLE)
    assert _table_exists(pg_dsn, "interaction_legs"), (
        "the legs table is 0009's, not this revision's to remove"
    )

    command.upgrade(cfg, "head")
    assert _table_exists(pg_dsn, TABLE)
    assert _pk_columns(pg_dsn, TABLE) == ["interaction_id", "leg_type"]
