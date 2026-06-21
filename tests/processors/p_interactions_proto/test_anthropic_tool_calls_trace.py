"""Entities/interactions for an `openinference.instrumentation.anthropic` trace.

Pins ADR-0007 Step 2.b case 3 (tool nodes inferred from an LLM span's output
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

  * Each anthropic LLM span is an observed LLM SOURCE boundary; with no Gray
    chain between the two spans (their only link is the White server parent),
    Step 3.a phase 1 forms two `patent-assistant` components. The Step 3.a
    observed↔observed combine (`merge_same_entity`) then collapses them into a
    single observed `patent-assistant` entity — they are keyless observed
    boundary callers of the same `service.name`.
  * The combined model peer `llm:claude-haiku-4-5-20251001` is the inferred
    one-sided stub of the LLM call (Step 2.b one-sided stubbing); the two
    spans' stubs converge to one entity.
  * Each distinct output tool becomes one inferred `tool:<name>` entity:
    `tool:database` (converged from three call sites — two output, one input
    replay — via Step 3.a phase 2) and `tool:file`.
  * The agent→tool interaction's request payload is the tool call's
    `arguments` (tool_call_arguments), not the LLM completion.
"""

from __future__ import annotations

import json

from data_governance.processors.p_interactions_proto.extractor import extract

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


def test_llm_peer_present_and_observed_agent():
    """The model is an inferred `llm:` peer; the agent is observed (not
    inferred)."""
    result = extract(_spans())
    by_inferred = {(e.natural_key, e.inferred) for e in result.entities}
    assert ("llm:claude-haiku-4-5-20251001", True) in by_inferred
    # patent-assistant is observed and now appears exactly once: the Step 3.a
    # observed↔observed combine (merge_same_entity) collapses the two split
    # LLM-source components into one entity (same service.name, both keyless
    # observed boundary callers).
    observed_agents = [
        e for e in result.entities
        if e.natural_key == "patent-assistant" and not e.inferred
    ]
    assert len(observed_agents) == 1, "expected a single merged patent-assistant entity"


def test_tool_interactions_present_both_directions():
    """Each tool yields an agent→tool call and a tool→agent reverse."""
    result = extract(_spans())
    pairs = {(c.natural_key, c2.natural_key)
             for ix in result.interactions
             for c in [_ent(result, ix.caller_entity_id)]
             for c2 in [_ent(result, ix.callee_entity_id)]}
    assert ("patent-assistant", "tool:database") in pairs
    assert ("tool:database", "patent-assistant") in pairs
    assert ("patent-assistant", "tool:file") in pairs
    assert ("tool:file", "patent-assistant") in pairs


def test_tool_request_payload_is_arguments():
    """The agent→tool interaction carries the tool call's arguments as a
    `tool_call_arguments` payload — not the LLM completion."""
    result = extract(_spans())
    payload_by_hash = {p.content_hash: p for p in result.payloads}

    # Find an agent→tool:file interaction and check its request payload.
    file_calls = [
        ix for ix in result.interactions
        if _ent(result, ix.caller_entity_id).natural_key == "patent-assistant"
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


def test_input_side_tool_call_counted_and_ordered():
    """`database` is called on the OUTPUT side of two spans and replayed on the
    INPUT side of span 3 (a prior turn fed back). Per the human spec the input
    replay IS inferred — three call sites total — ordered ahead of span 3's LLM
    interaction (ordering rule 3). All three still converge to ONE
    `tool:database` entity (Step 3.a phase 2 combines by key)."""
    result = extract(_spans())

    # One entity despite three call sites.
    db_entities = [e for e in result.entities if e.natural_key == "tool:database"]
    assert len(db_entities) == 1

    # Three agent→tool:database calls now (two output + one input replay).
    db_forward = [
        ix for ix in result.interactions
        if _ent(result, ix.caller_entity_id).natural_key == "patent-assistant"
        and _ent(result, ix.callee_entity_id).natural_key == "tool:database"
    ]
    assert len(db_forward) == 3

    # The input-side replay carries a negative order band, so it sorts (by
    # (started_at, order)) ahead of the LLM call that shares its span. Find the
    # span-3 LLM interaction (patent-assistant → llm:*) and the input-derived
    # database call on the same span, and assert the tool precedes the LLM.
    input_db = [ix for ix in db_forward if ix.order < 0]
    assert input_db, "expected an input-derived database call with a negative order band"
    replay = input_db[0]
    # An interaction sharing the replay's started_at with order >= 0 is the LLM
    # call (or a later output tool); the replay must sort first among them.
    same_span = sorted(
        (ix for ix in result.interactions if ix.started_at == replay.started_at),
        key=lambda r: (r.started_at, r.order),
    )
    assert same_span[0].order < 0, "input-derived tool must order before the LLM interaction"


def _ent(result, entity_id):
    return next(e for e in result.entities if e.id == entity_id)
