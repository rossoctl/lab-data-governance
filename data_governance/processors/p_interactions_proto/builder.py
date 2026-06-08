"""Graph construction for the p_interactions prototype. THROWAWAY.

Implements the algorithm described in docs/adr/0007-p-interactions-graph-algorithm.md:

  Step 1     — build the base graph (one node per span; one White directed
               parent→child edge per traceparent relationship).
  Step 2.a   — agentic coloring (additive). Color openinference-scope nodes
               Gray, add Gray edges between consecutive Gray nodes, color
               boundary nodes Black, add Black edges between Black-to-Black
               Gray edges. (a2a and mcp scopes are deferred — see ADR-0007.)
  Step 2.b   — combined source-and-target spans: duplicate the Black node;
               original keeps its parent/child chains and represents the
               source; duplicate stands alone and represents the target. Add
               request/response Black edges between them.
  Step 2.c   — synthesize missing peers: any Black boundary node with no
               Black edges represents a one-sided observation. Materialise a
               synthetic Black peer (carrying is_synthetic=True) and add
               bidirectional Black edges between them.
  Step 3.a   — entity graph: connected components over Gray/Black nodes via
               White and Gray edges (Black ignored) → entity nodes; Black
               edges → directed entity edges.
  Step 3.b   — merge identical synthetic peers: synthetic entity nodes whose
               source-span identifying attributes match collapse into one,
               so the same unobserved real peer called from N sources is
               represented by one entity rather than N look-alikes.
  Step 3.c   — name nodes: each entity is assigned the ID 'unknown' (richer
               naming is deferred — see ADR-0007).

Edge coloring is additive: an edge can carry multiple colors at once. The
underlying White connectivity is preserved when Gray/Black are added on top.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from data_governance.retrieval import Span

from .classifiers import (
    AgenticClassification,
    get_agentic_classifier,
    is_agentic_scope,
)
from .graph import (
    BLACK,
    BaseGraph,
    Edge,
    EntityEdge,
    EntityGraph,
    EntityNode,
    GRAY,
    Node,
    WHITE,
)


def _attrs(span: Span) -> dict[str, Any]:
    return dict(span.attributes or {})


def _scope_name(span: Span) -> str:
    return (span.scope or {}).get("name") or "(unknown)"


# ---------------------------------------------------------------------------
# Step 1 — Base graph
# ---------------------------------------------------------------------------


def build_base_graph(spans: list[Span]) -> BaseGraph:
    """Construct the white base graph: one node per span, one White directed
    edge per traceparent parent→child relationship.

    Spans whose parent is not in this trace (broken traceparent, root spans)
    simply have no incoming edge. The graph may be disconnected; that is
    handled implicitly by the connected-component step (no special logic).
    """
    graph = BaseGraph()
    node_for_span: dict[str, Node] = {}

    for span in spans:
        node = Node.make(span_id=span.span_id, scope=_scope_name(span), color=WHITE)
        node.attributes = _attrs(span)
        graph.nodes.append(node)
        node_for_span[span.span_id] = node

    for span in spans:
        if not span.parent_id:
            continue
        parent = node_for_span.get(span.parent_id)
        child = node_for_span.get(span.span_id)
        if parent is None or child is None:
            continue
        graph.edges.append(Edge.make(parent.id, child.id, WHITE))

    return graph


# ---------------------------------------------------------------------------
# Adjacency helpers
# ---------------------------------------------------------------------------


def _white_adjacency(graph: BaseGraph) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build directed (parent→child) and reverse adjacency over White edges."""
    fwd: dict[str, list[str]] = defaultdict(list)
    rev: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if WHITE in edge.colors:
            fwd[edge.from_node_id].append(edge.to_node_id)
            rev[edge.to_node_id].append(edge.from_node_id)
    return fwd, rev


# ---------------------------------------------------------------------------
# Step 2.a — Agentic coloring
# ---------------------------------------------------------------------------


def color_agentic(graph: BaseGraph, spans_by_id: dict[str, Span]) -> None:
    """Apply Step 2.a coloring in place.

    1. Color each agentic-scope node Gray; record the per-scope classifier
       result (boundary? combined? labels?) for use in 2.b.
    2. For each pair of Gray nodes connected by a White-edge chain that
       passes through no other Gray node, add a directed Gray edge between
       them in the same direction as the underlying chain.
    3. For each Gray node whose classifier said is_boundary, recolor Black.
    4. For each Gray edge whose endpoints are both Black, add Black to the
       edge's color set.
    """
    # 1. Gray nodes from agentic scopes.
    for node in graph.nodes:
        if not is_agentic_scope(node.scope):
            continue
        node.color = GRAY
        span = spans_by_id.get(node.span_id)
        if span is None:
            continue
        classifier = get_agentic_classifier(node.scope)
        if classifier is None:
            continue
        result: AgenticClassification = classifier(span)
        node.is_boundary = result.is_boundary
        if result.label and not node.label:
            node.label = result.label
        # Stash the classification on the node attributes for 2.b.
        if result.is_combined:
            node.attributes["_combined"] = True
            if result.target_label:
                node.attributes["_target_label"] = result.target_label

    # 2. Gray edges between consecutive Gray nodes.
    fwd, _rev = _white_adjacency(graph)
    nodes_by_id = {n.id: n for n in graph.nodes}

    for src in graph.nodes:
        if src.color != GRAY and src.color != BLACK:
            # Only Gray/Black are sources for this step (Black hasn't been
            # applied yet at this point, but written defensively).
            continue
        if src.color == WHITE:
            continue
        # BFS forward through White edges, stopping at the next Gray node.
        visited: set[str] = {src.id}
        queue: deque[str] = deque(fwd[src.id])
        for nid in fwd[src.id]:
            visited.add(nid)
        while queue:
            cur_id = queue.popleft()
            cur = nodes_by_id[cur_id]
            if cur.color == GRAY or cur.color == BLACK:
                # Reached a Gray node — add a Gray edge from src → cur.
                graph.edges.append(Edge.make(src.id, cur.id, GRAY))
                continue  # do not traverse past a Gray node
            for nxt in fwd[cur_id]:
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)

    # 3. Promote boundary Gray nodes to Black.
    for node in graph.nodes:
        if node.color == GRAY and node.is_boundary:
            node.color = BLACK

    # 4. Promote Gray edges between two Black endpoints to also Black.
    for edge in graph.edges:
        if GRAY not in edge.colors:
            continue
        a = nodes_by_id.get(edge.from_node_id)
        b = nodes_by_id.get(edge.to_node_id)
        if a is None or b is None:
            continue
        if a.color == BLACK and b.color == BLACK:
            edge.add_color(BLACK)


# ---------------------------------------------------------------------------
# Step 2.b — Combined source-and-target spans
# ---------------------------------------------------------------------------


def duplicate_combined_nodes(graph: BaseGraph) -> None:
    """For each Black node flagged combined (`_combined` attribute), create a
    duplicate Black node referencing the same span. The duplicate has no
    neighbours in the base graph. Add two directed Black edges between
    original (source) and duplicate (target): source→target (request) and
    target→source (response)."""
    new_nodes: list[Node] = []
    new_edges: list[Edge] = []
    for node in graph.nodes:
        if node.color != BLACK:
            continue
        if not node.attributes.get("_combined"):
            continue

        dup = Node.make(span_id=node.span_id, scope=node.scope, color=BLACK)
        dup.is_boundary = True
        dup.is_target_duplicate = True
        target_label = node.attributes.get("_target_label")
        dup.label = target_label if isinstance(target_label, str) else None
        # Pool attributes so the target entity has the span's payload too.
        dup.attributes = dict(node.attributes)
        # Strip the markers; only the original carries them.
        dup.attributes.pop("_combined", None)
        dup.attributes.pop("_target_label", None)
        new_nodes.append(dup)

        # Request: source → target. Response: target → source.
        req = Edge.make(node.id, dup.id, BLACK)
        resp = Edge.make(dup.id, node.id, BLACK)
        new_edges.append(req)
        new_edges.append(resp)

    graph.nodes.extend(new_nodes)
    graph.edges.extend(new_edges)


# ---------------------------------------------------------------------------
# Between-boundaries flag
# ---------------------------------------------------------------------------


def flag_between_boundaries(graph: BaseGraph) -> None:
    """Annotate every Gray (non-boundary) node that lies on a Gray chain
    between two Black boundary nodes. Surfaced in the UI; informational only.

    A Gray node is flagged iff both:
      - some Black node has a directed Gray-edge path to it, AND
      - it has a directed Gray-edge path to some Black node.

    This catches plumbing-tagged spans that are actually transmitting a call
    between boundaries — likely missing classifier entries.
    """
    nodes_by_id = {n.id: n for n in graph.nodes}

    # Build Gray adjacency (directed, considering both Gray and Black edges
    # — Black implies Gray since coloring is additive).
    gray_fwd: dict[str, list[str]] = defaultdict(list)
    gray_rev: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if GRAY not in edge.colors:
            continue
        gray_fwd[edge.from_node_id].append(edge.to_node_id)
        gray_rev[edge.to_node_id].append(edge.from_node_id)

    def _reachable(starts: list[str], adj: dict[str, list[str]]) -> set[str]:
        seen: set[str] = set()
        queue: deque[str] = deque(starts)
        while queue:
            nid = queue.popleft()
            if nid in seen:
                continue
            seen.add(nid)
            queue.extend(adj.get(nid, []))
        return seen

    blacks = [n.id for n in graph.nodes if n.color == BLACK]
    downstream_of_black = _reachable(blacks, gray_fwd) - set(blacks)
    upstream_of_black = _reachable(blacks, gray_rev) - set(blacks)

    flagged_ids = downstream_of_black & upstream_of_black

    for nid in flagged_ids:
        n = nodes_by_id.get(nid)
        if n is not None and n.color == GRAY:
            n.flagged = True


# ---------------------------------------------------------------------------
# Step 2.c — Synthesize missing peers (one-sided observation stubs)
# ---------------------------------------------------------------------------


def _peer_match_key(node: Node) -> str | None:
    """Identifying attribute of the boundary span — used as the merge key in
    Step 3.b. Two synthetic peers stubbing the same real callee from
    different sources end up with the same key.

    Per ADR-0007, the key is "the source-span identifying attribute used by
    the originating boundary's classifier" — for openinference TOOL spans
    that's `tool.name` (or the span name, which equals the tool name in the
    OpenAI Agents SDK convention); for LLM spans it's `llm.model_name`
    (with `gen_ai.request.model` as a fallback, matching `_llm_model` in
    classifiers.py). Returns None when no identifying attribute is
    available — Step 3.b leaves keyless synthetics distinct.
    """
    attrs = node.attributes or {}
    oi_kind = attrs.get("openinference.span.kind")
    if oi_kind == "TOOL":
        tool_name = attrs.get("tool.name")
        # OpenAI Agents emits FunctionSpanData where span name == tool name;
        # fall back to the span name when tool.name is absent. We don't have
        # span.name here directly, but the boundary node's label already
        # incorporates it via the classifier — fall back to that.
        if tool_name:
            return f"tool:{tool_name}"
        if node.label and not node.label.startswith("(unobserved"):
            return f"tool:{node.label}"
        return None
    if oi_kind == "LLM":
        model = attrs.get("llm.model_name") or attrs.get("gen_ai.request.model")
        if isinstance(model, str) and "/" in model:
            model = model.split("/", 1)[1]
        if model:
            return f"llm:{model}"
        return None
    if oi_kind == "AGENT":
        agent_name = attrs.get("agent.name") or attrs.get("gen_ai.agent.name")
        if agent_name:
            return f"agent:{agent_name}"
        return None
    return None


def synthesize_missing_peers(graph: BaseGraph) -> None:
    """For every Black boundary node with no Black edges, create a synthetic
    Black peer (is_synthetic=True) referencing the same span and add
    bidirectional Black edges between them.

    A Black node with no Black edges indicates that the peer side of the call
    was not observed. Step 2.a.4 will already have linked any pair of Black
    nodes that share a Gray chain in the trace, so an isolated Black node is
    a reliable signal for unobserved peer (not "peer present but unlinked").

    The synthetic node copies the original node's pooled attributes so the
    resulting entity carries something to display, and inverts the role
    label. Step 3.a's entity graph propagates the synthetic marker onto the
    entity node; Step 3.b then merges synthetic entities whose source-span
    attributes match.
    """
    # Index Black-edge endpoints (only edges with BLACK color count).
    black_endpoints: set[str] = set()
    for edge in graph.edges:
        if BLACK in edge.colors:
            black_endpoints.add(edge.from_node_id)
            black_endpoints.add(edge.to_node_id)

    new_nodes: list[Node] = []
    new_edges: list[Edge] = []
    for node in graph.nodes:
        if node.color != BLACK:
            continue
        if node.id in black_endpoints:
            continue
        # Skip combined-span participants — Step 2.b already paired them.
        # (Defensive: a combined-span original always has Black edges from
        # 2.b, so it would already be in black_endpoints. The duplicate too.)
        if node.is_target_duplicate or node.attributes.get("_combined"):
            continue

        peer = Node.make(span_id=node.span_id, scope=node.scope, color=BLACK)
        peer.is_boundary = True
        peer.is_synthetic = True
        peer.label = f"(unobserved peer of {node.label})" if node.label else "(unobserved peer)"
        # Step 3.b uses this key to merge synthetic peers stubbing the same
        # real callee from multiple sources. Built from the boundary span's
        # identifying attribute (tool.name / llm.model_name / agent.name)
        # rather than the classifier's display label, since the latter can
        # collapse to service.name when no specific identifier is present.
        peer.peer_match_key = _peer_match_key(node)
        # Pool attributes so the synthetic entity has something to display.
        peer.attributes = dict(node.attributes)
        peer.attributes.pop("_combined", None)
        peer.attributes.pop("_target_label", None)
        new_nodes.append(peer)

        new_edges.append(Edge.make(node.id, peer.id, BLACK))
        new_edges.append(Edge.make(peer.id, node.id, BLACK))

    graph.nodes.extend(new_nodes)
    graph.edges.extend(new_edges)


# ---------------------------------------------------------------------------
# Step 3.a — Form entities
# ---------------------------------------------------------------------------


def build_entity_graph(graph: BaseGraph) -> EntityGraph:
    """Compute connected components over Gray/Black nodes using White and
    Gray edges only (Black edges ignored). Each component becomes one entity
    node, with attributes pooled from every contributing span. Black edges
    crossing entity boundaries become directed entity edges."""
    entity_graph = EntityGraph()
    nodes_by_id = {n.id: n for n in graph.nodes}

    # Build undirected adjacency over White and Gray edges. An edge with
    # Black anywhere in its color set acts as a Black entity-boundary edge —
    # ignored for component finding even if it also carries White/Gray
    # (additive coloring keeps the underlying layers for inspection, but
    # Black takes precedence here).
    adj: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if BLACK in edge.colors:
            continue
        adj[edge.from_node_id].append(edge.to_node_id)
        adj[edge.to_node_id].append(edge.from_node_id)

    # Restrict to Gray/Black nodes — White nodes contribute no entity at this
    # stage (non-agentic spans are enrichment-only later).
    coloured_ids = {n.id for n in graph.nodes if n.color in (GRAY, BLACK)}

    seen: set[str] = set()
    node_to_entity: dict[str, str] = {}

    for start_id in coloured_ids:
        if start_id in seen:
            continue
        # BFS over adjacency, restricted to colored nodes.
        component: list[str] = []
        queue: deque[str] = deque([start_id])
        while queue:
            nid = queue.popleft()
            if nid in seen:
                continue
            if nid not in coloured_ids:
                continue
            seen.add(nid)
            component.append(nid)
            for nb in adj.get(nid, []):
                if nb not in seen and nb in coloured_ids:
                    queue.append(nb)

        # Build the entity node, pooling attributes.
        entity = EntityNode.make()
        for nid in component:
            entity.absorb(nodes_by_id[nid])
        entity_graph.nodes.append(entity)
        for nid in component:
            node_to_entity[nid] = entity.id

    # Each Black edge whose endpoints fall in different entities becomes an
    # entity edge. Same-entity Black edges are dropped (shouldn't happen at
    # this point, but guarded for safety).
    edge_index: dict[tuple[str, str], EntityEdge] = {}
    for edge in graph.edges:
        if BLACK not in edge.colors:
            continue
        src_eid = node_to_entity.get(edge.from_node_id)
        dst_eid = node_to_entity.get(edge.to_node_id)
        if src_eid is None or dst_eid is None or src_eid == dst_eid:
            continue
        key = (src_eid, dst_eid)
        ent_edge = edge_index.get(key)
        if ent_edge is None:
            ent_edge = EntityEdge.make(src_eid, dst_eid)
            edge_index[key] = ent_edge
            entity_graph.edges.append(ent_edge)
        # Pool span_ids from the Black edge's endpoint span(s).
        for endpoint_id in (edge.from_node_id, edge.to_node_id):
            n = nodes_by_id.get(endpoint_id)
            if n and n.span_id and n.span_id not in ent_edge.span_ids:
                ent_edge.span_ids.append(n.span_id)

    return entity_graph


# ---------------------------------------------------------------------------
# Step 3.b — Merge identical synthetic peers
# ---------------------------------------------------------------------------


def merge_synthetic_peers(entity_graph: EntityGraph) -> int:
    """Collapse synthetic entity nodes that stub the same unobserved real peer.

    Two synthetic entities are considered identical iff they carry the same
    `peer_match_key` (set in Step 2.c from the originating boundary's
    classifier label). When N synthetic entities share a key, all but one
    are dropped; every entity edge that referenced a dropped peer is
    rewritten to point at the surviving peer. Edges that become self-loops
    or duplicates after rewriting are removed.

    Observed entities are never merged — only `entity.synthetic == True`
    nodes participate.

    Returns the number of entities removed.
    """
    # Group synthetic entities by peer_match_key; entities without a key
    # cannot be matched and remain distinct.
    by_key: dict[str, list[EntityNode]] = defaultdict(list)
    for node in entity_graph.nodes:
        if not node.synthetic or not node.peer_match_key:
            continue
        by_key[node.peer_match_key].append(node)

    if not any(len(group) > 1 for group in by_key.values()):
        return 0

    # For each duplicate group, pick the first as the survivor and map all
    # others' ids to it.
    redirect: dict[str, str] = {}
    drop_ids: set[str] = set()
    for group in by_key.values():
        if len(group) <= 1:
            continue
        survivor = group[0]
        for dup in group[1:]:
            redirect[dup.id] = survivor.id
            drop_ids.add(dup.id)
            # Pool span_ids from the merged peer onto the survivor so its
            # evidence list reflects every observed source that pointed at
            # it.
            for sid in dup.span_ids:
                if sid not in survivor.span_ids:
                    survivor.span_ids.append(sid)

    if not drop_ids:
        return 0

    # Rewrite edges; drop self-loops and duplicates that arise from the
    # rewrite.
    rewritten: list[EntityEdge] = []
    seen_pairs: set[tuple[str, str]] = set()
    for edge in entity_graph.edges:
        src = redirect.get(edge.from_node_id, edge.from_node_id)
        dst = redirect.get(edge.to_node_id, edge.to_node_id)
        if src == dst:
            # Self-loop after rewriting (e.g. a synthetic peer that fed back
            # to itself via a duplicate). Drop.
            continue
        key = (src, dst)
        if key in seen_pairs:
            # Duplicate edge after rewriting — fold span_ids onto the kept
            # one.
            for prev in rewritten:
                if prev.from_node_id == src and prev.to_node_id == dst:
                    for sid in edge.span_ids:
                        if sid not in prev.span_ids:
                            prev.span_ids.append(sid)
                    break
            continue
        seen_pairs.add(key)
        edge.from_node_id = src
        edge.to_node_id = dst
        rewritten.append(edge)

    entity_graph.edges = rewritten
    entity_graph.nodes = [n for n in entity_graph.nodes if n.id not in drop_ids]
    return len(drop_ids)
