"""Drop the dead-weight ``interaction_legs.original_seq`` column (issue #133).

``interaction_legs.original_seq`` is **written once, read nowhere** — it was
carried over from the pre-split shape but never earns its keep on a leg:

- No ``UPDATE ... SET original_seq`` exists anywhere; both leg upserts in
  ``state.py`` omit it from ``DO UPDATE SET``, so it is write-once at INSERT and
  never mutated.
- No reader: the leg rehydrate SELECT reads only ``occurred_at/payload_hash/
  error``; neither the ``dg_interaction_leg_ready`` nor ``dg_entity_ready``
  consumers select it; the ``--scramble`` equivalence gate intentionally omits
  it from its byte comparison.
- Its ADR-0004 purpose — diff a frozen ``original_seq`` against a mutating
  ``seq`` to tell first-emission from mutation — is inert for legs: post-ADR-0027
  reversal a leg's ``seq`` is DB-owned (``nextval``) and *also* never mutates on
  re-derive, so ``seq == original_seq`` forever. The column carries no
  information a consumer could act on.

Scope is **legs only.** ``entities.original_seq`` stays — it IS read back
(into ``Entity.first_seen_seq``); ``interactions`` has no ``original_seq``
column (ADR-0025 moved the leg-dependent fields out of the parent). Neither is
touched here.

Hand-written ``op.execute`` per ADR-0002/0005 (no SQLAlchemy ORM models). The
derived tables are always rebuildable by cursor replay, so no back-fill is
needed. ``downgrade`` restores the column as ``BIGINT NOT NULL DEFAULT 0`` —
mirroring the 0009 restore pattern for the interactions ``original_seq``; the
``DEFAULT 0`` satisfies the NOT NULL for any rows present at downgrade, and a
re-derive re-populates the leg rows anyway.

No ADR: the change is reversible via ``downgrade()`` and is already explained by
the ADR-0027 reversal narrative (it fails all three ADR triggers). See the
ADR-0027 Consequences note.

Revision ID: 0011_drop_leg_original_seq
Revises: 0010_entity_ready_notify
Create Date: 2026-07-30
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0011_drop_leg_original_seq"
down_revision: Union[str, Sequence[str], None] = "0010_entity_ready_notify"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the write-only passenger column. ``seq`` (DB-owned per leg, migration
    # 0009) is unaffected and remains the leg's sole ordinal.
    op.execute("ALTER TABLE interaction_legs DROP COLUMN original_seq")


def downgrade() -> None:
    # Restore the column NOT NULL with a DEFAULT so any rows present satisfy the
    # constraint (mirrors the 0009 interactions restore). Re-derive re-populates
    # legs regardless; the value is inert (no reader).
    op.execute(
        "ALTER TABLE interaction_legs "
        "ADD COLUMN original_seq BIGINT NOT NULL DEFAULT 0"
    )
