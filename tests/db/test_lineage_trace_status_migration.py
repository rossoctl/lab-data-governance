"""Tests for the ``lineage_trace_status`` derived-table migration (issue #120).

Migration 0012 adds the trace-level **complete/partial** status for data lineage
(ADR-0027 D6). This resolves the ADR's open item on *where* that status lives: a
dedicated table keyed by trace, rather than derived on read.

The assertions that carry design weight:

- **PK is ``trace_id`` alone** — the status is a fact about the whole trace (one
  row per trace, ever), which is what makes the processor's upsert both the
  write path and the entire idempotency story. A composite key would admit two
  contradictory statuses for one trace.
- **``stopped_at_seq`` is the only nullable column** — ``NULL`` means "nothing
  was truncated", which is only ever true alongside ``status = 'complete'``, and
  a CHECK constraint enforces exactly that pairing so a half-written status
  cannot exist. ``status`` itself is NOT NULL: the absence of the *row* is the
  "not yet derived" signal (the same convention ``lineage_metadata`` uses), so a
  present row always makes a definite claim.
- **FK-free**, like every other derived table (ADR-0002/0005): rebuildable by
  truncate + cursor reset + re-drain.
"""

from __future__ import annotations

import psycopg
import pytest

TABLE = "lineage_trace_status"


# --- helpers (same shape as test_data_lineage_migration.py) -------------------


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


def _foreign_keys(dsn: str, table: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT c.conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "WHERE c.contype = 'f' AND t.relname = %s",
            (table,),
        ).fetchall()
    return [r[0] for r in rows]


def _upsert(
    conn: psycopg.Connection,
    trace_id: str,
    status: str,
    stopped_at_seq: int | None,
) -> None:
    conn.execute(
        f"INSERT INTO {TABLE} (trace_id, status, stopped_at_seq) "
        "VALUES (%s, %s, %s) ON CONFLICT (trace_id) DO UPDATE SET "
        "status = EXCLUDED.status, stopped_at_seq = EXCLUDED.stopped_at_seq",
        (trace_id, status, stopped_at_seq),
    )


# --- shape -------------------------------------------------------------------


def test_table_exists(migrated_dsn: str) -> None:
    assert _table_exists(migrated_dsn, TABLE)


def test_pk_is_trace_id(migrated_dsn: str) -> None:
    """One row per trace: the status is a whole-trace fact (ADR-0027 D6), and a
    single-row key is what makes the driver's upsert the complete idempotency
    story — a re-derivation overwrites the one row rather than accumulating."""
    assert _pk_columns(migrated_dsn, TABLE) == ["trace_id"]


def test_columns_are_exactly_trace_status_and_stop_position(migrated_dsn: str) -> None:
    """The ADR names the shape: ``(trace_id -> status, stopped_at_seq)``."""
    assert set(_columns(migrated_dsn, TABLE)) == {
        "trace_id",
        "status",
        "stopped_at_seq",
    }


def test_status_is_not_null_and_stop_seq_is_nullable(migrated_dsn: str) -> None:
    """A present row always makes a definite claim, so ``status`` is NOT NULL —
    absence of the ROW is what means "not yet derived" (the ``lineage_metadata``
    convention). ``stopped_at_seq`` is nullable because a complete trace has no
    stop position at all."""
    cols = _columns(migrated_dsn, TABLE)
    assert cols["status"]["is_nullable"] == "NO"
    assert cols["stopped_at_seq"]["is_nullable"] == "YES"
    assert cols["stopped_at_seq"]["data_type"] == "bigint"


def test_is_fk_free(migrated_dsn: str) -> None:
    """Derived tables carry no FKs (ADR-0002/0005) — rebuildable by truncate +
    cursor reset + re-drain, and a FK would only order the truncates."""
    assert _foreign_keys(migrated_dsn, TABLE) == []


# --- behaviour: the status/stop pairing cannot be half-written ----------------


def test_partial_requires_a_stop_position(migrated_dsn: str) -> None:
    """A ``partial`` with no stop position is the silent-truncation bug wearing a
    flag — it says "incomplete" without saying from where. Rejected in the
    schema, not just in the processor."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            _upsert(conn, "t1", "partial", None)


def test_complete_forbids_a_stop_position(migrated_dsn: str) -> None:
    """The mirror: a ``complete`` trace stopped nowhere. Allowing a stop seq
    alongside it would leave two readings of the same row."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            _upsert(conn, "t1", "complete", 5)


def test_out_of_set_status_is_rejected(migrated_dsn: str) -> None:
    """``complete`` / ``partial`` are the only two values ADR-0027 D6 defines."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.Error):
            _upsert(conn, "t1", "mostly", 5)


def test_both_valid_states_round_trip(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        _upsert(conn, "t_complete", "complete", None)
        _upsert(conn, "t_partial", "partial", 7)
        rows = dict(
            (r[0], (r[1], r[2]))
            for r in conn.execute(
                f"SELECT trace_id, status::text, stopped_at_seq FROM {TABLE}"
            ).fetchall()
        )
    assert rows == {"t_complete": ("complete", None), "t_partial": ("partial", 7)}


def test_upsert_flips_partial_to_complete_in_place(migrated_dsn: str) -> None:
    """The partial→complete transition (an acceptance criterion of #120) is a
    plain upsert on the one row — no delete, no stale second row to shadow it.
    This is the whole reason the status lives in a trace-keyed table."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        _upsert(conn, "t1", "partial", 3)
        _upsert(conn, "t1", "complete", None)
        rows = conn.execute(
            f"SELECT status::text, stopped_at_seq FROM {TABLE} WHERE trace_id = 't1'"
        ).fetchall()
    assert rows == [("complete", None)]


# --- migration chain / downgrade round-trip ----------------------------------


def test_revision_is_in_the_chain(migrated_dsn: str) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    walked = {rev.revision for rev in script.walk_revisions()}
    assert "0012_lineage_trace_status" in walked


def test_head_is_0012(migrated_dsn: str) -> None:
    """Applying the chain to head lands on this revision — it is the current head
    (chained after 0011). This assertion travels with whichever revision is head;
    it moved here from ``test_data_lineage_migration.py`` when 0012 landed, the
    same way that file inherited it from 0010's test."""
    with psycopg.connect(migrated_dsn) as conn:
        (version,) = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert version == "0012_lineage_trace_status"


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade(0011) -> upgrade drops and re-adds the table, leaving
    ``lineage_metadata`` (0011's, not this revision's to remove) intact."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    assert _table_exists(pg_dsn, TABLE)

    command.downgrade(cfg, "0011_lineage_metadata")
    assert not _table_exists(pg_dsn, TABLE)
    assert _table_exists(pg_dsn, "lineage_metadata"), (
        "the metadata table is 0011's, not this revision's to remove"
    )

    command.upgrade(cfg, "head")
    assert _table_exists(pg_dsn, TABLE)
    assert _pk_columns(pg_dsn, TABLE) == ["trace_id"]
