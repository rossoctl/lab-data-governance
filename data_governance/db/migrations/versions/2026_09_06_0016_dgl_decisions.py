"""Create dgl_decisions table.

One row per DGL intent-governance evaluation. Each tool call produces exactly
two rows: one for ``tool_request`` (outbound call to tool) and one for
``tool_result`` (response back from tool), sharing the same ``invocation_id``.

Schema rationale
----------------
exchange_id
    The lineage request span's span_id — the ``dg-parent`` tracestate value
    written by ``lineage-telemetry`` and read by ``intent-governance``. Equal
    to the anchor ``span_id`` in ``interaction_spans``. Join key:

        SELECT i.id
        FROM interactions i
        JOIN interaction_spans isp ON isp.interaction_id = i.id
        WHERE isp.span_id = dgl_decisions.exchange_id AND isp.role = 'anchor'

    Both rows of one tool invocation share the same exchange_id (both phases
    belong to the same MCP tool call, hence the same interaction anchor).

trace_id
    The W3C trace_id from the ``traceparent`` header — the same value stored in
    ``interactions.trace_id``. Stored here as a shortcut so the UI can join
    ``dgl_decisions`` to ``interactions`` on ``trace_id`` without going through
    ``interaction_spans``.

invocation_id
    The intent-governance plugin's per-invocation ID (``INV-<nanos>``). Unique
    per tool call; shared by the request and result rows. Natural key with
    ``phase``: ``(invocation_id, phase)`` is the unique pair.

original_payload
    The payload sent to the DGL runtime — tool arguments (request phase) or
    tool result (result phase) — before any governance transformation.

modified_payload
    The ``effective_payload`` returned by the DGL runtime. Non-null only for
    MODIFY decisions; null for ALLOW and BLOCK.

seq
    Monotonically increasing integer assigned on INSERT by the dedicated
    sequence ``dgl_decisions_seq``. Serves as both the PRIMARY KEY and the
    processor cursor (ADR-0007): the ``dgl_decisions`` processor reads
    ``WHERE seq > last_processed_seq`` and advances the cursor atomically
    with each write. The PRIMARY KEY index on ``seq`` is sufficient for
    cursor pagination — no separate ``seq`` index is needed, unlike tables
    where ``seq`` is not the PK (e.g. ``spans``, ``interactions``). The
    processor wakes on the existing ``dg_spans_inserted`` channel (spans
    table trigger) — no separate trigger on this table is needed.

Unique constraint
-----------------
``(invocation_id, phase)`` is the natural key. One tool call → one
``invocation_id`` → exactly one ``tool_request`` row and one ``tool_result``
row. ``ON CONFLICT (invocation_id, phase) DO NOTHING`` makes the processor
idempotent: re-processing the same span a second time (crash-recovery) is safe.

``exchange_id`` is NOT part of the unique constraint even though it is the
same for both rows of one invocation — adding it would be redundant because
``invocation_id`` already discriminates to one tool call.

Revision ID: 0016_dgl_decisions
Revises: 0015_lineage_entities_rename
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0016_dgl_decisions"
down_revision: Union[str, Sequence[str], None] = "0015_lineage_entities_rename"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Sequence that drives the cursorable stream (ADR-0007).
    # seq IS the primary key — one sequence, one column, matching the pattern
    # of spans/interactions/entities. No separate id column.
    op.execute("CREATE SEQUENCE dgl_decisions_seq")

    op.execute(
        """
        CREATE TABLE dgl_decisions (
            seq              BIGINT      NOT NULL DEFAULT nextval('dgl_decisions_seq'),
            exchange_id      TEXT        NOT NULL,
            trace_id         TEXT        NOT NULL,
            invocation_id    TEXT        NOT NULL,
            component        TEXT        NOT NULL,
            phase            TEXT        NOT NULL,
            decision         TEXT        NOT NULL,
            reason           TEXT,
            execution_id     TEXT,
            original_payload JSONB,
            modified_payload JSONB,
            occurred_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (seq),
            UNIQUE (invocation_id, phase)
        )
        """
    )
    op.execute("ALTER SEQUENCE dgl_decisions_seq OWNED BY dgl_decisions.seq")

    # Lookup indexes used by the UI.
    # Note: no explicit seq index — seq IS the PRIMARY KEY, so PostgreSQL
    # already creates a unique B-tree index on it. A separate seq index
    # would be redundant (unlike spans/interactions where seq != PK).
    op.execute("CREATE INDEX dgl_decisions_exchange_id_idx   ON dgl_decisions (exchange_id)")
    op.execute("CREATE INDEX dgl_decisions_trace_id_idx      ON dgl_decisions (trace_id)")
    op.execute("CREATE INDEX dgl_decisions_invocation_id_idx ON dgl_decisions (invocation_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dgl_decisions")
    op.execute("DROP SEQUENCE IF EXISTS dgl_decisions_seq")
