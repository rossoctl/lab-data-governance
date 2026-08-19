"""Tests for the pure interaction-risk aggregation functions (issue #101).

``data_governance.risk.engine.utils`` holds the risk semantics as plain
functions over plain dataclasses — no Postgres, no HTTP. Everything here is
in-process and deterministic: severity-max ranking (reusing
``catalog.RISK_LEVEL_ORDER``/``ENFORCEMENT_ORDER``), ``legs_evidenced``
derivation, the ``classification_summary`` JSONB shape, confidence
quantization to ``NUMERIC(4,3)``, and the idempotency fingerprint.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import jsonschema
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from data_governance.processors.classification.verdict import Verdict
from data_governance.risk.engine import utils
from data_governance.risk.engine.utils import LegEvidence
from data_governance.risk.rules import catalog

_SCHEMA_DIR = Path(catalog.__file__).parent / "schema"


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _opa_input_validator() -> Draft202012Validator:
    policy_schema = _load_json(_SCHEMA_DIR / "policy.schema.json")
    opa_input_schema = _load_json(_SCHEMA_DIR / "opa_input.schema.json")
    registry = Registry().with_resource(
        "policy.schema.json", Resource.from_contents(policy_schema)
    )
    return Draft202012Validator(opa_input_schema, registry=registry)


def _leg(leg_type: str, *, payload_hash: str | None = "h1") -> LegEvidence:
    return LegEvidence(leg_type=leg_type, payload_hash=payload_hash)


def _finding(**overrides) -> dict:
    defaults = dict(
        entity_type="SSN",
        start=0,
        end=11,
        text="123-45-6789",
        domain="finance",
        category="finance.data",
        regulatory_tags=["PII"],
        identifier_type="PID",
        sensitivity_level="RESTRICTED",
        is_personalized=False,
    )
    defaults.update(overrides)
    return defaults


def _verdict(**overrides) -> Verdict:
    defaults = dict(
        sensitivity_level="RESTRICTED",
        regulatory_tags=["PII"],
        contains_identity_bundle=False,
        is_personalized=False,
        primary_domain="finance",
        findings=[_finding(), _finding(entity_type="PN", identifier_type="NON_ID")],
        model_version=1,
    )
    defaults.update(overrides)
    return Verdict(**defaults)


# --- severity_max --------------------------------------------------------------


def test_severity_max_picks_more_severe_of_two_risk_levels():
    assert utils.severity_max("high", "low", order=utils.RISK_LEVEL_ORDER) == "high"
    assert utils.severity_max("low", "critical", order=utils.RISK_LEVEL_ORDER) == "critical"


def test_severity_max_is_order_independent():
    a = utils.severity_max("medium", "high", order=utils.RISK_LEVEL_ORDER)
    b = utils.severity_max("high", "medium", order=utils.RISK_LEVEL_ORDER)
    assert a == b == "high"


def test_severity_max_equal_values_returns_that_value():
    assert utils.severity_max("high", "high", order=utils.RISK_LEVEL_ORDER) == "high"


def test_severity_max_unranked_value_sorts_after_every_ranked_value():
    """A value absent from the order tuple (e.g. a novel OPA-sourced string)
    must never raise — it sorts as least severe, per the catalog's existing
    ``ranks.get(value, len(order))`` convention."""
    assert (
        utils.severity_max("low", "totally-unknown", order=utils.RISK_LEVEL_ORDER)
        == "low"
    )
    assert (
        utils.severity_max("totally-unknown", "totally-unknown", order=utils.RISK_LEVEL_ORDER)
        == "totally-unknown"
    )


def test_severity_max_none_is_treated_as_unranked():
    assert utils.severity_max("low", None, order=utils.RISK_LEVEL_ORDER) == "low"
    assert utils.severity_max(None, None, order=utils.RISK_LEVEL_ORDER) is None


def test_severity_max_works_for_enforcement_order_too():
    assert (
        utils.severity_max("warn", "block", order=utils.ENFORCEMENT_ORDER) == "block"
    )


# --- legs_evidenced --------------------------------------------------------------


def test_legs_evidenced_empty_when_no_legs():
    assert utils.legs_evidenced([]) == []


def test_legs_evidenced_request_only():
    assert utils.legs_evidenced([_leg("request")]) == ["request"]


def test_legs_evidenced_response_only():
    assert utils.legs_evidenced([_leg("response")]) == ["response"]


def test_legs_evidenced_both_in_request_then_response_order_regardless_of_input_order():
    assert utils.legs_evidenced([_leg("response"), _leg("request")]) == [
        "request",
        "response",
    ]


# --- classification_summary -----------------------------------------------------


def test_classification_summary_empty_when_no_legs():
    assert utils.classification_summary({}) == {}


def test_classification_summary_classified_leg_has_full_verdict_fields():
    summary = utils.classification_summary({"request": _verdict()})
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
    summary = utils.classification_summary({"request": utils.PENDING})
    assert summary == {"request": {"classification_pending": True}}


def test_classification_summary_leg_with_no_payload_is_null_payload():
    summary = utils.classification_summary({"response": utils.NO_PAYLOAD})
    assert summary == {"response": {"payload": None}}


def test_classification_summary_missing_leg_key_is_absent_entirely():
    """A leg that doesn't exist for this interaction must not appear as a key
    at all — not null, not pending, just absent."""
    summary = utils.classification_summary({"request": _verdict()})
    assert "response" not in summary


def test_classification_summary_both_legs_mixed_states():
    summary = utils.classification_summary(
        {"request": _verdict(sensitivity_level="PUBLIC", findings=[]), "response": utils.PENDING}
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
    assert utils.quantize_confidence(raw) == expected


def test_quantize_confidence_none_stays_none():
    assert utils.quantize_confidence(None) is None


@pytest.mark.parametrize("raw", [-0.001, 1.001, -1, 2])
def test_quantize_confidence_rejects_out_of_range(raw):
    with pytest.raises(ValueError):
        utils.quantize_confidence(raw)


def test_quantize_confidence_accepts_boundary_values():
    assert utils.quantize_confidence(0.0) == Decimal("0.000")
    assert utils.quantize_confidence(1.0) == Decimal("1.000")


# --- fingerprint -------------------------------------------------------------------


def test_fingerprint_is_stable_for_identical_evidence():
    legs = [_leg("request"), _leg("response")]
    classifications = {"request": _verdict()}
    fp1 = utils.fingerprint(legs, classifications)
    fp2 = utils.fingerprint(legs, classifications)
    assert fp1 == fp2


def test_fingerprint_changes_when_legs_evidenced_changes():
    classifications = {}
    fp_request_only = utils.fingerprint([_leg("request")], classifications)
    fp_both = utils.fingerprint([_leg("request"), _leg("response")], classifications)
    assert fp_request_only != fp_both


def test_fingerprint_changes_when_classification_summary_changes():
    legs = [_leg("request")]
    fp1 = utils.fingerprint(legs, {"request": _verdict()})
    fp2 = utils.fingerprint(legs, {"request": _verdict(sensitivity_level="PUBLIC")})
    assert fp1 != fp2


def test_fingerprint_returns_a_string():
    assert isinstance(utils.fingerprint([], {}), str)


# --- build_opa_input ----------------------------------------------------------------
#
# Maps DAS's own evidence shapes onto ``opa_input.schema.json`` (which shares
# ``policy.schema.json``'s $defs so the two schemas stay in sync). Every case
# below validates the produced dict against the real vendored schema rather
# than asserting field-by-field only, so a mapping that "looks right" but
# violates an enum/additionalProperties rule fails here rather than only at
# a real OPA call.


def test_build_opa_input_validates_against_the_schema():
    legs = [_leg("request"), _leg("response", payload_hash="h2")]
    classifications = {"request": _verdict(), "response": utils.PENDING}
    payload = utils.build_opa_input(
        legs=legs,
        span_ids=["s1", "s2"],
        classifications=classifications,
        caller_entity_id="agent:a",
        callee_entity_id="agent:b",
    )
    _opa_input_validator().validate(payload)


def test_build_opa_input_with_no_evidence_validates_against_the_schema():
    payload = utils.build_opa_input(
        legs=[],
        span_ids=[],
        classifications={},
        caller_entity_id=None,
        callee_entity_id=None,
    )
    _opa_input_validator().validate(payload)


def test_build_opa_input_rejects_stray_top_level_keys():
    """Guards ``additionalProperties: false`` actually being enforced — proves
    a validator that silently accepted anything wouldn't make the positive
    cases above meaningful."""
    payload = utils.build_opa_input(
        legs=[], span_ids=[], classifications={}, caller_entity_id=None, callee_entity_id=None
    )
    payload["not_a_real_field"] = "x"
    with pytest.raises(jsonschema.ValidationError):
        _opa_input_validator().validate(payload)


def test_build_opa_input_data_items_carry_one_entity_per_finding():
    legs = [_leg("request")]
    classifications = {"request": _verdict()}
    payload = utils.build_opa_input(
        legs=legs,
        span_ids=[],
        classifications=classifications,
        caller_entity_id=None,
        callee_entity_id=None,
    )
    assert len(payload["data_items"]) == 1
    entities = payload["data_items"][0]["entities"]
    assert [e["entity_type"] for e in entities] == ["SSN", "PN"]
    assert entities[0]["start"] == 0
    assert entities[0]["end"] == 11
    assert entities[0]["text"] == "123-45-6789"
    assert entities[0]["domain"] == "finance"
    assert entities[0]["category"] == "finance.data"
    assert entities[0]["regulatory_tags"] == ["PII"]


def test_build_opa_input_non_id_identifier_type_omits_data_type():
    """DAS's classifier emits ``identifier_type: "NON_ID"`` for an unrecognized
    tag (logic.py's conservative fallback), but the schema's ``dataTypeValues``
    enum only has PID/OPID/DATA — no member for NON_ID. Sending an invalid
    enum value would fail schema validation, so it must be omitted rather than
    passed through or defaulted."""
    legs = [_leg("request")]
    classifications = {
        "request": _verdict(findings=[_finding(identifier_type="NON_ID")])
    }
    payload = utils.build_opa_input(
        legs=legs,
        span_ids=[],
        classifications=classifications,
        caller_entity_id=None,
        callee_entity_id=None,
    )
    entity = payload["data_items"][0]["entities"][0]
    assert "data_type" not in entity
    _opa_input_validator().validate(payload)


def test_build_opa_input_data_item_carries_the_leg_verdict_summary():
    legs = [_leg("request")]
    classifications = {"request": _verdict()}
    payload = utils.build_opa_input(
        legs=legs,
        span_ids=[],
        classifications=classifications,
        caller_entity_id=None,
        callee_entity_id=None,
    )
    item = payload["data_items"][0]
    assert item["classification_level"] == "RESTRICTED"
    assert item["primary_domain"] == "finance"
    assert item["regulatory_tags"] == ["PII"]


def test_build_opa_input_identity_bundle_leg_lists_a_bundle_name():
    legs = [_leg("request")]
    classifications = {
        "request": _verdict(contains_identity_bundle=True, sensitivity_level="RESTRICTED")
    }
    payload = utils.build_opa_input(
        legs=legs,
        span_ids=[],
        classifications=classifications,
        caller_entity_id=None,
        callee_entity_id=None,
    )
    assert payload["data_items"][0]["identity_bundles"] == ["identity_bundle"]


def test_build_opa_input_pending_leg_produces_no_data_item():
    legs = [_leg("request")]
    classifications = {"request": utils.PENDING}
    payload = utils.build_opa_input(
        legs=legs,
        span_ids=[],
        classifications=classifications,
        caller_entity_id=None,
        callee_entity_id=None,
    )
    assert payload["data_items"] == []


def test_build_opa_input_no_payload_leg_produces_no_data_item():
    legs = [_leg("response", payload_hash=None)]
    classifications = {"response": utils.NO_PAYLOAD}
    payload = utils.build_opa_input(
        legs=legs,
        span_ids=[],
        classifications=classifications,
        caller_entity_id=None,
        callee_entity_id=None,
    )
    assert payload["data_items"] == []


def test_build_opa_input_requested_actions_reflect_legs_evidenced():
    legs = [_leg("response", payload_hash=None), _leg("request")]
    classifications = {"request": utils.PENDING, "response": utils.NO_PAYLOAD}
    payload = utils.build_opa_input(
        legs=legs,
        span_ids=[],
        classifications=classifications,
        caller_entity_id=None,
        callee_entity_id=None,
    )
    assert payload["requested_actions"] == ["send", "receive"]


def test_build_opa_input_no_legs_means_no_requested_actions():
    payload = utils.build_opa_input(
        legs=[], span_ids=[], classifications={}, caller_entity_id=None, callee_entity_id=None
    )
    assert payload["requested_actions"] == []


def test_build_opa_input_processing_agents_from_caller_and_callee():
    payload = utils.build_opa_input(
        legs=[],
        span_ids=[],
        classifications={},
        caller_entity_id="agent:a",
        callee_entity_id="agent:b",
    )
    assert payload["processing_agents"] == [
        {"agent_name": "agent:a"},
        {"agent_name": "agent:b"},
    ]


def test_build_opa_input_omits_processing_agents_entirely_when_both_entity_ids_are_none():
    """Neither entity id is known — no ``processing_agents`` key at all, not
    an empty list, matching the rest of this module's "absent means absent"
    convention (e.g. classification_summary's missing-leg behaviour)."""
    payload = utils.build_opa_input(
        legs=[], span_ids=[], classifications={}, caller_entity_id=None, callee_entity_id=None
    )
    assert "processing_agents" not in payload


def test_build_opa_input_includes_only_the_known_entity_id_when_one_is_none():
    payload = utils.build_opa_input(
        legs=[],
        span_ids=[],
        classifications={},
        caller_entity_id="agent:a",
        callee_entity_id=None,
    )
    assert payload["processing_agents"] == [{"agent_name": "agent:a"}]


def test_build_opa_input_never_carries_unmapped_fields():
    """No DAS source data feeds event_type/data_sources/data_destinations/
    data_lineage/scope/the intent strings/accessing_user yet — they must be
    absent rather than null, so a partially-known payload never reads as a
    confident (but wrong) empty declaration to a policy author."""
    payload = utils.build_opa_input(
        legs=[_leg("request")],
        span_ids=["s1"],
        classifications={"request": _verdict()},
        caller_entity_id="agent:a",
        callee_entity_id="agent:b",
    )
    for absent_key in (
        "event_type",
        "data_sources",
        "data_count",
        "data_destinations",
        "data_lineage",
        "scope",
        "declared_business_intent",
        "observed_business_intent",
        "observed_business_intent_description",
        "declared_operational_intent",
        "observed_operational_intent",
        "accessing_user",
    ):
        assert absent_key not in payload


# --- destination URL whitelist classification (issue #163) ------------------
#
# MVP-only: wildcard hostname matching against a configured whitelist, no URL
# parsing beyond a plain hostname/wildcard comparison. See
# ``matches_internal_whitelist``'s and ``build_opa_input``'s docstrings for
# the caveat — this will be treated more holistically in a future version.


def test_matches_internal_whitelist_exact_hostname_match():
    assert utils.matches_internal_whitelist(
        "https://svc.corp.internal/path", patterns=["svc.corp.internal"]
    )


def test_matches_internal_whitelist_wildcard_subdomain_match():
    assert utils.matches_internal_whitelist(
        "https://foo.corp.internal", patterns=["*.corp.internal"]
    )
    assert utils.matches_internal_whitelist(
        "https://bar.baz.corp.internal", patterns=["*.corp.internal"]
    )


def test_matches_internal_whitelist_no_match_returns_false():
    assert not utils.matches_internal_whitelist(
        "https://evil.example.com", patterns=["*.corp.internal"]
    )


def test_matches_internal_whitelist_empty_patterns_always_false():
    assert not utils.matches_internal_whitelist(
        "https://svc.corp.internal", patterns=[]
    )


def test_matches_internal_whitelist_checks_every_pattern():
    assert utils.matches_internal_whitelist(
        "https://svc.other.internal",
        patterns=["*.corp.internal", "*.other.internal"],
    )


def test_matches_internal_whitelist_wildcard_does_not_match_bare_domain():
    """``*.corp.internal`` must not match ``corp.internal`` itself — the
    wildcard stands for exactly one or more subdomain labels, not zero."""
    assert not utils.matches_internal_whitelist(
        "https://corp.internal", patterns=["*.corp.internal"]
    )


def test_matches_internal_whitelist_url_without_scheme():
    assert utils.matches_internal_whitelist(
        "svc.corp.internal/path", patterns=["*.corp.internal"]
    )


def test_matches_internal_whitelist_is_case_insensitive():
    assert utils.matches_internal_whitelist(
        "https://SVC.CORP.INTERNAL", patterns=["*.corp.internal"]
    )


def test_matches_internal_whitelist_rejects_attacker_controlled_suffix():
    """``fnmatch`` anchors the full hostname — a pattern must match end to
    end, so a hostname that merely *contains* the whitelisted suffix
    somewhere in the middle (an attacker-registered domain like
    ``evil.svc.corp.internal.attacker.com``) must not match."""
    assert not utils.matches_internal_whitelist(
        "https://evil.svc.corp.internal.attacker.com", patterns=["*.corp.internal"]
    )


# --- build_opa_input: destination_url classification -------------------------


def test_build_opa_input_destination_url_matching_whitelist_is_internal(monkeypatch):
    monkeypatch.setattr(
        "data_governance.risk.engine.utils.INTERNAL_URL_WHITELIST_PATTERNS",
        ["*.corp.internal"],
    )
    payload = utils.build_opa_input(
        legs=[],
        span_ids=[],
        classifications={},
        caller_entity_id=None,
        callee_entity_id=None,
        destination_url="https://svc.corp.internal/x",
    )
    assert payload["data_destinations"] == [{"data_destination_categories": ["internal"]}]
    _opa_input_validator().validate(payload)


def test_build_opa_input_destination_url_not_matching_whitelist_is_external(monkeypatch):
    monkeypatch.setattr(
        "data_governance.risk.engine.utils.INTERNAL_URL_WHITELIST_PATTERNS",
        ["*.corp.internal"],
    )
    payload = utils.build_opa_input(
        legs=[],
        span_ids=[],
        classifications={},
        caller_entity_id=None,
        callee_entity_id=None,
        destination_url="https://evil.example.com",
    )
    assert payload["data_destinations"] == [{"data_destination_categories": ["external"]}]
    _opa_input_validator().validate(payload)


def test_build_opa_input_destination_url_with_empty_whitelist_defaults_external(
    monkeypatch,
):
    monkeypatch.setattr(
        "data_governance.risk.engine.utils.INTERNAL_URL_WHITELIST_PATTERNS", []
    )
    payload = utils.build_opa_input(
        legs=[],
        span_ids=[],
        classifications={},
        caller_entity_id=None,
        callee_entity_id=None,
        destination_url="https://svc.corp.internal",
    )
    assert payload["data_destinations"] == [{"data_destination_categories": ["external"]}]


def test_build_opa_input_no_destination_url_omits_data_destinations():
    """Matches this module's existing "absent means absent" convention: no
    destination URL known yet (the common case until issue #163's
    evidence-gathering wiring lands) must not fabricate a category."""
    payload = utils.build_opa_input(
        legs=[], span_ids=[], classifications={}, caller_entity_id=None, callee_entity_id=None
    )
    assert "data_destinations" not in payload
