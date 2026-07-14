"""Entities extracted from the real single-agent anthropic patent-assistant trace.

This pins the P-interactions graph algorithm (ADR-0007) against the real trace
`8e8d7b1ee84bd8995e3c951f659292a2` — the **anthropic bare-leaf** case, the
counterpart to `travel_agent_II`'s four fully-observed agents.

What the trace contains (15 spans, one instrumentation scope —
`openinference.instrumentation.anthropic` — bridged by observed httpx/starlette
transport):

  * ONE agent, `patent-assistant`, which is *inferred* rather than observed:
    anthropic emits only bare leaf LLM spans (`messages.create`) that share a
    single transport (`POST /`) parent, with no run/agent/wrapper span. Per
    ADR-0007 Step 2.c case 4 (`infer_agent_from_bare_leaf_llms`) an agent node is
    inferred from that shared-parent structure — so the *agent* is what was
    inferred even though it absorbs observed LLM spans.
  * one LLM (`claude-haiku-4-5-20251001`) — remote, emits no spans, so it is a
    one-sided observation stubbed as an inferred peer (combined across the three
    turns into one entity).
  * two tools (`database`, `file`) the agent's LLM output asks for — each a
    one-sided observation, stubbed as an inferred peer.

Unlike `travel_agent_II` — where the four agents are OBSERVED and only the tools
and LLM are inferred — EVERY entity here is inferred: the agent (case 4), the
LLM, and the two tools. There are zero observed entities. Four entities total.

Entity identity is the `natural_key` (`agent:` / `tool:` / `llm:` prefixes). We
assert on the stable, ADR-0007-sanctioned signals — `natural_key`, `inferred`,
`detected_from` — never the display string (ADR-0007: "Inferred identity is a
boolean field, not a label convention").

STAGE 1 — this file validates the *entity set* only. Interaction pairs/counts,
bidirectionality, error signals, payloads/evidence are in `test_interactions.py`
(mirroring `travel_agent_II`, which pins entities first).
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
    "agent:patent-assistant": True,
    # The one LLM the agent calls — one-sided (the model emits no spans),
    # stubbed per turn and combined into a single entity.
    "llm:claude-haiku-4-5-20251001": True,
    # The two tools the LLM output asks for — one-sided observations, stubbed.
    "tool:database": True,
    "tool:file": True,
}


def test_entity_set(patent_agent_trace_spans):
    """The full entity set matches the agent/LLM/tools of the trace (4 entities)."""
    result = extract(patent_agent_trace_spans)

    by_key = {e.natural_key: e for e in result.entities}
    assert len(by_key) == len(result.entities), "natural_keys not unique"

    assert {k: v.inferred for k, v in by_key.items()} == EXPECTED_ENTITIES


def test_all_entities_inferred(patent_agent_trace_spans):
    """Every entity is inferred — there are NO observed entities in this trace.

    This is the defining difference from `travel_agent_II`: there the four agents
    are observed. Here the sole agent is a case-4 bare-leaf inference, and the LLM
    and both tools are one-sided stubs, so the observed set is empty.
    """
    result = extract(patent_agent_trace_spans)

    observed = {e.natural_key for e in result.entities if not e.inferred}
    inferred = {e.natural_key for e in result.entities if e.inferred}

    assert observed == set()
    assert inferred == {
        "agent:patent-assistant",
        "llm:claude-haiku-4-5-20251001",
        "tool:database",
        "tool:file",
    }

    # detected_from and the inferred boolean agree on every entity — ADR-0007
    # "Inferred identity is a boolean field, not a label convention".
    for e in result.entities:
        expected = "inferred" if e.inferred else "observed"
        assert e.detected_from == expected


def test_single_llm_entity_across_turns(patent_agent_trace_spans):
    """The one model the agent calls collapses to a SINGLE llm entity.

    The agent calls `claude-haiku-4-5-20251001` on all three turns. The model is
    remote and emits no spans, so each turn yields an inferred `llm:` peer; the
    Step 3.d semantic combine keys on the callee's typed identity, so all three
    per-turn stubs converge to exactly one entity.
    """
    result = extract(patent_agent_trace_spans)

    llms = [e for e in result.entities if e.natural_key.startswith("llm:")]
    assert [e.natural_key for e in llms] == ["llm:claude-haiku-4-5-20251001"]
    assert llms[0].inferred is True
