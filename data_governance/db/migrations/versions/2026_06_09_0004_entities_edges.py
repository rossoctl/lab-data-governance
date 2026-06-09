"""Add the derived entity/edge graph schema (issue #55 / ADR-0007).

Three additive tables — ``entities``, ``edges``, ``edge_annotations`` — plus
the ``edges_seq`` sequence and their indexes. These hold the derived lineage
graph that a second Layer-2 processor (the graph-builder) writes from ``spans``;
nothing reads or writes them yet (the builder lands in issue #56, the read path
in #57). Per ADR-0002 the schema is the source of truth and migrations are
hand-written SQL via ``op.execute(...)`` — no SQLAlchemy ORM models.

Design decisions encoded here (all pinned in ADR-0007):

- ``entities`` grain is ``(service_name, semantic_kind, sub_kind)``; identity is
  the deterministic ``entity_id`` over that tuple.
- ``edges`` is keyed by the **child** span ``(trace_id, span_id)`` — the same PK
  as ``spans`` — so a span has at most one edge. ``from_entity`` is **nullable**:
  an orphan boundary (parent span absent at derivation time) is recorded with
  ``from_entity = NULL`` rather than dropped, and flipped to the real entity on a
  later builder pass when the parent arrives.
- ``edge_seq`` is sequence-allocated at INSERT and preserved on update (mirrors
  ``spans.arrival_seq``), giving the read path a stable keyset axis.
- **No** ``started_at`` / ``ended_at`` / payload columns on ``edges``: timing and
  content join back to ``spans`` on ``(trace_id, span_id)`` (ADR-0006).
- ``edge_annotations`` keys on the child ``(trace_id, span_id)`` — a stable
  ``spans`` PK — and references ``spans`` (NOT ``edges``), so the marks layer is
  independent of whether ``edges`` stays a table or is later demoted to a VIEW
  (ADR-0007's de-materialization fallback).

Revision ID: 0004_entities_edges
Revises: 0003_rejected_spans
Create Date: 2026-06-09
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0004_entities_edges"
down_revision: Union[str, Sequence[str], None] = "0003_rejected_spans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Sequence backing edges.edge_seq. Allocated at INSERT, preserved on
    # re-derivation (the builder's ON CONFLICT DO UPDATE never touches it), so
    # the get_edges read path can keyset on the same axis it sorts on — the
    # #30 cursor/sort-axis fix carried over to edges. OWNED BY ties its
    # lifecycle to the column, mirroring spans_seq.
    op.execute("CREATE SEQUENCE edges_seq")

    # One node per (service_name, semantic_kind, sub_kind). entity_id is a
    # deterministic opaque key over that tuple (built by the graph-builder),
    # so upserts are idempotent.
    op.execute(
        """
        CREATE TABLE entities (
            entity_id     TEXT        PRIMARY KEY,
            service_name  TEXT,
            semantic_kind TEXT        NOT NULL,
            sub_kind      TEXT,
            display_name  TEXT,
            attributes    JSONB,
            first_seen_at TIMESTAMPTZ,
            last_seen_at  TIMESTAMPTZ
        )
        """
    )

    # One directed edge per cross-entity parent->child boundary, keyed by the
    # child span. from_entity is nullable (orphan boundary); to_entity is
    # always known. parent_id is NOT NULL because a real root (parent_id IS
    # NULL on the span) produces no edge at all. No time/payload columns —
    # those join back to spans (ADR-0006).
    op.execute(
        """
        CREATE TABLE edges (
            trace_id    TEXT   NOT NULL,
            span_id     TEXT   NOT NULL,
            parent_id   TEXT   NOT NULL,
            to_entity   TEXT   NOT NULL REFERENCES entities (entity_id),
            from_entity TEXT            REFERENCES entities (entity_id),
            edge_kind   TEXT,
            edge_seq    BIGINT NOT NULL DEFAULT nextval('edges_seq'),
            PRIMARY KEY (trace_id, span_id),
            FOREIGN KEY (trace_id, span_id) REFERENCES spans (trace_id, span_id)
        )
        """
    )
    op.execute("ALTER SEQUENCE edges_seq OWNED BY edges.edge_seq")

    # Side-table of governance marks. Keyed on the child (trace_id, span_id)
    # and referencing spans (NOT edges) so the marks layer survives a future
    # demotion of edges to a VIEW. derived_from is a self-reference giving
    # propagated marks (derived=true) their lineage back to the seed mark.
    op.execute(
        """
        CREATE TABLE edge_annotations (
            id           BIGSERIAL   PRIMARY KEY,
            trace_id     TEXT        NOT NULL,
            span_id      TEXT        NOT NULL,
            mark_type    TEXT        NOT NULL,
            mark_key     TEXT,
            value        JSONB,
            origin       TEXT        NOT NULL,
            derived      BOOLEAN     NOT NULL DEFAULT false,
            derived_from BIGINT      REFERENCES edge_annotations (id),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (trace_id, span_id) REFERENCES spans (trace_id, span_id)
        )
        """
    )

    # Aggregated graph view groups by endpoints; the read path keysets on
    # edge_seq; trace_id supports per-trace edge lookups overlaid on the
    # trace tree.
    op.execute("CREATE INDEX edges_from_entity_idx ON edges (from_entity)")
    op.execute("CREATE INDEX edges_to_entity_idx ON edges (to_entity)")
    op.execute("CREATE INDEX edges_trace_id_idx ON edges (trace_id)")
    op.execute("CREATE INDEX edges_edge_seq_idx ON edges (edge_seq)")

    # Annotation lookups by edge/child, and by mark category for overlay
    # filtering.
    op.execute(
        "CREATE INDEX edge_annotations_trace_span_idx "
        "ON edge_annotations (trace_id, span_id)"
    )
    op.execute(
        "CREATE INDEX edge_annotations_mark_idx "
        "ON edge_annotations (mark_type, mark_key)"
    )

    # Direct-mark idempotency: re-running a classifier must not duplicate a
    # mark. The exact NULL-handling semantics for mark_key (a NULL key under
    # the default NULLS DISTINCT does not collide) are pinned by ADR-0008 in
    # issue #60 when the marks writer lands; this index establishes the axis.
    op.execute(
        "CREATE UNIQUE INDEX edge_annotations_direct_mark_uq "
        "ON edge_annotations (trace_id, span_id, mark_type, mark_key, origin)"
    )


def downgrade() -> None:
    # Reverse dependency order: edge_annotations references spans; edges
    # references entities and spans; edges_seq is OWNED BY edges.edge_seq (so
    # dropping edges drops it, but the explicit guarded drop is harmless).
    op.execute("DROP TABLE IF EXISTS edge_annotations")
    op.execute("DROP TABLE IF EXISTS edges")
    op.execute("DROP TABLE IF EXISTS entities")
    op.execute("DROP SEQUENCE IF EXISTS edges_seq")
