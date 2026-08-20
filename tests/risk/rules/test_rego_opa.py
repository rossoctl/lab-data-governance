"""Real-OPA tests for the compiled Rego (issue #173).

Every test here PUTs a compiled policy into a real, containerized OPA
(``tests/risk/rules/conftest.py``'s session-scoped ``opa_base_url`` /
per-test ``opa_client`` fixtures) and POSTs to the actual decision path,
proving three things the structural tests (``test_rego.py``) and the
Python oracle (``test_combining.py``) cannot on their own:

1. The emitted Rego is syntactically valid — OPA's compiler accepts the PUT.
2. It evaluates to the values the Python oracle (:func:`combining.combine`)
   would produce for the same firing set — the actual point of compiling
   the combining logic into Rego rather than computing it in Python is that
   OPA remains the single decision authority, so this is the test that
   the two never diverge.
3. The response round-trips through
   :func:`data_governance.risk.engine.opa._parse_decision` unchanged —
   pinning the compiler -> OPA -> parser contract end to end.
4. The raw ``policy_decision`` value OPA returns validates against
   ``schema/opa_output.schema.json`` — the schema conformance tests at the
   bottom of this file check the actual OPA response, not the emitted Rego
   source or the Python oracle's dict shape, so a violation like an
   unexpected ``rule_id``/``rule_name`` key (which ``_parse_decision``
   would silently ignore) is caught here.

Deselected by default (``pytest.ini_options.addopts = "-ra -m 'not opa'"``);
run explicitly with ``-m opa`` (podman/docker must be up — see
``conftest.py``'s ``DOCKER_HOST`` handling).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from data_governance.risk.config import OPA_DECISION_PATH
from data_governance.risk.engine.opa import OpaClient
from data_governance.risk.rules import catalog
from data_governance.risk.rules.rego import compile_policy

pytestmark = pytest.mark.opa

_SCHEMA_DIR = Path(catalog.__file__).parent / "schema"

_DECISION = {
    "risk_level": "high",
    "enforcement_type": "block",
    "allowed_actions": [],
    "explanation": "test",
    "confidence": 0.9,
}


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _opa_output_validator() -> Draft202012Validator:
    policy_schema = _load_json(_SCHEMA_DIR / "policy.schema.json")
    opa_output_schema = _load_json(_SCHEMA_DIR / "opa_output.schema.json")
    registry = Registry().with_resource(
        "policy.schema.json", Resource.from_contents(policy_schema)
    )
    return Draft202012Validator(opa_output_schema, registry=registry)


def _policy(rules: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "runtime_enforcement_mode": {"unknown_behavior_enforcement_type": "allow"},
        "rule_combining_mode": "most_restrictive",
        "rules": rules,
    }
    policy.update(kwargs)
    return policy


def _rule(rule_id: str, /, **fields: Any) -> dict[str, Any]:
    rule = {"rule_id": rule_id, "rule_decision": dict(_DECISION)}
    rule.update(fields)
    return rule


def _put_policy(opa_client: httpx.Client, rego: str) -> httpx.Response:
    response = opa_client.put(
        "/v1/policies/data_governance",
        content=rego.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
    )
    return response


def _evaluate(opa_base_url: str, opa_input: dict[str, Any]):
    """Call OPA through the exact production client
    (:class:`data_governance.risk.engine.opa.OpaClient`) and return the
    parsed :class:`OpaDecision` — this is the contract-pinning step: if the
    compiled Rego ever emits a response shape ``_parse_decision`` cannot
    read, this call raises here rather than the structural tests missing
    it."""
    with httpx.Client(base_url=opa_base_url, timeout=5.0) as http_client:
        client = OpaClient(
            http_client=http_client, decision_path=OPA_DECISION_PATH, max_retries=0
        )
        return client.evaluate(interaction_id="test-interaction", opa_input=opa_input)


# --- PII / shipped-catalog-shaped scenario ------------------------------


def test_pii_to_untrusted_external_fires_the_block_rule(opa_client, opa_base_url):
    rego = compile_policy(catalog.load_rules_source())
    put_response = _put_policy(opa_client, rego)
    assert put_response.status_code == 200, put_response.text

    decision = _evaluate(
        opa_base_url,
        {
            "event_type": "external_sharing",
            "data_items": [{"regulatory_tags": ["PII"]}],
            "data_destinations": [
                {
                    "data_destination_categories": ["external"],
                    "data_destination_trust_level": "UNTRUSTED_EXTERNAL",
                }
            ],
        },
    )
    assert decision.risk_level == "critical"
    assert decision.enforcement_type == "block"
    assert decision.triggered_rules == ["DG-001"]


def test_no_matching_rule_falls_back_to_allow(opa_client, opa_base_url):
    rego = compile_policy(catalog.load_rules_source())
    put_response = _put_policy(opa_client, rego)
    assert put_response.status_code == 200, put_response.text

    decision = _evaluate(opa_base_url, {"event_type": "internal_view"})
    assert decision.risk_level == "none"
    assert decision.enforcement_type == "allow"
    assert decision.triggered_rules == ["0000"]


# --- combining mode: syntax + semantics on a controlled two-rule policy -


def _two_rule_policy() -> dict[str, Any]:
    """A hand-built two-rule policy where the *lower-severity* rule fires
    first in catalog order — the only way to distinguish first_fires from
    most_restrictive by outcome, since a real most-restrictive win would
    look identical to first-fires if the more severe rule happened to be
    listed first."""
    low = _rule(
        "LOW-1",
        event_type="x",
        rule_decision={
            "risk_level": "low",
            "enforcement_type": "warn",
            "explanation": "low rule",
            "confidence": 0.5,
        },
    )
    high = _rule(
        "HIGH-1",
        event_type="x",
        rule_decision={
            "risk_level": "critical",
            "enforcement_type": "block",
            "explanation": "high rule",
            "confidence": 0.9,
        },
    )
    return _policy([low, high])


def test_most_restrictive_picks_the_more_severe_of_two_firing_rules(
    opa_client, opa_base_url
):
    policy = _two_rule_policy()
    policy["rule_combining_mode"] = "most_restrictive"
    rego = compile_policy(policy, validate=False)
    put_response = _put_policy(opa_client, rego)
    assert put_response.status_code == 200, put_response.text

    decision = _evaluate(opa_base_url, {"event_type": "x"})
    assert decision.risk_level == "critical"
    assert decision.enforcement_type == "block"
    assert sorted(decision.triggered_rules) == ["HIGH-1", "LOW-1"]


def _crossed_axis_policy() -> dict[str, Any]:
    """One rule carries the worse risk_level, a different rule the worse
    enforcement_type — the case that actually distinguishes per-field
    most_restrictive from single-winner selection: with a composite-key
    ranking, one of the two axes would be silently discarded."""
    worse_risk = _rule(
        "WORSE-RISK-1",
        event_type="x",
        rule_decision={
            "risk_level": "critical",
            "enforcement_type": "warn",
            "allowed_actions": ["mask"],
            "explanation": "risk axis winner",
            "confidence": 0.4,
        },
    )
    worse_enforcement = _rule(
        "WORSE-ENF-1",
        event_type="x",
        rule_decision={
            "risk_level": "low",
            "enforcement_type": "block",
            "allowed_actions": ["redact"],
            "explanation": "enforcement axis winner",
            "confidence": 0.9,
        },
    )
    return _policy(
        [worse_risk, worse_enforcement], rule_combining_mode="most_restrictive"
    )


def test_most_restrictive_combines_independent_axis_winners(opa_client, opa_base_url):
    rego = compile_policy(_crossed_axis_policy(), validate=False)
    put_response = _put_policy(opa_client, rego)
    assert put_response.status_code == 200, put_response.text

    decision = _evaluate(opa_base_url, {"event_type": "x"})
    assert decision.risk_level == "critical"
    assert decision.enforcement_type == "block"
    assert decision.allowed_actions == ["redact"]
    assert decision.confidence == 0.9
    assert decision.explanation == "risk axis winner; enforcement axis winner"
    assert sorted(decision.triggered_rules) == ["WORSE-ENF-1", "WORSE-RISK-1"]


def test_first_fires_picks_the_catalog_order_winner_regardless_of_severity(
    opa_client, opa_base_url
):
    policy = _two_rule_policy()
    policy["rule_combining_mode"] = "first_fires"
    rego = compile_policy(policy, validate=False)
    put_response = _put_policy(opa_client, rego)
    assert put_response.status_code == 200, put_response.text

    decision = _evaluate(opa_base_url, {"event_type": "x"})
    # LOW-1 is listed first in _two_rule_policy, so first_fires must return
    # its (less severe) decision even though HIGH-1 also fired.
    assert decision.risk_level == "low"
    assert decision.enforcement_type == "warn"
    assert sorted(decision.triggered_rules) == ["HIGH-1", "LOW-1"]


# --- multiple entries of the same list field bind independent variables ----
#
# _rule_var (rego.py) must derive a distinct Rego variable per entry index,
# not just per rule id + field-type suffix — otherwise two data_items[]
# entries on one rule reuse the identical `some`-bound variable name, which
# Rego requires to resolve to the same value on every use within one rule
# body. In practice OPA's compiler rejects the resulting module outright
# ("var ... declared above") rather than silently misinterpreting it — this
# was confirmed by reverting the fix and observing the PUT below return 400
# — but only a real OPA compile catches that; the structural tests in
# test_rego.py only check for substrings and would not notice either the
# rejection or a hypothetical silent misbinding.


def _two_item_policy() -> dict[str, Any]:
    """One rule with two ``data_items[]`` entries requiring different
    classification levels — satisfiable only by two distinct input items,
    never by one item alone."""
    rule = _rule(
        "TWO-ITEM-1",
        event_type="x",
        data_items=[
            {"classification_level": "RESTRICTED"},
            {"classification_level": "CONFIDENTIAL"},
        ],
    )
    return _policy([rule])


def test_rule_with_two_data_items_entries_requires_two_distinct_items(
    opa_client, opa_base_url
):
    rego = compile_policy(_two_item_policy(), validate=False)
    put_response = _put_policy(opa_client, rego)
    assert put_response.status_code == 200, put_response.text

    # Two distinct items, one per entry: fires.
    decision = _evaluate(
        opa_base_url,
        {
            "event_type": "x",
            "data_items": [
                {"classification_level": "RESTRICTED"},
                {"classification_level": "CONFIDENTIAL"},
            ],
        },
    )
    assert decision.triggered_rules == ["TWO-ITEM-1"]


def test_rule_with_two_data_items_entries_does_not_fire_for_a_single_matching_item(
    opa_client, opa_base_url
):
    """The bug this guards against: reusing one variable name for both
    entries would let a single item satisfying only one classification level
    be treated as though it simultaneously satisfied both — this input has
    only a RESTRICTED item, no CONFIDENTIAL one, so the rule must not fire."""
    rego = compile_policy(_two_item_policy(), validate=False)
    put_response = _put_policy(opa_client, rego)
    assert put_response.status_code == 200, put_response.text

    decision = _evaluate(
        opa_base_url,
        {
            "event_type": "x",
            "data_items": [{"classification_level": "RESTRICTED"}],
        },
    )
    assert decision.triggered_rules == ["0000"]


# --- a syntax error is caught by the PUT, not silently accepted --------


def test_a_syntactically_invalid_rego_module_is_rejected_by_the_put(opa_client):
    broken_rego = "package data_governance\n\nthis is not valid rego {{{\n"
    put_response = _put_policy(opa_client, broken_rego)
    assert put_response.status_code != 200


# --- response contract -----------------------------------------------------


def test_opa_response_parses_through_the_real_opa_client_unchanged(
    opa_client, opa_base_url
):
    """Every scenario above already calls through the real, production
    :class:`OpaClient.evaluate` via :func:`_evaluate` — if the compiled
    Rego ever emitted a response shape ``_parse_decision`` (OpaClient's
    private parser) could not read, those calls would raise there, not
    pass silently. This test pins that explicitly: a policy_version-bearing
    decision still parses to an ``OpaDecision`` with every optional field
    populated, not just the required ``risk_level``."""
    rego = compile_policy(
        _policy(
            [
                _rule(
                    "R-1",
                    event_type="x",
                    rule_decision={
                        "risk_level": "high",
                        "enforcement_type": "block",
                        "allowed_actions": ["redact"],
                        "explanation": "explained",
                        "confidence": 0.8,
                    },
                )
            ]
        ),
        validate=False,
    )
    put_response = _put_policy(opa_client, rego)
    assert put_response.status_code == 200, put_response.text

    decision = _evaluate(opa_base_url, {"event_type": "x"})
    assert decision.risk_level == "high"
    assert decision.enforcement_type == "block"
    assert decision.allowed_actions == ["redact"]
    assert decision.explanation == "explained"
    assert decision.confidence == 0.8
    assert decision.triggered_rules == ["R-1"]


# --- opa_output.schema.json conformance -------------------------------------
#
# Requirement: the policy_decision OPA returns must validate against
# opa_output.schema.json. Nothing else in this suite checks the raw OPA
# response against that schema — test_rego.py and test_combining.py assert
# against the emitted Rego source / the Python oracle's dict shape, and
# OpaClient._parse_decision only reads the fields it knows about, so a
# schema violation (e.g. an unexpected rule_id/rule_name key) would pass
# silently through every other test in this file.


def _raw_policy_decision(opa_client: httpx.Client, opa_input: dict[str, Any]) -> dict:
    response = opa_client.post(OPA_DECISION_PATH, json={"input": opa_input})
    response.raise_for_status()
    return response.json()["result"]


def test_fallback_decision_conforms_to_opa_output_schema(opa_client):
    rego = compile_policy(catalog.load_rules_source())
    put_response = _put_policy(opa_client, rego)
    assert put_response.status_code == 200, put_response.text

    result = _raw_policy_decision(opa_client, {"event_type": "internal_view"})
    _opa_output_validator().validate(result)


def test_most_restrictive_combined_decision_conforms_to_opa_output_schema(opa_client):
    rego = compile_policy(_crossed_axis_policy(), validate=False)
    put_response = _put_policy(opa_client, rego)
    assert put_response.status_code == 200, put_response.text

    result = _raw_policy_decision(opa_client, {"event_type": "x"})
    _opa_output_validator().validate(result)
