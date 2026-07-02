"""P-interactions derived schema: entities / interactions / spans / payloads.

The first real derived-graph schema (issue #69). Everything before this
revision is the spans ingest side; this adds the tables the P-interactions
processor reads and writes:

  - ``entities``            cross-trace-stable identities (no trace_id; keyed
                            by ``natural_key``).
  - ``entity_spans``        which spans an entity was discovered / identified
                            via.
  - ``interactions``        single-row interactions (ADR-0013: request &
                            response payload hashes side by side, no
                            ``direction`` leg).
  - ``interaction_spans``   territory ownership: anchor / info / connector
                            spans of an interaction (ADR-0011 §3
                            ``UNIQUE(trace_id, span_id)``).
  - ``interaction_payloads`` content-addressed payload bodies, keyed by hash.
  - ``processor_state``     the durable per-processor cursor (ADR-0007).

Per ADR-0014 the three *structural* enums (``entity_kind``,
``entity_span_role``, ``interaction_span_role``) are Postgres ENUM types —
P-interactions defines them and is the classifier, so an out-of-set value is a
processor bug worth rejecting at write. ``interaction_payloads.content_kind``
stays TEXT (it churns as the payload classifier matures; ``unknown`` is the
open-world escape).

Two independent sequences (``entities_seq``, ``interactions_seq``), each OWNED
BY its table so each derived table is its own cursorable stream. Both
``entities`` and ``interactions`` keep ``seq`` (advances on every mutation) and
``original_seq`` (preserved at creation) so a consumer can tell creation from
in-place mutation (ADR-0012 mutates interactions in place). There is no
``retracted_at`` — ADR-0012 emit-once-final never writes a tombstone.

Per ADR-0002/0005 the DDL is hand-written ``op.execute(...)`` — no SQLAlchemy
ORM models. There are deliberately no foreign keys between the derived tables:
the processor re-derives lineage idempotently and order-independently
(delete+reinsert of territory sets, late-arriving parents), so a strict FK
could reject a valid mid-derive write; supporting indexes carry the access
paths instead. This matches the spans schema, which is also FK-free.

Revision ID: 0004_interactions_schema
Revises: 0003_rejected_spans
Create Date: 2026-06-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0004_interactions_schema"
down_revision: Union[str, Sequence[str], None] = "0003_rejected_spans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Structural ENUM types (ADR-0014) ---------------------------------
    # Domain-fixed closed sets that P-interactions defines and enforces. A
    # value outside the set is a processor bug; the ENUM rejects it at write.
    op.execute(
        "CREATE TYPE entity_kind AS ENUM "
        "('user', 'client', 'agent', 'tool', 'llm', 'service')"
    )
    op.execute(
        "CREATE TYPE entity_span_role AS ENUM ('discovered_via', 'identified_via')"
    )
    op.execute(
        "CREATE TYPE interaction_span_role AS ENUM ('anchor', 'info', 'connector')"
    )

    # --- Sequences (one cursorable stream per derived table) --------------
    op.execute("CREATE SEQUENCE entities_seq")
    op.execute("CREATE SEQUENCE interactions_seq")

    # --- entities ---------------------------------------------------------
    # Cross-trace stable: NO trace_id. Deduped across traces by natural_key.
    op.execute(
        """
        CREATE TABLE entities (
            id            TEXT        PRIMARY KEY,
            kind          entity_kind NOT NULL,
            natural_key   TEXT        NOT NULL UNIQUE,
            display_name  TEXT        NOT NULL,
            project_name  TEXT,
            detected_from TEXT        NOT NULL,
            seq           BIGINT      NOT NULL DEFAULT nextval('entities_seq'),
            original_seq  BIGINT      NOT NULL
        )
        """
    )
    op.execute("ALTER SEQUENCE entities_seq OWNED BY entities.seq")
    # Cursor pagination over the entity stream (ADR-0007).
    op.execute("CREATE INDEX entities_seq_idx ON entities (seq)")

    # --- entity_spans -----------------------------------------------------
    # Which spans an entity was discovered / identified via. Span key is
    # (trace_id, span_id); one entity may be tied to a span once per role.
    op.execute(
        """
        CREATE TABLE entity_spans (
            entity_id TEXT             NOT NULL,
            trace_id  TEXT             NOT NULL,
            span_id   TEXT             NOT NULL,
            role      entity_span_role NOT NULL,
            PRIMARY KEY (trace_id, span_id, entity_id, role)
        )
        """
    )
    # Reverse lookup: all spans for an entity.
    op.execute("CREATE INDEX entity_spans_entity_idx ON entity_spans (entity_id)")

    # --- interaction_payloads --------------------------------------------
    # Content-addressed payload bodies. content_kind stays TEXT (ADR-0014).
    op.execute(
        """
        CREATE TABLE interaction_payloads (
            content_hash TEXT   PRIMARY KEY,
            content_kind TEXT   NOT NULL,
            content      JSONB  NOT NULL,
            byte_size    BIGINT NOT NULL
        )
        """
    )

    # --- interactions -----------------------------------------------------
    # Single row per interaction (ADR-0013): request_payload_hash and
    # response_payload_hash side by side, no (id, direction) leg. Trace-scoped.
    op.execute(
        """
        CREATE TABLE interactions (
            id                    TEXT        PRIMARY KEY,
            trace_id              TEXT        NOT NULL,
            parent_interaction_id TEXT,
            caller_entity_id      TEXT        NOT NULL,
            callee_entity_id      TEXT        NOT NULL,
            started_at            TIMESTAMPTZ,
            ended_at              TIMESTAMPTZ,
            error                 BOOLEAN,
            request_payload_hash  TEXT,
            response_payload_hash TEXT,
            summary               TEXT        NOT NULL,
            seq                   BIGINT      NOT NULL DEFAULT nextval('interactions_seq'),
            original_seq          BIGINT      NOT NULL
        )
        """
    )
    op.execute("ALTER SEQUENCE interactions_seq OWNED BY interactions.seq")
    # An interaction is trace-scoped; index the trace it belongs to.
    op.execute("CREATE INDEX interactions_trace_idx ON interactions (trace_id)")
    # Cursor pagination over the interaction stream (ADR-0007).
    op.execute("CREATE INDEX interactions_seq_idx ON interactions (seq)")

    # --- interaction_spans ------------------------------------------------
    # Territory ownership of an interaction. ADR-0011 §3: a span belongs to at
    # most one interaction → UNIQUE(trace_id, span_id) is the load-bearing
    # invariant.
    op.execute(
        """
        CREATE TABLE interaction_spans (
            interaction_id TEXT                  NOT NULL,
            trace_id       TEXT                  NOT NULL,
            span_id        TEXT                  NOT NULL,
            role           interaction_span_role NOT NULL,
            PRIMARY KEY (trace_id, span_id)
        )
        """
    )
    # Reverse lookup: all spans of an interaction.
    op.execute(
        "CREATE INDEX interaction_spans_interaction_idx "
        "ON interaction_spans (interaction_id)"
    )

    # --- processor_state --------------------------------------------------
    # Durable per-processor cursor (ADR-0007). One row per processor name.
    op.execute(
        """
        CREATE TABLE processor_state (
            processor_name     TEXT        PRIMARY KEY,
            last_processed_seq BIGINT      NOT NULL DEFAULT 0,
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    # Drop in reverse dependency order: tables first (the sequences are OWNED
    # BY their tables so they drop with them, but we drop explicitly for the
    # processor_state / payload tables that own none), then the ENUM types
    # which the tables' columns reference.
    op.execute("DROP TABLE IF EXISTS processor_state")
    op.execute("DROP TABLE IF EXISTS interaction_spans")
    op.execute("DROP TABLE IF EXISTS interactions")
    op.execute("DROP TABLE IF EXISTS interaction_payloads")
    op.execute("DROP TABLE IF EXISTS entity_spans")
    op.execute("DROP TABLE IF EXISTS entities")
    # entities_seq / interactions_seq are OWNED BY the dropped tables and go
    # with them; guard anyway in case a table was absent.
    op.execute("DROP SEQUENCE IF EXISTS interactions_seq")
    op.execute("DROP SEQUENCE IF EXISTS entities_seq")
    op.execute("DROP TYPE IF EXISTS interaction_span_role")
    op.execute("DROP TYPE IF EXISTS entity_span_role")
    op.execute("DROP TYPE IF EXISTS entity_kind")
