"""Entities/interactions for an `openinference.instrumentation.anthropic` trace.

Pins ADR-0025 Step 2.b case 3 (tool nodes inferred from an LLM span's output
`tool_calls` attribute) and the dedicated anthropic adapter end-to-end.

The fixture is a `patent-assistant` service making two `messages.create` LLM
calls (raw Anthropic client instrumentation — every span is kind=LLM, no tool
span of its own):

  * LLM span 1 → output tool_call `database`.
  * LLM span 2 → output tool_calls `database` (again) and `file`. It also
    carries the previous `database` call as an *input*-side tool_call (a prior
    turn replayed back). Per the human spec the input-side replay IS inferred —
    ordered *before* span 2's LLM interaction (ordering rule 3) — so `database`
    has three call sites (two output, one input replay). They still converge to
    a single `tool:database` entity (Step 3.a phase 2 combines by key).

Expected behavior:

  * The two anthropic LLM spans are bare leaves under a single transport
    (`POST /`) parent with no agent/run wrapper, so Step 2.c case 4 infers a
    single agent node and rewires transport→agent→each-LLM. At the fuse the
    agent and its LLM spans form one `patent-assistant` entity, marked
    **inferred** (the agent itself was never observed).
  * The combined model peer `llm:claude-haiku-4-5-20251001` is the inferred
    one-sided stub of the LLM call (Step 2.c one-sided stubbing); the two
    spans' stubs converge to one entity.
  * Each distinct output tool becomes one inferred `tool:<name>` entity:
    `tool:database` (converged from three call sites — two output, one input
    replay — via the Step 2.d/3.b same-entity merge) and `tool:file`.
  * The agent→tool interaction's request payload is the tool call's
    `arguments` (tool_call_arguments), not the LLM completion.
"""

from __future__ import annotations

import json

from data_governance.processors.interactions.graph.extractor import extract

from .conftest import load_trace_spans

ANTHROPIC_TRACE = "trace_anthropic_tool_calls"


def _spans():
    return load_trace_spans(ANTHROPIC_TRACE)


def test_inferred_tools_from_tool_calls():
    """`tool:database` and `tool:file` appear as inferred entities; nothing
    else tool-shaped. `database` converges to ONE entity despite two output
    call sites, and the input-side replay does not add a third."""
    result = extract(_spans())
    tools = sorted(e.natural_key for e in result.entities if e.natural_key.startswith("tool:"))
    assert tools == ["tool:database", "tool:file"]
    # Both are inferred (no tool span was ever observed in this framework).
    for e in result.entities:
        if e.natural_key.startswith("tool:"):
            assert e.inferred is True


def test_llm_peer_present_and_inferred_agent():
    """The model is an inferred `llm:` peer; the agent is *inferred* too — this
    trace has only bare leaf LLM spans under a transport parent (no agent/run
    wrapper), so Step 2.c case 4 infers the agent node. It appears exactly once
    (the inferred agent fuses the per-turn LLM spans into one entity)."""
    result = extract(_spans())
    by_inferred = {(e.natural_key, e.inferred) for e in result.entities}
    assert ("llm:claude-haiku-4-5-20251001", True) in by_inferred
    inferred_agents = [
        e for e in result.entities
        if e.natural_key == "agent:patent-assistant" and e.inferred
    ]
    assert len(inferred_agents) == 1, "expected a single inferred patent-assistant entity"


def test_tool_interactions_present_both_directions():
    """Each tool yields an agent→tool call and a tool→agent reverse."""
    result = extract(_spans())
    pairs = {(c.natural_key, c2.natural_key)
             for ix in result.interactions
             for c in [_ent(result, ix.caller_entity_id)]
             for c2 in [_ent(result, ix.callee_entity_id)]}
    assert ("agent:patent-assistant", "tool:database") in pairs
    assert ("tool:database", "agent:patent-assistant") in pairs
    assert ("agent:patent-assistant", "tool:file") in pairs
    assert ("tool:file", "agent:patent-assistant") in pairs


def test_tool_request_payload_is_arguments():
    """The agent→tool interaction carries the tool call's arguments as a
    `tool_call_arguments` payload — not the LLM completion."""
    result = extract(_spans())
    payload_by_hash = {p.content_hash: p for p in result.payloads}

    # Find an agent→tool:file interaction and check its request payload.
    file_calls = [
        ix for ix in result.interactions
        if _ent(result, ix.caller_entity_id).natural_key == "agent:patent-assistant"
        and _ent(result, ix.callee_entity_id).natural_key == "tool:file"
    ]
    assert file_calls, "expected a patent-assistant → tool:file interaction"
    ix = file_calls[0]
    assert ix.request_payload_hash is not None
    payload = payload_by_hash[ix.request_payload_hash]
    assert payload.content_kind == "tool_call_arguments"
    # The fixture's file call arguments include name=results.txt.
    args = payload.content if isinstance(payload.content, str) else json.dumps(payload.content)
    assert "results.txt" in args


def test_merged_tool_call_orders_after_its_originating_llm():
    """A tool call created on an LLM's OUTPUT and later replayed on a following
    span's INPUT is the *same* logical call, merged by Step 4. The merged
    interaction must take the order of the **originating** (output) call — which
    sits AFTER that turn's LLM exchange (positive band) — NOT the negative band
    of the input replay. A plain `min(order)` over the merged edges would let
    the replay's negative band win and wrongly sort the call ahead of its own
    originating LLM (the "database before the first LLM" bug).

    `database` originates as output (positive band) and is replayed as input on
    later spans; all converge to ONE `tool:database` entity with two forward
    interactions (the `search` call + the distinct `refine` call)."""
    result = extract(_spans())

    db_entities = [e for e in result.entities if e.natural_key == "tool:database"]
    assert len(db_entities) == 1

    db_forward = [
        ix for ix in result.interactions
        if _ent(result, ix.caller_entity_id).natural_key == "agent:patent-assistant"
        and _ent(result, ix.callee_entity_id).natural_key == "tool:database"
    ]
    assert len(db_forward) == 2

    # No surviving forward database call carries a negative (input-replay) band:
    # the replays were folded into their output origin, which is positive.
    assert all(ix.order > 0 for ix in db_forward), (
        f"merged output-origin tool call must keep its positive (after-LLM) band; "
        f"got orders {[ix.order for ix in db_forward]}"
    )

    # For the turn whose LLM exchange shares a span with a database call, the
    # database call must sort AFTER the agent→LLM call (its originating LLM).
    for db in db_forward:
        llm_same_span = [
            ix for ix in result.interactions
            if ix.started_at == db.started_at
            and _ent(result, ix.caller_entity_id).natural_key == "agent:patent-assistant"
            and _ent(result, ix.callee_entity_id).natural_key.startswith("llm:")
        ]
        assert llm_same_span, "expected an agent→LLM call sharing the database call's span"
        assert db.order > llm_same_span[0].order, (
            "output-derived tool call must order after its originating LLM call"
        )


def test_interaction_time_follows_its_anchor_span():
    """Each interaction's `started_at` equals its **anchor span's** start time,
    not an aggregate over its pooled evidence spans.

    Regression for the Step-4 merge bug: the three patent-assistant → LLM calls
    are three distinct turns (spans at distinct times). Step 4 merges the LLM
    TARGET peers into one node, so each agent↔LLM interaction pools the merged
    peer's span (an *earlier* turn's). A `min(started_at)` over the pooled set
    dragged every turn's LLM interaction down to the first turn's time, so a
    later-turn tool call (e.g. `file`) sorted *after* LLM calls that really ran
    after it. Timing must follow the anchor, which keeps per-turn times distinct.
    """
    result = extract(_spans())
    span_started = {s.span_id: s.started_at for s in _spans()}

    anchor_of = {}
    for s in result.interaction_spans:
        if s.is_anchor:
            anchor_of[s.interaction_id] = s.span_id

    for ix in result.interactions:
        anchor_span_id = anchor_of.get(ix.id)
        assert anchor_span_id is not None, f"interaction {ix.id} has no anchor span"
        assert ix.started_at == span_started[anchor_span_id], (
            f"interaction {ix.summary!r} started_at {ix.started_at} != its anchor "
            f"span {anchor_span_id[-6:]} time {span_started[anchor_span_id]}"
        )

    # Each agent→LLM turn must carry a *distinct* started_at (the timing bug
    # collapsed them to one), matching its own span.
    llm_calls = [
        ix for ix in result.interactions
        if _ent(result, ix.caller_entity_id).natural_key == "agent:patent-assistant"
        and _ent(result, ix.callee_entity_id).natural_key.startswith("llm:")
    ]
    assert len({ix.started_at for ix in llm_calls}) == len(llm_calls) >= 2, (
        "each agent→LLM turn must keep its own span time"
    )

    # ...and so must the *response* direction (llm → agent). This is the
    # distinct anchor bug: Step 4 merges the LLM TARGET peers into one node, so
    # every response edge's *source* is the merged peer carrying the first
    # turn's span. Anchoring the response on the merged peer's span would stamp
    # all responses with the first turn's time (and stack them at one
    # (started_at, order)). The fuse anchors on the *observed* endpoint instead,
    # so each response keeps its own turn's time.
    llm_responses = [
        ix for ix in result.interactions
        if _ent(result, ix.caller_entity_id).natural_key.startswith("llm:")
        and _ent(result, ix.callee_entity_id).natural_key == "agent:patent-assistant"
    ]
    assert len({ix.started_at for ix in llm_responses}) == len(llm_responses) >= 2, (
        "each LLM→agent response must keep its own turn's span time, not the "
        "merged peer's first-turn span"
    )


def _ent(result, entity_id):
    return next(e for e in result.entities if e.id == entity_id)
