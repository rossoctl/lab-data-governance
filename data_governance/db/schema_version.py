"""Startup schema-version check (issue #10, ADR-0002 defence-in-depth).

The receiver does not run migrations — those are the deployment topology's
responsibility (Kubernetes init container, manual ``migrate`` invocation
otherwise). What the receiver *does* do, on startup, is read
``alembic_version.version_num`` from Postgres and compare it to the head
revision compiled into the receiver image. On mismatch (or on a missing /
empty ``alembic_version`` table) the receiver logs an actionable error and
exits non-zero — k8s surfaces this immediately as ``CrashLoopBackOff``.

This catches scenarios the init container alone would not:

- "wrong receiver image deployed against this DB" (operator forgot to
  re-run the migrate job after rolling forward the receiver),
- "init container forgotten in some non-k8s deployment".

The compiled head is derived from the in-image Alembic ScriptDirectory at
import time, so adding a migration does not require touching this file:
the receiver's expected head moves with the migration tree.
"""

from __future__ import annotations

from data_governance import db


__all__ = [
    "SchemaVersionMismatch",
    "check_schema_version",
    "compiled_head",
    "db_head",
]


class SchemaVersionMismatch(RuntimeError):
    """Raised when the DB's Alembic head does not match the receiver's
    compiled head, or the ``alembic_version`` table is missing or empty.

    Inherits from :class:`RuntimeError` rather than a Postgres-specific base
    because callers should treat this as a fatal startup misconfiguration —
    not a retryable connection-class error (ADR-0003)."""


def compiled_head() -> str:
    """Return the head revision compiled into the receiver image.

    Derived at call time from the Alembic :class:`ScriptDirectory` shipped
    alongside the receiver — this is the same source ``alembic upgrade
    head`` consults, so the migrate CLI and the receiver's startup check
    cannot drift. If the migration tree is empty, Alembic returns ``None``
    and we surface that as a hard error rather than letting the receiver
    accept any DB head as valid.
    """
    # Local imports keep the alembic dependency out of the import path of
    # callers that only want :class:`SchemaVersionMismatch`.
    from alembic.script import ScriptDirectory

    from data_governance.db.migrate import _alembic_config

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    if head is None:
        # Empty migration tree — should never happen in a real receiver
        # build; raise rather than return ``None`` so the contract stays
        # ``str``.
        raise RuntimeError(
            "Alembic ScriptDirectory has no head revision; the receiver "
            "image is built without migrations."
        )
    return head


def db_head(tx: db.Transaction) -> str | None:
    """Return the current head from ``alembic_version``, or ``None`` if the
    table is absent or empty.

    Returning ``None`` for both "table missing" and "table present but
    empty" lets the caller render a single "migrations were never run"
    error path. The two states are operationally indistinguishable: each
    means the schema is not at any known revision.
    """
    # information_schema lookup is cheap and avoids relying on a
    # try/except around the SELECT, which would couple the check to a
    # specific SQLSTATE.
    exists_row = tx.fetch_one(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'alembic_version'
        """
    )
    if exists_row is None:
        return None
    row = tx.fetch_one("SELECT version_num FROM alembic_version")
    if row is None:
        return None
    value = row[0]
    if value is None or value == "":
        return None
    return str(value)


def _format_mismatch_message(observed: str | None, compiled: str) -> str:
    """Build the actionable error message for a startup version mismatch.

    The message names BOTH revisions and points at the next concrete
    operator action (``python -m data_governance.db.migrate``), per the
    issue's acceptance criteria."""
    if observed is None:
        return (
            "Schema version check failed: alembic_version table is missing "
            "or empty — migrations have never been run against this database. "
            f"The receiver expects compiled head '{compiled}'. "
            "Run `python -m data_governance.db.migrate` (or the migrate "
            "init container) before starting the receiver."
        )
    return (
        "Schema version mismatch: database is at Alembic revision "
        f"'{observed}' but the receiver was compiled against head "
        f"'{compiled}'. Run `python -m data_governance.db.migrate` against "
        "this database, or roll the receiver image back to the matching "
        "revision."
    )


def check_schema_version() -> None:
    """Compare ``db_head`` to ``compiled_head``; raise on mismatch.

    Uses the Layer-1 :func:`data_governance.db.transaction` to read the
    version table. The pool must already be configured by the caller (the
    receiver entry point does this immediately after parsing
    ``DATABASE_URL``). Connection failures propagate as their normal
    ADR-0003 connection-class exceptions; mismatches and missing tables
    raise :class:`SchemaVersionMismatch`.
    """
    compiled = compiled_head()
    with db.transaction() as tx:
        observed = db_head(tx)
    if observed != compiled:
        raise SchemaVersionMismatch(
            _format_mismatch_message(observed, compiled)
        )
