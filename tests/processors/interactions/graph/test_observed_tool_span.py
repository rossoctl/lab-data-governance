"""Regression: an observed tool-execution span owned by the calling agent must
not be absorbed into that agent (ADR-0026 Step 2.d fold guard).

google_adk emits a tool call as an `execute_tool <name>` TOOL span under the
agent, carrying the *agent's own* `service.name`. The Step 2.d "inferred peer →
observed twin" fold keys on the typed identity (`tool:create_booking`), so
without a guard it folds the inferred tool peer of one invocation into the
*other* invocation's agent-owned observed span (a different White+Blue
component). That collapses the tool into the agent and yields nonsensical
`agent → agent` self-interactions, dropping the tool entity entirely.

The fix restricts the fold to twins emitted by a *different service* than the
inferred peer's caller — a same-service "twin" is the caller's own boundary,
not an independently-observed callee. This mirrors the legitimate case in
`test_inferred_observed_merge_trace.py`, where the observed tool runs as its own
`weather-tool` service (≠ the calling `weather-agent`) and *does* fold.
"""

from __future__ import annotations

from data_governance.processors.interactions.graph.extractor import extract


def _pairs(result):
    by_id = {e.id: e for e in result.entities}
    return [
        (by_id[ix.caller_entity_id].natural_key, by_id[ix.callee_entity_id].natural_key)
        for ix in result.interactions
    ]


def test_observed_tool_is_its_own_entity(observed_tool_two_invocations_trace_spans):
    """The agent-owned execute_tool span surfaces a distinct tool entity, not a
    self-absorbed agent."""
    result = extract(observed_tool_two_invocations_trace_spans)
    keys = {e.natural_key for e in result.entities}

    assert "tool:create_booking" in keys, keys
    # The two invocations converge to exactly one tool entity (Step 3.a).
    tools = [e for e in result.entities if e.natural_key == "tool:create_booking"]
    assert len(tools) == 1
    assert tools[0].inferred  # no observed *callee* span — the tool is inferred


def test_no_agent_self_interactions(observed_tool_two_invocations_trace_spans):
    """The bug produced agent→agent self-loops; there must be none, and the tool
    call/response must connect the agent to the tool in both directions."""
    result = extract(observed_tool_two_invocations_trace_spans)
    pairs = _pairs(result)

    assert not any(a == b for a, b in pairs), f"self-interaction present: {pairs}"

    agent = "agent:booking_agent"
    tool = "tool:create_booking"
    # Two invocations → two calls + two responses.
    assert pairs.count((agent, tool)) == 2, pairs
    assert pairs.count((tool, agent)) == 2, pairs
