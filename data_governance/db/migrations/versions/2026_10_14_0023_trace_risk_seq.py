"""Make trace_risk_records a cursorable, notifying stream (issue #175).

Mirror of ``0019_interaction_risk_seq`` one level up the DAS pipeline: a
consumer of new trace risk versions (the alert generator, #104) drains
``trace_risk_records`` the repo's standard way — ``WHERE seq > cursor ORDER
BY seq`` with the cursor advanced atomically per item (ADR-0007), woken by a
NOTIFY. Unlike ``interaction_risk_records`` (whose NOTIFY trigger 0016
already carried), the trace table had neither half, so this migration adds
both:

- a ``trace_risk_seq`` SEQUENCE and a ``seq BIGINT`` column on
  ``trace_risk_records`` (``DEFAULT nextval('trace_risk_seq')``, ``OWNED BY``
  the table so it drops with it), plus a ``trace_risk_records_seq_idx``
  cursor-pagination index — the correctness half;
- a statement-level ``dg_trace_risk_notify`` trigger firing
  ``pg_notify('dg_trace_risk_written', '')`` after every insert — the
  latency half. Statement-level (the 0007/0016 shape): the table is
  insert-only and immutably versioned, and the drain re-reads everything
  past the cursor regardless of notification count.

Like 0019 there is **no ``arrival_seq``**: ``seq`` never advances after
insert, so one column serves as both insert order and cursor watermark.

One thing 0019 did not need: **pre-existing rows are numbered in stream
order**, not in the heap order ``ADD COLUMN ... DEFAULT nextval()`` would
assign. A consumer starting from cursor 0 replays the history, and an
order-sensitive one (alert supersession) must see each trace's versions in
version order. The key is each row's ``computed_at`` raised to the latest
``computed_at`` of the trace's earlier versions (a running maximum over
``version``), then ``trace_id``, then ``version``: chronological across
traces, and never a later version before an earlier one, even where two
writers once stamped a later version with an earlier clock.

The sequence restarts above the ``risk_alerts`` cursor when one exists: after
a downgrade below this revision and back, the renumbered rows sit at 1..N,
and a sequence restarting at N+1 under a consumer whose cursor is past N
would hide every new version until ``seq`` caught up. That protects the
versions written after the re-upgrade only: the renumbered rows themselves
are not re-aligned with the cursor, so a version written while the schema
was below this revision is not delivered. After such a manual downgrade,
delete the ``risk_alerts`` row from ``processor_state`` to replay the stream.

Like every ``seq`` cursor in this repo, the stream inherits the
insert-allocation caveat (``docs/PROJECT.md`` §3): a ``seq`` allocated
earlier but committed later than one a drain has passed is skipped. It
cannot happen while the table has one writer committing serially (the
single-replica trace-trigger processor).

Hand-written ``op.execute`` only, per ADR-0002/0005.

Revision ID: 0023_trace_risk_seq
Revises: 0022_content_kind_on_legs
Create Date: 2026-10-14
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0023_trace_risk_seq"
down_revision: Union[str, Sequence[str], None] = "0022_content_kind_on_legs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE trace_risk_seq")
    op.execute(
        "ALTER TABLE trace_risk_records "
        "ADD COLUMN seq BIGINT NOT NULL DEFAULT nextval('trace_risk_seq')"
    )
    op.execute("ALTER SEQUENCE trace_risk_seq OWNED BY trace_risk_records.seq")
    # Renumber the rows that already exist in stream order (see docstring),
    # then move the sequence past them, and past the alerts cursor if one is
    # already stored, so new inserts continue from there.
    op.execute(
        """
        UPDATE trace_risk_records t
           SET seq = ordered.rn
          FROM (SELECT trace_risk_id,
                       row_number() OVER (ORDER BY stream_at, trace_id, version) AS rn
                  FROM (SELECT trace_risk_id, trace_id, version,
                               max(computed_at) OVER (PARTITION BY trace_id ORDER BY version)
                                   AS stream_at
                          FROM trace_risk_records) keyed) ordered
         WHERE ordered.trace_risk_id = t.trace_risk_id
        """
    )
    op.execute(
        """
        SELECT setval('trace_risk_seq',
                      GREATEST((SELECT COALESCE(max(seq), 0) FROM trace_risk_records),
                               (SELECT COALESCE(max(last_processed_seq), 0) FROM processor_state
                                 WHERE processor_name = 'risk_alerts')) + 1,
                      false)
        """
    )
    # Cursor pagination: WHERE seq > %s ORDER BY seq ASC.
    op.execute("CREATE INDEX trace_risk_records_seq_idx ON trace_risk_records (seq)")

    op.execute(
        """
        CREATE FUNCTION dg_notify_trace_risk() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_notify('dg_trace_risk_written', '');
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER dg_trace_risk_notify
        AFTER INSERT ON trace_risk_records
        FOR EACH STATEMENT
        EXECUTE FUNCTION dg_notify_trace_risk()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS dg_trace_risk_notify ON trace_risk_records")
    op.execute("DROP FUNCTION IF EXISTS dg_notify_trace_risk()")
    op.execute("DROP INDEX IF EXISTS trace_risk_records_seq_idx")
    # Dropping the column drops the OWNED BY sequence with it.
    op.execute("ALTER TABLE trace_risk_records DROP COLUMN IF EXISTS seq")
