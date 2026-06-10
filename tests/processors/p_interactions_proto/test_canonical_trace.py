"""Entities and interactions extracted from the canonical travel-advisor trace.

This pins the P-interactions graph algorithm (ADR-0007) end-to-end against the
real trace `8ae1f64d4bb51b750168c6ef1e11a2d8`: spans in → entity set + the
interactions between them. The expected entities are the agents / tools / LLMs
identified from that trace; the expected interactions are the calls between
them (with direction, count, and error signal).

What the trace contains (from the openinference openai_agents v1.4.1 spans):

  * one observed agent — the OpenAI Agents SDK runner, the only fully
    instrumented side of every call (service `dl-demo-travel-advisor`);
  * one LLM it calls — `claude-haiku-4-5-20251001`;
  * three tools it calls — `get_flights`, `get_weather`, `search_destinations`.

Per ADR-0007 the LLM and tools are *one-sided* observations: only the
caller (the agent) emitted spans, so Step 2.c stubs each peer as a
**synthetic** entity and Step 3.b merges the per-call-site stubs of the same
peer into one. The agent is the lone `observed` entity. Entity identity is the
`natural_key` (`llm:` / `tool:` / `agent:` / service-name) — `display_name` is
always `"unknown"` at Step 3.c, so we assert on the key, not the display name
(ADR-0007 "Natural-key prefixes are part of the public algorithm vocabulary").
"""

from __future__ import annotations

from data_governance.processors.p_interactions_proto.extractor import extract

# Expected entity set: natural_key -> synthetic?  (the two stable, ADR-0007
# sanctioned signals — never the display string, which is "unknown" here).
EXPECTED_ENTITIES = {
    # The observed agent: the only side of every call that emitted spans.
    "dl-demo-travel-advisor": False,
    # The LLM it calls — unobserved peer, stubbed + merged (synthetic).
    "llm:claude-haiku-4-5-20251001": True,
    # The three tools it calls — unobserved peers, stubbed + merged.
    "tool:get_flights": True,
    "tool:get_weather": True,
    "tool:search_destinations": True,
}


def test_canonical_trace_entity_set(canonical_trace_spans):
    """The full entity set matches the agents/tools/LLMs of the trace."""
    result = extract(canonical_trace_spans)

    # natural_key is unique per entity in this trace, so a dict keyed on it
    # loses nothing and gives a readable diff on failure.
    by_key = {e.natural_key: e for e in result.entities}
    assert len(by_key) == len(result.entities), "natural_keys not unique"

    assert {k: v.synthetic for k, v in by_key.items()} == EXPECTED_ENTITIES


def test_one_observed_agent_rest_synthetic(canonical_trace_spans):
    """Exactly one observed entity (the agent); LLM + tools are synthetic."""
    result = extract(canonical_trace_spans)

    observed = [e for e in result.entities if not e.synthetic]
    synthetic = [e for e in result.entities if e.synthetic]

    assert [e.natural_key for e in observed] == ["dl-demo-travel-advisor"]
    assert {e.natural_key for e in synthetic} == {
        "llm:claude-haiku-4-5-20251001",
        "tool:get_flights",
        "tool:get_weather",
        "tool:search_destinations",
    }

    # Synthetic entities are flagged by the boolean field, not the label —
    # ADR-0007 "Synthetic identity is a boolean field, not a label convention".
    for e in synthetic:
        assert e.detected_from == "synthetic"


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


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------
#
# Per ADR-0007 each call site becomes a *pair* of Black edges — source→target
# (request) and target→source (response) — so the agent and each unobserved
# peer are joined in both directions. The extractor turns every Black edge into
# one ProtoInteraction. Counting directed (caller -> callee) pairs:
#
#     5 x  dl-demo-travel-advisor      -> llm:claude-haiku-4-5-20251001
#     2 x  dl-demo-travel-advisor      -> tool:get_flights
#     1 x  dl-demo-travel-advisor      -> tool:get_weather
#     1 x  dl-demo-travel-advisor      -> tool:search_destinations
#   (and the same four mirrored back, peer -> agent)         = 18 total
#
# The agent calls the LLM 5 times and the three tools 2 / 1 / 1 times — the
# per-entity call counts seen directly in the trace's openinference spans.
EXPECTED_DIRECTED_PAIRS = {
    ("dl-demo-travel-advisor", "llm:claude-haiku-4-5-20251001"): 5,
    ("dl-demo-travel-advisor", "tool:get_flights"): 2,
    ("dl-demo-travel-advisor", "tool:get_weather"): 1,
    ("dl-demo-travel-advisor", "tool:search_destinations"): 1,
    ("llm:claude-haiku-4-5-20251001", "dl-demo-travel-advisor"): 5,
    ("tool:get_flights", "dl-demo-travel-advisor"): 2,
    ("tool:get_weather", "dl-demo-travel-advisor"): 1,
    ("tool:search_destinations", "dl-demo-travel-advisor"): 1,
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


def test_interaction_pairs_and_counts(canonical_trace_spans):
    """Each agent<->peer call site is one interaction per direction."""
    result = extract(canonical_trace_spans)

    assert len(result.interactions) == 18
    assert _directed_pairs(result) == EXPECTED_DIRECTED_PAIRS


def test_interactions_are_bidirectional(canonical_trace_spans):
    """Every call site appears as both source->peer and peer->source.

    ADR-0007 Step 2.c adds bidirectional Black edges for each stubbed peer,
    so the forward and return counts for any pair must match.
    """
    result = extract(canonical_trace_spans)
    pairs = _directed_pairs(result)

    for (caller, callee), n in pairs.items():
        assert pairs.get((callee, caller)) == n, (
            f"{caller} -> {callee} ({n}) has no matching return edge"
        )


def test_get_flights_interactions_errored(canonical_trace_spans):
    """The two get_flights calls errored; every other interaction is clean.

    This is the one error signal in the trace — both directions of the
    get_flights call site carry error=True, all 14 other interactions False.
    """
    result = extract(canonical_trace_spans)
    by_id = {e.id: e for e in result.entities}

    errored = {
        tuple(sorted((by_id[ix.caller_entity_id].natural_key,
                      by_id[ix.callee_entity_id].natural_key)))
        for ix in result.interactions
        if ix.error
    }
    assert errored == {("dl-demo-travel-advisor", "tool:get_flights")}

    n_error = sum(1 for ix in result.interactions if ix.error)
    n_clean = sum(1 for ix in result.interactions if ix.error is False)
    assert (n_error, n_clean) == (4, 14)


def test_interactions_have_payloads_and_evidence(canonical_trace_spans):
    """Every interaction carries request+response payloads and a span anchor."""
    result = extract(canonical_trace_spans)

    # Each interaction has both payload hashes populated...
    for ix in result.interactions:
        assert ix.request_payload_hash is not None
        assert ix.response_payload_hash is not None

    # ...one evidence span per interaction, and it is the anchor.
    assert len(result.interaction_spans) == len(result.interactions)
    assert all(s.is_anchor for s in result.interaction_spans)

    # Payloads are content-addressed; identical request/response bodies across
    # interactions collapse to one row, so the unique count is below 2 x 18.
    assert len(result.payloads) == 17
