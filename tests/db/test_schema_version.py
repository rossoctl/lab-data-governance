"""Tests for the receiver's startup schema-version check (issue #10, ADR-0002).

The receiver does not run migrations — those are the deployment topology's
responsibility (init container in k8s, manual ``migrate`` invocation
otherwise). What the receiver *does* do, on startup, is read
``alembic_version.version_num`` from Postgres and compare it to the head
revision compiled into the receiver image. On mismatch (or on a missing
``alembic_version`` table) the receiver logs an actionable error and exits
non-zero, surfacing as CrashLoopBackOff in k8s.

These tests exercise the Layer-1 building blocks of that contract:
``compiled_head``, ``db_head``, and ``check_schema_version``. The receiver
entry-point integration test (process actually exits non-zero on mismatch)
lives alongside the receiver tests.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest

from data_governance import db
from data_governance.db import schema_version


# --- helpers -----------------------------------------------------------------


@pytest.fixture()
def configured_pool(migrated_dsn: str) -> Iterator[str]:
    """Configure the Layer 1 pool against the migrated DB."""
    db.close_pool()
    db.configure(migrated_dsn)
    try:
        yield migrated_dsn
    finally:
        db.close_pool()


# --- compiled_head -----------------------------------------------------------


class TestCompiledHead:
    def test_returns_a_non_empty_revision_string(self) -> None:
        """The compiled head must be derivable at receiver-build time without
        manually duplicating the version string. We only assert the contract
        — that *some* head string exists — so the test does not pin a
        specific revision id (which would force a churn-only update on every
        new migration)."""
        head = schema_version.compiled_head()
        assert isinstance(head, str)
        assert head  # non-empty

    def test_matches_alembic_script_directory_head(self) -> None:
        """The compiled head must match what Alembic itself reports as the
        head of the migration tree, so that ``alembic upgrade head`` and the
        receiver's startup check stay in lockstep."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        from data_governance.db.migrate import _alembic_config

        cfg: Config = _alembic_config()
        expected = ScriptDirectory.from_config(cfg).get_current_head()
        assert schema_version.compiled_head() == expected


# --- db_head -----------------------------------------------------------------


class TestDbHead:
    def test_returns_the_alembic_version_after_migrate(
        self, configured_pool: str
    ) -> None:
        with db.transaction() as tx:
            head = schema_version.db_head(tx)
        assert head == schema_version.compiled_head()

    def test_returns_none_when_alembic_version_table_is_absent(
        self, pg_dsn: str
    ) -> None:
        # No migrate has run against pg_dsn — alembic_version table doesn't
        # exist yet.
        db.close_pool()
        db.configure(pg_dsn)
        try:
            with db.transaction() as tx:
                head = schema_version.db_head(tx)
        finally:
            db.close_pool()
        assert head is None

    def test_returns_none_when_alembic_version_table_is_empty(
        self, configured_pool: str
    ) -> None:
        # Manually create an empty alembic_version table state by deleting
        # the row; the check must treat that as "migrations were never run."
        with db.transaction() as tx:
            tx.execute("DELETE FROM alembic_version")
        with db.transaction() as tx:
            head = schema_version.db_head(tx)
        assert head is None


# --- check_schema_version ----------------------------------------------------


class TestCheckSchemaVersionGreen:
    def test_returns_normally_when_db_head_matches_compiled_head(
        self, configured_pool: str
    ) -> None:
        # The migrated DB is at compiled head by definition; this must not raise.
        schema_version.check_schema_version()


class TestCheckSchemaVersionRed:
    def test_raises_with_actionable_message_when_table_missing(
        self, pg_dsn: str
    ) -> None:
        db.close_pool()
        db.configure(pg_dsn)
        try:
            with pytest.raises(schema_version.SchemaVersionMismatch) as exc_info:
                schema_version.check_schema_version()
        finally:
            db.close_pool()
        message = str(exc_info.value)
        # Operators must learn what is wrong (no migrations ran) and what
        # the receiver expected to see.
        assert "alembic_version" in message
        compiled = schema_version.compiled_head()
        assert compiled in message
        # Mention that the migrate step is missing — actionable guidance.
        assert "migrate" in message.lower()

    def test_raises_with_actionable_message_when_db_at_older_revision(
        self, configured_pool: str
    ) -> None:
        # Force the alembic_version row to a stale revision value.
        # alembic_version.version_num is varchar(32), keep it short.
        stale = "0000_stale_rev"
        with db.transaction() as tx:
            tx.execute(
                "UPDATE alembic_version SET version_num = %s", (stale,)
            )

        with pytest.raises(schema_version.SchemaVersionMismatch) as exc_info:
            schema_version.check_schema_version()
        message = str(exc_info.value)
        # Actionable error names BOTH revisions per the issue's acceptance
        # criteria, so an operator can immediately tell what to do.
        assert stale in message
        assert schema_version.compiled_head() in message

    def test_raises_with_actionable_message_when_table_empty(
        self, configured_pool: str
    ) -> None:
        with db.transaction() as tx:
            tx.execute("DELETE FROM alembic_version")
        with pytest.raises(schema_version.SchemaVersionMismatch) as exc_info:
            schema_version.check_schema_version()
        message = str(exc_info.value)
        assert "alembic_version" in message
        assert schema_version.compiled_head() in message


# --- non-coupling check ------------------------------------------------------


class TestReceiverDoesNotInvokeAlembicMigrations:
    """Per the issue and ADR-0002, the receiver does NOT invoke Alembic
    itself on startup. The startup check only *reads* the version table; it
    must not call ``alembic upgrade``. This is enforced by the public
    surface of ``schema_version`` containing no migration-running symbol —
    the test pins that contract."""

    def test_module_has_no_upgrade_symbol(self) -> None:
        for forbidden in ("upgrade", "run_migrations", "migrate"):
            assert not hasattr(schema_version, forbidden), (
                f"schema_version.{forbidden} would imply the receiver runs "
                "migrations itself, contrary to ADR-0002."
            )


# --- alembic_version row sanity (regression guard) ---------------------------


class TestMigratedDbIsAtCompiledHead:
    """Sanity assertion: after the standard migrate CLI runs, the DB head
    equals the compiled head. This pins the round-trip we rely on
    everywhere else and would catch a botched migration tree."""

    def test_alembic_version_value_equals_compiled_head(
        self, migrated_dsn: str
    ) -> None:
        with psycopg.connect(migrated_dsn) as conn:
            row = conn.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        assert row is not None
        assert row[0] == schema_version.compiled_head()
