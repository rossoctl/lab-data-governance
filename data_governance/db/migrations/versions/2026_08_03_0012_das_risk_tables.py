"""DAS backbone: interaction/trace risk records, alerts, risk-written trigger.

Issue #98 — the Decision Aggregation & Storage (DAS) component's first
migration. Adds the three DAS-owned derived tables per
PRD-SENTRY-001 v8 §6.2-6.4:

  - ``interaction_risk_records`` — one immutable, versioned risk computation
    per interaction (§6.2). A recompute NEVER mutates a row — it inserts a new
    ``version``. ``UNIQUE (interaction_id, version)`` enforces that invariant.
  - ``trace_risk_records`` — one immutable, versioned risk rollup per trace
    (§6.3), same versioning discipline as above.
  - ``alerts`` — operator-facing alert records (§6.4), keyed by a
    ``dedup_key`` that is intentionally NOT unique: a superseding alert shares
    its dedup key with the alert(s) it supersedes (``superseded_by``), so the
    dedup lookup is "most recent row for this key," not "the only row."

Also adds the one NOTIFY trigger this issue owns: ``dg_interaction_risk_written``,
fired after every insert into ``interaction_risk_records`` so a future Trace
Risk Processor (#102) can wake on new/recomputed interaction risk. Two other
channels described by the PRD (``dg_interaction_legs_inserted``,
``dg_classifications_inserted``) are deliberately NOT added here — deferred to
a later issue, since ``payload_classifications`` has no ordering column to
cursor over yet (an open design question raised in #98 itself, not resolved by
this migration).

Two deliberate deviations from established repo conventions, documented here
per the #98 plan rather than silently applied:

  - **UUID primary keys with ``DEFAULT gen_random_uuid()``.** Every existing
    table in this repo (spans, interactions, entities, payloads,
    classifications) uses ``TEXT`` ids assigned upstream. The PRD explicitly
    specifies UUID PKs for all three DAS tables (§6.2-6.4), and Postgres 16
    provides ``gen_random_uuid()`` natively — no ``pgcrypto`` extension
    needed. Followed the PRD here rather than the repo's TEXT convention.
  - **``risk_level`` / ``enforcement_type`` / ``status`` stay ``TEXT``, not
    ENUM.** ADR-0014 reserves Postgres ENUMs for structural closed sets owned
    by the classifying processor (e.g. ``leg_type``). These three columns
    carry values that originate from OPA — an external, policy-driven
    authority — and churn with policy, same reasoning that keeps
    ``interaction_payloads.content_kind`` as TEXT.
  - **Confidence/score columns are ``NUMERIC(4,3)``, not ``DOUBLE
    PRECISION``.** ``overall_confidence`` on both ``interaction_risk_records``
    and ``trace_risk_records`` is a bounded probability in ``[0, 1]``, always
    expressed to three decimal places (per PRD confidence-scoring
    conventions). ``NUMERIC(4,3)`` stores that exactly — no float rounding
    drift on repeated reads/writes — and the fixed precision/scale rejects an
    out-of-range or over-precise value at the DB layer rather than silently
    truncating it. Apply ``NUMERIC(4,3)`` to any future DAS/ARC column with
    the same shape (confidence scores, risk scores in ``[0, 1]``), not
    ``DOUBLE PRECISION``/``FLOAT``.

All three tables are FK-free per the 0004 convention: DAS re-derives its
tables idempotently from upstream data rather than being enforced by a hard
foreign key, so a mid-derive write is never rejected by a dangling reference.

Hand-written ``op.execute`` only, per ADR-0002/0005.

Chained after ``0011_drop_leg_original_seq`` (not the ``0009_interaction_legs``
this revision originally chained from) so it applies last, after the
``0010``/``0011`` migrations that landed on ``main`` while this one was in
review. Renumbered from ``0010`` to ``0012`` accordingly — no DDL changed.

Revision ID: 0012_das_risk_tables
Revises: 0011_drop_leg_original_seq
Create Date: 2026-07-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0012_das_risk_tables"
down_revision: Union[str, Sequence[str], None] = "0011_drop_leg_original_seq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- interaction_risk_records (PRD §6.2) ------------------------------
    op.execute(
        """
        CREATE TABLE interaction_risk_records (
            interaction_risk_id      UUID        NOT NULL DEFAULT gen_random_uuid(),
            interaction_id           TEXT        NOT NULL,
            trace_id                 TEXT        NOT NULL,
            parent_interaction_id    TEXT,
            caller_entity_id         TEXT        NOT NULL,
            callee_entity_id         TEXT        NOT NULL,
            version                  INTEGER     NOT NULL,
            computed_at              TIMESTAMPTZ NOT NULL,
            risk_level               TEXT        NOT NULL,
            enforcement_type         TEXT,
            policy_event_count       INTEGER     NOT NULL,
            triggered_rule_ids       TEXT[]      NOT NULL DEFAULT '{}',
            legs_evidenced           TEXT[]      NOT NULL DEFAULT '{}',
            classification_summary   JSONB,
            opa_policy_versions_used TEXT[]      NOT NULL DEFAULT '{}',
            overall_confidence       NUMERIC(4,3),
            PRIMARY KEY (interaction_risk_id)
        )
        """
    )
    # One immutable snapshot per (interaction, version) — a recompute inserts
    # a new version rather than mutating the previous one.
    op.execute(
        "CREATE UNIQUE INDEX interaction_risk_records_version_uq "
        "ON interaction_risk_records (interaction_id, version)"
    )
    # "Latest version for this interaction" lookup.
    op.execute(
        "CREATE INDEX interaction_risk_records_latest_idx "
        "ON interaction_risk_records (interaction_id, computed_at DESC)"
    )
    op.execute(
        "CREATE INDEX interaction_risk_records_trace_idx "
        "ON interaction_risk_records (trace_id)"
    )
    op.execute(
        "CREATE INDEX interaction_risk_records_risk_level_idx "
        "ON interaction_risk_records (risk_level)"
    )
    # FR-DAS-035: lookups by triggered rule.
    op.execute(
        "CREATE INDEX interaction_risk_records_rule_ids_gin "
        "ON interaction_risk_records USING GIN (triggered_rule_ids)"
    )

    # --- trace_risk_records (PRD §6.3) ------------------------------------
    op.execute(
        """
        CREATE TABLE trace_risk_records (
            trace_risk_id                     UUID        NOT NULL DEFAULT gen_random_uuid(),
            trace_id                          TEXT        NOT NULL,
            version                           INTEGER     NOT NULL,
            computed_at                       TIMESTAMPTZ NOT NULL,
            trace_risk_level                  TEXT        NOT NULL,
            trace_enforcement_type            TEXT,
            risk_compounding_mode             TEXT,
            interaction_count                 INTEGER     NOT NULL,
            policy_event_count                INTEGER     NOT NULL,
            all_entity_ids                    TEXT[]      NOT NULL DEFAULT '{}',
            triggered_rule_ids                TEXT[]      NOT NULL DEFAULT '{}',
            overall_confidence                NUMERIC(4,3),
            contributing_interaction_risk_ids UUID[]      NOT NULL DEFAULT '{}',
            PRIMARY KEY (trace_risk_id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX trace_risk_records_version_uq "
        "ON trace_risk_records (trace_id, version)"
    )
    op.execute(
        "CREATE INDEX trace_risk_records_latest_idx "
        "ON trace_risk_records (trace_id, computed_at DESC)"
    )

    # --- alerts (PRD §6.4) -------------------------------------------------
    op.execute(
        """
        CREATE TABLE alerts (
            alert_id               UUID        NOT NULL DEFAULT gen_random_uuid(),
            timestamp              TIMESTAMPTZ NOT NULL,
            trace_id               TEXT        NOT NULL,
            trace_risk_record_id   UUID,
            risk_level             TEXT        NOT NULL,
            enforcement_type       TEXT,
            title                  TEXT        NOT NULL,
            description            TEXT        NOT NULL,
            involved_entities      JSONB       NOT NULL DEFAULT '[]',
            triggered_rule_ids     TEXT[]      NOT NULL DEFAULT '{}',
            status                 TEXT        NOT NULL DEFAULT 'open',
            alert_type             TEXT,
            assigned_to            TEXT,
            dedup_key              TEXT,
            duplicate_count        INTEGER     NOT NULL DEFAULT 0,
            superseded_by          UUID,
            PRIMARY KEY (alert_id)
        )
        """
    )
    # Not unique: a superseding alert shares its dedup_key with the alert(s)
    # it supersedes, so lookups resolve "most recent for this key," not
    # "the only row for this key."
    op.execute("CREATE INDEX alerts_dedup_key_idx ON alerts (dedup_key)")
    op.execute(
        "CREATE INDEX alerts_status_risk_level_idx ON alerts (status, risk_level)"
    )
    op.execute("CREATE INDEX alerts_trace_idx ON alerts (trace_id)")

    # --- dg_interaction_risk_written notify trigger -----------------------
    # Statement-level (the 0007 payloads shape), not row-level (0006's shape):
    # interaction_risk_records is insert-only and immutably versioned, so
    # there is no finalization/duplicate distinction to draw per row.
    op.execute(
        """
        CREATE FUNCTION dg_notify_interaction_risk() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_notify('dg_interaction_risk_written', '');
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER dg_interaction_risk_notify
        AFTER INSERT ON interaction_risk_records
        FOR EACH STATEMENT
        EXECUTE FUNCTION dg_notify_interaction_risk()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS dg_interaction_risk_notify ON interaction_risk_records")
    op.execute("DROP FUNCTION IF EXISTS dg_notify_interaction_risk()")

    op.execute("DROP TABLE IF EXISTS alerts")
    op.execute("DROP TABLE IF EXISTS trace_risk_records")
    op.execute("DROP TABLE IF EXISTS interaction_risk_records")
