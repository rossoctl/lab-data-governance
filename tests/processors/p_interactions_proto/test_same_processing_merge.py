"""Coverage for the two ADR-0025 Step 2.d / Step 3.a merge behaviours that
broaden the same-interaction merge / same-entity combine beyond inferred peers:

  * **Step 2.d observed↔observed same-interaction node merge** — two *observed*
    boundary nodes that represent the SAME processing (same tool name + kind,
    same scope, same input argument AND output result, same execution time)
    merge into one on the execution-flow graph, and every edge is preserved.
    A genuinely-distinct pair (different arguments) is NOT over-merged.

  * **Step 3.a broadened same-entity combine** — OBSERVED entities representing
    the same entity (same typed identity + kind + scope + inferred flag) combine
    on the entity graph while keeping every edge, and a distinct pair
    (different name, or inferred-vs-observed of the same name) stays separate.

These run the builder as a pure function — Step 2.d over a hand-built base
graph reconstructed from `Span`s, and the Step 3.a combine over a hand-built
entity graph — mirroring the other tests in this directory (extractor/builder
as pure functions over fixtures or hand-built graphs, no Postgres).
"""

from __future__ import annotations

import datetime as dt

from data_governance.processors.p_interactions_proto import builder as B
from data_governance.processors.p_interactions_proto.graph import (
    BLUE,
    EntityEdge,
    EntityGraph,
    EntityNode,
)
from data_governance.retrieval import Span

# An openinference google_adk TOOL span shape (copied from the observed
# `weather-tool` span in `trace_inferred_observed_merge`): kind=TOOL,
# role=SOURCE, natural_key `tool:<name>`.
_SCOPE = {"name": "openinference.instrumentation.google_adk", "version": "0.1.15"}
_T0 = "2026-06-14T12:19:53.000000+00:00"
_T1 = "2026-06-14T12:19:53.400000+00:00"


def _tool_span(
    span_id: str,
    *,
    name: str = "get_weather",
    inp: str = '{"city": "NYC"}',
    out: str = '{"temp_f": 71}',
    t0: str = _T0,
    t1: str = _T1,
    scope: dict | None = None,
) -> Span:
    return Span(
        seq=int(span_id[-1]),
        trace_id="ttrace",
        span_id=span_id,
        parent_id=None,
        name=name,
        started_at=dt.datetime.fromisoformat(t0),
        ended_at=dt.datetime.fromisoformat(t1),
        observed_at=None,
        arrival_seq=int(span_id[-1]),
        service_name="weather-tool",
        kind="INTERNAL",
        error=False,
        status_message=None,
        events=[],
        links=[],
        otlp={},
        scope=scope or _SCOPE,
        resource_attributes={"service.name": "weather-tool"},
        attributes={
            "openinference.span.kind": "TOOL",
            "tool.name": name,
            "input.mime_type": "application/json",
            "input.value": inp,
            "output.mime_type": "application/json",
            "output.value": out,
        },
    )


def _run_to_step_2d(spans: list[Span]) -> tuple:
    """Build the base graph and run Steps 2.a–2.d, returning
    `(graph, nodes_merged, servers_merged)`. Mirrors the extractor's ordering."""
    span_by_id = {s.span_id: s for s in spans}
    g = B.build_base_graph(spans)
    B.color_transport(g)
    B.color_agentic(g, span_by_id)
    B.duplicate_combined_nodes(g, span_by_id)
    B.infer_tool_calls_from_attributes(g, span_by_id)
    B.synthesize_missing_peers(g, span_by_id)
    B.infer_agent_from_bare_leaf_llms(g, span_by_id)
    nodes_merged, servers_merged = B.merge_identical_interactions(g, span_by_id)
    return g, nodes_merged, servers_merged


def _observed_tool_nodes(graph, span_ids: set[str]) -> list:
    return [
        n for n in graph.nodes
        if not n.is_inferred and n.color == BLUE and n.span_id in span_ids
    ]


# ---------------------------------------------------------------------------
# GAP 1 — Step 2.d observed↔observed same-interaction node merge
# ---------------------------------------------------------------------------


def test_step2d_merges_two_observed_same_interaction_nodes():
    """Two observed `get_weather` TOOL spans with identical name, arguments,
    output, scope, and execution time are the SAME processing captured twice.
    Step 2.d folds them into one observed node, preserving edges (the merged
    survivor keeps the incident interaction edges; self-loops drop)."""
    s1 = _tool_span("aaaa1")
    s2 = _tool_span("aaaa2")  # byte-identical processing
    graph, _nodes_merged, _servers = _run_to_step_2d([s1, s2])

    survivors = _observed_tool_nodes(graph, {"aaaa1", "aaaa2"})
    assert len(survivors) == 1, "the two observed same-processing tool nodes must merge to one"

    # Edges are preserved: the survivor still participates in interaction edges
    # (routed through its Teal server), none dangling to a dropped node.
    node_ids = {n.id for n in graph.nodes}
    for e in graph.edges:
        assert e.from_node_id in node_ids and e.to_node_id in node_ids


def test_step2d_does_not_overmerge_distinct_observed_calls():
    """Guard: two observed `get_weather` calls with DIFFERENT arguments are
    distinct processing and must NOT merge, even though tool name, kind, scope,
    and time all match."""
    d1 = _tool_span("bbbb1", inp='{"city": "NYC"}')
    d2 = _tool_span("bbbb2", inp='{"city": "LA"}')
    graph, _n, _s = _run_to_step_2d([d1, d2])

    survivors = _observed_tool_nodes(graph, {"bbbb1", "bbbb2"})
    assert len(survivors) == 2, "distinct-argument calls must stay separate"


def test_step2d_does_not_overmerge_across_scope_or_name():
    """Guard: same arguments/time but a different tool name (distinct entity),
    and same name/args but a different scope, both stay separate."""
    # Different tool name.
    n1 = _tool_span("cccc1", name="get_weather")
    n2 = _tool_span("cccc2", name="get_traffic")
    g1, _, _ = _run_to_step_2d([n1, n2])
    assert len(_observed_tool_nodes(g1, {"cccc1", "cccc2"})) == 2

    # Same name/args, different scope.
    other_scope = {"name": "openinference.instrumentation.langchain", "version": "0.1.0"}
    p1 = _tool_span("dddd1")
    p2 = _tool_span("dddd2", scope=other_scope)
    g2, _, _ = _run_to_step_2d([p1, p2])
    assert len(_observed_tool_nodes(g2, {"dddd1", "dddd2"})) == 2


# ---------------------------------------------------------------------------
# GAP 2 — Step 3.a broadened same-entity combine (observed entities)
# ---------------------------------------------------------------------------


def _entity(label, *, inferred, span_ids, peer_key=None):
    n = EntityNode.make()
    n.label = label
    n.inferred = inferred
    n.span_ids = list(span_ids)
    n.peer_match_key = peer_key
    return n


def test_step3a_merges_observed_same_entity_keeping_edges():
    """Two OBSERVED entities that are the same tool (same typed identity + kind
    + scope) merge to one, and both distinct calls to it survive as distinct
    interaction edges (edges preserved, re-pointed to the survivor)."""
    caller = _entity("svcA", inferred=False, span_ids=["c1"])
    t1 = _entity("tool:fs", inferred=False, span_ids=["a"])
    t2 = _entity("tool:fs", inferred=False, span_ids=["b"])
    g = EntityGraph()
    g.nodes = [caller, t1, t2]
    # Two distinct calls (different order bands) to the same tool.
    g.edges = [
        EntityEdge.make(caller.id, t1.id, order=0),
        EntityEdge.make(caller.id, t2.id, order=2),
    ]

    merged = B.combine_identical_entities(g)  # no spans → scope drops out of key

    assert merged == 1
    labels = sorted(n.label for n in g.nodes)
    assert labels == ["svcA", "tool:fs"]
    # Both interaction edges preserved and now point at the single survivor.
    tool_ids = {n.id for n in g.nodes if n.label == "tool:fs"}
    assert len(g.edges) == 2
    assert all(e.to_node_id in tool_ids for e in g.edges)
    assert {e.order for e in g.edges} == {0, 2}, "distinct calls stay distinct"


def test_step3a_scope_separates_same_named_observed_entities():
    """Two observed same-named tool entities in DIFFERENT scopes are treated as
    distinct entities and do NOT merge; same scope merges."""
    class _S:
        def __init__(self, sid, scope):
            self.span_id = sid
            self.scope = {"name": scope}

    # Different scope → no merge.
    a = _entity("tool:fs", inferred=False, span_ids=["a"])
    b = _entity("tool:fs", inferred=False, span_ids=["b"])
    g = EntityGraph()
    g.nodes = [a, b]
    span_by_id = {"a": _S("a", "scopeX"), "b": _S("b", "scopeY")}
    assert B.combine_identical_entities(g, span_by_id) == 0

    # Same scope → merge.
    a2 = _entity("tool:fs", inferred=False, span_ids=["a"])
    b2 = _entity("tool:fs", inferred=False, span_ids=["b"])
    g2 = EntityGraph()
    g2.nodes = [a2, b2]
    span_by_id2 = {"a": _S("a", "scopeX"), "b": _S("b", "scopeX")}
    assert B.combine_identical_entities(g2, span_by_id2) == 1


def test_step3a_does_not_merge_distinct_or_observed_vs_inferred():
    """Guard: distinct tool names never merge, and an observed entity is never
    merged into an inferred peer of the same name (inferred-vs-observed is part
    of the key). The existing inferred-peer convergence still works."""
    obs = _entity("tool:fs", inferred=False, span_ids=["o"])
    web = _entity("tool:web", inferred=False, span_ids=["w"])
    inf = _entity("tool:fs", inferred=True, span_ids=["i1"], peer_key="tool:fs")
    inf2 = _entity("tool:fs", inferred=True, span_ids=["i2"], peer_key="tool:fs")
    g = EntityGraph()
    g.nodes = [obs, web, inf, inf2]

    merged = B.combine_identical_entities(g)

    # The two inferred `tool:fs` peers converge (1 merged); observed `tool:fs`
    # and `tool:web` are untouched.
    assert merged == 1
    remaining = {(n.label, n.inferred) for n in g.nodes}
    assert ("tool:fs", False) in remaining   # observed survivor kept distinct
    assert ("tool:web", False) in remaining   # distinct name kept
    assert ("tool:fs", True) in remaining     # one inferred survivor
    # Exactly three nodes remain: observed fs, web, one inferred fs.
    assert len(g.nodes) == 3
