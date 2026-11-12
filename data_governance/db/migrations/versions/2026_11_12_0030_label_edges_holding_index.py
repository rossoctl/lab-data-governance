"""Index label edges by the holding they enter: (to leg, payload).

The labels read walks a value's sub-graph from holding to holding, looking up
the edges into each (to_interaction_id, to_leg_type, to_payload_hash, unit).
With only (to_interaction_id, to_leg_type), Postgres takes the origin-unit
index instead: it estimates that index at 1 row per lookup, treating the
unit's hash, start and end as independent, while it really returns every edge
of the value (5,000 on a 5,000-hop chain); the to-index it estimates at ~20.
With the payload hash in the to-index, the to-side lookup is both the cheaper
estimate and the true one (1 row). Measured on a 405,000-edge table: 5,000
chain 6.09 s -> 0.05 s; 15,000 diamonds over 120 s -> 0.32 s; a 5,000 x 5,000
fan, already one holding per step, unchanged (same plan, about 0.9 s).

The three-column index replaces the two-column one: every query that filtered
on (to_interaction_id[, to_leg_type]) uses its prefix. Like every index
migration here, it runs in the upgrade's transaction: DROP INDEX takes an
ACCESS EXCLUSIVE lock on payload_label_edges, so its reads and writes wait
until the upgrade commits.

Revision ID: 0030_label_edges_holding_index
Revises: 0029_hash_evidence_fingerprints
Create Date: 2026-11-12
"""

from __future__ import annotations

from alembic import op

revision: str = "0030_label_edges_holding_index"
down_revision: str = "0029_hash_evidence_fingerprints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX payload_label_edges_to_idx")
    op.execute(
        "CREATE INDEX payload_label_edges_to_idx "
        "ON payload_label_edges (to_interaction_id, to_leg_type, to_payload_hash)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX payload_label_edges_to_idx")
    op.execute(
        "CREATE INDEX payload_label_edges_to_idx "
        "ON payload_label_edges (to_interaction_id, to_leg_type)"
    )
