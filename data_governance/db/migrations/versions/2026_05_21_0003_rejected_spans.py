"""Add rejected_spans table (issue #8 / ADR-0003).

Integrity and too-large spans that cannot be written to ``spans`` are
recorded here with their error message so operators have a wire-visible
audit trail without aborting the whole batch.

Revision ID: 0003_rejected_spans
Revises: 0002_relax_kind_not_null
Create Date: 2026-05-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0003_rejected_spans"
down_revision: Union[str, Sequence[str], None] = "0002_relax_kind_not_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE rejected_spans (
            id             BIGSERIAL   PRIMARY KEY,
            trace_id       TEXT        NOT NULL,
            span_id        TEXT        NOT NULL,
            error_message  TEXT        NOT NULL,
            rejected_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rejected_spans")
