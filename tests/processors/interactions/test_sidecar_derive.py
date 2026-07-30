"""Pure unit tests for the two-span sidecar derivation: anchor detection, row
construction, and the interaction-id rule. No DB. (The classify table's own
tests live in ``tests/test_sidecar_facts.py`` with the shared vocabulary.)
"""

from __future__ import annotations

from data_governance.processors.interactions.procedure import _interaction_id
from data_governance.processors.interactions.sidecar import plan_trace

from . import sidecar_golden as golden


# --- anchors: outbound always; inbound entry only; echo is NOT an anchor ----

def test_golden_anchor_verdicts_including_the_echo():
    plan = plan_trace(golden.TRACE, golden.build_spans())
    assert plan.anchor_ids == golden.ANCHORS
    # The callee-side echo (D1) is inbound but sits under an outbound anchor (B3),
    # so it is NOT an anchor — it is the dedup that keeps the tool call one edge.
    assert golden.D1 not in plan.anchor_ids


def test_entry_is_anchor_despite_dangling_wire_parent():
    """A1's parent is the ghost wire root (never stored): entry detection must
    tolerate a dangling parent (not require parent_id IS NULL)."""
    plan = plan_trace(golden.TRACE, golden.build_spans())
    assert golden.A1 in plan.anchor_ids


# --- row construction on the golden trace ---------------------------------

def _rows_by_anchor(plan):
    return {r.anchor_span_id: r for r in plan.want.values()}


def test_golden_rows_endpoints_and_parenting():
    plan = plan_trace(golden.TRACE, golden.build_spans())
    rows = _rows_by_anchor(plan)
    assert len(rows) == 4

    i1 = rows[golden.A1]
    assert (i1.caller.natural_key, i1.callee.natural_key) == (
        "user:alice", "agent:weather-service")
    assert i1.parent_anchor_span_id is None  # the root

    i3 = rows[golden.B3]
    # Callee identity comes from the echo's self.id (weather-tool), NOT peer.host.
    assert i3.callee.natural_key == "tool:weather-tool"
    assert i3.parent_anchor_span_id == golden.A1

    i2 = rows[golden.B1]
    assert i2.callee.natural_key == f"llm:{golden._LLM_HOST}/qwen2.5:7b"
    assert i2.caller.natural_key == "agent:weather-service"


def test_golden_rows_carry_the_response_span_and_seqs():
    """The plan's arrival facts: each completed row knows its response span id
    and both spans' seqs (leg seq itself is DB-owned at write time)."""
    plan = plan_trace(golden.TRACE, golden.build_spans())
    rows = _rows_by_anchor(plan)
    i1 = rows[golden.A1]
    assert i1.response_span_id == golden.A2
    assert i1.resp_seq is not None and i1.resp_seq >= i1.seq


def test_golden_bodyless_llm_still_complete_row():
    """Strip the llm request body — the interaction is still first-class with a
    NULL request payload (payloads are enrichment, never a drop reason)."""
    spans = golden.build_spans()
    for s in spans:
        if s.span_id == golden.B1:
            s.attributes.pop("input.value")
    plan = plan_trace(golden.TRACE, spans)
    i2 = _rows_by_anchor(plan)[golden.B1]
    assert i2.request_payload is None
    assert i2.callee.natural_key == f"llm:{golden._LLM_HOST}/qwen2.5:7b"  # still complete
    assert i2.error is False  # response outcome=ok


def test_outcome_abandoned_is_error_row():
    spans = golden.build_spans()
    for s in spans:
        if s.span_id == golden.B2:  # the llm#1 response
            s.attributes["lineage.outcome"] = "abandoned"
            s.attributes.pop("output.value", None)
    plan = plan_trace(golden.TRACE, spans)
    i2 = _rows_by_anchor(plan)[golden.B1]
    assert i2.error is True
    assert i2.response_payload is None


def test_lone_request_is_in_flight():
    """No response span for an exchange → error None, no response span id: the
    response leg will simply be absent (in-flight, not failed)."""
    spans = [s for s in golden.build_spans() if s.span_id != golden.B2]
    plan = plan_trace(golden.TRACE, spans)
    i2 = _rows_by_anchor(plan)[golden.B1]
    assert i2.error is None
    assert i2.response_span_id is None
    assert i2.resp_seq is None


def test_connectors_cover_every_non_anchor_span():
    plan = plan_trace(golden.TRACE, golden.build_spans())
    connector_span_ids = {span_id for _owner, span_id in plan.connectors}
    all_ids = {s.span_id for s in golden.build_spans()}
    assert connector_span_ids == all_ids - plan.anchor_ids  # 6 connectors
    # The echo pair both belong to the tool interaction (B3).
    owners = dict((sid, owner) for owner, sid in plan.connectors)
    assert owners[golden.D1] == golden.B3
    assert owners[golden.D2] == golden.B3


def test_interaction_id_is_trace_slash_exchange():
    assert _interaction_id(golden.TRACE, golden.A1).count("-") == 4  # a uuid
    # Deterministic: same inputs → same id.
    assert _interaction_id("t", "x") == _interaction_id("t", "x")
