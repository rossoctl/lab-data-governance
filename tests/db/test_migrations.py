"""Tests for the Alembic baseline migration and the `migrate` CLI.

These tests run the actual migration against a real Postgres and inspect the
resulting schema via the information_schema / pg_catalog. They test what the
migration produces, not how it produces it — the test should not care
whether the migration uses one ``op.execute`` call or twenty.
"""

from __future__ import annotations

import subprocess
import sys

import psycopg
import pytest


# --- helpers -----------------------------------------------------------------


def _columns(dsn: str, table: str) -> dict[str, dict[str, object]]:
    """Map column-name -> {data_type, is_nullable, column_default}."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        ).fetchall()
    return {
        name: {"data_type": dt, "is_nullable": nn, "column_default": default}
        for name, dt, nn, default in rows
    }


def _pk_columns(dsn: str, table: str) -> list[str]:
    """Ordered list of primary-key column names for *table*."""
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


def _index_column_lists(dsn: str, table: str) -> set[tuple[str, ...]]:
    """All index column-tuples (ordered) on *table*. Includes the PK index."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT i.relname,
                   array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum))
            FROM pg_class t
            JOIN pg_index ix ON ix.indrelid = t.oid
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
            WHERE t.relname = %s
            GROUP BY i.relname
            """,
            (table,),
        ).fetchall()
    return {tuple(cols) for _, cols in rows}


def _table_exists(dsn: str, table: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchone()
    return row is not None


def _sequence_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM information_schema.sequences "
            "WHERE sequence_schema = 'public' AND sequence_name = %s",
            (name,),
        ).fetchone()
    return row is not None


def _run_migrate_cli(dsn: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m data_governance.processors.otlp_receiver migrate`` against *dsn*."""
    return subprocess.run(
        [sys.executable, "-m", "data_governance.processors.otlp_receiver", "migrate"],
        env={"DATABASE_URL": dsn, "PATH": "/usr/bin:/bin"},
        check=False,
        capture_output=True,
        text=True,
    )


# --- migration outcome -------------------------------------------------------


@pytest.fixture()
def migrated_dsn(pg_dsn: str) -> str:
    """A fresh database with the baseline migration applied via the CLI."""
    result = _run_migrate_cli(pg_dsn)
    assert result.returncode == 0, (
        f"migrate exited {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return pg_dsn


class TestSpansTable:
    def test_spans_table_exists(self, migrated_dsn: str) -> None:
        assert _table_exists(migrated_dsn, "spans")

    def test_spans_has_composite_pk_trace_id_span_id(self, migrated_dsn: str) -> None:
        assert _pk_columns(migrated_dsn, "spans") == ["trace_id", "span_id"]

    def test_spans_columns_match_project_spec(self, migrated_dsn: str) -> None:
        cols = _columns(migrated_dsn, "spans")

        # Every column listed in PROJECT.md §3 is present.
        expected = {
            "trace_id",
            "span_id",
            "parent_id",
            "kind",
            "name",
            "service_name",
            "started_at",
            "ended_at",
            "error",
            "status_message",
            "attributes",
            "events",
            "links",
            "otlp",
            "scope",
            "resource_attributes",
            "seq",
            "arrival_seq",
            "observed_at",
        }
        assert expected <= set(cols), f"missing columns: {expected - set(cols)}"

    def test_timestamp_columns_are_timestamptz(self, migrated_dsn: str) -> None:
        cols = _columns(migrated_dsn, "spans")
        for c in ("started_at", "ended_at", "observed_at"):
            assert cols[c]["data_type"] == "timestamp with time zone", c

    def test_jsonb_columns_are_jsonb(self, migrated_dsn: str) -> None:
        cols = _columns(migrated_dsn, "spans")
        for c in ("attributes", "events", "links", "otlp", "scope", "resource_attributes"):
            assert cols[c]["data_type"] == "jsonb", c

    def test_attributes_is_not_null_and_defaults_to_empty_object(
        self, migrated_dsn: str
    ) -> None:
        # PROJECT.md §3: attributes jsonb NOT NULL DEFAULT '{}'::jsonb
        cols = _columns(migrated_dsn, "spans")
        assert cols["attributes"]["is_nullable"] == "NO"
        # PG normalises the default; we assert the value materialises as {}.
        with psycopg.connect(migrated_dsn) as conn:
            conn.execute(
                "INSERT INTO spans "
                "(trace_id, span_id, kind, name, started_at, arrival_seq) "
                "VALUES (%s, %s, %s, %s, now(), 1)",
                ("t1", "s1", "INTERNAL", "x"),
            )
            row = conn.execute(
                "SELECT attributes FROM spans WHERE trace_id=%s", ("t1",)
            ).fetchone()
        assert row == ({},)

    def test_arrival_seq_is_not_null(self, migrated_dsn: str) -> None:
        cols = _columns(migrated_dsn, "spans")
        assert cols["arrival_seq"]["is_nullable"] == "NO"

    def test_parent_id_is_nullable(self, migrated_dsn: str) -> None:
        cols = _columns(migrated_dsn, "spans")
        assert cols["parent_id"]["is_nullable"] == "YES"

    def test_seq_is_bigint(self, migrated_dsn: str) -> None:
        cols = _columns(migrated_dsn, "spans")
        assert cols["seq"]["data_type"] == "bigint"
        assert cols["arrival_seq"]["data_type"] == "bigint"


class TestSpansIndexes:
    def test_required_indexes_exist(self, migrated_dsn: str) -> None:
        index_cols = _index_column_lists(migrated_dsn, "spans")
        # PK index (implicit) — composite (trace_id, span_id)
        assert ("trace_id", "span_id") in index_cols
        # Downward walks + orphan check (ADR-0001)
        assert ("trace_id", "parent_id") in index_cols
        # Window queries (§7 recent-traces)
        assert ("started_at",) in index_cols
        # Cursor pagination (§6)
        assert ("seq",) in index_cols


class TestSeqAllocation:
    def test_seq_inserts_are_monotonic_without_caller_supplying_seq(
        self, migrated_dsn: str
    ) -> None:
        """PROJECT.md §3: ``seq`` is allocated monotonically from a sequence
        at insert time. Behavioural assertion: a caller that omits ``seq``
        from the INSERT (i.e. relies on the column's DEFAULT) must still get
        strictly-increasing values across rows. This is what makes ``seq``
        usable as a cursor without the receiver having to manage allocation
        itself."""
        with psycopg.connect(migrated_dsn) as conn:
            for i in range(3):
                conn.execute(
                    "INSERT INTO spans "
                    "(trace_id, span_id, kind, name, started_at, arrival_seq) "
                    "VALUES (%s, %s, %s, %s, now(), %s)",
                    ("t", f"s{i}", "INTERNAL", "n", i + 1),
                )
            seqs = [
                row[0]
                for row in conn.execute(
                    "SELECT seq FROM spans ORDER BY span_id"
                ).fetchall()
            ]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 3  # strictly increasing → all distinct


class TestBlockedSpanCounts:
    def test_blocked_span_counts_table_exists(self, migrated_dsn: str) -> None:
        assert _table_exists(migrated_dsn, "blocked_span_counts")

    def test_blocked_span_counts_columns(self, migrated_dsn: str) -> None:
        cols = _columns(migrated_dsn, "blocked_span_counts")
        assert "pattern" in cols
        assert "count" in cols
        assert "last_seen_at" in cols
        assert cols["last_seen_at"]["data_type"] == "timestamp with time zone"


# --- migrate CLI -------------------------------------------------------------


class TestMigrateCli:
    def test_migrate_exits_zero_against_fresh_db(self, pg_dsn: str) -> None:
        result = _run_migrate_cli(pg_dsn)
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_migrate_is_idempotent(self, pg_dsn: str) -> None:
        first = _run_migrate_cli(pg_dsn)
        assert first.returncode == 0, first.stderr

        # Capture the alembic_version row after the first run.
        with psycopg.connect(pg_dsn) as conn:
            v1 = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert v1 is not None

        # Second run must succeed and not change the head revision.
        second = _run_migrate_cli(pg_dsn)
        assert second.returncode == 0, second.stderr

        with psycopg.connect(pg_dsn) as conn:
            v2 = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert v2 == v1

    def test_alembic_version_table_is_present_after_migrate(self, pg_dsn: str) -> None:
        _run_migrate_cli(pg_dsn)
        assert _table_exists(pg_dsn, "alembic_version")
