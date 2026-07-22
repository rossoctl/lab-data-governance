"""Split interactions into a parent identity row + request/response legs.

ADR-0025 supersedes ADR-0013: a single directed call is now a parent
``interactions`` row (identity shared across both legs) plus one or two
``interaction_legs`` rows (the temporal half — one payload, one timestamp,
one error, its own seq per leg).

Changes to the derived schema first landed in 0004:

  - ``interactions`` loses every leg-dependent column (``started_at``,
    ``ended_at``, ``error``, ``request_payload_hash``,
    ``response_payload_hash``, ``seq``, ``original_seq``) — it keeps only the
    identity columns that are identical on both legs: ``id``, ``trace_id``,
    ``parent_interaction_id``, ``caller_entity_id``, ``callee_entity_id``,
    ``summary``. The parent has NO ``seq`` and is not independently cursorable
    (identity is immutable once decided); it is a join target.
  - ``interaction_legs`` (new) — PK ``(interaction_id, leg_type)`` with
    ``leg_type`` the ``('request','response')`` ENUM, carrying ``occurred_at``,
    ``payload_hash``, ``error``, ``seq``, ``original_seq``. Each leg finalizes
    independently and cursors on its own ``seq``.
  - ``interaction_legs_seq`` (new) replaces the retired ``interactions_seq`` as
    the cursorable stream (ADR-0007 shape, one layer down).
  - ``interaction_spans`` gains a ``leg_type`` column so a span attributes to a
    specific leg. Its ``(trace_id, span_id)`` PK (ADR-0011) is untouched — both
    legs of the current source cite the same one span.

The derived tables are always rebuildable (the P-interactions processor
re-derives them from ``spans`` by cursor replay), so this migration drops and
recreates the leg-dependent structure rather than back-filling — a truncate +
processor-cursor-reset is the operational recovery, exactly as for the
classification model-version bump (ADR-0024). Hand-written ``op.execute`` per
ADR-0002/0005; FK-free per the 0004 convention.

Revision ID: 0009_interaction_legs
Revises: 0008_payload_classifications
Create Date: 2026-07-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0009_interaction_legs"
down_revision: Union[str, Sequence[str], None] = "0008_payload_classifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- leg_type ENUM (ADR-0025) -----------------------------------------
    # Domain-fixed closed set: a call has a request leg and a response leg.
    op.execute("CREATE TYPE leg_type AS ENUM ('request', 'response')")

    # Rebuild the derived tables to clear any rows keyed on the old shape (they
    # are re-derived by cursor replay anyway).
    op.execute("TRUNCATE interaction_spans, interactions RESTART IDENTITY")

    # --- interactions -> identity only ------------------------------------
    # Drop the leg-dependent columns FIRST — the ``seq`` column's DEFAULT
    # depends on ``interactions_seq``, so the sequence cannot be dropped until
    # the column is gone. ``interactions_seq_idx`` indexed the dropped ``seq``
    # column and goes with it; the trace index stays.
    op.execute(
        """
        ALTER TABLE interactions
            DROP COLUMN started_at,
            DROP COLUMN ended_at,
            DROP COLUMN error,
            DROP COLUMN request_payload_hash,
            DROP COLUMN response_payload_hash,
            DROP COLUMN seq,
            DROP COLUMN original_seq
        """
    )

    # --- interaction_legs_seq (replaces interactions_seq) -----------------
    # The parent interactions row is no longer cursorable; the leg is.
    op.execute("DROP SEQUENCE IF EXISTS interactions_seq")
    op.execute("CREATE SEQUENCE interaction_legs_seq")

    # --- interaction_legs -------------------------------------------------
    # One row per temporal half. PK (interaction_id, leg_type) enforces at most
    # one request + one response leg per interaction.
    op.execute(
        """
        CREATE TABLE interaction_legs (
            interaction_id TEXT        NOT NULL,
            leg_type       leg_type    NOT NULL,
            occurred_at    TIMESTAMPTZ,
            payload_hash   TEXT,
            error          BOOLEAN,
            seq            BIGINT      NOT NULL DEFAULT nextval('interaction_legs_seq'),
            original_seq   BIGINT      NOT NULL,
            PRIMARY KEY (interaction_id, leg_type)
        )
        """
    )
    op.execute("ALTER SEQUENCE interaction_legs_seq OWNED BY interaction_legs.seq")
    # Cursor pagination over the leg stream (ADR-0007, one layer down).
    op.execute("CREATE INDEX interaction_legs_seq_idx ON interaction_legs (seq)")
    # Reverse lookup: all legs of an interaction (join target from the parent).
    op.execute(
        "CREATE INDEX interaction_legs_interaction_idx "
        "ON interaction_legs (interaction_id)"
    )

    # --- interaction_spans gains leg_type ---------------------------------
    # Span evidence attributes to a specific leg. Nullable so a span that
    # predates the split (or a role that is not leg-specific) is representable;
    # the current source attaches its one span to the request leg.
    op.execute("ALTER TABLE interaction_spans ADD COLUMN leg_type leg_type")


def downgrade() -> None:
    # Reverse: restore interactions' leg-dependent columns, drop the legs table
    # and the leg_type column/ENUM, recreate interactions_seq. Derived data is
    # rebuildable, so we do not attempt to fold legs back into the parent.
    op.execute("ALTER TABLE interaction_spans DROP COLUMN leg_type")
    op.execute("DROP TABLE IF EXISTS interaction_legs")
    op.execute("DROP SEQUENCE IF EXISTS interaction_legs_seq")

    op.execute("CREATE SEQUENCE interactions_seq")
    op.execute(
        """
        ALTER TABLE interactions
            ADD COLUMN started_at            TIMESTAMPTZ,
            ADD COLUMN ended_at              TIMESTAMPTZ,
            ADD COLUMN error                 BOOLEAN,
            ADD COLUMN request_payload_hash  TEXT,
            ADD COLUMN response_payload_hash TEXT,
            ADD COLUMN seq                   BIGINT NOT NULL DEFAULT nextval('interactions_seq'),
            ADD COLUMN original_seq          BIGINT NOT NULL DEFAULT 0
        """
    )
    op.execute("ALTER SEQUENCE interactions_seq OWNED BY interactions.seq")
    op.execute("CREATE INDEX interactions_seq_idx ON interactions (seq)")
    op.execute("DROP TYPE IF EXISTS leg_type")
