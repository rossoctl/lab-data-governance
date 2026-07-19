"""Entities extracted from the real 4-agent cross-framework travel-advisor trace.

This pins the P-interactions graph algorithm (ADR-0025) against the real trace
`e8f7f7c4d7b35e5aa4fbdaaae2a90f75` — the first fixture here where multiple fully
instrumented agents delegate to one another across different frameworks.

What the trace contains (310 spans across three instrumentation scopes —
openai_agents, langchain, google_adk — bridged by observed a2a/httpx/starlette
transport):

  * four observed agents, each the fully instrumented caller side of its calls:
      - agent:travel-advisor  (the entry agent; delegates to the other two)
      - agent:research-agent
      - agent:booking_agent
      - agent:payment-agent
  * six tools those agents call — each a one-sided observation (only the
    caller emitted spans), so Step 2.c stubs each as an *inferred* peer and
    Step 3.a phase 2 combines the per-call-site stubs of the same tool into one.
  * one LLM (`claude-haiku-4-5-20251001`) — all four agents call the same model
    across all three frameworks; the model is remote and emits no spans, so it
    too is a one-sided observation stubbed as an inferred peer. Every agent's
    per-call `llm:` stub combines into ONE shared LLM entity in Step 3.a (the
    combine keys on the callee's typed identity, NOT the caller's framework
    scope — see `combine_identical_entities`).

The travel-advisor's two `delegate_to_*` call sites (`delegate_to_research_agent`,
`delegate_to_booking_agent`) are NOT tools/entities. Each is an openinference
TOOL span that is the shared traceparent root of both an inferred one-sided
callee chain AND an observed a2a/httpx/starlette transport chain reaching the
downstream agent — so ADR-0025 Step 2.d rule 4 collapses them: the observed
downstream agent wins over the synthetic `tool:` peer, and the delegation
surfaces as an `agent → agent` interaction (travel-advisor → research-agent /
booking_agent), not a call to a `tool:delegate_to_*` entity. (See
`_absorb_inferred_call_into_observed_agent` in builder.py.)

Per ADR-0025 the four agents are the `observed` entities; the six tools and
the one LLM are `inferred`. Eleven entities total (4 + 6 + 1).

Entity identity is the `natural_key` (`agent:` / `tool:` prefixes). Although
`display_name` is a real service name here (not "unknown" as in the canonical
trace), we still assert on the stable signals — `natural_key`, `inferred`,
`detected_from` — per ADR-0025 ("Natural-key prefixes are part of the public
algorithm vocabulary"; "Inferred identity is a boolean field, not a label").

STAGE 1 — this file validates the *entity set* only. Interaction pairs/counts,
bidirectionality, error signals, payloads/evidence, and ordering are a later
stage (mirroring `test_canonical_trace.py`, which pins entities first).
"""

from __future__ import annotations

from data_governance.processors.p_interactions_proto.extractor import extract

# Expected entity set: natural_key -> inferred?  (the stable, ADR-0025
# sanctioned signals — never the display string).
#
# GROUND TRUTH — DO NOT EDIT TO MATCH THE CODE. This set was reviewed and
# confirmed correct by a human against the real trace: these are exactly the
# agents, tools, and the one LLM it contains. It is the oracle, not a snapshot
# of current output. If the extractor ever produces a different set, the
# extractor regressed — fix the code, not this expectation.
EXPECTED_ENTITIES = {
    # The four observed agents: the fully instrumented caller side of every
    # call they make.
    "agent:travel-advisor": False,
    "agent:research-agent": False,
    "agent:booking_agent": False,
    "agent:payment-agent": False,
    # The six tools they call — one-sided observations, stubbed + combined.
    # NOTE: the two `delegate_to_*` "tools" are NOT here. Per ADR-0025 Step 2.d
    # rule 4 each delegation call site is the shared root of an inferred callee
    # chain AND an observed transport chain reaching the downstream agent, so the
    # observed agent wins and the delegation becomes an `agent → agent` edge
    # (see test_interactions.py) — there is no `tool:delegate_to_*` entity.
    "tool:get_flights": True,
    "tool:get_weather": True,
    "tool:search_destinations": True,
    "tool:create_booking": True,
    "tool:get_payment_info": True,
    "tool:charge_card": True,
    # The one LLM every agent calls — one-sided (the model emits no spans),
    # stubbed per call and combined cross-framework into a single entity.
    "llm:claude-haiku-4-5-20251001": True,
}


def test_entity_set(multi_agent_delegation_trace_spans):
    """The full entity set matches the agents/tools/LLM of the trace (11 entities)."""
    result = extract(multi_agent_delegation_trace_spans)

    by_key = {e.natural_key: e for e in result.entities}
    assert len(by_key) == len(result.entities), "natural_keys not unique"

    assert {k: v.inferred for k, v in by_key.items()} == EXPECTED_ENTITIES


def test_four_observed_agents_rest_inferred(multi_agent_delegation_trace_spans):
    """The four agents are the only observed entities; tools + LLM are inferred."""
    result = extract(multi_agent_delegation_trace_spans)

    observed = {e.natural_key for e in result.entities if not e.inferred}
    inferred = {e.natural_key for e in result.entities if e.inferred}

    assert observed == {
        "agent:travel-advisor",
        "agent:research-agent",
        "agent:booking_agent",
        "agent:payment-agent",
    }
    assert inferred == {
        "tool:get_flights",
        "tool:get_weather",
        "tool:search_destinations",
        "tool:create_booking",
        "tool:get_payment_info",
        "tool:charge_card",
        "llm:claude-haiku-4-5-20251001",
    }

    # detected_from and the inferred boolean agree on every entity — ADR-0025
    # "Inferred identity is a boolean field, not a label convention".
    for e in result.entities:
        expected = "inferred" if e.inferred else "observed"
        assert e.detected_from == expected


def test_single_llm_entity_across_frameworks(multi_agent_delegation_trace_spans):
    """The one model every agent calls collapses to a SINGLE llm entity.

    All four agents call `claude-haiku-4-5-20251001` across three instrumentation
    scopes (openai_agents / langchain / google_adk). The model is remote and
    emits no spans, so each call yields an inferred `llm:` peer; Step 3.a's
    semantic combine keys on the callee's typed identity — not the caller's
    framework scope — so all those per-call stubs converge to exactly one
    entity. (This is the regression guard for the Step 2.d over-fold and the
    caller-scope over-split that previously erased the LLM entirely.)
    """
    result = extract(multi_agent_delegation_trace_spans)

    llms = [e for e in result.entities if e.natural_key.startswith("llm:")]
    assert [e.natural_key for e in llms] == ["llm:claude-haiku-4-5-20251001"]
    assert llms[0].inferred is True
