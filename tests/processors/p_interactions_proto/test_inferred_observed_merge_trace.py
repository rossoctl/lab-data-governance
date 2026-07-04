"""End-to-end coverage for the four ADR-0007 clauses implemented together:

  * **Step 2.b case 3, input side (ordering rule 3):** a tool replayed on the
    LLM span's *input* messages that was never an output is inferred and
    ordered *before* the LLM interaction.
  * **Inferred interaction ordering (F6a):** each interaction carries an
    explicit `order`; within one LLM turn input tools < agent↔LLM call/response
    < output tools, and every call precedes its response.
  * **Step 2.c (inferred↔observed merge):** an inferred peer folds into an
    *independently observed* twin of the same entity in a different White/Gray
    component, so the LLM's output tool call resolves to the real observed tool
    entity (not a phantom inferred peer).
  * **Step 3.b naming:** entities are named from service.name / the natural-key
    suffix rather than the literal 'unknown'.

The fixture (`trace_inferred_observed_merge`) is a `weather-agent` making one
anthropic `messages.create` call: input replays a `calendar` tool, output asks
for `get_weather`. `get_weather` is *also* observed executing as its own
`weather-tool` span in a separate (traceparent-broken) component. See the
fixture docstring in conftest.py.
"""

from __future__ import annotations

from data_governance.processors.p_interactions_proto.extractor import extract

from .conftest import load_trace_spans

MERGE_TRACE = "trace_inferred_observed_merge"


def _spans():
    return load_trace_spans(MERGE_TRACE)


def _ent(result, entity_id):
    return next(e for e in result.entities if e.id == entity_id)


def _pairs(result):
    return {
        (_ent(result, ix.caller_entity_id).natural_key,
         _ent(result, ix.callee_entity_id).natural_key)
        for ix in result.interactions
    }


# --- Step 2.c: inferred↔observed merge ------------------------------------


def test_inferred_tool_folds_into_observed_twin():
    """The `get_weather` tool the LLM asked for is *observed* as its own span
    in a separate component. Step 2.c folds the inferred peer into that
    observed `weather-tool` entity, so the agent's output tool call resolves to
    the OBSERVED entity — not an inferred `tool:get_weather`."""
    result = extract(_spans())

    # The agent's resolved get_weather call lands on the observed weather-tool.
    pairs = _pairs(result)
    assert ("agent:weather-agent", "weather-tool") in pairs
    assert ("weather-tool", "agent:weather-agent") in pairs

    # No agent→inferred-tool:get_weather edge survives the merge: the LLM's
    # output tool call points at the observed twin instead.
    assert ("agent:weather-agent", "tool:get_weather") not in pairs

    # weather-tool is observed, not inferred.
    wt = [e for e in result.entities if e.natural_key == "weather-tool"]
    assert wt and wt[0].inferred is False


def test_step4_merge_recorded_in_notes():
    """The Step 2.d merge records its node/edge merge counts in the notes;
    the inferred↔observed fold of the `get_weather` peer is counted among the
    merged nodes (>= 1)."""
    result = extract(_spans())
    note = next(n for n in result.notes if "Step 2.d merged" in n)
    import re
    m = re.search(r"Step 2.d merged (\d+) same-entity nodes", note)
    assert m and int(m.group(1)) >= 1


# --- F6b input side (rule 3) ----------------------------------------------


def test_input_side_tool_inferred():
    """The input-replayed `calendar` tool (never an output) is inferred."""
    result = extract(_spans())
    cal = [e for e in result.entities if e.natural_key == "tool:calendar"]
    assert cal, "expected an inferred tool:calendar entity from the input replay"
    assert cal[0].inferred is True


# --- F6a ordering ----------------------------------------------------------


def test_intra_turn_ordering():
    """Within the single LLM turn: input tool (calendar) < agent↔LLM call <
    output tool (get_weather), and each call precedes its response."""
    result = extract(_spans())
    order_by_pair = {
        (_ent(result, ix.caller_entity_id).natural_key,
         _ent(result, ix.callee_entity_id).natural_key): ix.order
        for ix in result.interactions
    }

    cal_call = order_by_pair[("agent:weather-agent", "tool:calendar")]
    cal_resp = order_by_pair[("tool:calendar", "agent:weather-agent")]
    llm_call = order_by_pair[("agent:weather-agent", "llm:claude-haiku-4-5-20251001")]
    llm_resp = order_by_pair[("llm:claude-haiku-4-5-20251001", "agent:weather-agent")]
    tool_call = order_by_pair[("agent:weather-agent", "weather-tool")]

    # rule 3: input tool before the LLM.
    assert cal_call < llm_call
    # rule 1: call before response (each pair).
    assert cal_call < cal_resp
    assert llm_call < llm_resp
    # rule 4: output tool after the LLM.
    assert tool_call > llm_call


# --- Step 3.b naming -------------------------------------------------------


def test_entities_named_from_service_or_key():
    """No entity is named the literal 'unknown'. Observed entities take their
    service.name; inferred peers fall back to the service that emitted the
    originating span (or the natural-key suffix)."""
    result = extract(_spans())
    names = {e.natural_key: e.display_name for e in result.entities}
    # natural_key carries the `agent:` prefix; display_name stays the service.
    assert names["agent:weather-agent"] == "weather-agent"
    assert names["weather-tool"] == "weather-tool"
    # Inferred peers reference the originating LLM span, whose service is the
    # agent — so they are named after it, never 'unknown'.
    assert names["tool:calendar"] != "unknown"
    assert names["llm:claude-haiku-4-5-20251001"] != "unknown"
