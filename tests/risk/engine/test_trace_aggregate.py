"""Tests for the pure trace-risk aggregation functions (issue #102).

``data_governance.risk.engine.trace_aggregate`` holds the FR-DAS-021
aggregation semantics as plain functions over plain dataclasses — no
Postgres. Mirrors ``tests/risk/engine/test_utils.py``'s style for #101's
interaction-risk aggregation functions: severity-max reuse (via
``utils.severity_max``/``catalog.RISK_LEVEL_ORDER``/``ENFORCEMENT_ORDER``),
plus the trace-specific rollups (entity id union, policy event sum,
contributing ids, confidence averaging).
"""

from __future__ import annotations

from decimal import Decimal

from data_governance.risk.engine import trace_aggregate
from data_governance.risk.engine.trace_aggregate import CurrentInteractionRisk


def _record(**overrides) -> CurrentInteractionRisk:
    defaults = dict(
        interaction_risk_id="00000000-0000-0000-0000-000000000001",
        risk_level="low",
        enforcement_type=None,
        policy_event_count=1,
        triggered_rule_ids=[],
        caller_entity_id="ent-a",
        callee_entity_id="ent-b",
        overall_confidence=None,
    )
    defaults.update(overrides)
    return CurrentInteractionRisk(**defaults)


# --- aggregate_trace_risk: risk_level / enforcement --------------------------


def test_single_record_trace_risk_level_matches_its_risk_level():
    result = trace_aggregate.aggregate_trace_risk([_record(risk_level="medium")])
    assert result.trace_risk_level == "medium"


def test_trace_risk_level_is_highest_across_records():
    records = [
        _record(risk_level="low"),
        _record(risk_level="critical"),
        _record(risk_level="medium"),
    ]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.trace_risk_level == "critical"


def test_trace_risk_level_order_independent():
    a = trace_aggregate.aggregate_trace_risk(
        [_record(risk_level="high"), _record(risk_level="low")]
    )
    b = trace_aggregate.aggregate_trace_risk(
        [_record(risk_level="low"), _record(risk_level="high")]
    )
    assert a.trace_risk_level == b.trace_risk_level == "high"


def test_trace_enforcement_type_is_strictest_across_records():
    records = [
        _record(enforcement_type="warn"),
        _record(enforcement_type="block"),
        _record(enforcement_type=None),
    ]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.trace_enforcement_type == "block"


def test_trace_enforcement_type_none_when_all_none():
    records = [_record(enforcement_type=None), _record(enforcement_type=None)]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.trace_enforcement_type is None


def test_dg_008_dual_severity_scenario_reports_highest_across_trace():
    """AC-DAS-011 / §9.3: DG-008 fires at high/escalate on one interaction and
    critical/block on another within the same trace — the trace risk record
    must report critical/block (highest across the trace)."""
    records = [
        _record(
            risk_level="high",
            enforcement_type="escalate",
            triggered_rule_ids=["DG-008"],
        ),
        _record(
            risk_level="critical",
            enforcement_type="block",
            triggered_rule_ids=["DG-008"],
        ),
    ]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.trace_risk_level == "critical"
    assert result.trace_enforcement_type == "block"


def test_unranked_risk_level_never_raises_and_sorts_last():
    records = [_record(risk_level="low"), _record(risk_level="totally-unknown")]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.trace_risk_level == "low"


# --- aggregate_trace_risk: mode selection --------------------------------------


def test_explicit_severity_max_risk_level_mode_matches_default():
    records = [_record(risk_level="low"), _record(risk_level="critical")]
    result = trace_aggregate.aggregate_trace_risk(
        records, risk_level_mode="severity_max"
    )
    assert result.trace_risk_level == "critical"


def test_explicit_severity_max_enforcement_type_mode_matches_default():
    records = [_record(enforcement_type="warn"), _record(enforcement_type="block")]
    result = trace_aggregate.aggregate_trace_risk(
        records, enforcement_type_mode="severity_max"
    )
    assert result.trace_enforcement_type == "block"


def test_unknown_risk_level_mode_raises_value_error():
    try:
        trace_aggregate.aggregate_trace_risk([_record()], risk_level_mode="bogus")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "bogus" in str(exc)
        assert "severity_max" in str(exc)


def test_unknown_enforcement_type_mode_raises_value_error():
    try:
        trace_aggregate.aggregate_trace_risk(
            [_record()], enforcement_type_mode="bogus"
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "bogus" in str(exc)
        assert "severity_max" in str(exc)


# --- interaction_count / policy_event_count -----------------------------------


def test_interaction_count_is_number_of_records():
    records = [_record(), _record(), _record()]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.interaction_count == 3


def test_policy_event_count_is_sum_across_records():
    records = [
        _record(policy_event_count=2),
        _record(policy_event_count=5),
        _record(policy_event_count=1),
    ]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.policy_event_count == 8


def test_policy_event_count_zero_when_no_records():
    result = trace_aggregate.aggregate_trace_risk([])
    assert result.policy_event_count == 0
    assert result.interaction_count == 0


# --- all_entity_ids ------------------------------------------------------------


def test_all_entity_ids_is_union_of_caller_and_callee_deduplicated_and_sorted():
    records = [
        _record(caller_entity_id="ent-b", callee_entity_id="ent-a"),
        _record(caller_entity_id="ent-a", callee_entity_id="ent-c"),
    ]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.all_entity_ids == ["ent-a", "ent-b", "ent-c"]


def test_all_entity_ids_omits_none_entity_id():
    records = [_record(caller_entity_id=None, callee_entity_id="ent-a")]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.all_entity_ids == ["ent-a"]


def test_all_entity_ids_empty_when_no_records():
    result = trace_aggregate.aggregate_trace_risk([])
    assert result.all_entity_ids == []


# --- triggered_rule_ids --------------------------------------------------------


def test_triggered_rule_ids_is_union_deduplicated_and_sorted():
    records = [
        _record(triggered_rule_ids=["DG-002", "DG-001"]),
        _record(triggered_rule_ids=["DG-001", "DG-003"]),
    ]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.triggered_rule_ids == ["DG-001", "DG-002", "DG-003"]


def test_triggered_rule_ids_empty_when_no_records_trigger_any():
    records = [_record(triggered_rule_ids=[]), _record(triggered_rule_ids=[])]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.triggered_rule_ids == []


# --- contributing_interaction_risk_ids ----------------------------------------


def test_contributing_interaction_risk_ids_matches_input_record_ids():
    records = [
        _record(interaction_risk_id="id-1"),
        _record(interaction_risk_id="id-2"),
    ]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.contributing_interaction_risk_ids == ["id-1", "id-2"]


def test_contributing_interaction_risk_ids_empty_when_no_records():
    result = trace_aggregate.aggregate_trace_risk([])
    assert result.contributing_interaction_risk_ids == []


def test_contributing_interaction_risk_ids_preserves_input_order_not_sorted():
    """Unlike entity/rule ids (identity sets), contributing ids are a
    positional record of which record versions fed this computation — order
    is whatever the caller supplied, not resorted."""
    records = [
        _record(interaction_risk_id="id-9"),
        _record(interaction_risk_id="id-1"),
    ]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.contributing_interaction_risk_ids == ["id-9", "id-1"]


# --- overall_confidence --------------------------------------------------------


def test_overall_confidence_is_average_of_present_confidences():
    records = [
        _record(overall_confidence=Decimal("0.800")),
        _record(overall_confidence=Decimal("0.600")),
    ]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.overall_confidence == Decimal("0.700")


def test_overall_confidence_quantized_to_three_decimal_places_half_up():
    records = [
        _record(overall_confidence=Decimal("0.801")),
        _record(overall_confidence=Decimal("0.600")),
    ]
    result = trace_aggregate.aggregate_trace_risk(records)
    # (0.801 + 0.600) / 2 = 0.7005 -> rounds half-up to 0.701
    assert result.overall_confidence == Decimal("0.701")


def test_overall_confidence_none_when_no_record_has_a_confidence():
    records = [_record(overall_confidence=None), _record(overall_confidence=None)]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.overall_confidence is None


def test_overall_confidence_ignores_records_with_no_confidence():
    """A record with a None confidence (OPA omitted one) is excluded from the
    average rather than treated as 0 — a missing signal must not silently
    drag the aggregate down."""
    records = [
        _record(overall_confidence=Decimal("0.900")),
        _record(overall_confidence=None),
    ]
    result = trace_aggregate.aggregate_trace_risk(records)
    assert result.overall_confidence == Decimal("0.900")


def test_overall_confidence_none_when_no_records_at_all():
    result = trace_aggregate.aggregate_trace_risk([])
    assert result.overall_confidence is None
