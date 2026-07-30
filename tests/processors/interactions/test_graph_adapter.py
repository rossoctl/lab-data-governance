"""Adapter tests: graph ``ExtractResult`` → production row model.

These are PURE (no DB): they run the graph algorithm's ``extract`` over the same
captured fixtures the algorithm's own suite uses, then assert the adapter's
``ProductionRows`` satisfy the production schema's contracts. The graph algorithm's
own output is validated by ``tests/processors/interactions/graph`` — here we only
assert the mapping to production shape.

The DB round-trip (``adapt`` → ``state.flush`` against Postgres, ENUM acceptance,
idempotent re-run) lives in ``test_graph_adapter_db.py`` (testcontainers), which is
skipped where Docker is unavailable.
"""

from __future__ import annotations

import pytest

from data_governance.processors.interactions import graph_adapter, procedure
from data_governance.processors.interactions.graph.extractor import extract
from tests.processors.interactions.graph.conftest import load_trace_spans

# The production entity_kind Postgres ENUM (migration 0004).
ENTITY_KINDS = {"user", "client", "agent", "tool", "llm", "service"}

# Every captured fixture the graph algorithm's suite pins.
FIXTURES = [
    "travel_agent_I",
    "travel_agent_II",
    "travel_agent_III",
    "patent_agent_I",
    "patent_agent_II",
    "trace_anthropic_tool_calls",
    "trace_claude_subagent",
    "trace_cross_service_transport",
    "trace_inferred_observed_merge",
    "trace_observed_tool_two_invocations",
]


def _adapt(name: str):
    spans = load_trace_spans(name)
    return spans, graph_adapter.adapt(extract(spans), spans)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_entity_kinds_are_in_the_enum(fixture: str) -> None:
    """Every emitted entity kind is one of the six production ENUM values —
    otherwise the INSERT would be rejected at write time."""
    _, rows = _adapt(fixture)
    assert rows.entities, f"{fixture}: expected at least one entity"
    for e in rows.entities.values():
        assert e.kind in ENTITY_KINDS, f"{fixture}: bad kind {e.kind!r} ({e.natural_key})"


@pytest.mark.parametrize("fixture", FIXTURES)
def test_entity_ids_are_deterministic_uuid5_of_natural_key(fixture: str) -> None:
    """Entity id must be uuid5 of the natural_key so the same logical entity
    collapses onto one row across algorithms and re-runs."""
    _, rows = _adapt(fixture)
    for nk, e in rows.entities.items():
        assert e.natural_key == nk  # dict is keyed by natural_key
        assert e.id == procedure._entity_id(e.natural_key)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_interaction_ids_are_deterministic(fixture: str) -> None:
    """Interaction id must reproduce from (trace_id, anchor_span/callee_nk) and be
    unique — co-anchored inferred calls must not collide."""
    _, rows = _adapt(fixture)
    ids = [ix.id for ix in rows.interactions_by_anchor.values()]
    assert len(ids) == len(set(ids)), f"{fixture}: duplicate interaction ids"


@pytest.mark.parametrize("fixture", FIXTURES)
def test_interaction_spans_are_unique_per_trace_span(fixture: str) -> None:
    """Main's UNIQUE(trace_id, span_id) on interaction_spans is a load-bearing
    guardrail (ADR-0011). The adapter must never emit two rows sharing it — a
    co-anchored call gets a synthetic span_id instead."""
    _, rows = _adapt(fixture)
    seen: set[tuple[str, str]] = set()
    for r in rows.interaction_spans:
        key = (r.trace_id, r.span_id)
        assert key not in seen, f"{fixture}: duplicate interaction_span {key}"
        seen.add(key)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_interactions_reference_resolved_entities(fixture: str) -> None:
    """Every interaction's caller/callee id points at an emitted entity — no
    dangling endpoints."""
    _, rows = _adapt(fixture)
    entity_ids = {e.id for e in rows.entities.values()}
    for ix in rows.interactions_by_anchor.values():
        assert ix.caller_entity_id in entity_ids, f"{fixture}: dangling caller"
        assert ix.callee_entity_id in entity_ids, f"{fixture}: dangling callee"


@pytest.mark.parametrize("fixture", FIXTURES)
def test_payload_hashes_are_referenced_and_present(fixture: str) -> None:
    """Every payload hash referenced by an interaction exists in the payload set
    (content-addressed FK), and hashes reuse the graph algorithm's own values."""
    spans, rows = _adapt(fixture)
    proto = extract(spans)
    proto_hashes = {p.content_hash for p in proto.payloads}
    for ix in rows.interactions_by_anchor.values():
        for h in (ix.request_payload_hash, ix.response_payload_hash):
            if h is not None:
                assert h in rows.payloads, f"{fixture}: payload {h[:8]} missing"
                assert h in proto_hashes, f"{fixture}: hash diverged from proto"


@pytest.mark.parametrize("fixture", FIXTURES)
def test_interaction_seq_matches_anchor_span(fixture: str) -> None:
    """seq/original_seq are taken from the anchor span (mirrors _materialise)."""
    spans, rows = _adapt(fixture)
    span_by_id = {s.span_id: s for s in spans}
    for ix in rows.interactions_by_anchor.values():
        anchor = span_by_id.get(ix.primary_anchor_span_id)
        if anchor is not None:
            assert ix.seq == anchor.seq
            assert ix.original_seq == anchor.seq


@pytest.mark.parametrize("fixture", FIXTURES)
def test_parent_interaction_forest_is_well_formed(fixture: str) -> None:
    """parent_interaction_id forms a valid ADR-0008 forest: every non-NULL parent
    resolves to another emitted interaction, no interaction is its own parent, and
    there are no cycles."""
    _, rows = _adapt(fixture)
    by_id = {ix.id: ix for ix in rows.interactions_by_anchor.values()}
    for ix in by_id.values():
        pid = ix.parent_interaction_id
        if pid is None:
            continue
        assert pid in by_id, f"{fixture}: parent {pid[:8]} is not an emitted interaction"
        assert pid != ix.id, f"{fixture}: interaction is its own parent"
    # No cycles: walking parents from any node terminates at NULL.
    for start in by_id:
        seen: set[str] = set()
        cur = start
        while cur is not None:
            assert cur not in seen, f"{fixture}: parent cycle at {cur[:8]}"
            seen.add(cur)
            cur = by_id[cur].parent_interaction_id


@pytest.mark.parametrize("fixture", FIXTURES)
def test_parent_is_a_true_ancestor_by_span(fixture: str) -> None:
    """A derived parent's anchor span must be a real span-tree ancestor of the
    child's anchor (ADR-0008: parent = first enclosing anchor up the parent chain)."""
    spans, rows = _adapt(fixture)
    span_by_id = {s.span_id: s for s in spans}
    by_id = {ix.id: ix for ix in rows.interactions_by_anchor.values()}
    for ix in by_id.values():
        if ix.parent_interaction_id is None:
            continue
        parent_anchor = by_id[ix.parent_interaction_id].primary_anchor_span_id
        # Walk ix's anchor ancestry; the parent's anchor must appear.
        cur = span_by_id.get(ix.primary_anchor_span_id)
        cur = span_by_id.get(cur.parent_id) if cur and cur.parent_id else None
        found = False
        while cur is not None:
            if cur.span_id == parent_anchor:
                found = True
                break
            cur = span_by_id.get(cur.parent_id) if cur.parent_id else None
        assert found, f"{fixture}: parent anchor is not an ancestor of the child"


def test_co_anchored_inferred_tool_call_stays_distinct() -> None:
    """The inferred-tool traces anchor an LLM call and a tool call on the SAME
    LLM span. Both must survive as distinct interactions."""
    _, rows = _adapt("patent_agent_I")
    summaries = sorted(ix.summary for ix in rows.interactions_by_anchor.values())
    # Expect both the LLM calls and the inferred database + file tool calls.
    assert any("tool:database" in s for s in summaries)
    assert any("tool:file" in s for s in summaries)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_legs_are_real_edges_never_fabricated(fixture: str) -> None:
    """ADR-0025: `adapt` supplies explicit per-leg rows via `legs_by_ix`. Each leg
    is backed by a REAL graph edge — the adapter never fabricates one. So an
    interaction has a request leg and/or a response leg (1 or 2, never zero, never
    a duplicated type), and every leg's `seq` (its edge's global `order`) is
    GLOBALLY UNIQUE across the whole trace — the invariant a self-paired leg used
    to violate by giving two legs the same `order`. Where both legs exist, request
    precedes response (`seq` strictly lower)."""
    _, rows = _adapt(fixture)
    assert rows.legs_by_ix is not None
    assert set(rows.legs_by_ix) == {ix.id for ix in rows.interactions_by_anchor.values()}
    all_seqs: list[int] = []
    for ix_id, legs in rows.legs_by_ix.items():
        types = sorted(leg.leg_type for leg in legs)
        assert types in (["request"], ["response"], ["request", "response"]), (
            f"{fixture}: {ix_id[:8]} legs {types}"
        )
        for leg in legs:
            all_seqs.append(leg.seq)
        req = next((leg for leg in legs if leg.leg_type == "request"), None)
        resp = next((leg for leg in legs if leg.leg_type == "response"), None)
        if req is not None and resp is not None:
            assert req.seq < resp.seq, (
                f"{fixture}: request seq {req.seq} !< response {resp.seq}"
            )
    # No two legs anywhere in the trace share a `seq` (global ordinal per edge).
    assert len(all_seqs) == len(set(all_seqs)), f"{fixture}: duplicate leg seq: {all_seqs}"


def test_a2a_response_leg_uses_the_responding_span_not_the_request_edge() -> None:
    """The A2A-delegation response leg anchors on the RESPONDING agent's own span,
    so its `occurred_at` and `seq` differ from the request edge's — the bug the
    single-row collapse hid. `travel-advisor → research-agent` (a delegation) has a
    request edge (order 14, the `delegate_to_research_agent` TOOL span) and a
    response edge (order 17, research-agent's own wrapper span); the response leg
    must carry order 17 and the responding span's completion time, NOT the request
    edge's `ended_at`."""
    _, rows = _adapt("travel_agent_II")

    def _nk(eid: str) -> str:
        e = next(e for e in rows.entities.values() if e.id == eid)
        return e.natural_key

    delegations = [
        ix
        for ix in rows.interactions_by_anchor.values()
        if _nk(ix.caller_entity_id).startswith("agent:")
        and _nk(ix.callee_entity_id).startswith("agent:")
        and _nk(ix.caller_entity_id) != _nk(ix.callee_entity_id)
    ]
    assert delegations, "expected at least one agent→agent delegation"
    for ix in delegations:
        legs = rows.legs_by_ix[ix.id]
        req = next(leg for leg in legs if leg.leg_type == "request")
        resp = next(leg for leg in legs if leg.leg_type == "response")
        # Split anchors → the response leg is a strictly later edge with its own
        # (later) occurrence, never a copy of the request edge's end.
        assert resp.seq > req.seq, f"{ix.summary}: response seq not after request"
        assert resp.occurred_at is not None and req.occurred_at is not None
        assert resp.occurred_at >= req.occurred_at, ix.summary


def test_single_turn_trace_maps_one_interaction() -> None:
    """The single-clarifying-turn travel trace: one observed agent, one inferred
    llm, one agent→llm interaction."""
    _, rows = _adapt("travel_agent_I")
    kinds = sorted(e.kind for e in rows.entities.values())
    assert kinds == ["agent", "llm"]
    assert len(rows.interactions_by_anchor) == 1
    (ix,) = rows.interactions_by_anchor.values()
    caller = next(e for e in rows.entities.values() if e.id == ix.caller_entity_id)
    callee = next(e for e in rows.entities.values() if e.id == ix.callee_entity_id)
    assert caller.kind == "agent"
    assert callee.kind == "llm"


# --- interaction_spans territory (info/connector, ADR-0008) -----------------


@pytest.mark.parametrize("fixture", FIXTURES)
def test_interaction_span_roles_are_valid(fixture: str) -> None:
    """Every interaction_spans row carries a valid role, and exactly one anchor row
    per interaction (co-anchored calls use a synthetic span_id, still one anchor)."""
    _, rows = _adapt(fixture)
    from collections import Counter

    for r in rows.interaction_spans:
        assert r.role in {"anchor", "info", "connector"}, f"{fixture}: bad role {r.role!r}"
    anchors = Counter(
        r.interaction_id for r in rows.interaction_spans if r.role == "anchor"
    )
    for ix in rows.interactions_by_anchor.values():
        assert anchors[ix.id] == 1, f"{fixture}: {ix.id[:8]} has {anchors[ix.id]} anchors"


@pytest.mark.parametrize("fixture", FIXTURES)
def test_territory_spans_own_a_true_ancestor_anchor(fixture: str) -> None:
    """A non-anchor (info/connector) span's owning interaction must be anchored on
    an ancestor-or-self of that span (ADR-0008 innermost-territory rule)."""
    spans, rows = _adapt(fixture)
    span_by_id = {s.span_id: s for s in spans}
    by_id = {ix.id: ix for ix in rows.interactions_by_anchor.values()}
    for r in rows.interaction_spans:
        if r.role == "anchor":
            continue
        owner_anchor = by_id[r.interaction_id].primary_anchor_span_id
        cur = span_by_id.get(r.span_id)
        found = False
        while cur is not None:
            if cur.span_id == owner_anchor:
                found = True
                break
            cur = span_by_id.get(cur.parent_id) if cur.parent_id else None
        assert found, f"{fixture}: territory span {r.span_id[:8]} owner is not an ancestor-anchor"


def test_territory_adds_info_and_connector_rows() -> None:
    """A multi-turn delegation trace whose anchors have payload-bearing descendants
    yields both info (payload/error) and connector (traceability-only) rows on top
    of the anchors — the per-interaction evidence the anchor-only cut lacked."""
    _, rows = _adapt("travel_agent_II")
    roles = {r.role for r in rows.interaction_spans}
    assert "info" in roles
    assert "connector" in roles
