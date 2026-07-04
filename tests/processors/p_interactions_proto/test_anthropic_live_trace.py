"""End-to-end pinning of a *real* 3-turn anthropic trace captured from the
deployment (`trace_8e8d7b1e`, trace id 8e8d7b1ee84bd8995e3c951f659292a2).

This is the trace the in-pod CLI runs are verified against. Unlike the
hand-built two-turn `trace_anthropic_tool_calls`, it has three turns, which is
what surfaced the Step-4 ordering/timing bugs fixed in this branch:

  * merged output-derived tool calls must order AFTER their originating LLM
    (the "database before the first LLM" bug — originating-order, not min);
  * each agent↔LLM turn — call AND response — must keep its own span's
    started_at (timing follows the anchor; the response anchors on the observed
    endpoint, not the merged peer's first-turn span).

The fixture was captured verbatim from the `spans` table (ADR-0006: the row IS
the Span), so this test exercises the same data the live CLI does, as a pure
function over the snapshot (no Postgres).
"""

from __future__ import annotations

from data_governance.processors.p_interactions_proto.extractor import extract

from .conftest import load_trace_spans

LIVE_TRACE = "trace_8e8d7b1e"


def _spans():
    return load_trace_spans(LIVE_TRACE)


def _ent(result, entity_id):
    return next(e for e in result.entities if e.id == entity_id)


# Expected entity set: natural_key -> inferred?  This trace has only bare leaf
# LLM spans under a transport parent (no agent/run wrapper), so the agent itself
# is *inferred* by Step 2.c case 4 — hence `patent-assistant` is inferred. The
# LLM and both tools are also inferred from the anthropic LLM spans.
EXPECTED_ENTITIES = {
    "agent:patent-assistant": True,
    "llm:claude-haiku-4-5-20251001": True,
    "tool:database": True,
    "tool:file": True,
}


def test_entity_set():
    result = extract(_spans())
    by_key = {e.natural_key: e for e in result.entities}
    assert len(by_key) == len(result.entities), "natural_keys not unique"
    assert {k: v.inferred for k, v in by_key.items()} == EXPECTED_ENTITIES


def test_interaction_count():
    """Ten interactions: each of the three agent↔LLM turns (call + response)
    plus the two merged tool calls (`database`, `file`) call + response."""
    result = extract(_spans())
    assert len(result.interactions) == 10


def test_merged_tool_calls_order_after_their_llm():
    """`database` and `file` are created as LLM *output* and replayed on later
    spans' *input*; each collapses to one interaction that must sort AFTER its
    originating turn's LLM call (positive order band), never before it."""
    result = extract(_spans())

    tool_calls = [
        ix for ix in result.interactions
        if _ent(result, ix.caller_entity_id).natural_key == "agent:patent-assistant"
        and _ent(result, ix.callee_entity_id).natural_key.startswith("tool:")
    ]
    assert tool_calls, "expected agent→tool interactions"
    assert all(ix.order > 0 for ix in tool_calls), (
        f"merged output-derived tool calls must keep a positive (after-LLM) band; "
        f"got {[(_ent(result, ix.callee_entity_id).natural_key, ix.order) for ix in tool_calls]}"
    )

    for tc in tool_calls:
        llm_same_span = [
            ix for ix in result.interactions
            if ix.started_at == tc.started_at
            and _ent(result, ix.caller_entity_id).natural_key == "agent:patent-assistant"
            and _ent(result, ix.callee_entity_id).natural_key.startswith("llm:")
        ]
        assert llm_same_span, "expected an agent→LLM call sharing the tool call's turn"
        assert tc.order > llm_same_span[0].order, (
            "output-derived tool call must order after its originating LLM call"
        )


def test_each_turn_keeps_distinct_times_both_directions():
    """The three agent↔LLM turns ran at distinct times; the Step-4 LLM-peer
    merge must not collapse them. Both the agent→LLM calls and the LLM→agent
    responses must each carry three distinct started_at values."""
    result = extract(_spans())

    calls = [
        ix for ix in result.interactions
        if _ent(result, ix.caller_entity_id).natural_key == "agent:patent-assistant"
        and _ent(result, ix.callee_entity_id).natural_key.startswith("llm:")
    ]
    responses = [
        ix for ix in result.interactions
        if _ent(result, ix.caller_entity_id).natural_key.startswith("llm:")
        and _ent(result, ix.callee_entity_id).natural_key == "agent:patent-assistant"
    ]
    assert len(calls) == 3 and len(responses) == 3
    assert len({ix.started_at for ix in calls}) == 3, "agent→LLM turns must keep distinct times"
    assert len({ix.started_at for ix in responses}) == 3, "LLM→agent responses must keep distinct times"


def test_interaction_time_follows_anchor_span():
    """Every interaction's started_at equals its anchor span's start time (not
    an aggregate over pooled evidence spans, which after the LLM-peer merge can
    include another turn's span)."""
    result = extract(_spans())
    span_started = {s.span_id: s.started_at for s in _spans()}
    anchor_of = {
        s.interaction_id: s.span_id
        for s in result.interaction_spans if s.is_anchor
    }
    for ix in result.interactions:
        anchor = anchor_of.get(ix.id)
        assert anchor is not None, f"interaction {ix.id} has no anchor span"
        assert ix.started_at == span_started[anchor], (
            f"{ix.summary!r} started_at {ix.started_at} != anchor span "
            f"{anchor[-6:]} time {span_started[anchor]}"
        )


def test_no_two_interactions_stack_on_one_sort_key():
    """No (started_at, order) key carries more than one interaction in the same
    caller→callee direction — the same-band stacking the timing fixes removed."""
    result = extract(_spans())
    seen: dict[tuple, int] = {}
    for ix in result.interactions:
        key = (ix.started_at, ix.order, ix.caller_entity_id, ix.callee_entity_id)
        seen[key] = seen.get(key, 0) + 1
    stacked = {k: n for k, n in seen.items() if n > 1}
    assert not stacked, f"interactions stacked on a single sort key: {stacked}"
