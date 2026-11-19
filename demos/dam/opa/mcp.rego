# MCP tool-call decisions for DAM agents: the access-control half of Sentry's
# PDP behind DAM's MCP policy enforcement point.
#
# aiac generates one Rego module per agent, package
# dam.generated.<agent id with '-' as '_'>, from the agent's description and
# the organisation policy (aiac demo/dam/onboard_dam_agent.py, to_rego), and
# loads it into OPA. This policy evaluates the calling agent's module. The
# onboarder also keeps each agent's onboarding status in data.dam.agents
# (pending, decided, failed). Every reason names the policy and the agent's
# own words, never the mechanism that derived the decision.
#
# input: {"agent": "<agent id>", "host": "...", "rpc": "<JSON-RPC method>",
#         "tool": "<tools/call name>"}
package dam.mcp

import rego.v1

# Only a tool call is decided; the session's own messages (initialize,
# tools/list, ...) carry no operation on a resource.
decision := {"allow": true, "reason": "not a tool call"} if input.rpc != "tools/call"

# The agent's own module: what its description grants and the policy prohibits.
decision := {"allow": true, "reason": generated.reasons[input.tool]} if {
	input.rpc == "tools/call"
	decided
	input.tool in generated.allow
}

decision := {"allow": false, "reason": generated.reasons[input.tool]} if {
	input.rpc == "tools/call"
	decided
	not input.tool in generated.allow
	generated.reasons[input.tool]
}

decision := {"allow": false, "reason": sprintf("%s.%s for agent %s is denied: the tool is new since the agent's access was decided, so nothing grants it", [generated.connection, input.tool, generated.name])} if {
	input.rpc == "tools/call"
	decided
	not generated.reasons[input.tool]
}

decision := {"allow": false, "reason": sprintf("%s.%s for agent %s is denied: the agent's access is being decided from its description; try again shortly", [entry.connection, input.tool, entry.name])} if {
	input.rpc == "tools/call"
	entry := known_entry
	entry.status == "pending"
}

decision := {"allow": false, "reason": sprintf("%s.%s for agent %s is denied: the agent's access could not be decided (%s), so nothing grants it", [entry.connection, input.tool, entry.name, failure])} if {
	input.rpc == "tools/call"
	entry := known_entry
	not entry.status in {"pending"}
	not decided
	failure := object.get(entry, "error", "no generated policy")
}

decision := {"allow": false, "reason": sprintf("%s on %s is denied: the policy has no decision for agent %s, so nothing grants it", [input.tool, input.host, input.agent])} if {
	input.rpc == "tools/call"
	not known_entry
}

known_entry := entry if {
	entry := data.dam.agents[input.agent]
	input.host == entry.host
}

generated := data.dam.generated[regex.replace(input.agent, `[^A-Za-z0-9_]`, "_")]

decided if {
	known_entry.status == "decided"
	generated.host == input.host
}
