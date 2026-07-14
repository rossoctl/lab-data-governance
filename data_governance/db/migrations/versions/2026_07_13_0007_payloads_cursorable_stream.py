"""Make interaction_payloads a cursorable stream (seq + NOTIFY trigger; issue #76).

Gives ``interaction_payloads`` the same cursorable-stream shape ``spans`` already
has, so the downstream P-classification processor (issue #77, ADR-0024) can drain
it by ``seq`` and be woken on insert instead of only polling.

What this revision adds:

- a ``payloads_seq`` SEQUENCE and a ``seq BIGINT`` column on
  ``interaction_payloads`` (``DEFAULT nextval('payloads_seq')``, ``OWNED BY`` the
  table so it drops with the table), plus a ``payloads_seq_idx`` cursor-pagination
  index on ``seq`` — mirroring how ``spans_seq`` / ``spans.seq`` /
  ``spans_seq_idx`` back the spans stream (migration 0001);
- a ``dg_notify_payloads()`` plpgsql function + a **statement-level**
  ``AFTER INSERT`` trigger on ``interaction_payloads`` firing
  ``pg_notify('dg_payloads_inserted', '')`` — mirroring the spans NOTIFY trigger
  from migration 0005 **exactly**: empty payload, statement-level, correctness
  depending only on the consumer's poll backstop (ADR-0007).

What this revision deliberately does NOT add — the payload stream is simpler than
the spans stream (ADR-0024):

- **No ``arrival_seq`` column.** Spans carry both ``seq`` and ``arrival_seq`` and
  the interactions driver runs a finalization tripwire because spans finalize
  (ADR-0004). Payloads never finalize.
- **No finalization tripwire / no row-level notify split.** Payloads are
  content-addressed and insert-only (P-interactions writes them with
  ``ON CONFLICT (content_hash) DO NOTHING``), so ``seq`` never advances after
  insert. There is no ``INSERTED`` / ``FINALIZED`` / ``DUPLICATE`` distinction to
  make (the distinction migration 0006 drew for spans): a payload row is written
  exactly once and frozen. The statement-level ``AFTER INSERT`` trigger — 0005's
  original shape, not 0006's row-level pair — is the right fit. A ``DO NOTHING``
  conflict inserts no row; whether the statement-level trigger fires on a
  zero-row insert or not is immaterial because the consumer cursor-drains
  ``WHERE seq > cursor`` on any wake and finds nothing new.

Per ADR-0002/0005 the DDL is hand-written ``op.execute(...)`` — no SQLAlchemy ORM
models.

Revision ID: 0007_payloads_cursorable_stream
Revises: 0006_spans_notify_on_write
Create Date: 2026-07-13
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0007_payloads_cursorable_stream"
down_revision: Union[str, Sequence[str], None] = "0006_spans_notify_on_write"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- payloads_seq + seq column (cursorable stream) --------------------
    # One sequence per cursorable stream, OWNED BY its table so it drops with
    # the table — mirrors spans_seq / entities_seq / interactions_seq.
    op.execute("CREATE SEQUENCE payloads_seq")
    op.execute(
        "ALTER TABLE interaction_payloads "
        "ADD COLUMN seq BIGINT NOT NULL DEFAULT nextval('payloads_seq')"
    )
    op.execute("ALTER SEQUENCE payloads_seq OWNED BY interaction_payloads.seq")
    # Cursor pagination over the payload stream (ADR-0007).
    op.execute("CREATE INDEX payloads_seq_idx ON interaction_payloads (seq)")

    # --- NOTIFY trigger (mirrors migration 0005 exactly) ------------------
    # The notify function: payload-less pg_notify on the agreed channel. The
    # function RETURNS trigger and returns NULL — the return value of an AFTER
    # statement-level trigger is ignored, and NULL is the conventional body.
    op.execute(
        """
        CREATE FUNCTION dg_notify_payloads() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_notify('dg_payloads_inserted', '');
            RETURN NULL;
        END;
        $$
        """
    )
    # Statement-level AFTER INSERT: one notification per insert statement. Empty
    # payload — "something happened" is the whole signal; the consumer always
    # re-drains WHERE seq > cursor and coalesces a burst into one wake. Unlike
    # spans (migration 0006) there is no INSERTED/FINALIZED/DUPLICATE split:
    # payloads are content-addressed and insert-only (ON CONFLICT DO NOTHING),
    # so seq never advances after insert and statement-level (0005's shape) is
    # the right fit (ADR-0024).
    op.execute(
        """
        CREATE TRIGGER dg_payloads_notify
        AFTER INSERT ON interaction_payloads
        FOR EACH STATEMENT
        EXECUTE FUNCTION dg_notify_payloads()
        """
    )


def downgrade() -> None:
    # Drop the trigger before the function it calls.
    op.execute(
        "DROP TRIGGER IF EXISTS dg_payloads_notify ON interaction_payloads"
    )
    op.execute("DROP FUNCTION IF EXISTS dg_notify_payloads()")
    op.execute("DROP INDEX IF EXISTS payloads_seq_idx")
    # Dropping the column drops its DEFAULT's dependency on payloads_seq; the
    # OWNED BY sequence goes with the column, but guard explicitly anyway.
    op.execute("ALTER TABLE interaction_payloads DROP COLUMN IF EXISTS seq")
    op.execute("DROP SEQUENCE IF EXISTS payloads_seq")
