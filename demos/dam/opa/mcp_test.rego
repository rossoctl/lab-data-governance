# The story's decisions, as a table: two agents (issue-workbench, qa-signoff),
# Alice (developer) and Bob (tester), Acts 2 and 3 of the Sentry on DAM demo.
package dam.mcp_test

import rego.v1

import data.dam.mcp

host := "api.githubcopilot.com"

agents := {
	"agent-w": {"name": "issue-workbench", "connection": "github-mcp", "host": host, "status": "decided"},
	"agent-q": {"name": "qa-signoff", "connection": "github-mcp", "host": host, "status": "decided"},
}

generated := {
	"agent_w": {
		"name": "issue-workbench", "connection": "github-mcp", "host": host,
		"allow": {"issue_read", "add_issue_comment", "issue_write.label"},
		"reasons": {
			"issue_read": "github-mcp.issue_read for agent issue-workbench is allowed: agent states that it reads and summarizes issues.",
			"add_issue_comment": "github-mcp.add_issue_comment for agent issue-workbench is allowed: agent states that it posts comments.",
			"issue_write.label": "github-mcp.issue_write.label for agent issue-workbench is allowed: agent states that it hands an issue back and forth with its labels.",
			"issue_write.close": "github-mcp.issue_write.close for agent issue-workbench is denied: the policy prohibits it, agent specifically states that it never closes issues.",
		},
	},
	"agent_q": {
		"name": "qa-signoff", "connection": "github-mcp", "host": host,
		"allow": {"issue_read", "add_issue_comment", "issue_write.close"},
		"reasons": {
			"issue_read": "github-mcp.issue_read for agent qa-signoff is allowed: agent states that it reads an issue and its history.",
			"add_issue_comment": "github-mcp.add_issue_comment for agent qa-signoff is allowed: agent states that it posts the sign-off comment.",
			"issue_write.close": "github-mcp.issue_write.close for agent qa-signoff is allowed: agent states that it closes the issue as completed, or as not planned.",
			"issue_write.label": "github-mcp.issue_write.label for agent qa-signoff is denied: the policy prohibits it, agent specifically states that it never relabels issues.",
		},
	},
}

no_company := {"version": 0, "status": "applied", "role_rules": [], "data_rules": []}

company := {
	"version": 1,
	"status": "applied",
	"role_rules": [
		{
			"id": "R1", "text": "Developers and testers may read issues, comment on them and label them.",
			"operations": ["github-mcp.issue_read", "github-mcp.add_issue_comment", "github-mcp.issue_write.label"],
			"agents": [], "roles": ["developer", "tester"], "holders": ["alice", "bob"],
		},
		{
			"id": "R2", "text": "Only testers close an issue.",
			"operations": ["github-mcp.issue_write.close"],
			"agents": [], "roles": ["tester"], "holders": ["bob"],
		},
		{
			"id": "R3", "text": "The QA sign-off agent is used by testers only.",
			"operations": [], "agents": ["agent-q"], "roles": ["tester"], "holders": ["bob"],
		},
	],
	"data_rules": [{"text": "Personal data and restricted data must not be sent to an untrusted external destination.", "dg_rules": ["DG-001", "DG-004"]}],
}

call(agent, tool, ops, people) := {"agent": agent, "host": host, "rpc": "tools/call", "tool": tool, "operations": ops, "people": people}

decide(c, inp) := d if {
	d := mcp.decision with input as inp
		with data.dam.agents as agents
		with data.dam.generated as generated
		with data.dam.company as c
}

# Act 2: from each agent's description alone.

test_act2_workbench_ping_pong_allowed if {
	decide(no_company, call("agent-w", "issue_write", ["issue_write.label"], ["alice"])).allow
	decide(no_company, call("agent-w", "issue_write", ["issue_write.label"], ["bob"])).allow
}

test_act2_workbench_close_denied_by_description if {
	d := decide(no_company, call("agent-w", "issue_write", ["issue_write.close"], ["bob"]))
	not d.allow
	contains(d.reason, "never closes issues")
}

test_act2_signoff_reassign_denied_by_description if {
	d := decide(no_company, call("agent-q", "issue_write", ["issue_write.label"], ["bob"]))
	not d.allow
	contains(d.reason, "never relabels")
}

test_act2_alice_closes_through_signoff if {
	d := decide(no_company, call("agent-q", "issue_write", ["issue_write.close"], ["alice"]))
	d.allow
	d.data_rules == {}
}

# Act 3: plus the company security policy.

test_act3_alice_denied_signoff_by_company_policy if {
	d := decide(company, call("agent-q", "issue_write", ["issue_write.close"], ["alice"]))
	not d.allow
	contains(d.reason, "asked by alice")
	contains(d.reason, "company security policy states \"The QA sign-off agent is used by testers only.\"")
}

test_act3_bob_signs_off_with_data_rules if {
	d := decide(company, call("agent-q", "issue_write", ["issue_write.close"], ["bob"]))
	d.allow
	d.data_rules["DG-001"] == "Personal data and restricted data must not be sent to an untrusted external destination."
}

test_act3_ping_pong_still_allowed if {
	decide(company, call("agent-w", "issue_write", ["issue_write.label"], ["alice"])).allow
	decide(company, call("agent-w", "issue_write", ["issue_write.label"], ["bob"])).allow
}

test_act3_description_still_decides_first if {
	d := decide(company, call("agent-w", "issue_write", ["issue_write.close"], ["bob"]))
	not d.allow
	contains(d.reason, "never closes issues")
}

# Whom the agent acts for.

test_two_people_get_only_what_both_may if {
	not decide(company, call("agent-q", "issue_write", ["issue_write.close"], ["alice", "bob"])).allow
}

test_unknown_people_denied_where_a_role_rule_covers if {
	d := decide(company, call("agent-q", "issue_read", ["issue_read"], null))
	not d.allow
	contains(d.reason, "who asked for it could not be told")
}

test_no_person_denied_where_a_role_rule_covers if {
	d := decide(company, call("agent-q", "issue_read", ["issue_read"], []))
	not d.allow
	contains(d.reason, "no signed-in person asked for it")
}

# The company policy's own state.

test_pending_company_policy_denies if {
	d := decide({"version": 2, "status": "pending"}, call("agent-w", "issue_read", ["issue_read"], ["alice"]))
	not d.allow
	contains(d.reason, "being applied")
}

test_unavailable_company_policy_denies if {
	not decide({"status": "unavailable"}, call("agent-w", "issue_read", ["issue_read"], ["alice"])).allow
}

test_a_call_doing_two_operations_needs_both if {
	not decide(no_company, call("agent-q", "issue_write", ["issue_write.close", "issue_write.label"], ["bob"])).allow
}

test_not_a_tool_call_passes if {
	decide(company, {"agent": "agent-q", "host": host, "rpc": "tools/list"}).allow
}
