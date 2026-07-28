"""Entities extracted from the real single-turn travel-advisor trace.

This pins the P-interactions graph algorithm (ADR-0026) against the real trace
`186b5703acde0532adc6940e9eda3cb1` — a single clarifying turn: the
`travel-advisor` agent (OpenAI Agents SDK, fronted by an A2A server) makes one
LLM call and asks the user for more detail, so no tool is invoked.

What the trace contains (56 spans, `openinference.instrumentation.openai_agents`
bridged by observed a2a/httpx/starlette transport):

  * ONE agent, `travel-advisor`, which is *observed*: openai_agents emits a
    proper agent/run span (unlike the anthropic bare-leaf `patent_agent_*` traces
    where the agent is inferred).
  * one LLM (`claude-haiku-4-5-20251001`) — remote, emits no spans, so it is a
    one-sided observation stubbed as an inferred peer.
  * NO tools — the three `mcp_tools` spans are framework-internal tool discovery
    (`kagenti.node.type=framework-internal`), not calls.

Two entities total: one observed agent + one inferred LLM.

Entity identity is the `natural_key` (`agent:` / `llm:` prefixes). We assert on
the stable, ADR-0026-sanctioned signals — `natural_key`, `inferred`,
`detected_from` — never the display string (ADR-0026: "Inferred identity is a
boolean field, not a label convention").

STAGE 1 — this file validates the *entity set* only. Interaction pairs/counts,
bidirectionality, error signals, payloads/evidence, and ordering are in
`test_interactions.py`.
"""

from __future__ import annotations

from data_governance.processors.interactions.graph.extractor import extract

# Expected entity set: natural_key -> inferred?  (the stable, ADR-0026
# sanctioned signals — never the display string).
#
# GROUND TRUTH — HUMAN-VALIDATED. DO NOT EDIT WITHOUT CONFIRMATION BY A HUMAN.
# This set was reviewed and confirmed correct by a human against the real trace:
# exactly the one observed agent and the one inferred LLM it contains, and no
# tools. It is the oracle, not a snapshot of current output. If the extractor
# ever produces a different set, the extractor regressed — fix the code, not this
# expectation.
EXPECTED_ENTITIES = {
    # The single agent — OBSERVED: openai_agents emits a proper agent/run span.
    "agent:travel-advisor": False,
    # The one LLM the agent calls — one-sided (the model emits no spans), stubbed
    # as an inferred peer.
    "llm:claude-haiku-4-5-20251001": True,
}


def test_entity_set(clarifying_turn_trace_spans):
    """The full entity set matches the agent + LLM of the trace (2 entities, no tools)."""
    result = extract(clarifying_turn_trace_spans)

    by_key = {e.natural_key: e for e in result.entities}
    assert len(by_key) == len(result.entities), "natural_keys not unique"

    assert {k: v.inferred for k, v in by_key.items()} == EXPECTED_ENTITIES


def test_observed_agent_inferred_llm(clarifying_turn_trace_spans):
    """The agent is the only observed entity; the LLM is inferred; no tools exist.

    This is the contrast with `patent_agent_*` (where the agent is inferred): here
    the openai_agents run span makes the agent observed. And the contrast with a
    tool-using trace: the `mcp_tools` discovery spans produce NO `tool:` entity.
    """
    result = extract(clarifying_turn_trace_spans)

    observed = {e.natural_key for e in result.entities if not e.inferred}
    inferred = {e.natural_key for e in result.entities if e.inferred}

    assert observed == {"agent:travel-advisor"}
    assert inferred == {"llm:claude-haiku-4-5-20251001"}

    # No tool entities were created from the framework-internal discovery spans.
    assert not any(e.natural_key.startswith("tool:") for e in result.entities)

    # detected_from and the inferred boolean agree on every entity — ADR-0026
    # "Inferred identity is a boolean field, not a label convention".
    for e in result.entities:
        expected = "inferred" if e.inferred else "observed"
        assert e.detected_from == expected
