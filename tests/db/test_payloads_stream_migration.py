"""Tests for the interaction_payloads cursorable-stream migration (issue #76).

Migration 0007 gives ``interaction_payloads`` the same cursorable-stream shape
``spans`` already has, so a downstream processor (P-classification, issue #77)
can drain it by ``seq`` and be woken on insert:

- a ``payloads_seq`` sequence and a ``seq BIGINT`` column on
  ``interaction_payloads`` (OWNED BY the table, ``DEFAULT nextval(...)``), plus a
  cursor-pagination index on ``seq``;
- a ``dg_notify_payloads()`` function + a **statement-level** ``AFTER INSERT``
  trigger firing ``pg_notify('dg_payloads_inserted', '')``, mirroring the spans
  notify trigger from migration 0005 exactly (empty payload, statement-level).

Unlike ``spans`` there is **no** ``arrival_seq`` column and **no** finalization
tripwire: payloads are content-addressed and insert-only (``ON CONFLICT
(content_hash) DO NOTHING``), so ``seq`` never advances after insert (ADR-0024).

Like the sibling migration tests these run the real migration chain against a
real Postgres (testcontainers) and assert what it produces, not how — the test
does not care whether the DDL is one ``op.execute`` or twenty.
"""

from __future__ import annotations

import psycopg

CHANNEL = "dg_payloads_inserted"


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


def _sequence_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM information_schema.sequences "
            "WHERE sequence_schema = 'public' AND sequence_name = %s",
            (name,),
        ).fetchone()
    return row is not None


def _index_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname = 'public' AND indexname = %s",
            (name,),
        ).fetchone()
    return row is not None


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


def _insert_payload(conn: psycopg.Connection, *, content_hash: str) -> None:
    """Insert a content-addressed payload row the way P-interactions does:
    omit ``seq`` so the DEFAULT allocates it."""
    conn.execute(
        "INSERT INTO interaction_payloads "
        "(content_hash, content_kind, content, byte_size) "
        "VALUES (%s, 'unknown', '{}'::jsonb, 2)",
        (content_hash,),
    )


# --- structure ---------------------------------------------------------------


def test_seq_column_and_sequence_exist(migrated_dsn: str) -> None:
    """interaction_payloads gains a ``seq`` column and the ``payloads_seq``
    sequence backing it — the cursorable-stream shape (ADR-0007, ADR-0024)."""
    assert "seq" in _columns(migrated_dsn, "interaction_payloads")
    assert _sequence_exists(migrated_dsn, "payloads_seq")


def test_cursor_index_on_seq_exists(migrated_dsn: str) -> None:
    """A supporting index on ``seq`` makes it a usable cursor (ADR-0007) —
    mirrors ``spans_seq_idx`` / ``interactions_seq_idx``."""
    assert _index_exists(migrated_dsn, "payloads_seq_idx")


# --- seq allocation ----------------------------------------------------------


def test_insert_allocates_monotonic_seq(migrated_dsn: str) -> None:
    """A writer that omits ``seq`` relies on the DEFAULT and must get
    strictly-increasing values in *insertion order* — that is what makes
    ``seq`` a usable cursor (ADR-0007). Mirrors
    ``test_entities_seq_allocates_monotonically``.

    The content_hashes are chosen so their lexical order is the *reverse* of
    insertion order (``h2`` < ``h1`` < ``h0`` inserted last-first). Capturing
    ``seq`` via ``RETURNING`` at insert time — rather than reading it back with
    an ``ORDER BY`` on some column — is what makes this a real monotonicity
    check: a DEFAULT that allocated ``seq`` in any order other than the order
    the inserts ran would be caught, not masked by a sort that happens to
    align with allocation."""
    with psycopg.connect(migrated_dsn) as conn:
        # Insert in an order uncorrelated with (here: opposite to) the sort
        # order of the content_hash key, capturing the allocated seq per insert.
        allocated = []
        for content_hash in ("h2", "h1", "h0"):
            (seq,) = conn.execute(
                "INSERT INTO interaction_payloads "
                "(content_hash, content_kind, content, byte_size) "
                "VALUES (%s, 'unknown', '{}'::jsonb, 2) RETURNING seq",
                (content_hash,),
            ).fetchone()
            allocated.append(seq)
    # seq tracks insertion order (strictly increasing as the inserts ran) and
    # every row got a distinct value.
    assert allocated == sorted(allocated)
    assert allocated[0] < allocated[1] < allocated[2]
    assert len(set(allocated)) == 3


# --- NOTIFY trigger (mirrors migration 0005 exactly) -------------------------


def test_notify_function_and_trigger_exist(migrated_dsn: str) -> None:
    assert _function_exists(migrated_dsn, "dg_notify_payloads")
    assert _trigger_exists(migrated_dsn, "dg_payloads_notify")


def test_trigger_is_statement_level(migrated_dsn: str) -> None:
    """Mirrors 0005 exactly: STATEMENT-level, not the row-level pair 0006 drew
    for spans. Payloads never finalize, so there is no INSERTED/FINALIZED/
    DUPLICATE distinction that would motivate row-level (ADR-0024)."""
    assert not _trigger_is_row_level(migrated_dsn, "dg_payloads_notify")


def test_insert_fires_notification_with_empty_payload(migrated_dsn: str) -> None:
    """A payload insert wakes a LISTENer; the notification carries an empty
    payload ('something happened' is the whole signal — the consumer re-drains
    from its durable cursor). Mirrors the spans notify test."""
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            _insert_payload(writer, content_hash="n0")
        notifies = list(listener.notifies(timeout=5.0, stop_after=1))
    assert notifies, "expected a notification after a payload insert"
    assert notifies[0].channel == CHANNEL
    assert notifies[0].payload == "", "payload must be empty"


# --- ADR-0024: content-addressed, insert-only — no finalization shape --------


def test_no_arrival_seq_column(migrated_dsn: str) -> None:
    """Spans carry ``arrival_seq`` because they finalize (ADR-0004). Payloads
    never do — content-addressed, insert-only — so the payload stream gets a
    single ``seq`` column and NO ``arrival_seq`` (ADR-0024)."""
    assert "arrival_seq" not in _columns(migrated_dsn, "interaction_payloads")


def test_seq_does_not_advance_on_conflict_do_nothing(migrated_dsn: str) -> None:
    """A payload is written exactly once and frozen: P-interactions writes with
    ``ON CONFLICT (content_hash) DO NOTHING``, so re-inserting the same hash is a
    no-op and ``seq`` never advances (ADR-0024 — no finalization). This is why
    the notify trigger needs no INSERTED/FINALIZED/DUPLICATE split."""
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO interaction_payloads "
            "(content_hash, content_kind, content, byte_size) "
            "VALUES ('c0', 'unknown', '{}'::jsonb, 2)"
        )
        (first_seq,) = conn.execute(
            "SELECT seq FROM interaction_payloads WHERE content_hash = 'c0'"
        ).fetchone()
        # Re-ingest the same content-addressed body (the dedup case).
        conn.execute(
            "INSERT INTO interaction_payloads "
            "(content_hash, content_kind, content, byte_size) "
            "VALUES ('c0', 'unknown', '{}'::jsonb, 2) "
            "ON CONFLICT (content_hash) DO NOTHING"
        )
        (second_seq,) = conn.execute(
            "SELECT seq FROM interaction_payloads WHERE content_hash = 'c0'"
        ).fetchone()
    assert second_seq == first_seq, "seq must not advance on a DO NOTHING re-insert"


# --- migration chain ---------------------------------------------------------


def test_head_is_0007(migrated_dsn: str) -> None:
    """Applying the chain to head lands on this revision (0007)."""
    with psycopg.connect(migrated_dsn) as conn:
        (version,) = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert version == "0007_payloads_cursorable_stream"


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade(0006) -> upgrade cleanly removes and re-adds the seq
    column, sequence, index, function and trigger. Downgrade stops one revision
    below this one — the interactions/spans schema stays put. Drives Alembic
    through the project's own config builder (the migrate CLI's)."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    # Up to head: the payload stream shape is present.
    command.upgrade(cfg, "head")
    assert "seq" in _columns(pg_dsn, "interaction_payloads")
    assert _sequence_exists(pg_dsn, "payloads_seq")
    assert _index_exists(pg_dsn, "payloads_seq_idx")
    assert _function_exists(pg_dsn, "dg_notify_payloads")
    assert _trigger_exists(pg_dsn, "dg_payloads_notify")

    # Down one revision to 0006: the payload stream shape is gone, but the
    # interaction_payloads table itself (from 0004) and the spans side stay.
    command.downgrade(cfg, "0006_spans_notify_on_write")
    cols = _columns(pg_dsn, "interaction_payloads")
    assert cols, "the interaction_payloads table itself must survive the downgrade"
    assert "seq" not in cols
    assert not _sequence_exists(pg_dsn, "payloads_seq")
    assert not _index_exists(pg_dsn, "payloads_seq_idx")
    assert not _function_exists(pg_dsn, "dg_notify_payloads")
    assert not _trigger_exists(pg_dsn, "dg_payloads_notify")

    # Back up to head: everything returns.
    command.upgrade(cfg, "head")
    assert "seq" in _columns(pg_dsn, "interaction_payloads")
    assert _sequence_exists(pg_dsn, "payloads_seq")
    assert _index_exists(pg_dsn, "payloads_seq_idx")
    assert _function_exists(pg_dsn, "dg_notify_payloads")
    assert _trigger_exists(pg_dsn, "dg_payloads_notify")
