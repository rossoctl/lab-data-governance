"""Label lineage across conversations: what a correction reads, and the cut a limit
leaves.

One table beside those of 0031 and one column on them; the label-lineage tables of
0024 are not altered.

``label_sends`` — one row per payload-bearing leg a persistent entity sent with an
event time, written with the derivation of its conversation and replaced like the
rest. It is what a correction reads: when a derivation adds, removes or changes an
occurrence, every other conversation with a leg the entity sent inside the
occurrence's interval is queued to be derived again (index entity, time).

``label_crossings.cut`` — a row may now be a cut: ``cut`` names the limit that cut
the leg's result and nothing else is set, so ``value_hash`` and ``conversations``
are required only on a row that is not a cut (an asserted crossing or a common
value, as in 0031).

The count of what an entity holds at a payload's time, which the holdings limit
compares, is served by 0031's ``label_occurrences_valid_idx`` (entity, valid_to,
including valid_from and trace_id), so it needs no index of its own.

The downgrade deletes the cut rows (derived, re-derivable) before it restores the
NOT NULLs, so it leaves exactly the schema of 0031.

Revision ID: 0032_label_corrections
Revises: 0031_label_crossings
Create Date: 2026-11-13
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0032_label_corrections"
down_revision: Union[str, Sequence[str], None] = "0031_label_crossings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE label_crossings
            ALTER COLUMN value_hash DROP NOT NULL,
            ALTER COLUMN conversations DROP NOT NULL,
            ADD COLUMN cut TEXT CHECK (cut IN ('holdings_per_entity', 'payload_bytes', 'candidates_per_payload')),
            ADD CHECK (CASE WHEN cut IS NULL THEN value_hash IS NOT NULL AND conversations IS NOT NULL
                            ELSE num_nonnulls(value_hash, conversations, edge_id) = 0 END)
        """
    )
    op.execute(
        """
        CREATE TABLE label_sends (
            interaction_id TEXT NOT NULL,
            leg_type       leg_type NOT NULL,
            entity_id      TEXT NOT NULL,
            trace_id       TEXT NOT NULL,
            occurred_at    TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (interaction_id, leg_type)
        )
        """
    )
    op.execute("CREATE INDEX label_sends_entity_idx ON label_sends (entity_id, occurred_at)")
    op.execute("CREATE INDEX label_sends_trace_idx ON label_sends (trace_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS label_sends")
    op.execute("DELETE FROM label_crossings WHERE cut IS NOT NULL")
    # dropping the column drops both CHECKs that name it
    op.execute(
        """
        ALTER TABLE label_crossings
            DROP COLUMN cut,
            ALTER COLUMN value_hash SET NOT NULL,
            ALTER COLUMN conversations SET NOT NULL
        """
    )
