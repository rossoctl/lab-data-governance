"""Make interaction_risk_records a cursorable stream (seq column; issue #164).

The Trace Risk Processor (#102) is a durable-cursor consumer over
``interaction_risk_records``: every new interaction risk version must trigger
exactly one trace-risk recompute (FR-DAS-019/020, AC-DAS-008), delivered by
the repo's standard drain — ``WHERE seq > cursor ORDER BY seq`` with the
cursor advanced atomically per item (ADR-0007). The
``dg_interaction_risk_written`` NOTIFY trigger already exists (migration
0016, statement-level — the latency tap), but the table has no ordering
column for the correctness half of that idiom. This migration adds it:

- an ``interaction_risk_seq`` SEQUENCE and a ``seq BIGINT`` column on
  ``interaction_risk_records`` (``DEFAULT nextval('interaction_risk_seq')``,
  ``OWNED BY`` the table so it drops with the table), plus an
  ``interaction_risk_records_seq_idx`` cursor-pagination index — mirroring
  how ``payloads_seq`` / ``interaction_payloads.seq`` back the payloads
  stream (migration 0007).

Like 0007 (and unlike spans), there is **no ``arrival_seq``**: the table is
insert-only and immutably versioned (a recompute inserts a new version, never
mutates a row — 0016's invariant), so ``seq`` never advances after insert and
one column serves as both insert order and cursor watermark.

No trigger change: 0016's statement-level ``dg_interaction_risk_notify``
already fires on every insert, and the drain re-reads everything past the
cursor regardless of notification count, so the existing blind wake is
exactly what the consumer needs.

Hand-written ``op.execute`` only, per ADR-0002/0005.

Renumbered 0018 -> 0019 on 2026-08-20. This revision and #102's
``0018_trace_aggregation_modes`` were written concurrently on separate
branches and both numbered 0018 off ``0017_policy_decisions``; the other
reached `risk` first, so this one was re-parented onto it (the same
renumber-and-re-parent fix ``0016_das_risk_tables`` documents). The two touch
disjoint tables, so nothing but linearity depends on the order. Per this
repo's convention for renumbered revisions (see 0010-0015), the filename keeps
its original creation date rather than being restamped.

Revision ID: 0019_interaction_risk_seq
Revises: 0018_trace_aggregation_modes
Create Date: 2026-08-16
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0019_interaction_risk_seq"
down_revision: Union[str, Sequence[str], None] = "0018_trace_aggregation_modes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One sequence per cursorable stream, OWNED BY its table so it drops with
    # the table — mirrors spans_seq / payloads_seq / interactions_seq.
    op.execute("CREATE SEQUENCE interaction_risk_seq")
    op.execute(
        "ALTER TABLE interaction_risk_records "
        "ADD COLUMN seq BIGINT NOT NULL DEFAULT nextval('interaction_risk_seq')"
    )
    op.execute(
        "ALTER SEQUENCE interaction_risk_seq OWNED BY interaction_risk_records.seq"
    )
    # Cursor pagination: WHERE seq > %s ORDER BY seq ASC.
    op.execute(
        "CREATE INDEX interaction_risk_records_seq_idx "
        "ON interaction_risk_records (seq)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS interaction_risk_records_seq_idx")
    # Dropping the column drops the OWNED BY sequence with it.
    op.execute("ALTER TABLE interaction_risk_records DROP COLUMN IF EXISTS seq")
