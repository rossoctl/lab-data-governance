"""End-to-end pinning of a second *real* anthropic trace captured from the
deployment (`trace_4ee02393`, trace id 4ee0239356d61584bb4c3b6965041788).

A `patent_search` agent makes three `messages.create` LLM calls; two turns'
outputs each ask for a *different* tool (`file`, then `web_search`), each
invoked exactly once. This is the output-only, no-replay counterpart to the
replay-heavy `trace_8e8d7b1e`: it has the same 4-entity / 10-interaction shape
but no two tool calls merge, so every agent→tool interaction stays in its
positive (after-LLM) band on its own turn.

The fixture was captured verbatim from the `spans` table (ADR-0006: the row IS
the Span), so this test exercises the same data the in-pod CLI does, as a pure
function over the snapshot (no Postgres).
"""

from __future__ import annotations

from data_governance.processors.p_interactions_proto.extractor import extract

from .conftest import load_trace_spans

MULTITOOL_TRACE = "trace_4ee02393"


def _spans():
    return load_trace_spans(MULTITOOL_TRACE)


def _ent(result, entity_id):
    return next(e for e in result.entities if e.id == entity_id)


# Expected entity set: natural_key -> inferred?  Bare leaf LLM spans under a
# transport parent (no agent/run wrapper), so the agent is *inferred* by Step
# 2.c case 4 — hence `patent_search` is inferred, as are the LLM and both tools.
EXPECTED_ENTITIES = {
    "agent:patent_search": True,
    "llm:claude-haiku-4-5-20251001": True,
    "tool:file": True,
    "tool:web_search": True,
}


def test_entity_set():
    result = extract(_spans())
    by_key = {e.natural_key: e for e in result.entities}
    assert len(by_key) == len(result.entities), "natural_keys not unique"
    assert {k: v.inferred for k, v in by_key.items()} == EXPECTED_ENTITIES


def test_interaction_count():
    """Ten interactions: each of the three agent↔LLM turns (call + response)
    plus the two distinct tool calls (`file`, `web_search`) call + response."""
    result = extract(_spans())
    assert len(result.interactions) == 10


def test_tool_calls_order_after_their_llm():
    """Both tools are evidenced as LLM *output* and (unlike `trace_8e8d7b1e`)
    are never replayed, so each stays in the positive (after-LLM) band and
    orders after the LLM call sharing its turn's span."""
    result = extract(_spans())

    tool_calls = [
        ix for ix in result.interactions
        if _ent(result, ix.caller_entity_id).natural_key == "agent:patent_search"
        and _ent(result, ix.callee_entity_id).natural_key.startswith("tool:")
    ]
    assert len(tool_calls) == 2, "expected one call each to file and web_search"
    assert all(ix.order > 0 for ix in tool_calls), (
        f"output-derived tool calls must keep a positive (after-LLM) band; "
        f"got {[(_ent(result, ix.callee_entity_id).natural_key, ix.order) for ix in tool_calls]}"
    )

    for tc in tool_calls:
        llm_same_span = [
            ix for ix in result.interactions
            if ix.started_at == tc.started_at
            and _ent(result, ix.caller_entity_id).natural_key == "agent:patent_search"
            and _ent(result, ix.callee_entity_id).natural_key.startswith("llm:")
        ]
        assert llm_same_span, "expected an agent→LLM call sharing the tool call's turn"
        assert tc.order > llm_same_span[0].order, (
            "output-derived tool call must order after its originating LLM call"
        )


def test_distinct_tools_are_distinct_callees():
    """`file` and `web_search` are different tools, so they must materialise as
    two distinct inferred TARGET peers — the Step-4 inferred↔inferred merge
    converges only same-key peers and must not collapse these."""
    result = extract(_spans())
    tool_callees = {
        _ent(result, ix.callee_entity_id).natural_key
        for ix in result.interactions
        if _ent(result, ix.callee_entity_id).natural_key.startswith("tool:")
    }
    assert tool_callees == {"tool:file", "tool:web_search"}


def test_each_llm_turn_keeps_distinct_times_both_directions():
    """The three agent↔LLM turns ran at distinct times; the Step-4 LLM-peer
    merge must not collapse them. Both the agent→LLM calls and the LLM→agent
    responses must each carry three distinct started_at values."""
    result = extract(_spans())

    calls = [
        ix for ix in result.interactions
        if _ent(result, ix.caller_entity_id).natural_key == "agent:patent_search"
        and _ent(result, ix.callee_entity_id).natural_key.startswith("llm:")
    ]
    responses = [
        ix for ix in result.interactions
        if _ent(result, ix.caller_entity_id).natural_key.startswith("llm:")
        and _ent(result, ix.callee_entity_id).natural_key == "agent:patent_search"
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
    caller→callee direction."""
    result = extract(_spans())
    seen: dict[tuple, int] = {}
    for ix in result.interactions:
        key = (ix.started_at, ix.order, ix.caller_entity_id, ix.callee_entity_id)
        seen[key] = seen.get(key, 0) + 1
    stacked = {k: n for k, n in seen.items() if n > 1}
    assert not stacked, f"interactions stacked on a single sort key: {stacked}"
