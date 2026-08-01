"""lineage_trace_status — trace-level lineage coverage (issue #120, ADR-0028 D6).

Adds ``lineage_trace_status``: for each trace, whether its derived **data
lineage** covers the whole trace (``complete``) or stops at an absent payload
(``partial``, with the leg ``seq`` it stopped at). This table is what keeps D6's
prefix cutoff from being *silent*; the two rules a consumer needs for reading the
result — absence of a row is *unknown* and never ``complete``, and ``partial`` is a
warning rather than an error — are ADR-0028 D6 "Reading the status".

**This revision resolves ADR-0028's open item on where the status lives**: a
dedicated trace-keyed table, not derived on read. The reasoning, recorded in the
ADR alongside:

  - Derived-on-read would have to infer the gap from the persisted rows, and it
    cannot. Before this revision the driver re-derived a whole trace per arriving
    leg and **upserted without deleting** (the write path 0011 shipped), so rows
    from an earlier, longer derivation outlived a later, shorter one; "no lineage
    row past seq N" is therefore not a reliable signal. Inferring the gap from
    ``interaction_legs.payload_hash IS NULL`` instead would work, but it
    re-implements the cutoff rule in SQL at read time — a second copy of the
    algorithm, drifting from the traversal that actually produced the rows.

    Note this revision *also* fixes the underlying staleness it describes: the
    driver gained a stale-row delete alongside the upsert (ADR-0028 D9). That
    makes the rows consistent with the status, but it does not resurrect
    derived-on-read as an option — the authoritative answer must be the one the
    traversal reached, not a second inference over its output.
  - A dedicated table's idempotency story is the smallest possible one: PK
    ``trace_id`` means exactly one row per trace, ever, so the driver's upsert
    *is* the whole story. The partial→complete transition (a late payload
    arrives) overwrites that single row; there is no earlier, longer answer left
    behind to shadow it — which is precisely the failure mode derived-on-read
    would have inherited.
  - Recovery is unchanged and shared: truncate the lineage tables, reset the
    ``data_lineage`` ``processor_state`` cursor to 0, re-drain. Both tables are
    written in the same transaction as the cursor advance, so they cannot
    disagree about a trace.

Shape:

  - ``trace_id``        PK. One row per trace — the status is a whole-trace fact.
  - ``status``          ``lineage_status`` ENUM (``complete`` | ``partial``), the
                        two values D6 defines. A structural enum, per ADR-0014's
                        convention for closed value sets, so the column cannot
                        hold a third reading of the same fact. **NOT NULL**, which
                        is what makes absence of the ROW the only way to express
                        "unknown" (the ``lineage_metadata`` convention from 0011):
                        a present row always makes a definite claim, and there is
                        no in-band NULL for a reader to reinterpret as
                        ``complete``. Why unknown must never collapse into
                        ``complete``: ADR-0028 D6 "Reading the status".
  - ``stopped_at_seq``  the ``interaction_legs.seq`` of the first absent-payload
                        leg; NULL for a complete trace. Nullable *and* CHECK-
                        paired with ``status``, because "partial" without a stop
                        position is the silent-truncation bug wearing a flag, and
                        "complete" with one has two readings.

No ``seq`` cursor column: nothing downstream drains this table, and the trace's
own coverage is not a stream of events. Should a consumer ever need one, it is an
additive migration.

FK-free like the rest of the derived schema (ADR-0002/0005), hand-written DDL per
ADR-0002/0005 (no ORM models).

What this revision deliberately does NOT add — these stay open in ADR-0028 D6:

- **No reason/classification column.** Telling *not captured* from *redacted*
  (data flowed but is opaque) from *genuinely empty* from *response in-flight* is
  deferred; a column now would fix that open choice by accident, exactly as a
  status column in 0011 would have fixed this one.
- **No per-leg or per-path taint marking.** This ticket stops the WHOLE trace at
  the gap; taint/reachability cutoff (poisoning only the paths through the gap)
  is deferred, and would be per-leg state rather than this trace-level row.

Revision ID: 0012_lineage_trace_status
Revises: 0011_lineage_metadata
Create Date: 2026-07-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0012_lineage_trace_status"
down_revision: Union[str, Sequence[str], None] = "0011_lineage_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The two values ADR-0028 D6 defines, as a structural ENUM (ADR-0014).
    op.execute("CREATE TYPE lineage_status AS ENUM ('complete', 'partial')")
    op.execute(
        """
        CREATE TABLE lineage_trace_status (
            trace_id       TEXT           NOT NULL,
            status         lineage_status NOT NULL,
            stopped_at_seq BIGINT,
            PRIMARY KEY (trace_id),
            -- The stop position and the status are one fact, so the schema
            -- refuses to hold half of it: a partial trace names where it
            -- stopped, a complete one stopped nowhere.
            CONSTRAINT lineage_trace_status_stop_matches_status CHECK (
                (status = 'partial' AND stopped_at_seq IS NOT NULL)
                OR (status = 'complete' AND stopped_at_seq IS NULL)
            )
        )
        """
    )


def downgrade() -> None:
    # 0011's lineage_metadata and 0009's leg_type ENUM are not this revision's to
    # remove; the lineage_status ENUM is, and only this table uses it.
    op.execute("DROP TABLE IF EXISTS lineage_trace_status")
    op.execute("DROP TYPE IF EXISTS lineage_status")
