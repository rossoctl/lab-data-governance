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
               Black edges represents a one-sided observation. Materialise an
               inferred Black peer (carrying is_inferred=True) and add
               bidirectional Black edges between them.
  Step 3.a   — entity graph: connected components over Gray/Black nodes via
               White and Gray edges (Black ignored) → entity nodes (phase 1);
               Black edges → directed entity edges. Phase 2 then combines
               inferred entity nodes whose source-span identifying attributes
               match, so the same unobserved real peer called from N sources
               is represented by one entity rather than N look-alikes.
  Step 3.b   — name nodes: each entity is named from its subgraph
               (service.name, else the natural-key suffix, else 'unknown';
               hostname-based naming is deferred — see ADR-0007).

Edge coloring is additive: an edge can carry multiple colors at once. The
underlying White connectivity is preserved when Gray/Black are added on top.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from data_governance.retrieval import Span

from .adapters import Kind, extract_facts
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


# Role/Kind values carried on Node as plain strings (Role/Kind are str-enums
# in adapters.py; mirrored here to avoid an import cycle).
_ROLE_SOURCE = "SOURCE"
_ROLE_TARGET = "TARGET"
_ROLE_BOTH = "BOTH"


# ---------------------------------------------------------------------------
# Interaction order bands (ADR-0007 "Inferred interaction ordering")
# ---------------------------------------------------------------------------
#
# Several Black (interaction) edges derived from one span share that span's
# `started_at`, so an explicit `order` breaks the tie. The bands encode the
# spec rules directly:
#
#   rule 3 — input-derived tools BEFORE the LLM   → negative band
#   rule 1 — call BEFORE its response              → call = even, response = odd
#   rule 4 — output-derived tools AFTER the LLM    → positive band
#
# `_INPUT_TOOL_BASE` + 2*k gives the k-th input tool's call slot (response is
# +1); the LLM/agent/one-sided call sits at 0 (response 1); `_OUTPUT_TOOL_BASE`
# + 3*k gives the k-th output tool's call slot (response +1, leaving room for
# the Gray fold edge which carries no order). The bands are spaced far apart so
# they never interleave for realistic tool counts.
_INPUT_TOOL_BASE = -40   # input tool k: call = -40+2k, response = -40+2k+1
_CALL_ORDER = 0          # the agent↔LLM / combined / one-sided call
_RESPONSE_ORDER = 1      # its response
_OUTPUT_TOOL_BASE = 40   # output tool k: call = 40+3k, response = 40+3k+1


def _is_matched_call_pair(a: Node, b: Node) -> bool:
    """True iff Black nodes `a` and `b` form a matched cross-entity call pair
    for Step 2.d edge promotion: one is exactly the SOURCE (caller) side, the
    other exactly the TARGET (callee) side, AND they share the same
    entity-kind (tool→tool, llm→llm, agent→agent).

    A pair where both nodes are SOURCE is NOT a call pair: an agent's `query`
    span and its own `ClaudeAgentSDK.{tool}` dispatch span are both SOURCE and
    belong to the same entity, so the Gray edge between them must stay Gray
    (the dispatch's real callee is materialised as an inferred TARGET peer in
    Step 2.b instead).

    `BOTH` (a combined source-and-target span) is deliberately excluded here:
    its target is the duplicate node created in Step 2.b, wired with Black
    edges directly — it does not acquire a target by gray-chain promotion to
    an unrelated adjacent boundary. Same-kind is required so an LLM call
    adjacent to a tool call is not mistaken for a call between them.
    """
    if a.kind is None or b.kind is None or a.kind != b.kind:
        return False
    return (a.role == _ROLE_SOURCE and b.role == _ROLE_TARGET) or (
        a.role == _ROLE_TARGET and b.role == _ROLE_SOURCE
    )


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
        node.role = result.role
        node.kind = result.kind
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

    # 4. Promote a Gray edge between two Black endpoints to Black ONLY when
    #    the endpoints form a matched call pair: one is the SOURCE (caller)
    #    side and the other the TARGET (callee) side of the SAME entity-kind
    #    (tool→tool, llm→llm, agent→agent). A Black edge means a cross-entity
    #    call, so two adjacent SOURCE boundaries on the same chain (e.g. an
    #    agent's `query` span and its own `ClaudeAgentSDK.{tool}` dispatch
    #    span — both SOURCE) must stay Gray: they belong to the same entity.
    #    See ADR-0007 Step 2.d.
    for edge in graph.edges:
        if GRAY not in edge.colors:
            continue
        a = nodes_by_id.get(edge.from_node_id)
        b = nodes_by_id.get(edge.to_node_id)
        if a is None or b is None:
            continue
        if a.color == BLACK and b.color == BLACK and _is_matched_call_pair(a, b):
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
        # The duplicate is the target side of the combined span; it shares the
        # original's entity-kind and plays the TARGET role.
        dup.kind = node.kind
        dup.role = _ROLE_TARGET
        target_label = node.attributes.get("_target_label")
        dup.label = target_label if isinstance(target_label, str) else None
        # Pool attributes so the target entity has the span's payload too.
        dup.attributes = dict(node.attributes)
        # Strip the markers; only the original carries them.
        dup.attributes.pop("_combined", None)
        dup.attributes.pop("_target_label", None)
        new_nodes.append(dup)

        # Request: source → target. Response: target → source.
        req = Edge.make(node.id, dup.id, BLACK, order=_CALL_ORDER)
        resp = Edge.make(dup.id, node.id, BLACK, order=_RESPONSE_ORDER)
        new_edges.append(req)
        new_edges.append(resp)

    graph.nodes.extend(new_nodes)
    graph.edges.extend(new_edges)


# ---------------------------------------------------------------------------
# Step 2.b case 3 — Tool nodes inferred from an LLM span's tool_calls attribute
# ---------------------------------------------------------------------------


# Role/Kind string mirrors (Role/Kind are str-enums in adapters.py; mirrored
# here as plain strings to avoid an import cycle — same pattern as the
# _ROLE_* constants above).
_KIND_TOOL = "TOOL"


def infer_tool_calls_from_attributes(
    graph: BaseGraph, spans_by_id: dict[str, Span]
) -> None:
    """ADR-0007 Step 2.b case 3 — materialise inferred tool nodes from an LLM
    span's `tool_calls`, both output- and input-side.

    Some agentic spans evidence a *tool the model asked to invoke* in an
    attribute rather than as a separate span (the raw Anthropic client
    instrumentation does exactly this — see `openinference_anthropic_v1.0.6_…`).
    The adapter surfaces those on `SpanFacts.tool_calls` (output side, what the
    model asked for *as a result of* this call) and `SpanFacts.input_tool_calls`
    (input side, a prior turn's tool use replayed back into the request). For
    each tool call we infer the two nodes and three edges the spec mandates:

      * a **tool-call node** (the source — the act of calling, inside the LLM's
        turn): BLACK, role=SOURCE, kind=TOOL;
      * a **tool node** (the target — the tool itself): BLACK, role=TARGET,
        kind=TOOL, carrying `peer_match_key=tool:<name>` so repeated calls to
        the same tool converge in Step 3.a phase 2;
      * edge (a) current span → tool-call node — **Gray** (so the tool-call
        node folds into the LLM's entity in Step 3.a, exactly like a dispatch
        span folding into its agent);
      * edge (b) tool-call node → tool node — **Black** (the cross-entity call);
      * edge (c) tool node → tool-call node — **Black** (the reverse / response).

    **Ordering (ADR-0007 "Inferred interaction ordering").** Output-derived
    tools are ordered *after* the LLM interaction (positive band, rule 4);
    input-derived tools *before* it (negative band, rule 3). The call edge (b)
    always precedes its response edge (c). The Gray fold edge (a) carries no
    order (it is not an interaction).

    Both inferred nodes reference the *originating LLM span* (they have no span
    of their own). The tool-call node carries the call's name/arguments as
    `_tool_call_name` / `_tool_call_arguments`; `build_entity_graph` copies the
    arguments onto the resulting entity edge so the LLM→tool interaction's
    request payload is the tool arguments, not the LLM completion.

    Gated by the adapter: only adapters that populate `SpanFacts.tool_calls` /
    `input_tool_calls` (currently anthropic) trigger this. Frameworks that emit
    a real tool-execution span (e.g. openai_agents) leave both empty, so their
    already-observed tools are not double-inferred. Per the human spec, every
    input-side tool is materialised (including a replay of a prior turn's
    output) — it is a genuine prior interaction fed back, ordered ahead of this
    turn's LLM call.
    """
    new_nodes: list[Node] = []
    new_edges: list[Edge] = []

    def _materialise(node: Node, call: dict, *, call_order: int) -> None:
        name = call.get("name")
        if not name:
            return
        natural_key = f"tool:{name}"

        tool_call_node = Node.make(span_id=node.span_id, scope=node.scope, color=BLACK)
        tool_call_node.is_boundary = True
        tool_call_node.is_inferred = True
        tool_call_node.kind = _KIND_TOOL
        tool_call_node.role = _ROLE_SOURCE
        # Deliberately NO label: the tool-call node folds (via the Gray edge)
        # into the caller's entity, and `EntityNode.absorb` takes the first
        # non-empty label it sees. A label here would race the caller's own
        # identity (e.g. relabel the agent/LLM entity `tool:database`). The
        # tool identity lives on the tool node below.
        tool_call_node.attributes["_tool_call_name"] = name
        tool_call_node.attributes["_tool_call_arguments"] = call.get("arguments")

        tool_node = Node.make(span_id=node.span_id, scope=node.scope, color=BLACK)
        tool_node.is_boundary = True
        tool_node.is_inferred = True
        tool_node.kind = _KIND_TOOL
        tool_node.role = _ROLE_TARGET
        tool_node.label = natural_key
        tool_node.peer_match_key = natural_key

        new_nodes.append(tool_call_node)
        new_nodes.append(tool_node)
        # (a) Gray: folds the tool-call node into the LLM span's entity.
        new_edges.append(Edge.make(node.id, tool_call_node.id, GRAY))
        # (b)/(c) Black: the cross-entity call (call_order) and its reverse
        # (call_order + 1, so the call always precedes its response).
        new_edges.append(Edge.make(tool_call_node.id, tool_node.id, BLACK, order=call_order))
        new_edges.append(Edge.make(tool_node.id, tool_call_node.id, BLACK, order=call_order + 1))

    for node in graph.nodes:
        if node.color not in (GRAY, BLACK):
            continue
        span = spans_by_id.get(node.span_id)
        if span is None:
            continue
        facts = extract_facts(span)
        # Input-derived tools first (negative band — ordered before the LLM).
        for k, call in enumerate(facts.input_tool_calls or ()):
            _materialise(node, call, call_order=_INPUT_TOOL_BASE + 2 * k)
        # Output-derived tools (positive band — ordered after the LLM). The
        # 3*k stride leaves the odd slot free for the response edge.
        for k, call in enumerate(facts.tool_calls or ()):
            _materialise(node, call, call_order=_OUTPUT_TOOL_BASE + 3 * k)

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


def _peer_match_key(node: Node, spans_by_id: dict[str, Span]) -> str | None:
    """Identifying attribute of the boundary span — used as the combine key in
    Step 3.a phase 2. Two inferred peers stubbing the same real callee from
    different sources end up with the same key.

    Per ADR-0007, the key is "the source-span identifying attribute used by
    the originating boundary's classifier". The actual attribute lookup
    lives in `adapters.py`, dispatched on (scope, framework, version);
    here we just ask the matching adapter for `SpanFacts.natural_key`.
    Returns None when no identifying attribute is available — Step 3.a
    phase 2 leaves keyless inferred peers distinct.
    """
    span = spans_by_id.get(node.span_id)
    if span is None:
        return None
    return extract_facts(span).natural_key


def synthesize_missing_peers(graph: BaseGraph, spans_by_id: dict[str, Span]) -> None:
    """For every Black boundary node with no Black edges, create an inferred
    Black peer (is_inferred=True) referencing the same span and add
    bidirectional Black edges between them.

    A Black node with no Black edges indicates that the peer side of the call
    was not observed. Step 2.a.4 will already have linked any pair of Black
    nodes that share a Gray chain in the trace, so an isolated Black node is
    a reliable signal for unobserved peer (not "peer present but unlinked").

    The inferred node copies the original node's pooled attributes so the
    resulting entity carries something to display, and inverts the role
    label. Step 3.a's entity graph propagates the inferred marker onto the
    entity node; Step 3.a phase 2 then combines inferred entities whose
    source-span attributes match.
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
        peer.is_inferred = True
        # The inferred peer is the callee, so it plays the opposite role of the
        # observed boundary and shares its entity-kind (a tool call's peer is
        # the tool, an LLM call's peer is the LLM). This keeps the source→peer
        # pair a matched call pair under Step 2.d.
        peer.kind = node.kind
        peer.role = _ROLE_TARGET if node.role in (_ROLE_SOURCE, _ROLE_BOTH) else _ROLE_SOURCE
        # Step 3.a phase 2 uses this key to combine inferred peers stubbing the
        # same real callee from multiple sources. The adapter computes it from
        # the boundary span's identifying attribute, normalised by kind.
        peer.peer_match_key = _peer_match_key(node, spans_by_id)
        # The inferred peer represents the callee, not the emitter. The
        # natural-key (e.g. `tool:get_weather`) IS the callee's identity,
        # so it's the right thing to show. Fall back to the generic
        # "unobserved peer of X" only when no key was extractable —
        # those cases stay distinct in 3.a phase 2 too, so a generic label
        # is honest about the missing information.
        peer.label = peer.peer_match_key or (
            f"(unobserved peer of {node.label})" if node.label else "(unobserved peer)"
        )
        # Pool attributes so the inferred entity has something to display.
        peer.attributes = dict(node.attributes)
        peer.attributes.pop("_combined", None)
        peer.attributes.pop("_target_label", None)
        new_nodes.append(peer)

        new_edges.append(Edge.make(node.id, peer.id, BLACK, order=_CALL_ORDER))
        new_edges.append(Edge.make(peer.id, node.id, BLACK, order=_RESPONSE_ORDER))

    graph.nodes.extend(new_nodes)
    graph.edges.extend(new_edges)


# ---------------------------------------------------------------------------
# Step 2.c — Intra-trace merge (inferred ↔ observed)
# ---------------------------------------------------------------------------


def _white_gray_neighbors(graph: BaseGraph) -> dict[str, set[str]]:
    """Undirected adjacency over White and Gray edges only (Black ignored) —
    the same connectivity Step 3.a uses to form entities. Used by Step 2.c as
    a proximity guard: an inferred node may only fold into an observed twin
    that is reachable over this adjacency (a sibling/ancestor in the
    execution-flow graph), never an unrelated same-key node elsewhere in the
    trace.
    """
    adj: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if BLACK in edge.colors:
            continue
        adj[edge.from_node_id].add(edge.to_node_id)
        adj[edge.to_node_id].add(edge.from_node_id)
    return adj


def _white_gray_components(graph: BaseGraph) -> dict[str, int]:
    """Label every node with its White+Gray connected-component id — the same
    components Step 3.a collapses into entities. Two nodes in the same
    component are the *same* entity (a caller and its own plumbing); two in
    different components are distinct entities linked only by a Black call.
    """
    adj = _white_gray_neighbors(graph)
    comp: dict[str, int] = {}
    cid = 0
    for node in graph.nodes:
        if node.id in comp:
            continue
        queue: deque[str] = deque([node.id])
        comp[node.id] = cid
        while queue:
            nid = queue.popleft()
            for nb in adj.get(nid, ()):
                if nb not in comp:
                    comp[nb] = cid
                    queue.append(nb)
        cid += 1
    return comp


def merge_inferred_into_observed(
    graph: BaseGraph, spans_by_id: dict[str, Span]
) -> int:
    """ADR-0007 / spec Step 2.c — merge an **inferred** node into the
    **observed** node representing the same entity, in the same trace, pooling
    their edges and attributes.

    Heuristic (spec def. 8 — "can be based on heuristics"; spec lists two
    signals: proximity in the trace, and similarity of attributes):
      1. **Identifying attribute + kind.** An inferred node folds only into an
         observed boundary node whose identifying key (the adapter's
         `natural_key` / `peer_match_key`) AND entity-kind both match. Kind is
         required so an inferred `tool:foo` never folds into an unrelated
         observed boundary that happens to share a key string.
      2. **Different White/Gray component.** The observed twin must live in a
         *different* White+Gray connected component than the inferred node's
         source — i.e. it is an *independent observation of the callee*, not
         another caller of it. This is the decisive guard: in a single-agent
         trace every same-key boundary is a sibling *caller* on the agent's own
         Gray chain (same component) — none of those is a second observation of
         the callee, so none qualifies and the pass is a no-op. An observed
         callee that emitted its own spans sits in its own component (reached
         only across the Black call edge), so it qualifies. This is exactly the
         spec's "matched send/receive across the boundary represent the same
         entity" case. If several qualify, the nearest component by Black-hop is
         not distinguished here — the first is taken (prototype-simple).

    On a match the inferred node's attributes and `label` are pooled onto the
    observed node, its Black edges are rewired onto the observed node (self
    loops dropped), and the inferred node is removed.

    Complementary to the Step 3.a phase-2 combine (`merge_inferred_peers`): 2.c
    removes an inferred node *in favour of an observed twin* (needs an
    independent observation of the same callee); phase 2 fuses inferred
    entities that share a key *when none was observed*. With no observed callee
    in the trace (the common case), this pass is a no-op and every inferred peer
    passes through to Step 3.a unchanged.

    Returns the number of inferred nodes merged away.
    """
    comp = _white_gray_components(graph)

    # The inferred peer references the same span as the observed boundary it
    # stubs for; that boundary's node sits in the peer's "source" component.
    # Map span_id → component of the observed (non-inferred) node for that span.
    source_comp_for_span: dict[str, int] = {}
    for node in graph.nodes:
        if not node.is_inferred and node.span_id:
            source_comp_for_span.setdefault(node.span_id, comp[node.id])

    # Observed boundary nodes, indexed by (identifying key, kind).
    observed_by_key: dict[tuple[str, str | None], list[Node]] = defaultdict(list)
    for node in graph.nodes:
        if node.is_inferred or node.color != BLACK or not node.is_boundary:
            continue
        key = _peer_match_key(node, spans_by_id) or node.label
        if key:
            observed_by_key[(key, node.kind)].append(node)

    if not observed_by_key:
        return 0

    redirect: dict[str, str] = {}
    drop_ids: set[str] = set()
    for node in graph.nodes:
        if not node.is_inferred:
            continue
        key = node.peer_match_key or node.label
        candidates = observed_by_key.get((key, node.kind)) if key else None
        if not candidates:
            continue
        # The peer's source component (the caller it stubs for). An observed
        # twin in that same component is a sibling caller, not the callee.
        src_comp = source_comp_for_span.get(node.span_id, comp.get(node.id))
        best: Node | None = None
        for obs in candidates:
            if obs.id in drop_ids:
                continue
            if comp[obs.id] == src_comp:
                continue  # same component → a caller, not the observed callee
            best = obs
            break
        if best is None:
            continue
        # Pool attributes + label onto the observed survivor.
        for k, v in node.attributes.items():
            best.attributes.setdefault(k, v)
        if node.label and not best.label:
            best.label = node.label
        redirect[node.id] = best.id
        drop_ids.add(node.id)

    if not drop_ids:
        return 0

    # Rewire every edge referencing a merged inferred node onto its survivor;
    # drop self-loops created by the rewrite.
    rewritten: list[Edge] = []
    for edge in graph.edges:
        src = redirect.get(edge.from_node_id, edge.from_node_id)
        dst = redirect.get(edge.to_node_id, edge.to_node_id)
        if src == dst:
            continue
        edge.from_node_id = src
        edge.to_node_id = dst
        rewritten.append(edge)
    graph.edges = rewritten
    graph.nodes = [n for n in graph.nodes if n.id not in drop_ids]
    return len(drop_ids)


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
            # Carry the originating Black edge's intra-turn order onto the
            # entity edge (ADR-0007 "Inferred interaction ordering"). Pre-merge
            # entity pairs are distinct per call site, so each EntityEdge owns
            # one order; the Step 3.a phase-2 rewrite preserves it.
            ent_edge = EntityEdge.make(src_eid, dst_eid, order=edge.order)
            edge_index[key] = ent_edge
            entity_graph.edges.append(ent_edge)
        # Pool span_ids from the Black edge's endpoint span(s).
        for endpoint_id in (edge.from_node_id, edge.to_node_id):
            n = nodes_by_id.get(endpoint_id)
            if n and n.span_id and n.span_id not in ent_edge.span_ids:
                ent_edge.span_ids.append(n.span_id)
        # Step 2.b case 3: a tool-call node (the Black edge's source) carries
        # the inferred call's arguments. Surface them as the LLM→tool
        # interaction's request payload (otherwise the extractor would derive
        # the LLM completion off the shared span — see EntityEdge.req_payload).
        from_node = nodes_by_id.get(edge.from_node_id)
        if (
            ent_edge.req_payload is None
            and from_node is not None
            and "_tool_call_arguments" in from_node.attributes
        ):
            ent_edge.req_payload = (
                "tool_call_arguments",
                from_node.attributes["_tool_call_arguments"],
            )

    return entity_graph


# ---------------------------------------------------------------------------
# Step 3.a phase 2 — Combine inferred entity nodes by identifying attribute
# ---------------------------------------------------------------------------


def merge_inferred_peers(entity_graph: EntityGraph) -> int:
    """Combine inferred entity nodes that stub the same unobserved real peer.

    Two inferred entities are considered identical iff they carry the same
    `peer_match_key` (set in Step 2.c from the originating boundary's
    classifier label). When N inferred entities share a key, all but one
    are dropped; every entity edge that referenced a dropped peer is
    rewritten to point at the surviving peer.

    All edges and interactions are preserved across the combine (ADR-0007
    Step 3.a phase 2): every entity edge incident on any combined peer
    survives as a distinct edge on the surviving entity — no dedup by endpoint
    pair, no collapsing. Each pre-combine edge represents a distinct observed
    call site, so the count and provenance of calls to the unobserved peer
    survives. Self-loops created by the rewrite (would only arise if two
    inferred peers with the same key were directly connected — not produced by
    Step 2.c today) are still dropped.

    Observed entities are not combined here — only `entity.inferred == True`
    nodes participate.

    Returns the number of entities removed.
    """
    # Group inferred entities by peer_match_key; entities without a key
    # cannot be matched and remain distinct.
    by_key: dict[str, list[EntityNode]] = defaultdict(list)
    for node in entity_graph.nodes:
        if not node.inferred or not node.peer_match_key:
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

    # Rewrite edges in place; preserve every edge as a distinct edge per
    # ADR-0007 Step 3.a phase 2. Self-loops (would only arise if two inferred
    # peers with the same key were connected directly) are dropped.
    rewritten: list[EntityEdge] = []
    for edge in entity_graph.edges:
        src = redirect.get(edge.from_node_id, edge.from_node_id)
        dst = redirect.get(edge.to_node_id, edge.to_node_id)
        if src == dst:
            continue
        edge.from_node_id = src
        edge.to_node_id = dst
        rewritten.append(edge)

    entity_graph.edges = rewritten
    entity_graph.nodes = [n for n in entity_graph.nodes if n.id not in drop_ids]
    return len(drop_ids)
