"""Coverage for ADR-0007 Step 3.b reconstruction of an *observed* transport
chain (as opposed to an inferred Teal server).

ADR-0007 Step 3.a/3.b: the entity fuse drops **all** Teal nodes — inferred
servers *and* observed transport spans — and each Teal chain between two Blue
components becomes one interaction. Before this was implemented the fuse keyed
solely on the inferred `_server` marker, so an observed `httpx→starlette` hop
between two services was silently ignored (neither an entity boundary nor an
interaction). These tests pin the corrected behavior.

The `trace_cross_service_transport` fixture is a `caller-agent` (svc-caller)
calling a `callee-agent` (svc-callee) over HTTP, bridged only by an observed
`httpx (client) → starlette (server)` Teal chain. See the fixture docstring in
conftest.py.
"""

from __future__ import annotations

from data_governance.processors.p_interactions_proto.extractor import extract

from .conftest import load_trace_spans


def _spans():
    return load_trace_spans("trace_cross_service_transport")


def _ent(result, entity_id):
    return next(e for e in result.entities if e.id == entity_id)


def _pairs(result):
    return [
        (_ent(result, ix.caller_entity_id).natural_key,
         _ent(result, ix.callee_entity_id).natural_key)
        for ix in result.interactions
    ]


def test_observed_transport_chain_separates_two_entities():
    """Dropping the observed Teal chain groups the two agents into two distinct
    entities (Step 3.a), not one merged blob."""
    result = extract(_spans())
    # Two entities, both observed (the transport hop is real, not inferred).
    assert len(result.entities) == 2
    assert all(not e.inferred for e in result.entities)
    keys = sorted(e.natural_key for e in result.entities)
    # Agent identity is the typed `agent.name` key (services are svc-caller /
    # svc-callee; display_name keeps those, natural_key is the agent name).
    assert keys == ["agent:callee-agent", "agent:caller-agent"], keys


def test_observed_transport_chain_reconstructs_one_call_and_response():
    """The observed httpx→starlette chain reconstructs into exactly one call
    (caller→callee) and one response (callee→caller) — Step 3.b."""
    result = extract(_spans())
    pairs = _pairs(result)
    caller = next(e for e in result.entities if "caller" in e.natural_key)
    callee = next(e for e in result.entities if "callee" in e.natural_key)
    assert len(result.interactions) == 2
    assert (caller.natural_key, callee.natural_key) in pairs  # call
    assert (callee.natural_key, caller.natural_key) in pairs  # response
    # Call is ordered before its response.
    by_order = sorted(result.interactions, key=lambda ix: (ix.started_at, ix.order))
    first = by_order[0]
    assert _ent(result, first.caller_entity_id).natural_key == caller.natural_key


def test_interaction_anchors_on_agentic_span_not_transport():
    """Timing/evidence anchors on the observed *agentic* endpoint spans, never a
    (dropped) transport span."""
    result = extract(_spans())
    agentic_span_ids = {
        s.span_id for s in _spans()
        if (s.scope or {}).get("name", "").startswith("openinference")
    }
    transport_span_ids = {
        s.span_id for s in _spans()
        if (s.scope or {}).get("name", "").startswith("opentelemetry.instrumentation")
    }
    anchor_ids = {isp.span_id for isp in result.interaction_spans if isp.is_anchor}
    assert anchor_ids  # at least one anchor
    assert anchor_ids <= agentic_span_ids
    assert not (anchor_ids & transport_span_ids)


def test_dangling_transport_leaf_yields_no_extra_interaction():
    """Regression guard: a trace whose observed Teal appears only as dangling
    leaves within a single entity (no chain bridging two Blue components) gains
    no interaction from the observed-chain reconstruction. The live 3-turn
    anthropic trace has 12 observed Teal nodes, none bridging two components, and
    its interaction count must be unchanged (10)."""
    result = extract(load_trace_spans("trace_8e8d7b1e"))
    assert len(result.interactions) == 10
