"""Interaction policy-decision table (issue #101, PRD-SENTRY-001 v8 §6.2 support).

The interaction risk computation engine (#101) calls OPA once per
**interaction** (not per span — the issue text's "any span not yet
evaluated" is read as "any interaction not yet evaluated") and persists the
decision here. This table is what gives "not yet evaluated" a durable
meaning: the engine computes an ``evidence_fingerprint`` from the
interaction's current legs/classifications/spans, and only calls OPA
(inserting a new decision ``version``) when that fingerprint differs from
the interaction's latest stored decision's fingerprint. When it matches, the
cached decision is reused with no OPA call.

Append-only/versioned, mirroring ``interaction_risk_records`` (0016)'s
discipline rather than a keyed-per-interaction "latest row" shape: a
decision can change for the same interaction (FR-DAS-012 condition 3, "new
or updated OPA decision"), and that change must be detectable by comparing
against a specific prior version rather than being silently overwritten.
``UNIQUE (interaction_id, version)`` enforces the same immutable-snapshot
invariant as 0016's risk records.

FK-free per the 0004 derived-table convention. ``risk_level`` /
``enforcement_type`` stay TEXT, not ENUM — they are OPA-sourced and churn
with policy (ADR-0014), same reasoning as 0016's risk-record columns.
``confidence`` is ``NUMERIC(4,3)`` — a bounded [0, 1] probability to three
decimal places — not ``DOUBLE PRECISION``, same reasoning as 0016's
``overall_confidence``.

No NOTIFY trigger is added here. A ``dg_interaction_policy_decided`` channel
would be the natural wake-signal for FR-DAS-012 condition 3, but nothing
consumes it yet — the interaction risk engine calls OPA and reads the
decision back synchronously within its own compute path, it does not need
to be notified of its own write. Adding an unconsumed channel now would be
speculative; recorded as a follow-up (tracked alongside issue #158) if a
future consumer needs to react to decisions changing independently of a
risk-record recompute.

Hand-written ``op.execute`` only, per ADR-0002/0005.

Chained after ``0016_das_risk_tables``. Renumbered from ``0013`` to ``0017``
(and ``0012_das_risk_tables`` from ``0012`` to ``0016``) when merging
``main`` into this branch, to resolve the two-heads collision with main's
lineage chain — see ``0016``'s docstring. No DDL changed.

Revision ID: 0017_policy_decisions
Revises: 0016_das_risk_tables
Create Date: 2026-08-06

Note on the revision id: ``alembic_version.version_num`` is
``VARCHAR(32)`` (set by alembic's own bootstrap migration/tooling, not this
repo). ``"0013_interaction_policy_decisions"`` (33 chars) overflows that
column — confirmed by running this migration against the real chain, not a
guess — so the id is shortened to ``0013_policy_decisions`` (21 chars), and
that same shortening carries over to the ``0017`` renumbering. The table
name itself stays the fully descriptive ``interaction_policy_decisions``;
only the revision slug is abbreviated.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0017_policy_decisions"
down_revision: Union[str, Sequence[str], None] = "0016_das_risk_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE interaction_policy_decisions (
            decision_id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            interaction_id       TEXT        NOT NULL,
            version              INTEGER     NOT NULL,
            evaluated_at         TIMESTAMPTZ NOT NULL,
            risk_level           TEXT        NOT NULL,
            enforcement_type     TEXT,
            allowed_actions      TEXT[]      NOT NULL DEFAULT '{}',
            explanation          TEXT,
            triggered_rules      TEXT[]      NOT NULL DEFAULT '{}',
            confidence           NUMERIC(4,3),
            policy_version       TEXT,
            evidence_fingerprint TEXT        NOT NULL,
            PRIMARY KEY (decision_id)
        )
        """
    )
    # One immutable snapshot per (interaction, version) — a re-evaluation
    # inserts a new version rather than mutating the previous one.
    op.execute(
        "CREATE UNIQUE INDEX interaction_policy_decisions_version_uq "
        "ON interaction_policy_decisions (interaction_id, version)"
    )
    # "Latest decision for this interaction" lookup — the fingerprint check
    # the engine runs before deciding whether to call OPA.
    op.execute(
        "CREATE INDEX interaction_policy_decisions_latest_idx "
        "ON interaction_policy_decisions (interaction_id, evaluated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS interaction_policy_decisions")
