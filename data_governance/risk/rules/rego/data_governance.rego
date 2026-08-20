# The data-governance decision policy (issue #162, PRD-SENTRY-001 v8 §4.1).
#
# Serves POST /v1/data/data_governance/policy_decision — the decision path the
# merged OPA client (data_governance/risk/engine/opa.py) is configured with
# (RISK_OPA_DECISION_PATH). Deployed into a stock OPA server (no OPA code
# changes) alongside the rule catalog's rules_source.json, loaded as base
# data: the policy is DATA-DRIVEN — it iterates data.rules and matches each
# rule's structural criteria against the input, so a policy change is a data
# (ConfigMap) change, never a Rego change. That is the whole point of the
# architecture: policy is data + this one generic evaluator.
#
# Matching semantics (policy.schema.json: "a rule has no condition language —
# the presence of fields is the predicate"):
#   - rule.event_type, when present, must equal input.event_type;
#   - every rule.data_items template must be satisfied by SOME input data
#     item (template regulatory_tags ⊆ item's; template classification_level
#     equals the item's);
#   - every rule.data_destinations template must be satisfied by SOME input
#     destination (template categories ⊆ destination's; template trust level
#     equals the destination's).
# A criterion the rule does not state is not checked; a criterion the input
# cannot satisfy (including a wholly absent input field — honest absence)
# fails the match. (Before #163 the engine input carried no
# data_destinations at all, so every rule fell through to the fallback; #163
# supplies them from the anchor span's wire facts, categorised by #178's
# hostname whitelist.)
#
# The decision ALWAYS exists (§4.1 / the client raises OpaResponseError on a
# result without risk_level): when no rule matches, the fallback decision is
# risk_level "none" with the policy file's declared
# runtime_enforcement_mode.unknown_behavior_enforcement_type ("allow").
# When rules match, severities aggregate most-severe-wins using the SAME
# most-severe-first orders the DAS catalog pins
# (data_governance/risk/rules/catalog.py RISK_LEVEL_ORDER /
# ENFORCEMENT_ORDER — kept verbatim below; the *_orders_match_catalog test
# in tests/risk/rules/test_rego_policy.py guards the duplication), and
# allowed_actions combine by INTERSECTION — an action survives only if every
# matched rule allows it (the strictest reading; e.g. DG-001 allows "redact"
# but DG-001+DG-004 together allow nothing).
#
# policy_version is reported from the loaded policy data
# (policy_id:version), so the stored decision names the exact policy that
# produced it (FR-DAS "report a real policy_version").

package data_governance

# Most-severe-first, pinned verbatim to catalog.py. Values missing from
# these orders never match a min() rank and are dropped from aggregation;
# the policy-data well-formedness tests reject a rules_source.json that
# carries an unrankable value, so this cannot silently mis-rank in
# production.
risk_level_order := ["critical", "high", "medium", "low", "none", "unknown"]

enforcement_order := [
	"block",
	"quarantine",
	"require_approval",
	"redact",
	"mask",
	"anonymize",
	"encrypt",
	"escalate",
	"notify",
	"warn",
	"log_only",
	"audit",
	"allow",
]

policy_version := sprintf("%s:%s", [data.policy_id, data.version])

# --- rule matching -----------------------------------------------------------

matched_rules contains rule if {
	some rule in data.rules
	event_type_matches(rule)
	data_items_match(rule)
	destinations_match(rule)
}

event_type_matches(rule) if not rule.event_type

event_type_matches(rule) if rule.event_type == input.event_type

data_items_match(rule) if not rule.data_items

data_items_match(rule) if {
	every template in rule.data_items {
		some item in input.data_items
		item_matches(item, template)
	}
}

item_matches(item, template) if {
	item_tags_match(item, template)
	item_level_matches(item, template)
}

item_tags_match(_, template) if not template.regulatory_tags

item_tags_match(item, template) if {
	every tag in template.regulatory_tags {
		tag in item.regulatory_tags
	}
}

item_level_matches(_, template) if not template.classification_level

item_level_matches(item, template) if {
	template.classification_level == item.classification_level
}

destinations_match(rule) if not rule.data_destinations

destinations_match(rule) if {
	every template in rule.data_destinations {
		some dest in input.data_destinations
		destination_matches(dest, template)
	}
}

destination_matches(dest, template) if {
	destination_categories_match(dest, template)
	destination_trust_matches(dest, template)
}

destination_categories_match(_, template) if not template.data_destination_categories

destination_categories_match(dest, template) if {
	every category in template.data_destination_categories {
		category in dest.data_destination_categories
	}
}

destination_trust_matches(_, template) if not template.data_destination_trust_level

destination_trust_matches(dest, template) if {
	template.data_destination_trust_level == dest.data_destination_trust_level
}

# --- severity aggregation (most-severe-wins, catalog order) ------------------

rank(order, value) := idx if {
	some idx, v in order
	v == value
}

most_severe_risk := risk_level_order[min({rank(risk_level_order, level) |
	some rule in matched_rules
	level := rule.policy_decision.risk_level
})]

matched_enforcements := {enforcement |
	some rule in matched_rules
	enforcement := rule.policy_decision.enforcement_type
}

strictest_enforcement := enforcement_order[min({rank(enforcement_order, e) |
	some e in matched_enforcements
})]

# An action is allowed only if EVERY matched rule allows it (strictest
# combination; a rule without allowed_actions contributes the empty set,
# i.e. allows nothing on top of its enforcement).
allowed_actions_matched := sort([action |
	some action in intersection({actions |
		some rule in matched_rules
		actions := {a | some a in rule.policy_decision.allowed_actions}
	})
])

matched_confidences := {confidence |
	some rule in matched_rules
	confidence := rule.policy_decision.confidence
}

matched_explanation := concat(" ", sort([text |
	some rule in matched_rules
	text := sprintf("[%s] %s", [rule.rule_id, rule.policy_decision.explanation])
]))

enforcement_part := {"enforcement_type": strictest_enforcement} if {
	count(matched_enforcements) > 0
}

enforcement_part := {} if count(matched_enforcements) == 0

confidence_part := {"confidence": max(matched_confidences)} if {
	count(matched_confidences) > 0
}

confidence_part := {} if count(matched_confidences) == 0

# --- the decision (always defined) -------------------------------------------

policy_decision := object.union(
	{
		"risk_level": most_severe_risk,
		"allowed_actions": allowed_actions_matched,
		"explanation": matched_explanation,
		"triggered_rules": sort([rule.rule_id | some rule in matched_rules]),
		"policy_version": policy_version,
	},
	object.union(enforcement_part, confidence_part),
) if {
	count(matched_rules) > 0
}

policy_decision := {
	"risk_level": "none",
	"enforcement_type": fallback_enforcement,
	"allowed_actions": [],
	"explanation": "No policy rule matched this event.",
	"triggered_rules": [],
	"policy_version": policy_version,
} if {
	count(matched_rules) == 0
}

fallback_enforcement := mode if {
	mode := data.runtime_enforcement_mode.unknown_behavior_enforcement_type
}

fallback_enforcement := "allow" if {
	not data.runtime_enforcement_mode.unknown_behavior_enforcement_type
}
