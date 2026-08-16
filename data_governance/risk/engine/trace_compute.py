"""Orchestration + write path for the trace risk processor (issue #102).

``compute_trace_risk`` is this engine's one directly-callable entry point.
Wiring it to the ``dg_interaction_risk_written`` NOTIFY channel/poll fallback
(StreamSpec driver + ``__main__.py``) is deferred to whichever issue lands
#164's trigger/cursoring redesign — this module only needs a ``trace_id``:

    gather the trace's current (latest-version per interaction_id)
    interaction risk records -> aggregate (FR-DAS-021, via
    ``trace_aggregate.aggregate_trace_risk``) -> write a new
    ``trace_risk_records`` version, but only when the computed record
    actually differs from the latest stored one (mirrors #101's
    ``compute.py`` FR-DAS-014 idempotency discipline). A prior version is
    never mutated (FR-DAS-013/032-equivalent immutability for trace risk).

No OPA call here — unlike #101's interaction-level compute, trace risk is a
pure rollup over already-computed interaction risk records, so there is no
policy decision to cache/refresh.

The insert allocates its next ``version`` in-statement (``SELECT
COALESCE(MAX(version), 0) + 1 ...``), same as #101's writes, so two
concurrent recomputes for the same trace collide on
``trace_risk_records``' ``UNIQUE (trace_id, version)`` index rather than
silently double-writing. On that collision this module retries once.

FR-DAS-022: no ``is_complete``/``status`` field is ever written — a trace is
an open-ended forest with no defined "done" point, so a trace risk record is
always just the best current estimate.

Each written record also persists which rollup mode produced it —
``aggregate.risk_level_mode``/``enforcement_type_mode`` (see
``trace_aggregate.py``) go into ``risk_compounding_mode``/
``enforcement_aggregation_mode`` respectively. Both are included in the
idempotency comparison, so a trace's very first recompute after this modes
column landed writes one extra version (stored ``NULL`` vs. the newly
computed ``"severity_max"``) — a one-time, self-correcting bump, not an
infinite-recompute risk, since the next recompute then matches.
"""

from __future__ import annotations

from decimal import Decimal

import psycopg

from data_governance import db
from data_governance.risk.engine.trace_aggregate import (
    CurrentInteractionRisk,
    TraceRiskAggregate,
    aggregate_trace_risk,
)

__all__ = ["compute_trace_risk"]

_MAX_ATTEMPTS = 2

# "Current" = the latest version per interaction_id within this trace_id,
# for interactions that still EXIST. DISTINCT ON (interaction_id) ... ORDER BY
# interaction_id, version DESC picks exactly that row per interaction,
# mirroring the *_latest_idx query shape used everywhere else in this package
# for "latest version for this key."
#
# The INNER JOIN against `interactions` is load-bearing (observed live): the
# sidecar lineage derivation rewrites a trace's interactions wholesale as
# spans arrive, and an exchange can be re-keyed mid-derivation — leaving
# immutable risk records for interaction ids that no longer exist. Those
# ghost records must not contribute to the rollup (they would inflate
# interaction_count and could freeze a stale risk level into every future
# trace version). Re-reading the live lineage table at aggregation time is
# the DAS discipline (FR-DAS-004: read live, never cache) — the records
# themselves stay append-only and untouched.
_CURRENT_INTERACTION_RISK_SQL = """
SELECT DISTINCT ON (r.interaction_id)
    r.interaction_risk_id, r.risk_level, r.enforcement_type,
    r.policy_event_count, r.triggered_rule_ids, r.caller_entity_id,
    r.callee_entity_id, r.overall_confidence
FROM interaction_risk_records r
JOIN interactions i ON i.id = r.interaction_id
WHERE r.trace_id = %s
ORDER BY r.interaction_id, r.version DESC
"""

_LATEST_TRACE_RECORD_SQL = (
    "SELECT version, trace_risk_level, trace_enforcement_type, "
    "interaction_count, policy_event_count, all_entity_ids, "
    "triggered_rule_ids, overall_confidence, contributing_interaction_risk_ids, "
    "risk_compounding_mode, enforcement_aggregation_mode "
    "FROM trace_risk_records WHERE trace_id = %s "
    "ORDER BY version DESC LIMIT 1"
)

_INSERT_TRACE_RECORD_SQL = """
INSERT INTO trace_risk_records (
    trace_id, version, computed_at, trace_risk_level, trace_enforcement_type,
    interaction_count, policy_event_count, all_entity_ids, triggered_rule_ids,
    overall_confidence, contributing_interaction_risk_ids,
    risk_compounding_mode, enforcement_aggregation_mode
)
SELECT %(trace_id)s,
       COALESCE(MAX(version), 0) + 1,
       now(), %(trace_risk_level)s, %(trace_enforcement_type)s,
       %(interaction_count)s, %(policy_event_count)s, %(all_entity_ids)s,
       %(triggered_rule_ids)s, %(overall_confidence)s,
       %(contributing_interaction_risk_ids)s,
       %(risk_compounding_mode)s, %(enforcement_aggregation_mode)s
  FROM trace_risk_records WHERE trace_id = %(trace_id)s
"""


def _fetch_current_records(tx: db.Transaction, trace_id: str) -> list[CurrentInteractionRisk]:
    rows = tx.fetch_all(_CURRENT_INTERACTION_RISK_SQL, (trace_id,))
    return [
        CurrentInteractionRisk(
            interaction_risk_id=str(interaction_risk_id),
            risk_level=risk_level,
            enforcement_type=enforcement_type,
            policy_event_count=policy_event_count,
            triggered_rule_ids=list(triggered_rule_ids or []),
            caller_entity_id=caller_entity_id,
            callee_entity_id=callee_entity_id,
            overall_confidence=overall_confidence,
        )
        for (
            interaction_risk_id,
            risk_level,
            enforcement_type,
            policy_event_count,
            triggered_rule_ids,
            caller_entity_id,
            callee_entity_id,
            overall_confidence,
        ) in rows
    ]


def _normalized_latest_record(row: tuple | None) -> dict | None:
    if row is None:
        return None
    (
        _version,
        trace_risk_level,
        trace_enforcement_type,
        interaction_count,
        policy_event_count,
        all_entity_ids,
        triggered_rule_ids,
        overall_confidence,
        contributing_interaction_risk_ids,
        risk_compounding_mode,
        enforcement_aggregation_mode,
    ) = row
    return {
        "trace_risk_level": trace_risk_level,
        "trace_enforcement_type": trace_enforcement_type,
        "interaction_count": interaction_count,
        "policy_event_count": policy_event_count,
        "all_entity_ids": sorted(all_entity_ids or []),
        "triggered_rule_ids": sorted(triggered_rule_ids or []),
        "overall_confidence": (
            str(overall_confidence) if overall_confidence is not None else None
        ),
        "contributing_interaction_risk_ids": [
            str(rid) for rid in (contributing_interaction_risk_ids or [])
        ],
        "risk_compounding_mode": risk_compounding_mode,
        "enforcement_aggregation_mode": enforcement_aggregation_mode,
    }


def _record_params(trace_id: str, aggregate: TraceRiskAggregate) -> tuple[dict, dict]:
    """Build the params dict for both the insert and the idempotency
    comparison, returned together so callers can't drift them apart."""
    overall_confidence: Decimal | None = aggregate.overall_confidence
    normalized = {
        "trace_risk_level": aggregate.trace_risk_level,
        "trace_enforcement_type": aggregate.trace_enforcement_type,
        "interaction_count": aggregate.interaction_count,
        "policy_event_count": aggregate.policy_event_count,
        "all_entity_ids": sorted(aggregate.all_entity_ids),
        "triggered_rule_ids": sorted(aggregate.triggered_rule_ids),
        "overall_confidence": (
            str(overall_confidence) if overall_confidence is not None else None
        ),
        "contributing_interaction_risk_ids": list(
            aggregate.contributing_interaction_risk_ids
        ),
        "risk_compounding_mode": aggregate.risk_level_mode,
        "enforcement_aggregation_mode": aggregate.enforcement_type_mode,
    }
    params = {
        "trace_id": trace_id,
        "trace_risk_level": aggregate.trace_risk_level,
        "trace_enforcement_type": aggregate.trace_enforcement_type,
        "interaction_count": aggregate.interaction_count,
        "policy_event_count": aggregate.policy_event_count,
        "all_entity_ids": aggregate.all_entity_ids,
        "triggered_rule_ids": aggregate.triggered_rule_ids,
        "overall_confidence": (
            str(overall_confidence) if overall_confidence is not None else None
        ),
        "contributing_interaction_risk_ids": aggregate.contributing_interaction_risk_ids,
        "risk_compounding_mode": aggregate.risk_level_mode,
        "enforcement_aggregation_mode": aggregate.enforcement_type_mode,
    }
    return params, normalized


def _attempt_in(tx: db.Transaction, trace_id: str) -> None:
    current_records = _fetch_current_records(tx, trace_id)
    aggregate = aggregate_trace_risk(current_records)
    params, normalized = _record_params(trace_id, aggregate)

    latest_row = tx.fetch_one(_LATEST_TRACE_RECORD_SQL, (trace_id,))
    if _normalized_latest_record(latest_row) == normalized:
        return

    tx.execute(_INSERT_TRACE_RECORD_SQL, params)


def compute_trace_risk(trace_id: str, *, tx: db.Transaction | None = None) -> None:
    """Compute and persist the current trace risk record for *trace_id*.

    Idempotent: if the freshly-computed record is identical to the latest
    stored version, nothing is written and no NOTIFY fires.

    When *tx* is provided the whole gather/aggregate/write runs inside the
    caller's transaction — the trace-trigger processor (#102/#164) passes its
    per-item transaction here so the write commits atomically with the
    stream's cursor advance (ADR-0007: an engine failure rolls the cursor
    back and the item is re-delivered, never silently skipped). No retry in
    this mode: a ``UNIQUE (trace_id, version)`` collision propagates, the
    caller's transaction rolls back, and the stream re-delivers the item —
    the re-delivery IS the retry, and it re-checks idempotency on arrival.

    When *tx* is ``None`` the call owns its transaction and retries once on
    that collision from a racing concurrent recompute — the retry re-gathers
    current records and re-checks idempotency, so a retry that lost the race
    to a winner whose write already matches simply becomes a no-op.
    """
    if tx is not None:
        _attempt_in(tx, trace_id)
        return

    last_error: psycopg.errors.UniqueViolation | None = None
    for _attempt_number in range(_MAX_ATTEMPTS):
        try:
            with db.transaction() as own_tx:
                _attempt_in(own_tx, trace_id)
            return
        except psycopg.errors.UniqueViolation as exc:
            last_error = exc
            continue
    raise last_error
