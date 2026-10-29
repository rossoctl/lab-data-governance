"""Persist the trace-level aggregation mode alongside each trace risk record (issue #102 follow-up).

``trace_risk_records.risk_compounding_mode`` (added by ``0016_das_risk_tables``,
reserved there for "post-MVP compounding logic") has never been populated by
any write path — the trace risk processor's ``aggregate_trace_risk`` hardcoded
``severity_max`` and recorded nothing. Now that the rollup mode is
config-driven (``RISK_TRACE_AGGREGATION_RISK_LEVEL_MODE`` /
``RISK_TRACE_AGGREGATION_ENFORCEMENT_TYPE_MODE``, see
``data_governance/risk/config.py``), each stored record should say *which*
mode produced its ``trace_risk_level``/``trace_enforcement_type`` — otherwise
two rows computed under different rollup strategies (e.g. a future weighted
compounding mode alongside today's ``severity_max``) would be
indistinguishable after the fact.

This migration adds the missing second column, ``enforcement_aggregation_mode``,
so the enforcement-type rollup mode has the same persisted record as the
risk-level rollup mode (``risk_compounding_mode``). The write path (a
follow-up change to ``trace_compute.py``, not this migration) starts
populating both columns with the mode name used for that computation.

Both columns stay nullable ``TEXT``, not ENUM, matching every other
policy/mode-sourced column in this table (``trace_risk_level``,
``trace_enforcement_type`` — see ``0016``'s ADR-0014 rationale): the set of
aggregation modes is expected to grow as new compounding strategies are
added, so a closed Postgres enum would need a migration for every new mode
name.

No backfill: rows already in ``trace_risk_records`` keep
``risk_compounding_mode IS NULL``, same house policy ``0011`` documents for
derived tables ("always rebuildable by cursor replay, so no back-fill is
needed"). The write path's idempotency comparison treats a stored NULL as
different from a freshly computed ``"severity_max"``, so each such trace
picks up exactly one extra version the next time it is recomputed — a
one-time, self-correcting bump, not a design flaw.

Hand-written ``op.execute`` only, per ADR-0002/0005.

Revision ID: 0018_trace_aggregation_modes
Revises: 0017_policy_decisions
Create Date: 2026-08-17
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0018_trace_aggregation_modes"
down_revision: Union[str, Sequence[str], None] = "0017_policy_decisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE trace_risk_records ADD COLUMN enforcement_aggregation_mode TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE trace_risk_records DROP COLUMN IF EXISTS enforcement_aggregation_mode"
    )
