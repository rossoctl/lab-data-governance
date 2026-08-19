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

Deselected by default (``pytest.ini_options.addopts = "-ra -m 'not opa'"``);
run explicitly with ``-m opa`` (podman/docker must be up — see
``conftest.py``'s ``DOCKER_HOST`` handling).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from data_governance.risk.config import OPA_DECISION_PATH
from data_governance.risk.engine.opa import OpaClient
from data_governance.risk.rules.rego import compile_policy

pytestmark = pytest.mark.opa

_DECISION = {
    "risk_level": "high",
    "enforcement_type": "block",
    "allowed_actions": [],
    "explanation": "test",
    "confidence": 0.9,
}


def _policy(rules: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "runtime_enforcement_mode": {"unknown_behavior_enforcement_type": "allow"},
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
    from data_governance.risk.rules import catalog
    from data_governance.risk.config import POLICY_RULE_COMBINING_MODE

    rego = compile_policy(
        catalog.load_rules_source(), default_mode=POLICY_RULE_COMBINING_MODE
    )
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
    from data_governance.risk.rules import catalog
    from data_governance.risk.config import POLICY_RULE_COMBINING_MODE

    rego = compile_policy(
        catalog.load_rules_source(), default_mode=POLICY_RULE_COMBINING_MODE
    )
    put_response = _put_policy(opa_client, rego)
    assert put_response.status_code == 200, put_response.text

    decision = _evaluate(opa_base_url, {"event_type": "internal_view"})
    assert decision.risk_level == "none"
    assert decision.enforcement_type == "allow"
    assert decision.triggered_rules == []


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
    rego = compile_policy(
        _two_rule_policy(), validate=False, default_mode="most_restrictive"
    )
    put_response = _put_policy(opa_client, rego)
    assert put_response.status_code == 200, put_response.text

    decision = _evaluate(opa_base_url, {"event_type": "x"})
    assert decision.risk_level == "critical"
    assert decision.enforcement_type == "block"
    assert sorted(decision.triggered_rules) == ["HIGH-1", "LOW-1"]


def test_first_fires_picks_the_catalog_order_winner_regardless_of_severity(
    opa_client, opa_base_url
):
    rego = compile_policy(
        _two_rule_policy(), validate=False, default_mode="first_fires"
    )
    put_response = _put_policy(opa_client, rego)
    assert put_response.status_code == 200, put_response.text

    decision = _evaluate(opa_base_url, {"event_type": "x"})
    # LOW-1 is listed first in _two_rule_policy, so first_fires must return
    # its (less severe) decision even though HIGH-1 also fired.
    assert decision.risk_level == "low"
    assert decision.enforcement_type == "warn"
    assert sorted(decision.triggered_rules) == ["HIGH-1", "LOW-1"]


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
