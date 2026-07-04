"""Entities and interactions extracted from a single-LLM-call trace.

This pins the P-interactions graph algorithm (ADR-0007) end-to-end against the
real trace `186b5703acde0532adc6940e9eda3cb1`: spans in → entity set + the
interactions between them.

Where the canonical trace (`test_canonical_trace.py`) exercises an agent
calling an LLM *and* three tools, this trace is the degenerate-but-real case
of a single clarifying turn — the user said "Yes USA, August 8th 2022 for
about 10 days" and the agent's one LLM call replied by asking for the missing
origin/destination cities. No tool is ever invoked.

What the trace contains (from the openinference openai_agents spans):

  * one observed agent — service `dl-demo-travel-advisor`;
  * one LLM it calls once — `claude-haiku-4-5-20251001`
    (`generation` span, kagenti.edge.kind=agent.llm_call).

The three `mcp_tools` spans in the trace are framework-internal tool
*discovery* (`kagenti.node.type=framework-internal`), listing the tools the
agent has available — `search_destinations`, `get_weather`, `get_flights` —
not invocations. Per the trace analysis they produce no interactions and are
deliberately *not* promoted to tool entities: this test asserts that the
extractor agrees (no `tool:` entities, no tool interactions).

As in the canonical test, entity identity is the `natural_key`
(`llm:` / service-name); `display_name` is `"unknown"` at Step 3.b, so we
assert on the key, not the display name.
"""

from __future__ import annotations

from data_governance.processors.p_interactions_proto.extractor import extract

# Expected entity set: natural_key -> inferred?  Only the observed agent and
# the single LLM peer it calls. No tools — the mcp_tools spans are discovery,
# not calls.
EXPECTED_ENTITIES = {
    # The observed agent: the only side of the call that emitted spans.
    "agent:travel-advisor": False,
    # The LLM it calls once — unobserved peer, stubbed (inferred).
    "llm:claude-haiku-4-5-20251001": True,
}


def test_clarifying_turn_entity_set(clarifying_turn_trace_spans):
    """The full entity set is exactly the agent and its one LLM peer."""
    result = extract(clarifying_turn_trace_spans)

    by_key = {e.natural_key: e for e in result.entities}
    assert len(by_key) == len(result.entities), "natural_keys not unique"

    assert {k: v.inferred for k, v in by_key.items()} == EXPECTED_ENTITIES


def test_one_observed_agent_one_inferred_llm(clarifying_turn_trace_spans):
    """Exactly one observed entity (the agent); the LLM is inferred."""
    result = extract(clarifying_turn_trace_spans)

    observed = [e for e in result.entities if not e.inferred]
    inferred = [e for e in result.entities if e.inferred]

    assert [e.natural_key for e in observed] == ["agent:travel-advisor"]
    assert {e.natural_key for e in inferred} == {
        "llm:claude-haiku-4-5-20251001",
    }

    # Inferred identity is a boolean field with detected_from="inferred"
    # (ADR-0007), not a label convention.
    for e in inferred:
        assert e.detected_from == "inferred"


def test_no_tool_entities(clarifying_turn_trace_spans):
    """The framework-internal mcp_tools spans yield no tool entities.

    The trace advertises three tools (search_destinations, get_weather,
    get_flights) via discovery spans, but none is invoked this turn, so the
    extractor must not promote any of them to a `tool:` entity.
    """
    result = extract(clarifying_turn_trace_spans)
    keys = {e.natural_key for e in result.entities}

    assert {k for k in keys if k.startswith("tool:")} == set()
    assert {k for k in keys if k.startswith("llm:")} == {
        "llm:claude-haiku-4-5-20251001",
    }


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------
#
# One call site (the agent's single LLM call) → one pair of Black edges:
# agent->LLM (request) and LLM->agent (response). No tool calls, so that is
# the whole interaction set.
EXPECTED_DIRECTED_PAIRS = {
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"): 1,
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"): 1,
}


def _directed_pairs(result):
    """{(caller_key, callee_key): count} over all interactions."""
    by_id = {e.id: e for e in result.entities}
    counts: dict[tuple[str, str], int] = {}
    for ix in result.interactions:
        caller = by_id[ix.caller_entity_id].natural_key
        callee = by_id[ix.callee_entity_id].natural_key
        counts[(caller, callee)] = counts.get((caller, callee), 0) + 1
    return counts


def test_interaction_pairs_and_counts(clarifying_turn_trace_spans):
    """The single LLM call site is one interaction per direction."""
    result = extract(clarifying_turn_trace_spans)

    assert len(result.interactions) == 2
    assert _directed_pairs(result) == EXPECTED_DIRECTED_PAIRS


def test_interactions_are_bidirectional(clarifying_turn_trace_spans):
    """The call site appears as both agent->LLM and LLM->agent."""
    result = extract(clarifying_turn_trace_spans)
    pairs = _directed_pairs(result)

    for (caller, callee), n in pairs.items():
        assert pairs.get((callee, caller)) == n, (
            f"{caller} -> {callee} ({n}) has no matching return edge"
        )


def test_no_interaction_errored(clarifying_turn_trace_spans):
    """The LLM call succeeded — every interaction is clean (no error)."""
    result = extract(clarifying_turn_trace_spans)

    assert all(ix.error is False for ix in result.interactions)


def test_interactions_have_payloads_and_evidence(clarifying_turn_trace_spans):
    """Both interactions carry request+response payloads and a span anchor."""
    result = extract(clarifying_turn_trace_spans)

    for ix in result.interactions:
        assert ix.request_payload_hash is not None
        assert ix.response_payload_hash is not None

    # One evidence span per interaction, and it is the anchor.
    assert len(result.interaction_spans) == len(result.interactions)
    assert all(s.is_anchor for s in result.interaction_spans)

    # Two distinct payloads: the chat prompt and the completion.
    assert len(result.payloads) == 2
