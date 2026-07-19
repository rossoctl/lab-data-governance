"""NOTIFY trigger on spans inserts: low-latency wake for P-interactions (issue #71).

Adds a database trigger that fires ``pg_notify('dg_spans_inserted', '')`` on
inserts into ``spans``, so the P-interactions processor (ADR-0007) can ``LISTEN``
for it and drain immediately instead of waiting up to a poll interval. The
trigger lives in the database, not the receiver Python — the receiver stays
unaware of derived consumers (ADR-0007); a new consumer just starts listening.

Decisions baked into the DDL:

- **STATEMENT-level**, not row-level. Today the receiver writes one row per
  statement per transaction, so statement- and row-level fire identically. A
  future batched writer (one statement, many rows) would fire one notification
  per batch instead of one per row — strictly better, at zero cost now. The
  consumer cursor-drains ``WHERE seq > cursor`` on any wake, so notification
  count never mattered.
- **Empty payload.** The notification carries no data: "something happened" is
  the entire signal. The consumer always re-drains from its durable cursor, so
  a payload would be ignored. Empty payloads also coalesce a burst of inserts
  into effectively one wake (Postgres deduplicates identical pending
  notifications within a transaction).
- The function ``RETURNS trigger`` and returns ``NULL`` — the return value of
  an ``AFTER`` statement-level trigger is ignored, and ``NULL`` is the
  conventional body.

Correctness does not depend on this trigger: the processor's poll loop is the
backstop (ADR-0007, issue #71). With the trigger absent or a notification
dropped, the processor still drains on the poll alone — this revision only
removes latency. The Layer-1 ``db.listen()`` helper the consumer uses is
recorded in ADR-0015.

Revision ID: 0005_spans_notify_trigger
Revises: 0004_interactions_schema
Create Date: 2026-06-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0005_spans_notify_trigger"
down_revision: Union[str, Sequence[str], None] = "0004_interactions_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The notify function: payload-less pg_notify on the agreed channel.
    op.execute(
        """
        CREATE FUNCTION dg_notify_spans() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_notify('dg_spans_inserted', '');
            RETURN NULL;
        END;
        $$
        """
    )
    # Statement-level AFTER INSERT: one notification per insert statement.
    op.execute(
        """
        CREATE TRIGGER dg_spans_notify
        AFTER INSERT ON spans
        FOR EACH STATEMENT
        EXECUTE FUNCTION dg_notify_spans()
        """
    )


def downgrade() -> None:
    # Drop the trigger before the function it calls.
    op.execute("DROP TRIGGER IF EXISTS dg_spans_notify ON spans")
    op.execute("DROP FUNCTION IF EXISTS dg_notify_spans()")
