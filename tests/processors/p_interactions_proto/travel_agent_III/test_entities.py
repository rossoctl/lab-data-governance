"""Entities extracted from the canonical travel-advisor trace.

This pins the P-interactions graph algorithm (ADR-0025) against the real trace
`8ae1f64d4bb51b750168c6ef1e11a2d8` — the canonical trace referenced throughout
ADR-0025 and the openai_agents v1.4.1 span reference.

What the trace contains (from the openinference openai_agents v1.4.1 spans):

  * one observed agent — the OpenAI Agents SDK runner, the only fully
    instrumented side of every call (service `dl-demo-travel-advisor`);
  * one LLM it calls — `claude-haiku-4-5-20251001`;
  * three tools it calls — `get_flights`, `get_weather`, `search_destinations`.

Per ADR-0025 the LLM and tools are *one-sided* observations: only the caller
(the agent) emitted spans, so Step 2.c stubs each peer as an **inferred** entity
and Step 3.d combines the per-call-site stubs of the same peer into one. The
agent is the lone `observed` entity (openai_agents emits a proper run span,
unlike the anthropic bare-leaf `patent_agent_*` traces where the agent is
inferred). Five entities total.

Entity identity is the `natural_key` (`llm:` / `tool:` / `agent:` prefixes) —
`display_name` is `"unknown"` at Step 3.a for the peers, so we assert on the key
and the `inferred` boolean, never the display string (ADR-0025 "Inferred
identity is a boolean field, not a label convention").

STAGE 1 — this file validates the *entity set* only. Interaction pairs/counts,
bidirectionality, the error signal, payloads/evidence, and ordering are in
`test_interactions.py`.
"""

from __future__ import annotations

from data_governance.processors.p_interactions_proto.extractor import extract

# Expected entity set: natural_key -> inferred?  (the stable, ADR-0025
# sanctioned signals — never the display string, which is "unknown" here).
#
# GROUND TRUTH — HUMAN-VALIDATED. DO NOT EDIT WITHOUT CONFIRMATION BY A HUMAN.
# This set was reviewed and confirmed correct by a human against the real trace:
# exactly the one observed agent, the one inferred LLM, and the three inferred
# tools it contains. It is the oracle, not a snapshot of current output. If the
# extractor ever produces a different set, the extractor regressed — fix the
# code, not this expectation.
EXPECTED_ENTITIES = {
    # The observed agent: the only side of every call that emitted spans.
    "agent:travel-advisor": False,
    # The LLM it calls — unobserved peer, stubbed + combined (inferred).
    "llm:claude-haiku-4-5-20251001": True,
    # The three tools it calls — unobserved peers, stubbed + combined.
    "tool:get_flights": True,
    "tool:get_weather": True,
    "tool:search_destinations": True,
}


def test_entity_set(canonical_trace_spans):
    """The full entity set matches the agent/LLM/tools of the trace (5 entities)."""
    result = extract(canonical_trace_spans)

    by_key = {e.natural_key: e for e in result.entities}
    assert len(by_key) == len(result.entities), "natural_keys not unique"

    assert {k: v.inferred for k, v in by_key.items()} == EXPECTED_ENTITIES


def test_one_observed_agent_rest_inferred(canonical_trace_spans):
    """Exactly one observed entity (the agent); LLM + tools are inferred."""
    result = extract(canonical_trace_spans)

    observed = [e for e in result.entities if not e.inferred]
    inferred = [e for e in result.entities if e.inferred]

    assert [e.natural_key for e in observed] == ["agent:travel-advisor"]
    assert {e.natural_key for e in inferred} == {
        "llm:claude-haiku-4-5-20251001",
        "tool:get_flights",
        "tool:get_weather",
        "tool:search_destinations",
    }

    # detected_from and the inferred boolean agree — ADR-0025 "Inferred identity
    # is a boolean field, not a label convention".
    for e in result.entities:
        expected = "inferred" if e.inferred else "observed"
        assert e.detected_from == expected


def test_one_llm_three_tools(canonical_trace_spans):
    """The agent/tool/LLM breakdown matches the trace analysis: 1 LLM, 3 tools."""
    result = extract(canonical_trace_spans)
    keys = {e.natural_key for e in result.entities}

    llms = {k for k in keys if k.startswith("llm:")}
    tools = {k for k in keys if k.startswith("tool:")}

    assert llms == {"llm:claude-haiku-4-5-20251001"}
    assert tools == {
        "tool:get_flights",
        "tool:get_weather",
        "tool:search_destinations",
    }
