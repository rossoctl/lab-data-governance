"""Make `spans.kind` permanently nullable so OTLP `SPAN_KIND_UNSPECIFIED` maps to SQL NULL.

The baseline schema declared `kind TEXT NOT NULL`, which is incompatible
with OTLP's six-valued `SpanKind` enum: `SPAN_KIND_UNSPECIFIED` carries
no information and has no SQL string representation worth inventing. The
receiver's mapping (`_SPAN_KIND_NAMES.get(span.kind)`) returns ``None``
for `SPAN_KIND_UNSPECIFIED`, and `write_span` writes that ``None`` through
to the column unchanged.

Dropping ``NOT NULL`` here is therefore the steady-state shape of the
column, not a transitional relaxation: ``kind IS NULL`` is the canonical
SQL encoding of "the producer did not specify a kind". The five named
kinds (``INTERNAL``/``SERVER``/``CLIENT``/``PRODUCER``/``CONSUMER``) are
the only non-NULL values that ever land in this column.

The ``downgrade()`` direction re-applies ``NOT NULL`` purely for revision
symmetry; operators rolling back must first populate or drop the rows
where ``kind IS NULL``, since those rows are valid by design under the
current schema.

Revision ID: 0002_relax_kind_not_null
Revises: 0001_baseline
Create Date: 2026-05-20
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0002_relax_kind_not_null"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE spans ALTER COLUMN kind DROP NOT NULL")


def downgrade() -> None:
    # Re-tightening would fail if any rows have NULL kind, which is the
    # canonical encoding of OTLP SPAN_KIND_UNSPECIFIED under the current
    # schema. The downgrade exists for revision symmetry; operators rolling
    # back must first populate or drop those rows.
    op.execute("ALTER TABLE spans ALTER COLUMN kind SET NOT NULL")
