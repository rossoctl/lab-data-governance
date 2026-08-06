"""Tests for the pure interaction-risk aggregation functions (issue #101).

``data_governance.risk.engine.aggregate`` holds the risk semantics as plain
functions over plain dataclasses — no Postgres, no HTTP. Everything here is
in-process and deterministic: severity-max ranking (reusing
``catalog.RISK_LEVEL_ORDER``/``ENFORCEMENT_ORDER``), ``legs_evidenced``
derivation, the ``classification_summary`` JSONB shape, confidence
quantization to ``NUMERIC(4,3)``, and the idempotency fingerprint.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from data_governance.processors.classification.verdict import Verdict
from data_governance.risk.engine import aggregate
from data_governance.risk.engine.aggregate import LegEvidence, PolicyDecision


def _leg(leg_type: str, *, payload_hash: str | None = "h1") -> LegEvidence:
    return LegEvidence(leg_type=leg_type, payload_hash=payload_hash)


def _verdict(**overrides) -> Verdict:
    defaults = dict(
        sensitivity_level="RESTRICTED",
        regulatory_tags=["PII"],
        contains_identity_bundle=False,
        is_personalized=False,
        primary_domain="finance",
        findings=[{"entity_type": "SSN"}, {"entity_type": "NAME"}],
        model_version=1,
    )
    defaults.update(overrides)
    return Verdict(**defaults)


def _decision(**overrides) -> PolicyDecision:
    defaults = dict(
        risk_level="low",
        enforcement_type=None,
        allowed_actions=[],
        explanation=None,
        triggered_rules=[],
        confidence=None,
        policy_version=None,
    )
    defaults.update(overrides)
    return PolicyDecision(**defaults)


# --- severity_max --------------------------------------------------------------


def test_severity_max_picks_more_severe_of_two_risk_levels():
    assert aggregate.severity_max("high", "low", order=aggregate.RISK_LEVEL_ORDER) == "high"
    assert aggregate.severity_max("low", "critical", order=aggregate.RISK_LEVEL_ORDER) == "critical"


def test_severity_max_is_order_independent():
    a = aggregate.severity_max("medium", "high", order=aggregate.RISK_LEVEL_ORDER)
    b = aggregate.severity_max("high", "medium", order=aggregate.RISK_LEVEL_ORDER)
    assert a == b == "high"


def test_severity_max_equal_values_returns_that_value():
    assert aggregate.severity_max("high", "high", order=aggregate.RISK_LEVEL_ORDER) == "high"


def test_severity_max_unranked_value_sorts_after_every_ranked_value():
    """A value absent from the order tuple (e.g. a novel OPA-sourced string)
    must never raise — it sorts as least severe, per the catalog's existing
    ``ranks.get(value, len(order))`` convention."""
    assert (
        aggregate.severity_max("low", "totally-unknown", order=aggregate.RISK_LEVEL_ORDER)
        == "low"
    )
    assert (
        aggregate.severity_max("totally-unknown", "totally-unknown", order=aggregate.RISK_LEVEL_ORDER)
        == "totally-unknown"
    )


def test_severity_max_none_is_treated_as_unranked():
    assert aggregate.severity_max("low", None, order=aggregate.RISK_LEVEL_ORDER) == "low"
    assert aggregate.severity_max(None, None, order=aggregate.RISK_LEVEL_ORDER) is None


def test_severity_max_works_for_enforcement_order_too():
    assert (
        aggregate.severity_max("warn", "block", order=aggregate.ENFORCEMENT_ORDER) == "block"
    )


# --- legs_evidenced --------------------------------------------------------------


def test_legs_evidenced_empty_when_no_legs():
    assert aggregate.legs_evidenced([]) == []


def test_legs_evidenced_request_only():
    assert aggregate.legs_evidenced([_leg("request")]) == ["request"]


def test_legs_evidenced_response_only():
    assert aggregate.legs_evidenced([_leg("response")]) == ["response"]


def test_legs_evidenced_both_in_request_then_response_order_regardless_of_input_order():
    assert aggregate.legs_evidenced([_leg("response"), _leg("request")]) == [
        "request",
        "response",
    ]


# --- classification_summary -----------------------------------------------------


def test_classification_summary_empty_when_no_legs():
    assert aggregate.classification_summary({}) == {}


def test_classification_summary_classified_leg_has_full_verdict_fields():
    summary = aggregate.classification_summary({"request": _verdict()})
    assert summary == {
        "request": {
            "sensitivity_level": "RESTRICTED",
            "regulatory_tags": ["PII"],
            "contains_identity_bundle": False,
            "is_personalized": False,
            "primary_domain": "finance",
            "finding_count": 2,
            "model_version": 1,
        }
    }


def test_classification_summary_leg_with_payload_but_no_classification_yet_is_pending():
    """A leg that has a payload_hash but no matching payload_classifications
    row yet reads as pending, with no verdict fields — never defaulted to
    'none' (explicit issue requirement)."""
    summary = aggregate.classification_summary({"request": aggregate.PENDING})
    assert summary == {"request": {"classification_pending": True}}


def test_classification_summary_leg_with_no_payload_is_null_payload():
    summary = aggregate.classification_summary({"response": aggregate.NO_PAYLOAD})
    assert summary == {"response": {"payload": None}}


def test_classification_summary_missing_leg_key_is_absent_entirely():
    """A leg that doesn't exist for this interaction must not appear as a key
    at all — not null, not pending, just absent."""
    summary = aggregate.classification_summary({"request": _verdict()})
    assert "response" not in summary


def test_classification_summary_both_legs_mixed_states():
    summary = aggregate.classification_summary(
        {"request": _verdict(sensitivity_level="PUBLIC", findings=[]), "response": aggregate.PENDING}
    )
    assert summary["request"]["finding_count"] == 0
    assert summary["request"]["sensitivity_level"] == "PUBLIC"
    assert summary["response"] == {"classification_pending": True}


# --- quantize_confidence ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, Decimal("0.000")),
        (1, Decimal("1.000")),
        (0.0005, Decimal("0.001")),  # ROUND_HALF_UP
        (0.9995, Decimal("1.000")),  # ROUND_HALF_UP
        (0.5, Decimal("0.500")),
    ],
)
def test_quantize_confidence_rounds_half_up_to_three_places(raw, expected):
    assert aggregate.quantize_confidence(raw) == expected


def test_quantize_confidence_none_stays_none():
    assert aggregate.quantize_confidence(None) is None


@pytest.mark.parametrize("raw", [-0.001, 1.001, -1, 2])
def test_quantize_confidence_rejects_out_of_range(raw):
    with pytest.raises(ValueError):
        aggregate.quantize_confidence(raw)


def test_quantize_confidence_accepts_boundary_values():
    assert aggregate.quantize_confidence(0.0) == Decimal("0.000")
    assert aggregate.quantize_confidence(1.0) == Decimal("1.000")


# --- fingerprint -------------------------------------------------------------------


def test_fingerprint_is_stable_for_identical_evidence():
    legs = [_leg("request"), _leg("response")]
    classifications = {"request": _verdict()}
    decision = _decision(risk_level="high", triggered_rules=["r1", "r2"])
    fp1 = aggregate.fingerprint(legs, classifications, decision)
    fp2 = aggregate.fingerprint(legs, classifications, decision)
    assert fp1 == fp2


def test_fingerprint_is_stable_under_triggered_rules_reordering():
    legs = [_leg("request")]
    classifications = {}
    d1 = _decision(triggered_rules=["r1", "r2"])
    d2 = _decision(triggered_rules=["r2", "r1"])
    assert aggregate.fingerprint(legs, classifications, d1) == aggregate.fingerprint(
        legs, classifications, d2
    )


def test_fingerprint_changes_when_risk_level_changes():
    legs = [_leg("request")]
    classifications = {}
    d1 = _decision(risk_level="low")
    d2 = _decision(risk_level="high")
    assert aggregate.fingerprint(legs, classifications, d1) != aggregate.fingerprint(
        legs, classifications, d2
    )


def test_fingerprint_changes_when_legs_evidenced_changes():
    classifications = {}
    decision = _decision()
    fp_request_only = aggregate.fingerprint([_leg("request")], classifications, decision)
    fp_both = aggregate.fingerprint(
        [_leg("request"), _leg("response")], classifications, decision
    )
    assert fp_request_only != fp_both


def test_fingerprint_changes_when_classification_summary_changes():
    legs = [_leg("request")]
    decision = _decision()
    fp1 = aggregate.fingerprint(legs, {"request": _verdict()}, decision)
    fp2 = aggregate.fingerprint(
        legs, {"request": _verdict(sensitivity_level="PUBLIC")}, decision
    )
    assert fp1 != fp2


def test_fingerprint_returns_a_string():
    assert isinstance(aggregate.fingerprint([], {}, _decision()), str)
