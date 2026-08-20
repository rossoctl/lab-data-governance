# opa-test suite for the data-governance decision policy (issue #162).
#
# Runs against the REAL policy data (rules_source.json loaded as base data,
# exactly as the server loads it), so these tests pin the end-to-end pair —
# generic evaluator + shipped rules — not a synthetic fixture. Run with:
#
#   opa test data_governance/risk/rules/rego data_governance/risk/rules/_policy_data/rules_source.json
#
# (tests/risk/rules/test_rego_policy.py runs exactly that inside the pinned
# OPA container, so `uv run pytest` covers this suite too.)

package data_governance_test

import data.data_governance

# --- shared input fragments --------------------------------------------------

pii_item := {"regulatory_tags": ["PII"], "classification_level": "CONFIDENTIAL"}

phi_item := {"regulatory_tags": ["PHI", "PII"], "classification_level": "RESTRICTED"}

restricted_item := {"regulatory_tags": [], "classification_level": "RESTRICTED"}

clean_item := {"regulatory_tags": [], "classification_level": "PUBLIC"}

external_dest := {
	"data_destination_name": "partner.example.com",
	"data_destination_url": "https://partner.example.com/ingest",
	"data_destination_categories": ["external"],
	"data_destination_trust_level": "UNTRUSTED_EXTERNAL",
}

internal_dest := {
	"data_destination_name": "records-tool.team2.svc",
	"data_destination_categories": ["internal"],
}

# --- the three MVP rules fire ------------------------------------------------

test_dg001_pii_to_external_fires if {
	decision := data_governance.policy_decision with input as {
		"event_type": "external_sharing",
		"data_items": [pii_item],
		"data_destinations": [external_dest],
	}
	decision.risk_level == "critical"
	decision.enforcement_type == "block"
	decision.triggered_rules == ["DG-001"]
	decision.allowed_actions == ["redact"]
	contains(decision.explanation, "[DG-001]")
	decision.confidence == 0.95
	decision.policy_version == "data-governance-v1:1.0.0"
}

test_dg002_phi_to_external_fires if {
	decision := data_governance.policy_decision with input as {
		"event_type": "external_sharing",
		"data_items": [phi_item],
		"data_destinations": [external_dest],
	}
	decision.risk_level == "critical"
	decision.enforcement_type == "block"
	"DG-002" in decision.triggered_rules
}

test_dg004_restricted_to_external_fires if {
	decision := data_governance.policy_decision with input as {
		"event_type": "external_sharing",
		"data_items": [restricted_item],
		"data_destinations": [external_dest],
	}
	decision.risk_level == "critical"
	decision.enforcement_type == "block"
	decision.triggered_rules == ["DG-004"]
	decision.allowed_actions == []
}

# --- aggregation across multiple matched rules -------------------------------

test_multiple_rules_aggregate_and_intersect_allowed_actions if {
	# A PHI+PII RESTRICTED item matches DG-001, DG-002 and DG-004 at once.
	# allowed_actions is the intersection: DG-001's ["redact"] ∩ [] ∩ [] = [].
	decision := data_governance.policy_decision with input as {
		"event_type": "external_sharing",
		"data_items": [phi_item],
		"data_destinations": [external_dest],
	}
	decision.triggered_rules == ["DG-001", "DG-002", "DG-004"]
	decision.risk_level == "critical"
	decision.allowed_actions == []
	contains(decision.explanation, "[DG-001]")
	contains(decision.explanation, "[DG-004]")
}

# --- negative controls: the fallback decision --------------------------------

fallback_expected := {
	"risk_level": "none",
	"enforcement_type": "allow",
	"allowed_actions": [],
	"explanation": "No policy rule matched this event.",
	"triggered_rules": [],
	"policy_version": "data-governance-v1:1.0.0",
}

test_internal_destination_gets_fallback if {
	# Same sensitive payload, internal destination: the negative control.
	decision := data_governance.policy_decision with input as {
		"event_type": "internal_sharing",
		"data_items": [phi_item],
		"data_destinations": [internal_dest],
	}
	decision == fallback_expected
}

test_external_dest_with_internal_event_type_gets_fallback if {
	# event_type is part of every MVP rule's predicate.
	decision := data_governance.policy_decision with input as {
		"event_type": "internal_sharing",
		"data_items": [pii_item],
		"data_destinations": [external_dest],
	}
	decision == fallback_expected
}

test_clean_payload_to_external_gets_fallback if {
	decision := data_governance.policy_decision with input as {
		"event_type": "external_sharing",
		"data_items": [clean_item],
		"data_destinations": [external_dest],
	}
	decision == fallback_expected
}

test_input_without_destinations_gets_fallback if {
	# Today's engine input (pre-#163): no data_destinations, no event_type —
	# honest absence must yield the fallback, never an error (the client
	# raises OpaResponseError on a missing decision; this is the guard).
	decision := data_governance.policy_decision with input as {
		"interaction_id": "i-1",
		"data_items": [phi_item],
		"requested_actions": ["send", "receive"],
	}
	decision == fallback_expected
}

test_empty_input_gets_fallback if {
	decision := data_governance.policy_decision with input as {}
	decision == fallback_expected
}

test_external_dest_without_trust_level_gets_fallback if {
	# The MVP rules' destination template requires trust level
	# UNTRUSTED_EXTERNAL; a destination categorised external but with no
	# trust fact does not match (#163's evidence always stamps the trust
	# level with the external category — this pins that requirement).
	no_trust := object.remove(external_dest, ["data_destination_trust_level"])
	decision := data_governance.policy_decision with input as {
		"event_type": "external_sharing",
		"data_items": [pii_item],
		"data_destinations": [no_trust],
	}
	decision == fallback_expected
}

# --- policy-data well-formedness ---------------------------------------------

test_every_rule_risk_level_is_rankable if {
	every rule in data.rules {
		rule.policy_decision.risk_level in data_governance.risk_level_order
	}
}

test_every_rule_enforcement_is_rankable if {
	every rule in data.rules {
		rule.policy_decision.enforcement_type in data_governance.enforcement_order
	}
}

test_policy_version_reflects_loaded_data if {
	data_governance.policy_version == "data-governance-v1:1.0.0"
}
