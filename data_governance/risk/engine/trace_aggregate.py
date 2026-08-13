"""Pure trace-risk aggregation functions (issue #102, PRD-SENTRY-001 v8 §6.3, FR-DAS-021).

A plain function over a plain dataclass — no Postgres. ``trace_compute.py``
is the I/O shell that gathers the trace's current (latest-version)
interaction risk records and hands them to :func:`aggregate_trace_risk`;
keeping the semantics pure makes the aggregation matrix (severity rollup,
entity/rule id union, confidence averaging, empty-trace corner cases)
testable with no DB.

Severity rollup reuses :func:`data_governance.risk.engine.utils.severity_max`
over :data:`RISK_LEVEL_ORDER`/``ENFORCEMENT_ORDER` — the same
most-severe-first ranking #101 uses, not a re-derived ordering.

Two aggregation rules are not spelled out by FR-DAS-021 (which only pins
``trace_risk_level``, ``trace_enforcement_type``, ``interaction_count``,
``policy_event_count`` and ``contributing_interaction_risk_ids``) but are
required by the §6.3 trace risk record shape, so they are inferred here and
called out explicitly rather than left undocumented:

  - ``all_entity_ids``: the union of every current record's
    ``caller_entity_id``/``callee_entity_id``, deduplicated and sorted.
  - ``overall_confidence``: the mean of the current records' confidences
    that are actually present, quantized with the same
    :func:`data_governance.risk.engine.utils.quantize_confidence` helper
    #101 uses for the interaction-level value. A record with no confidence
    is excluded from the average rather than treated as 0, so a missing
    signal cannot silently drag the aggregate down.
"""

from __future__ import annotations

import dataclasses
import functools
from decimal import Decimal

from data_governance.risk.engine.utils import (
    ENFORCEMENT_ORDER,
    RISK_LEVEL_ORDER,
    quantize_confidence,
    severity_max,
)

__all__ = [
    "RISK_LEVEL_ORDER",
    "ENFORCEMENT_ORDER",
    "CurrentInteractionRisk",
    "TraceRiskAggregate",
    "aggregate_trace_risk",
]


@dataclasses.dataclass(frozen=True)
class CurrentInteractionRisk:
    """The subset of one current (latest-version) ``interaction_risk_records``
    row needed for trace-level aggregation."""

    interaction_risk_id: str
    risk_level: str
    enforcement_type: str | None
    policy_event_count: int
    triggered_rule_ids: list[str]
    caller_entity_id: str | None
    callee_entity_id: str | None
    overall_confidence: Decimal | None


@dataclasses.dataclass(frozen=True)
class TraceRiskAggregate:
    """The computed FR-DAS-021 rollup for one trace, ready to be written as a
    ``trace_risk_records`` row."""

    trace_risk_level: str
    trace_enforcement_type: str | None
    interaction_count: int
    policy_event_count: int
    all_entity_ids: list[str]
    triggered_rule_ids: list[str]
    overall_confidence: Decimal | None
    contributing_interaction_risk_ids: list[str]


def aggregate_trace_risk(
    records: list[CurrentInteractionRisk],
) -> TraceRiskAggregate:
    """FR-DAS-021: roll up a trace's current interaction risk records.

    ``trace_risk_level``/``trace_enforcement_type`` are the highest/strictest
    values across *records* (via :func:`severity_max`, so an unranked or
    missing value never raises). An empty *records* list (a trace with no
    interaction risk yet) rolls up to ``risk_level="none"``/no enforcement —
    the least-severe value, not an error.
    """
    interaction_count = len(records)
    trace_risk_level = functools.reduce(
        lambda a, b: severity_max(a, b, order=RISK_LEVEL_ORDER),
        (r.risk_level for r in records),
        "none",
    )
    trace_enforcement_type = functools.reduce(
        lambda a, b: severity_max(a, b, order=ENFORCEMENT_ORDER),
        (r.enforcement_type for r in records),
        None,
    )
    policy_event_count = sum(r.policy_event_count for r in records)
    all_entity_ids = sorted(
        {
            entity_id
            for r in records
            for entity_id in (r.caller_entity_id, r.callee_entity_id)
            if entity_id is not None
        }
    )
    triggered_rule_ids = sorted(
        {rule_id for r in records for rule_id in r.triggered_rule_ids}
    )
    confidences = [r.overall_confidence for r in records if r.overall_confidence is not None]
    overall_confidence = (
        quantize_confidence(float(sum(confidences)) / len(confidences))
        if confidences
        else None
    )
    contributing_interaction_risk_ids = [r.interaction_risk_id for r in records]

    return TraceRiskAggregate(
        trace_risk_level=trace_risk_level,
        trace_enforcement_type=trace_enforcement_type,
        interaction_count=interaction_count,
        policy_event_count=policy_event_count,
        all_entity_ids=all_entity_ids,
        triggered_rule_ids=triggered_rule_ids,
        overall_confidence=overall_confidence,
        contributing_interaction_risk_ids=contributing_interaction_risk_ids,
    )
