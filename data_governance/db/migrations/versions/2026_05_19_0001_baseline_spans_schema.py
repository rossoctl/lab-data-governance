"""Baseline: spans schema + blocked_span_counts.

Creates the v1 §3 spans schema verbatim per PROJECT.md, plus the §3.1
blocked_span_counts tally table. Per ADR-0002 the schema is the source of
truth and migrations are hand-written SQL via op.execute(...) — no
SQLAlchemy ORM models are involved.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-19
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# Alembic revision identifiers.
revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Sequence backing both `seq` (watermark, may advance once on
    # finalisation per §3.2 / ADR-0004) and `arrival_seq` (stable per-row,
    # set at INSERT and never updated). Both columns draw from the same
    # sequence so the values are globally monotonic across all writes.
    op.execute("CREATE SEQUENCE spans_seq")

    # The single append-and-finalize spans table per PROJECT.md §3.
    op.execute(
        """
        CREATE TABLE spans (
            trace_id            TEXT        NOT NULL,
            span_id             TEXT        NOT NULL,
            parent_id           TEXT,
            kind                TEXT        NOT NULL,
            name                TEXT        NOT NULL,
            service_name        TEXT,
            started_at          TIMESTAMPTZ NOT NULL,
            ended_at            TIMESTAMPTZ,
            error               BOOLEAN,
            status_message      TEXT,
            attributes          JSONB       NOT NULL DEFAULT '{}'::jsonb,
            events              JSONB,
            links               JSONB,
            otlp                JSONB,
            scope               JSONB,
            resource_attributes JSONB,
            seq                 BIGINT      NOT NULL DEFAULT nextval('spans_seq'),
            arrival_seq         BIGINT      NOT NULL,
            observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (trace_id, span_id)
        )
        """
    )
    op.execute("ALTER SEQUENCE spans_seq OWNED BY spans.seq")

    # Indexes per PROJECT.md §3 "Indexes":
    #   (trace_id, span_id) is the implicit PK index.
    #   (trace_id, parent_id) — downward walks + orphan NOT EXISTS check.
    #   (started_at)         — §7 recent-traces window query.
    #   (seq)                — §6 cursor pagination.
    op.execute("CREATE INDEX spans_trace_parent_idx ON spans (trace_id, parent_id)")
    op.execute("CREATE INDEX spans_started_at_idx ON spans (started_at)")
    op.execute("CREATE INDEX spans_seq_idx ON spans (seq)")

    # §3.1 blocklist tally: dropped spans are not stored row-by-row, just
    # counted by matched pattern.
    op.execute(
        """
        CREATE TABLE blocked_span_counts (
            pattern      TEXT        PRIMARY KEY,
            count        BIGINT      NOT NULL DEFAULT 0,
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    # The baseline does not support downgrade in v1 — the schema is the
    # initial state of the database, and v1 has no need to roll back to a
    # pre-baseline state. A future non-additive migration ships its own
    # downgrade if needed.
    op.execute("DROP TABLE IF EXISTS blocked_span_counts")
    op.execute("DROP TABLE IF EXISTS spans")
    op.execute("DROP SEQUENCE IF EXISTS spans_seq")
