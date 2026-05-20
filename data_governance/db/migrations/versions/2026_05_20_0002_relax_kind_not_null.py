"""Relax `spans.kind` NOT NULL for the tracer-bullet write_span (issue #3).

The minimal `write_span` introduced in issue #3 writes only the columns it
needs to round-trip a span — `kind` is deliberately deferred to the
schema-completion slice (#6) along with the other OTLP top-level fields
(`ended_at`, `error`, `status_message`, `service_name`, `events`, `links`,
`otlp`, `scope`, `resource_attributes`).

The baseline schema declared `kind TEXT NOT NULL`, which would force the
tracer bullet either to invent a placeholder value or to populate `kind`
out-of-band — both contradict the issue's "all other columns left
NULL/default at this stage" requirement. Relaxing the column to nullable
here is additive and reversible; #6 can re-tighten to NOT NULL once kind
is always written by the receiver.

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
    # Re-tightening would fail if any rows have NULL kind, which is by design
    # the state issue #3 intentionally produces. The downgrade exists for
    # symmetry; operators rolling back must populate kind manually first.
    op.execute("ALTER TABLE spans ALTER COLUMN kind SET NOT NULL")
