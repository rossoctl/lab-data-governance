"""Entities/interactions for a claude_agent_sdk tool/sub-agent dispatch trace.

Pins ADR-0026 Step 2.b case 2 (the `ClaudeAgentSDK.{tool_name}` dispatch) and
the Step 2.d kind+role-matched edge-coloring rule end-to-end.

The fixture is a `ClaudeAgentSDK.query` combined agent→LLM span with three
`ClaudeAgentSDK.{tool_name}` dispatch children: two to `get_weather` and one to
`book_flight`. None of the dispatched targets emit a span of their own.

Expected behavior:

  * The agent's `query` span and its three dispatch spans are all SOURCE/BOTH
    boundaries on one Gray chain. Under the new Step 2.d rule the Gray edges
    between them stay Gray (BOTH↔SOURCE is not a SOURCE↔TARGET call pair), so
    they collapse into a single observed agent entity — not four.
  * Each dispatch's unobserved callee is materialised as an inferred TARGET
    peer (Step 2.b), keyed on its `natural_key` (`agent:get_weather`,
    `agent:book_flight`).
  * The two `get_weather` dispatches converge to one inferred entity
    (Step 3.a phase 2), so the entity set has a single `agent:get_weather`.
  * The combined `query` span still yields the `llm:<model>` peer.
"""

from __future__ import annotations

from data_governance.processors.interactions.graph.extractor import extract

# natural_key -> inferred?  (the ADR-0026 sanctioned boolean signal).
EXPECTED_ENTITIES = {
    "dl-demo-claude-agent": False,        # the observed agent (query + dispatches)
    "llm:claude-3-7-sonnet": False,       # combined-span target (observed-via-same-span)
    "agent:get_weather": True,            # inferred peer, two dispatches → one entity
    "agent:book_flight": True,            # inferred peer, one dispatch
}


def test_claude_subagent_entity_set(claude_subagent_trace_spans):
    """Dispatch targets become distinct (inferred) entities; the agent stays one."""
    result = extract(claude_subagent_trace_spans)

    by_key = {e.natural_key: e for e in result.entities}
    assert len(by_key) == len(result.entities), "natural_keys not unique"
    assert {k: v.inferred for k, v in by_key.items()} == EXPECTED_ENTITIES


def test_repeated_dispatch_converges_to_one_entity(claude_subagent_trace_spans):
    """Two get_weather dispatches yield ONE agent:get_weather entity, but both
    call sites survive as separate interactions (Step 3.a phase 2 preserves
    every edge)."""
    result = extract(claude_subagent_trace_spans)

    weather = [e for e in result.entities if e.natural_key == "agent:get_weather"]
    assert len(weather) == 1

    # Each dispatch is a source→target + target→source Black edge pair, so two
    # get_weather dispatches → 2 caller→callee + 2 callee→caller interactions.
    summaries = [i.summary for i in result.interactions]
    assert summaries.count("dl-demo-claude-agent → agent:get_weather") == 2
    assert summaries.count("agent:get_weather → dl-demo-claude-agent") == 2


def test_agent_dispatch_spans_not_split_into_separate_entities(
    claude_subagent_trace_spans,
):
    """The agent's own dispatch spans stay folded into the single agent entity:
    exactly one observed entity, named for the agent service."""
    result = extract(claude_subagent_trace_spans)

    observed = [e for e in result.entities if not e.inferred]
    assert {e.natural_key for e in observed} == {
        "dl-demo-claude-agent",
        "llm:claude-3-7-sonnet",
    }
