# MCP tool-call decisions for DAM agents (the PDP behind DAM's MCP policy
# enforcement point). data.dam.agents holds one decision per agent and tool,
# with the reason the agent is told; it is derived from each agent's
# description and the organisation policy (aiac demo/dam/onboard_dam_agent.py).
# This policy only reads it. Every reason names the policy and the agent's own
# words, never the mechanism that derived the decision.
#
# input: {"agent": "<agent id>", "host": "...", "rpc": "<JSON-RPC method>",
#         "tool": "<tools/call name>"}
package dam.mcp

import rego.v1

# Only a tool call is decided; the session's own messages (initialize,
# tools/list, ...) carry no operation on a resource.
decision := {"allow": true, "reason": "not a tool call"} if input.rpc != "tools/call"

decision := {"allow": t.effect == "allow", "reason": t.reason} if {
	input.rpc == "tools/call"
	entry := decided_entry
	t := entry.tools[input.tool]
}

decision := {"allow": false, "reason": sprintf("%s.%s for agent %s is denied: the agent's access is being decided from its description; try again shortly", [entry.connection, input.tool, entry.name])} if {
	input.rpc == "tools/call"
	entry := known_entry
	entry.status == "pending"
}

decision := {"allow": false, "reason": sprintf("%s.%s for agent %s is denied: the agent's access could not be decided (%s), so nothing grants it", [entry.connection, input.tool, entry.name, object.get(entry, "error", "unknown error")])} if {
	input.rpc == "tools/call"
	entry := known_entry
	entry.status == "failed"
}

decision := {"allow": false, "reason": sprintf("%s on %s is denied: the policy has no decision for agent %s, so nothing grants it", [input.tool, input.host, input.agent])} if {
	input.rpc == "tools/call"
	not known_agent
}

decision := {"allow": false, "reason": sprintf("%s.%s for agent %s is denied: the tool is new since the agent's access was decided, so nothing grants it", [entry.connection, input.tool, entry.name])} if {
	input.rpc == "tools/call"
	entry := decided_entry
	not entry.tools[input.tool]
}

known_agent if known_entry

known_entry := entry if {
	entry := data.dam.agents[input.agent]
	input.host == entry.host
}

# An entry without a status was written before the onboarder kept one: decided.
decided_entry := entry if {
	entry := known_entry
	object.get(entry, "status", "decided") == "decided"
}
