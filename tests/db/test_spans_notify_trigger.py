"""Tests for the spans NOTIFY trigger migrations (issue #71 + 0006 follow-up).

Migration 0005 added ``dg_notify_spans()`` and a statement-level
``AFTER INSERT ON spans`` trigger. Migration 0006 replaced that single trigger
with two **row-level** triggers so the notification fires **only when a row is
actually written**:

- ``dg_spans_notify_ins`` — AFTER INSERT FOR EACH ROW (real inserts → INSERTED).
- ``dg_spans_notify_fin`` — AFTER UPDATE FOR EACH ROW WHEN (NEW.seq IS DISTINCT
  FROM OLD.seq) (finalizations → FINALIZED; the no-op DUPLICATE upsert leaves
  ``seq`` unchanged and is excluded).

These run the real migration chain against a real Postgres (testcontainers) and
assert what it produces: the function/triggers exist and are row-level, a fresh
insert wakes a listener with an empty payload, a finalization wakes it, a no-op
duplicate does NOT, a burst coalesces, and the migration round-trips on downgrade.
"""

from __future__ import annotations

import datetime as dt

import psycopg

CHANNEL = "dg_spans_inserted"


# --- helpers -----------------------------------------------------------------


def _function_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_proc WHERE proname = %s", (name,)
        ).fetchone()
    return row is not None


def _trigger_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_trigger WHERE tgname = %s AND NOT tgisinternal",
            (name,),
        ).fetchone()
    return row is not None


def _trigger_is_row_level(dsn: str, name: str) -> bool:
    """tgtype bit 0 (value 1) set => row-level; clear => statement-level."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT tgtype FROM pg_trigger WHERE tgname = %s AND NOT tgisinternal",
            (name,),
        ).fetchone()
    assert row is not None, name
    return (row[0] & 1) == 1


def _insert_min_span(
    conn: psycopg.Connection, *, span_id: str, ended_at: dt.datetime | None = None
) -> None:
    """Insert a fresh span row (the INSERTED path)."""
    conn.execute(
        """
        INSERT INTO spans (trace_id, span_id, parent_id, kind, name,
                           started_at, ended_at, attributes,
                           seq, arrival_seq, observed_at)
        VALUES ('t1', %s, NULL, 'INTERNAL', 'n', %s, %s, '{}'::jsonb,
                nextval('spans_seq'), currval('spans_seq'), now())
        """,
        (span_id, dt.datetime.now(dt.timezone.utc), ended_at),
    )


# Mirrors the trigger-relevant parts of write_span._INSERT_SQL: the ON CONFLICT
# predicate (``EXCLUDED.ended_at IS NOT NULL AND spans.ended_at IS NULL``) and the
# ``seq = nextval('spans_seq')`` advance. The real SQL also refreshes the other
# span columns on finalize, but only ended_at/seq matter to what the notify
# triggers key on. Finalize iff incoming ended_at is non-null and the stored row's
# ended_at is still null; otherwise the DO UPDATE WHERE is false, the update is
# skipped (DUPLICATE), and seq is left untouched.
_UPSERT_SQL = """
    INSERT INTO spans (trace_id, span_id, parent_id, kind, name,
                       started_at, ended_at, attributes,
                       seq, arrival_seq, observed_at)
    VALUES ('t1', %s, NULL, 'INTERNAL', 'n', %s, %s, '{}'::jsonb,
            nextval('spans_seq'), currval('spans_seq'), now())
    ON CONFLICT (trace_id, span_id) DO UPDATE SET
        ended_at = EXCLUDED.ended_at,
        seq      = nextval('spans_seq')
    WHERE EXCLUDED.ended_at IS NOT NULL AND spans.ended_at IS NULL
"""


def _upsert_span(
    conn: psycopg.Connection, *, span_id: str, ended_at: dt.datetime | None
) -> None:
    conn.execute(_UPSERT_SQL, (span_id, dt.datetime.now(dt.timezone.utc), ended_at))


def _drain_notifies(
    listener: psycopg.Connection, *, timeout: float
) -> list[psycopg.Notify]:
    """Collect notifications arriving within ``timeout`` seconds.

    Unlike ``notifies(stop_after=1)``, this does not early-exit on the first
    notification, so it can also assert that *zero* arrive (the duplicate case).
    """
    got: list[psycopg.Notify] = []
    for n in listener.notifies(timeout=timeout):
        got.append(n)
    return got


# --- structure ---------------------------------------------------------------


def test_function_and_triggers_exist(migrated_dsn: str) -> None:
    assert _function_exists(migrated_dsn, "dg_notify_spans")
    assert _trigger_exists(migrated_dsn, "dg_spans_notify_ins")
    assert _trigger_exists(migrated_dsn, "dg_spans_notify_fin")
    # The old statement-level trigger from 0005 is gone after 0006.
    assert not _trigger_exists(migrated_dsn, "dg_spans_notify")


def test_triggers_are_row_level(migrated_dsn: str) -> None:
    """0006 pins ROW-level so notification fires per actual row written, not per
    statement (which fired even on 0-row no-op writes)."""
    assert _trigger_is_row_level(migrated_dsn, "dg_spans_notify_ins")
    assert _trigger_is_row_level(migrated_dsn, "dg_spans_notify_fin")


# --- behaviour ---------------------------------------------------------------


def test_insert_fires_notification_with_empty_payload(migrated_dsn: str) -> None:
    """A fresh span insert wakes a LISTENer; the payload is empty ('something
    happened' is the whole signal)."""
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            _insert_min_span(writer, span_id="s0")
        notifies = list(listener.notifies(timeout=5.0, stop_after=1))
    assert notifies, "expected a notification after a span insert"
    assert notifies[0].channel == CHANNEL
    assert notifies[0].payload == "", "payload must be empty"


def test_finalization_notifies(migrated_dsn: str) -> None:
    """A partial→complete finalization (DO UPDATE branch, seq advances) wakes the
    listener — the consumer must re-drain the finalized span."""
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            # Partial insert (ended_at NULL) — this itself notifies (INSERTED)...
            _upsert_span(writer, span_id="f0", ended_at=None)
            list(listener.notifies(timeout=5.0, stop_after=1))  # drain the insert
            # ...now finalize it (ended_at set) → FINALIZED, seq bumped.
            _upsert_span(
                writer, span_id="f0", ended_at=dt.datetime.now(dt.timezone.utc)
            )
        got = list(listener.notifies(timeout=5.0, stop_after=1))
    assert got, "expected a notification on finalization (seq advanced)"
    assert got[0].payload == ""


def test_duplicate_write_does_not_notify(migrated_dsn: str) -> None:
    """A no-op duplicate upsert (DO UPDATE WHERE-false → 0 rows, seq unchanged)
    must NOT wake the listener. This is the whole point of migration 0006."""
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            # First sight: a fully-complete span (ended_at set). This inserts and
            # notifies once.
            _upsert_span(
                writer, span_id="d0", ended_at=dt.datetime.now(dt.timezone.utc)
            )
        insert_wake = list(listener.notifies(timeout=5.0, stop_after=1))
        assert insert_wake, "sanity: the initial insert should have notified"

        # Re-ingest the same span (the losing-replica / redelivery case). The
        # conflict predicate is false (stored ended_at is already non-null), so the
        # DO UPDATE is skipped: 0 rows updated, so AFTER UPDATE never fires (and even
        # if it did, seq is unchanged so the WHEN guard would suppress it).
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            _upsert_span(
                writer, span_id="d0", ended_at=dt.datetime.now(dt.timezone.utc)
            )
        dup_wake = _drain_notifies(listener, timeout=1.5)
    assert dup_wake == [], f"duplicate write must not notify; got {dup_wake!r}"


def test_burst_of_inserts_coalesces_to_at_least_one_wake(migrated_dsn: str) -> None:
    """A burst still wakes the listener; the consumer cursor-drains on any wake,
    so the count is irrelevant (issue #71)."""
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            for i in range(5):
                _insert_min_span(writer, span_id=f"b{i}")
        got = list(listener.notifies(timeout=5.0, stop_after=1))
    assert got, "expected at least one notification from the insert burst"
    assert all(n.payload == "" for n in got)


# --- downgrade round-trip ----------------------------------------------------


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade(0005) -> upgrade drops and recreates the 0006 triggers
    cleanly, and downgrade restores the 0005 statement-level trigger. Drives
    Alembic through the project's own config builder."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    assert _trigger_exists(pg_dsn, "dg_spans_notify_ins")
    assert _trigger_exists(pg_dsn, "dg_spans_notify_fin")
    assert not _trigger_exists(pg_dsn, "dg_spans_notify")
    assert _function_exists(pg_dsn, "dg_notify_spans")

    # Down one revision to 0005: the 0006 row-level triggers are gone and the
    # original statement-level trigger returns; the function is still present.
    command.downgrade(cfg, "0005_spans_notify_trigger")
    assert not _trigger_exists(pg_dsn, "dg_spans_notify_ins")
    assert not _trigger_exists(pg_dsn, "dg_spans_notify_fin")
    assert _trigger_exists(pg_dsn, "dg_spans_notify")
    assert _function_exists(pg_dsn, "dg_notify_spans")

    # Back up to head: the 0006 pair returns and the statement-level trigger is
    # dropped again.
    command.upgrade(cfg, "head")
    assert _trigger_exists(pg_dsn, "dg_spans_notify_ins")
    assert _trigger_exists(pg_dsn, "dg_spans_notify_fin")
    assert not _trigger_exists(pg_dsn, "dg_spans_notify")
