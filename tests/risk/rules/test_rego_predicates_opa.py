"""Sparse policy predicates must constrain real OPA decisions, at every depth."""

from __future__ import annotations

import copy

import pytest

from data_governance.risk.config import OPA_DECISION_PATH
from data_governance.processors.classification.verdict import Verdict
from data_governance.risk.engine import utils
from data_governance.risk.engine.opa import OpaClient
from data_governance.risk.rules import catalog
from data_governance.risk.rules.rego import FALLBACK_RULE_ID, compile_policy

pytestmark = pytest.mark.opa


def _put_rule(client, **predicates):
    policy = copy.deepcopy(catalog.load_rules_source())
    rule = policy["rules"][0]
    # Keep a schema-valid rule's metadata and decision, replacing its predicates.
    rule.pop("event_type")
    rule.pop("data_items")
    rule.pop("data_destinations")
    rule.update(predicates)
    policy["rules"] = [rule]
    response = client.put(
        "/v1/policies/data_governance",
        content=compile_policy(policy).encode(),
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 200, response.text


def _fired(client, flow):
    opa = OpaClient(http_client=client, decision_path=OPA_DECISION_PATH, max_retries=0)
    decision = opa.evaluate(interaction_id="test", opa_input={"flows": {"request": flow}})
    return decision.triggered_rules


def test_destination_name_and_url_must_match_the_same_destination(opa_client):
    wanted = {
        "data_destination_name": "api.allowed.example",
        "data_destination_url": "https://api.allowed.example/private",
    }
    _put_rule(opa_client, data_destinations=[wanted])
    assert _fired(opa_client, {"data_destinations": [{
        **wanted, "data_destination_name": "api.other.example",
    }]}) == [FALLBACK_RULE_ID]
    assert _fired(opa_client, {"data_destinations": [{
        **wanted, "data_destination_url": "https://api.allowed.example/public",
    }]}) == [FALLBACK_RULE_ID]
    assert _fired(opa_client, {"data_destinations": [wanted]}) == ["DG-001"]
    assert _fired(opa_client, {"data_destinations": [
        {"data_destination_name": wanted["data_destination_name"]},
        {"data_destination_url": wanted["data_destination_url"]},
    ]}) == [FALLBACK_RULE_ID]


@pytest.mark.parametrize("axis,field,wanted,other", [
    ("data_sources", "data_source_name", "records", "other-records"),
    ("data_sources", "data_source_url", "https://records/private", "https://records/public"),
    ("data_items", "list_of_domains", ["finance"], ["person"]),
    ("data_items", "identity_bundles", ["identity_bundle"], ["other_bundle"]),
    ("entities", "entity_type", "SSN", "PHONE"),
    ("entities", "start", 0, 1),
    ("entities", "end", 11, 12),
    ("entities", "text", "123-45-6789", "987-65-4321"),
    ("entities", "domain", "person", "finance"),
    ("entities", "category", "person.govID", "finance.data"),
    ("entities", "regulatory_tags", ["PII"], ["PCI"]),
    ("entities", "data_type", "PID", "OPID"),
    ("entities", "classification_level", "RESTRICTED", "PUBLIC"),
])
def test_nested_predicates_reject_mismatched_or_missing_values(
    opa_client, axis, field, wanted, other,
):
    base = {"agent_name": "agent-a"} if axis == "processing_agents" else {}

    def flow(value):
        item = {**base, **value}
        if axis == "entities":
            return {"data_items": [{"entities": [item]}]}
        return {axis: [item]}

    required = flow({field: wanted})
    _put_rule(opa_client, **required)
    assert _fired(opa_client, flow({field: other})) == [FALLBACK_RULE_ID]
    assert _fired(opa_client, flow({})) == [FALLBACK_RULE_ID]
    assert _fired(opa_client, required) == ["DG-001"]
    if isinstance(wanted, list):
        # List predicates require a subset, rather than exact equality.
        assert _fired(opa_client, flow({field: wanted + other})) == ["DG-001"]


@pytest.mark.parametrize("predicates", [
    {"event_type": "external_sharing"},
    {"requested_actions": ["send"]},
    {"data_destinations": [{
        "data_destination_name": "outside.example.test",
        "data_destination_url": "https://outside.example.test/data",
        "data_destination_categories": ["external"],
        "data_destination_trust_level": "UNTRUSTED_EXTERNAL",
    }]},
    {"data_sources": [{"data_source_name": "review-agent",
                       "data_source_categories": ["internal"]}]},
    {"processing_agents": [{"agent_name": "review-agent"}]},
    {"accessing_user": {"username": "review-user", "user_roles": []}},
    {"data_items": [{"entities": [{
        "entity_type": "SSN", "start": 0, "end": 11, "text": "123-45-6789",
        "domain": "finance", "category": "finance.data", "regulatory_tags": ["PII"],
        "data_type": "PID", "classification_level": "RESTRICTED",
    }]}]},
    {"data_items": [{"classification_level": "RESTRICTED", "primary_domain": "finance",
                     "regulatory_tags": ["PII"], "list_of_entities": ["SSN"],
                     "list_of_domains": ["finance"], "identity_bundles": ["identity_bundle"]}]},
])
def test_supported_predicates_match_actual_runtime_input(opa_client, predicates):
    _put_rule(opa_client, **predicates)
    verdict = Verdict(
        sensitivity_level="RESTRICTED", regulatory_tags=["PII"], contains_identity_bundle=True,
        is_personalized=False, primary_domain="finance", model_version=1,
        findings=[{
            "entity_type": "SSN", "start": 0, "end": 11, "text": "123-45-6789",
            "domain": "finance", "category": "finance.data", "regulatory_tags": ["PII"],
            "identifier_type": "PID", "sensitivity_level": "RESTRICTED",
        }],
    )
    payload = utils.build_opa_input(
        legs=[utils.LegEvidence("request", "synthetic-request")], span_ids=[],
        classifications={"request": verdict}, caller_entity_id="review-agent",
        callee_entity_id="outside-service", internal_patterns=[],
        anchor=utils.AnchorFacts(direction="outbound", self_id="review-agent",
                                 peer_host="outside.example.test", url_scheme="https",
                                 url_path="/data", principal_sub="review-user"),
    )
    opa = OpaClient(http_client=opa_client, decision_path=OPA_DECISION_PATH, max_retries=0)
    decision = opa.evaluate(interaction_id="runtime-test", opa_input=payload)
    assert decision.triggered_rules == ["DG-001"]
    assert decision.enforcement_type == "block"


def test_all_fields_of_one_entity_predicate_match_the_same_entity(opa_client):
    _put_rule(opa_client, data_items=[{"entities": [{
        "entity_type": "SSN", "classification_level": "RESTRICTED",
    }]}])
    assert _fired(opa_client, {"data_items": [{"entities": [
        {"entity_type": "SSN", "classification_level": "PUBLIC"},
        {"entity_type": "PHONE", "classification_level": "RESTRICTED"},
    ]}]}) == [FALLBACK_RULE_ID]
    assert _fired(opa_client, {"data_items": [{"entities": [{
        "entity_type": "SSN", "classification_level": "RESTRICTED",
    }]}]}) == ["DG-001"]


def test_entities_in_different_item_predicates_bind_independently(opa_client):
    _put_rule(opa_client, data_items=[
        {"entities": [{"entity_type": "SSN"}]},
        {"entities": [{"entity_type": "PHONE"}]},
    ])
    assert _fired(opa_client, {"data_items": [
        {"entities": [{"entity_type": "SSN"}]},
    ]}) == [FALLBACK_RULE_ID]
    assert _fired(opa_client, {"data_items": [
        {"entities": [{"entity_type": "SSN"}]},
        {"entities": [{"entity_type": "PHONE"}]},
    ]}) == ["DG-001"]
