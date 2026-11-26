# MCP tool-call decisions for DAM agents: the access half of Sentry's PDP behind
# DAM's MCP policy enforcement point.
#
# A call goes through only if the agent's description and the company security
# policy both allow it:
#
# - The agent's description. aiac generates one Rego module per agent, package
#   dam.generated.<agent id with '-' as '_'>, from the agent's description and
#   the organisation policy (aiac demo/dam/onboard_dam_agent.py), deciding each
#   operation (a tool, or a tool's operation such as issue_write.close). The
#   onboarder keeps each agent's onboarding status in data.dam.agents.
# - The company security policy, which an operator writes in DAM. The onboarder
#   splits it (aiac demo/dam/company_policy.py) into data.dam.company: role
#   rules, each with the Keycloak usernames that hold its roles, and data rules,
#   which an allowed call hands to data governance.
#
# Every reason names the agent's own words or the company policy, never the
# mechanism that derived the decision.
#
# input: {"agent": "<agent id>", "host": "...", "rpc": "<JSON-RPC method>",
#         "tool": "<tools/call name>", "operations": ["<operation>", ...],
#         "people": ["<username>", ...] | null}
package dam.mcp

import rego.v1

decision := {"allow": true, "reason": "not a tool call"} if input.rpc != "tools/call"

decision := {"allow": false, "reason": denials[0]} if {
	input.rpc == "tools/call"
	count(denials) > 0
}

decision := {"allow": true, "reason": generated.reasons[operations[0]], "data_rules": data_rules} if {
	input.rpc == "tools/call"
	count(denials) == 0
}

# The order a reason is chosen in: the agent's standing, its description, the
# company policy's own state, then the company's role rules, those naming the
# agent first.
denials := array.concat(
	array.concat(sort(access_denials), sort(description_denials)),
	array.concat(sort(company_denials), array.concat(sort(agent_role_denials), sort(role_denials))),
)

# A role rule that names the agent ("the QA sign-off agent is used by testers
# only") is the reason given before one about operations alone.
agent_role_denials := {reason |
	some reason in role_denials
	some [_, rule] in covering
	count(rule.agents) > 0
	contains(reason, sprintf("\"%s\"", [rule.text]))
}

operations := input.operations if {
	is_array(input.operations)
	count(input.operations) > 0
} else := [input.tool]

known_entry := entry if {
	entry := data.dam.agents[input.agent]
	input.host == entry.host
}

generated := data.dam.generated[regex.replace(input.agent, `[^A-Za-z0-9_]`, "_")]

decided if {
	known_entry.status == "decided"
	generated.host == input.host
}

# --- the agent's standing ---------------------------------------------------

access_denials contains sprintf("%s on %s is denied: the policy has no decision for agent %s, so nothing grants it", [input.tool, input.host, input.agent]) if {
	not known_entry
}

access_denials contains sprintf("%s.%s for agent %s is denied: the agent's access is being decided from its description; try again shortly", [known_entry.connection, input.tool, known_entry.name]) if {
	known_entry.status == "pending"
}

access_denials contains sprintf("%s.%s for agent %s is denied: the agent's access could not be decided (%s), so nothing grants it", [known_entry.connection, input.tool, known_entry.name, failure]) if {
	not known_entry.status in {"pending"}
	not decided
	failure := object.get(known_entry, "error", "no generated policy")
}

# --- the agent's description ------------------------------------------------

description_denials contains generated.reasons[op] if {
	decided
	some op in operations
	not op in generated.allow
	generated.reasons[op]
}

description_denials contains sprintf("%s.%s for agent %s is denied: the operation is new since the agent's access was decided, so nothing grants it", [generated.connection, op, generated.name]) if {
	decided
	some op in operations
	not generated.reasons[op]
}

# --- the company security policy --------------------------------------------

company := c if {
	c := data.dam.company
} else := {"status": "unavailable"}

company_denials contains sprintf("%s.%s for agent %s is denied: the company security policy is being applied; try again shortly", [generated.connection, input.tool, generated.name]) if {
	decided
	company.status == "pending"
}

company_denials contains sprintf("%s.%s for agent %s is denied: the company security policy could not be applied, so nothing allows it", [generated.connection, input.tool, generated.name]) if {
	decided
	not company.status in {"pending", "applied"}
}

covers(rule, op) if {
	count(rule.operations) == 0
	count(rule.agents) == 0
}

covers(rule, op) if {
	sprintf("%s.%s", [generated.connection, op]) in rule.operations
	count(rule.agents) == 0
}

covers(rule, op) if {
	count(rule.operations) == 0
	input.agent in rule.agents
}

covers(rule, op) if {
	sprintf("%s.%s", [generated.connection, op]) in rule.operations
	input.agent in rule.agents
}

covering contains [op, rule] if {
	decided
	company.status == "applied"
	some op in operations
	some rule in company.role_rules
	covers(rule, op)
}

role_denials contains sprintf("%s.%s for agent %s is denied: the company security policy states \"%s\", and who asked for it could not be told", [generated.connection, op, generated.name, rule.text]) if {
	some [op, rule] in covering
	not is_array(input.people)
}

role_denials contains sprintf("%s.%s for agent %s is denied: the company security policy states \"%s\", and no signed-in person asked for it", [generated.connection, op, generated.name, rule.text]) if {
	some [op, rule] in covering
	is_array(input.people)
	count(input.people) == 0
}

role_denials contains sprintf("%s.%s for agent %s, asked by %s, is denied: the company security policy states \"%s\"", [generated.connection, op, generated.name, person, rule.text]) if {
	some [op, rule] in covering
	some person in input.people
	not person in rule.holders
}

# The company's data rules, for data governance: each dg rule id with the
# policy sentence it comes from.
data_rules := {id: rule.text |
	company.status == "applied"
	some rule in company.data_rules
	some id in rule.dg_rules
}
