"""Policies must not accept guards the risk engine can never supply."""

from __future__ import annotations

import copy
import json
import re

import pytest

from data_governance.risk.rules import catalog, compile
from data_governance.risk.rules.rego import compile_policy


def _policy(predicates):
    policy = copy.deepcopy(catalog.load_rules_source())
    policy["rules"] = [policy["rules"][0]]
    policy["rules"][0].update(predicates)
    return policy


@pytest.mark.parametrize("predicates,field", [
    ({"data_destinations": [{"data_destination_trust_level_category": "UNTRUSTED"}]},
     "data_destination_trust_level_category"),
    ({"data_destinations": [{"data_destination_types": ["external_api"]}]},
     "data_destination_types"),
    ({"data_sources": [{"data_source_types": ["database"]}]}, "data_source_types"),
    *[({field: "approved-purpose"}, field) for field in (
        "declared_business_intent", "observed_business_intent",
        "observed_business_intent_description", "declared_operational_intent",
        "observed_operational_intent",
    )],
    ({"data_items": [{"text": "synthetic content"}]}, "text"),
    ({"data_items": [{"entities": [{"confidence": 0.9}]}]}, "confidence"),
    ({"data_items": [{"entities": [{"data_state": ["raw"]}]}]}, "data_state"),
    ({"processing_agents": [{"agent_name": "review-agent",
                             "agent_trust_level": "UNTRUSTED_EXTERNAL"}]},
     "agent_trust_level"),
    ({"processing_agents": [{"agent_name": "review-agent",
                             "agent_trust_level_category": "UNTRUSTED"}]},
     "agent_trust_level_category"),
    ({"data_count": 1}, "data_count"),
    ({"accessing_user": {"username": "review-user", "user_roles": ["admin"]}},
     "user_roles"),
])
@pytest.mark.parametrize("validate", [True, False])
def test_unavailable_runtime_predicates_are_rejected(predicates, field, validate):
    # Schema validation alone permits these fields. Neither compiler entry
    # path may produce an ineffective guard that falls through to allow.
    with pytest.raises(NotImplementedError, match=rf"DG-001.*{re.escape(field)}"):
        compile_policy(_policy(predicates), validate=validate)


def test_custom_bundle_cannot_publish_an_unavailable_runtime_predicate(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(_policy({
        "data_destinations": [{"data_destination_trust_level_category": "UNTRUSTED"}],
    })))
    with pytest.raises(NotImplementedError, match="data_destination_trust_level_category"):
        compile.compile_bundle(path)
