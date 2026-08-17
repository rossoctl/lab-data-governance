"""Pure trace-risk aggregation functions (issue #102, PRD-SENTRY-001 v8 §6.3, FR-DAS-021).

A plain function over a plain dataclass — no Postgres. ``trace_compute.py``
is the I/O shell that gathers the trace's current (latest-version)
interaction risk records and hands them to :func:`aggregate_trace_risk`;
keeping the semantics pure makes the aggregation matrix (severity rollup,
entity/rule id union, confidence averaging, empty-trace corner cases)
testable with no DB.

The rollup mode for ``trace_risk_level``/``trace_enforcement_type`` is
config-driven —
:data:`data_governance.risk.config.TRACE_AGGREGATION_RISK_LEVEL_MODE`/
``TRACE_AGGREGATION_ENFORCEMENT_TYPE_MODE`` — rather than hardcoded, so a
future compounding strategy can replace ``severity_max`` without changing
:func:`aggregate_trace_risk`'s signature. ``"severity_max"`` (highest
value across the trace's current interaction risk records, via
:func:`data_governance.risk.engine.utils.severity_max` over
:data:`RISK_LEVEL_ORDER`/``ENFORCEMENT_ORDER`) is the only mode implemented
today, for both params. An unrecognized mode raises :class:`ValueError`
rather than silently falling back, so a config typo fails loudly instead of
quietly picking the default.

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
from typing import Callable

from data_governance.risk import config
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

# Mode name -> reducer over a value order tuple. Each reducer takes the
# records' values for one field (risk_level or enforcement_type) plus that
# field's severity order, and folds them into a single rolled-up value.
# "severity_max" is the only mode either config var can name today; adding a
# new compounding strategy is a matter of adding another entry here.
_RiskLevelReducer = Callable[[list[str], tuple[str, ...]], str]
_EnforcementTypeReducer = Callable[[list[str | None], tuple[str, ...]], str | None]


def _severity_max_reduce_risk_level(values: list[str], order: tuple[str, ...]) -> str:
    return functools.reduce(
        lambda a, b: severity_max(a, b, order=order), values, "none"
    )


def _severity_max_reduce_enforcement_type(
    values: list[str | None], order: tuple[str, ...]
) -> str | None:
    return functools.reduce(
        lambda a, b: severity_max(a, b, order=order), values, None
    )


_RISK_LEVEL_MODES: dict[str, _RiskLevelReducer] = {
    "severity_max": _severity_max_reduce_risk_level,
}

_ENFORCEMENT_TYPE_MODES: dict[str, _EnforcementTypeReducer] = {
    "severity_max": _severity_max_reduce_enforcement_type,
}


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
    *,
    risk_level_mode: str = config.TRACE_AGGREGATION_RISK_LEVEL_MODE,
    enforcement_type_mode: str = config.TRACE_AGGREGATION_ENFORCEMENT_TYPE_MODE,
) -> TraceRiskAggregate:
    """FR-DAS-021: roll up a trace's current interaction risk records.

    ``trace_risk_level``/``trace_enforcement_type`` are computed by
    *risk_level_mode*/*enforcement_type_mode* (default: the
    ``RISK_TRACE_AGGREGATION_RISK_LEVEL_MODE``/
    ``RISK_TRACE_AGGREGATION_ENFORCEMENT_TYPE_MODE`` config values). Today
    the only implemented mode for either param is ``"severity_max"`` — the
    highest/strictest value across *records*, via :func:`severity_max`, so an
    unranked or missing value never raises. An empty *records* list (a trace
    with no interaction risk yet) rolls up to ``risk_level="none"``/no
    enforcement — the least-severe value, not an error.

    Raises :class:`ValueError` if either mode name is not recognized.
    """
    try:
        risk_level_reducer = _RISK_LEVEL_MODES[risk_level_mode]
    except KeyError:
        raise ValueError(
            f"unknown trace risk_level aggregation mode: {risk_level_mode!r} "
            f"(known modes: {sorted(_RISK_LEVEL_MODES)})"
        ) from None
    try:
        enforcement_type_reducer = _ENFORCEMENT_TYPE_MODES[enforcement_type_mode]
    except KeyError:
        raise ValueError(
            f"unknown trace enforcement_type aggregation mode: "
            f"{enforcement_type_mode!r} (known modes: {sorted(_ENFORCEMENT_TYPE_MODES)})"
        ) from None

    interaction_count = len(records)
    trace_risk_level = risk_level_reducer(
        [r.risk_level for r in records], RISK_LEVEL_ORDER
    )
    trace_enforcement_type = enforcement_type_reducer(
        [r.enforcement_type for r in records], ENFORCEMENT_ORDER
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
