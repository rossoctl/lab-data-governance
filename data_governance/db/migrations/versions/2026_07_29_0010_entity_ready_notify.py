"""NOTIFY trigger on entity inserts: the ``dg_entity_ready`` stream (issue #121, ADR-0027).

Adds a database trigger that fires ``pg_notify('dg_entity_ready', '')`` on inserts
into ``entities``, so a downstream Layer-2 governance consumer (risk / data-lineage
/ the eventual PDP) can ``LISTEN`` for it and drain the new entity immediately
instead of waiting up to a poll interval. This is the clean, independent half of
the ADR-0027 leg-readiness work: it validates the notify → cursor-drain →
durable-cursor → restart shape before the harder readiness-gated leg path builds
on it.

The ``entities`` table already carries the full cursorable-stream infrastructure
from migration 0004 — ``seq BIGINT NOT NULL DEFAULT nextval('entities_seq')``, the
``entities_seq`` sequence OWNED BY it, and the ``entities_seq_idx`` cursor index.
So this revision adds **only the notify half**; the consumer drains
``entities WHERE seq > cursor ORDER BY seq ASC`` directly and reuses the shared
driver (``processors/_driver.py``) verbatim — this stream has no readiness gate
(that is what makes it the easy half; ADR-0027).

Decisions baked into the DDL:

- **ROW-level ``AFTER INSERT``**, per ADR-0027's ``AFTER INSERT ON entities FOR
  EACH ROW`` wording. Row-level is also what makes the channel **first-detection
  only**: an entity's identity is set once at creation and, for the current
  source, never mutated — the P-interactions write path
  (``processors/interactions/state.py``) is ``INSERT ... ON CONFLICT
  (natural_key) DO UPDATE`` and the conflict branch only ever rewrites identical
  values (including ``seq = EXCLUDED.seq``, the span-derived value recomputed on
  every re-derive). The ``ON CONFLICT DO UPDATE`` conflict path fires
  ``AFTER UPDATE``, **not** ``AFTER INSERT``, so this trigger never sees a
  re-derive of an already-known entity — a no-op update is silent regardless of
  whether it touches ``seq``. (Contrast: the payloads trigger, migration 0007,
  is ``FOR EACH STATEMENT``; either granularity is correct here since the
  notification is payload-less and the consumer always re-drains ``WHERE seq >
  cursor``. Row-level is chosen to match the ADR's wording and to make the
  first-detection property hold structurally rather than incidentally.)

- **Empty payload.** The notification carries no data: "an entity may be ready"
  is the entire signal. The consumer always re-drains from its durable
  ``processor_state`` cursor, so a payload would be ignored; empty payloads also
  coalesce a burst of inserts into effectively one wake (Postgres deduplicates
  identical pending notifications within a transaction). Mirrors
  ``dg_notify_spans`` (0005) / ``dg_notify_payloads`` (0007) exactly.

- The function ``RETURNS trigger`` and returns ``NULL`` — the return value of an
  ``AFTER`` trigger is ignored, and ``NULL`` is the conventional body.

Correctness does not depend on this trigger: the consumer's poll loop is the
backstop (ADR-0007, ADR-0015, ADR-0027). With the trigger absent or a
notification dropped, the consumer still drains on the poll alone — this revision
only removes latency. Per ADR-0002/0005 the DDL is hand-written ``op.execute(...)``
— no SQLAlchemy ORM models.

Forward-compatibility note (ADR-0027, out of scope here): when an entity-mutation
writer lands (ADR-0011 retarget), an ``AFTER UPDATE ... WHEN (NEW.seq IS DISTINCT
FROM OLD.seq)`` trigger reusing this same ``dg_notify_entities()`` function is an
additive migration — exactly how migration 0005 → 0006 evolved the spans notify.

Revision ID: 0010_entity_ready_notify
Revises: 0009_interaction_legs
Create Date: 2026-07-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0010_entity_ready_notify"
down_revision: Union[str, Sequence[str], None] = "0009_interaction_legs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The notify function: payload-less pg_notify on the consumer-facing channel.
    # Mirrors dg_notify_spans (0005) / dg_notify_payloads (0007).
    op.execute(
        """
        CREATE FUNCTION dg_notify_entities() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_notify('dg_entity_ready', '');
            RETURN NULL;
        END;
        $$
        """
    )
    # Row-level AFTER INSERT (ADR-0027). Fires once per genuinely-inserted entity
    # row; the ON CONFLICT DO UPDATE conflict path fires AFTER UPDATE and is never
    # seen here, so the channel is first-detection only.
    op.execute(
        """
        CREATE TRIGGER dg_entities_notify
        AFTER INSERT ON entities
        FOR EACH ROW
        EXECUTE FUNCTION dg_notify_entities()
        """
    )


def downgrade() -> None:
    # Drop the trigger before the function it calls.
    op.execute("DROP TRIGGER IF EXISTS dg_entities_notify ON entities")
    op.execute("DROP FUNCTION IF EXISTS dg_notify_entities()")
