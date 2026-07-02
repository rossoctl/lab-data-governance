"""NOTIFY only when a span row is actually written (issue #71 follow-up).

Migration 0005 installed a **statement-level** ``AFTER INSERT`` trigger
(``dg_spans_notify``) that fires ``pg_notify('dg_spans_inserted', '')`` once per
INSERT *statement* — regardless of how many rows the statement actually wrote. The
receiver's write is
``INSERT ... ON CONFLICT (trace_id, span_id) DO UPDATE ... WHERE (finalization
predicate)`` (see ``processors/otlp_receiver/write_span.py``), which has three
outcomes:

- ``INSERTED``  — a new row lands (``seq`` bumped from ``spans_seq``).
- ``FINALIZED`` — a partial row is completed via the DO UPDATE branch (``seq``
  bumped; ADR-0004).
- ``DUPLICATE`` — the DO UPDATE ``WHERE`` predicate is false, so nothing changes
  (0 rows written, ``seq`` unchanged). This is the losing-replica re-ingest or any
  OTLP redelivery of an already-stored span.

With the statement-level trigger, even a ``DUPLICATE`` write fired the
notification, waking the P-interactions consumer (``processors/interactions``) for
a write that stored nothing. The consumer is cursor-driven (``WHERE seq > cursor``)
and payload-agnostic, so this was harmless-but-noisy — the wake just drained,
found nothing past the cursor, and slept. This revision removes the spurious wake
at the source: **the notification now fires only when a row was actually written.**

Design — two **row-level** triggers sharing the unchanged ``dg_notify_spans()``
function:

- ``dg_spans_notify_ins`` — ``AFTER INSERT ... FOR EACH ROW``. A row-level
  AFTER INSERT trigger fires only for rows that were genuinely inserted; the
  DO NOTHING / no-op conflict path inserts no row and does not fire it. Covers
  ``INSERTED``.
- ``dg_spans_notify_fin`` — ``AFTER UPDATE ... FOR EACH ROW
  WHEN (NEW.seq IS DISTINCT FROM OLD.seq)``. The ON CONFLICT branch fires AFTER
  UPDATE (not AFTER INSERT), but only for rows the UPDATE actually touches: when
  the ``DO UPDATE ... WHERE`` predicate is false the update is skipped entirely,
  so a ``DUPLICATE`` never runs the UPDATE and its AFTER UPDATE trigger never
  fires. That alone excludes ``DUPLICATE``. The ``WHEN (NEW.seq IS DISTINCT FROM
  OLD.seq)`` guard is a defensive belt-and-suspenders on top: it keys the
  notification to a real ``seq`` advance (``FINALIZED`` bumps ``seq`` via
  ``nextval('spans_seq')``; ADR-0004), so even a future ``DO UPDATE`` that runs
  without advancing ``seq`` would stay silent. So this trigger fires on
  ``FINALIZED`` and stays silent on ``DUPLICATE``.

Net: ``INSERTED`` → notify, ``FINALIZED`` → notify, ``DUPLICATE`` → silent.

Row-level is safe here: the receiver writes one row per statement per transaction
(``write_span`` opens its own Layer-1 tx per span), so there is no batch
amplification. Migration 0005 chose statement-level so a *future* batched writer
would fire once per batch rather than once per row — that argument was about wake
*count*, not correctness (the consumer cursor-drains on any wake). If a batched
writer ever lands, revisit this trade-off; until then, precise per-write semantics
win.

The ``dg_notify_spans()`` function itself is untouched — same channel
(``dg_spans_inserted``), same empty payload, same consumer contract (ADR-0007). This
revision only changes *when* the function is invoked.

Revision ID: 0006_spans_notify_on_write
Revises: 0005_spans_notify_trigger
Create Date: 2026-07-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0006_spans_notify_on_write"
down_revision: Union[str, Sequence[str], None] = "0005_spans_notify_trigger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the statement-level trigger from 0005; the dg_notify_spans() function
    # it calls stays and is reused by the two row-level triggers below.
    op.execute("DROP TRIGGER IF EXISTS dg_spans_notify ON spans")

    # Real inserts only. A row-level AFTER INSERT trigger does not fire for the
    # DO NOTHING / no-op conflict path (no row inserted → no per-row INSERT fire).
    op.execute(
        """
        CREATE TRIGGER dg_spans_notify_ins
        AFTER INSERT ON spans
        FOR EACH ROW
        EXECUTE FUNCTION dg_notify_spans()
        """
    )

    # Finalizations only. When the ON CONFLICT DO UPDATE ... WHERE predicate is
    # false the update is skipped, so a DUPLICATE never fires AFTER UPDATE — the
    # WHERE clause already excludes it. The WHEN (NEW.seq IS DISTINCT FROM OLD.seq)
    # guard is a defensive extra keyed to the seq advance (ADR-0004): FINALIZED
    # bumps seq via nextval('spans_seq'), so any future DO UPDATE that runs without
    # advancing seq would still stay silent.
    op.execute(
        """
        CREATE TRIGGER dg_spans_notify_fin
        AFTER UPDATE ON spans
        FOR EACH ROW
        WHEN (NEW.seq IS DISTINCT FROM OLD.seq)
        EXECUTE FUNCTION dg_notify_spans()
        """
    )


def downgrade() -> None:
    # Drop the two row-level triggers and restore 0005's statement-level trigger
    # verbatim so the round-trip is faithful. dg_notify_spans() is untouched.
    op.execute("DROP TRIGGER IF EXISTS dg_spans_notify_fin ON spans")
    op.execute("DROP TRIGGER IF EXISTS dg_spans_notify_ins ON spans")
    op.execute(
        """
        CREATE TRIGGER dg_spans_notify
        AFTER INSERT ON spans
        FOR EACH STATEMENT
        EXECUTE FUNCTION dg_notify_spans()
        """
    )
