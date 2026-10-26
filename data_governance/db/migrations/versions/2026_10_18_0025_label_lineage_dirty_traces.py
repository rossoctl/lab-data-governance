"""Label lineage across conversations (#273): the dirty-traces queue.

``label_lineage_dirty_traces`` — one row per trace that must be derived again
because a trace it read through the cross-conversation priors changed. The
``label_lineage`` processor writes it when a derivation changes a trace's edges
(the traces that share a stateful entity with it inside the window, the mirror
of how priors are loaded) and drains it after its stale arm, deleting each row in
the transaction that re-derives the trace. One row per trace bounds the table.

Nothing else: a cross-conversation edge is an ordinary ``payload_label_edges``
row whose ``from_trace_id`` (the carrier's trace) or ``origin_trace_id`` (the
unit's) is set — both columns exist since 0024, where they were always NULL.

Hand-written ``op.execute`` only, per ADR-0002/0005. Reversible: the downgrade
drops the table (a queue; dropping it loses pending re-derivations, which the
stale arm does not replay — reset the ``label_lineage`` cursor to rebuild).

Revision ID: 0025_label_lineage_dirty_traces
Revises: 0024_label_lineage
Create Date: 2026-10-18
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0025_label_lineage_dirty_traces"
down_revision: Union[str, Sequence[str], None] = "0024_label_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE label_lineage_dirty_traces (
            trace_id     TEXT        PRIMARY KEY,
            requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            reason       TEXT        NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS label_lineage_dirty_traces")
