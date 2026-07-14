"""Entities extracted from the real single-agent anthropic patent_search trace.

This pins the P-interactions graph algorithm (ADR-0007) against the real trace
`4ee0239356d61584bb4c3b6965041788` — the **anthropic bare-leaf, multi-tool**
case, the no-replay counterpart to `patent_agent_I`.

What the trace contains (15 spans, one instrumentation scope —
`openinference.instrumentation.anthropic` — bridged by observed httpx/starlette
transport):

  * ONE agent, `patent_search`, which is *inferred* rather than observed:
    anthropic emits only bare leaf LLM spans (`messages.create`) that share a
    single transport (`POST /`) parent, with no run/agent/wrapper span. Per
    ADR-0007 Step 2.c case 4 (`infer_agent_from_bare_leaf_llms`) an agent node is
    inferred from that shared-parent structure.
  * one LLM (`claude-haiku-4-5-20251001`) — remote, emits no spans, so it is a
    one-sided observation stubbed as an inferred peer (combined across turns).
  * two tools (`file`, `web_search`) the agent's LLM output asks for — each a
    one-sided observation, stubbed as an inferred peer, and each invoked exactly
    once (no replay, unlike `patent_agent_I`).

As in `patent_agent_I`, EVERY entity is inferred: the agent (case 4), the LLM,
and the two tools. There are zero observed entities. Four entities total.

Entity identity is the `natural_key` (`agent:` / `tool:` / `llm:` prefixes). We
assert on the stable, ADR-0007-sanctioned signals — `natural_key`, `inferred`,
`detected_from` — never the display string (ADR-0007: "Inferred identity is a
boolean field, not a label convention").

STAGE 1 — this file validates the *entity set* only. Interaction pairs/counts,
bidirectionality, error signals, payloads/evidence are in `test_interactions.py`.
"""

from __future__ import annotations

from data_governance.processors.p_interactions_proto.extractor import extract

# Expected entity set: natural_key -> inferred?  (the stable, ADR-0007
# sanctioned signals — never the display string).
#
# GROUND TRUTH — DO NOT EDIT WITHOUT CONFIRMATION BY A HUMAN. This set was
# reviewed and confirmed correct by a human against the real trace: these are
# exactly the agent, LLM, and tools it contains. It is the oracle, not a
# snapshot of current output. If the extractor ever produces a different set,
# the extractor regressed — fix the code, not this expectation.
EXPECTED_ENTITIES = {
    # The single agent — INFERRED, not observed: anthropic emits only bare leaf
    # LLM spans, so the agent is inferred from their shared transport parent
    # (ADR-0007 Step 2.c case 4, `infer_agent_from_bare_leaf_llms`).
    "agent:patent_search": True,
    # The one LLM the agent calls — one-sided (the model emits no spans),
    # stubbed per turn and combined into a single entity.
    "llm:claude-haiku-4-5-20251001": True,
    # The two tools the LLM output asks for — one-sided observations, stubbed.
    # Each is called exactly once (no replay, so no Step 2.d merge).
    "tool:file": True,
    "tool:web_search": True,
}


def test_entity_set(patent_search_trace_spans):
    """The full entity set matches the agent/LLM/tools of the trace (4 entities)."""
    result = extract(patent_search_trace_spans)

    by_key = {e.natural_key: e for e in result.entities}
    assert len(by_key) == len(result.entities), "natural_keys not unique"

    assert {k: v.inferred for k, v in by_key.items()} == EXPECTED_ENTITIES


def test_all_entities_inferred(patent_search_trace_spans):
    """Every entity is inferred — there are NO observed entities in this trace.

    As with `patent_agent_I`, the sole agent is a case-4 bare-leaf inference and
    the LLM and both tools are one-sided stubs, so the observed set is empty.
    """
    result = extract(patent_search_trace_spans)

    observed = {e.natural_key for e in result.entities if not e.inferred}
    inferred = {e.natural_key for e in result.entities if e.inferred}

    assert observed == set()
    assert inferred == {
        "agent:patent_search",
        "llm:claude-haiku-4-5-20251001",
        "tool:file",
        "tool:web_search",
    }

    # detected_from and the inferred boolean agree on every entity — ADR-0007
    # "Inferred identity is a boolean field, not a label convention".
    for e in result.entities:
        expected = "inferred" if e.inferred else "observed"
        assert e.detected_from == expected


def test_single_llm_entity_across_turns(patent_search_trace_spans):
    """The one model the agent calls collapses to a SINGLE llm entity.

    The agent calls `claude-haiku-4-5-20251001` on all three turns. The model is
    remote and emits no spans, so each turn yields an inferred `llm:` peer; the
    Step 3.d semantic combine keys on the callee's typed identity, so all three
    per-turn stubs converge to exactly one entity.
    """
    result = extract(patent_search_trace_spans)

    llms = [e for e in result.entities if e.natural_key.startswith("llm:")]
    assert [e.natural_key for e in llms] == ["llm:claude-haiku-4-5-20251001"]
    assert llms[0].inferred is True
