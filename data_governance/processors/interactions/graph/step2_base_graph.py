"""Step 2 — enrich the execution-flow graph (Step 2.a–2.d).

ADR-0026:
  Step 2.a — transport coloring (Teal): `color_transport`.
  Step 2.b — agentic coloring (Blue) + Blue chain edges: `color_agentic`.
  Step 2.c — extend the execution graph with inferred nodes and edges:
             `duplicate_combined_nodes`, `infer_tool_calls_from_attributes`,
             `synthesize_missing_peers`, `infer_agent_from_bare_leaf_llms`.
  Step 2.d — node-and-edge merge on the execution-flow graph:
             `merge_identical_interactions`.
"""

from __future__ import annotations

from collections import defaultdict, deque

from data_governance.retrieval import Span

from .adapters import Role, _service, extract_facts, is_transport_scope
from .classifiers import (
    AgenticClassification,
    get_agentic_classifier,
    is_agentic_scope,
)
from .graph import BLUE, BaseGraph, Edge, Node, TEAL, WHITE

from ._shared import (
    _CALL_ORDER,
    _INPUT_TOOL_BASE,
    _KIND_AGENT,
    _KIND_LLM,
    _KIND_TOOL,
    _OUTPUT_TOOL_BASE,
    _ROLE_BOTH,
    _ROLE_SOURCE,
    _ROLE_TARGET,
    _insert_teal_server,
    _is_observed_transport,
    _node_is_boundary,
    _node_kind,
    _node_role,
    _peer_match_key,
    _server_endpoints,
    _server_ids,
    _white_blue_components,
)


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
# Step 2.a — Transport coloring (Teal)
# ---------------------------------------------------------------------------


def color_transport(graph: BaseGraph) -> None:
    """Apply Step 2.a coloring in place: color every transport-scope node
    (httpx / starlette / asgi / aiohttp) Teal.

    Additive, like agentic coloring: the node's White traceparent edges are
    preserved. Teal marks the communication/proxy hop between agentic entities;
    the Step 3.a fuse drops Teal nodes, so a Teal node is what *separates* two
    entities rather than forming one. Transport-scope recognition lives in the
    adapter layer (`is_transport_scope`) so the raw scope strings stay isolated
    there.
    """
    for node in graph.nodes:
        if is_transport_scope(node.scope):
            node.color = TEAL


# ---------------------------------------------------------------------------
# Step 2.b — Agentic coloring (Blue)
# ---------------------------------------------------------------------------


def color_agentic(graph: BaseGraph, spans_by_id: dict[str, Span]) -> None:
    """Apply Step 2.b coloring in place.

    1. Color each agentic-scope node Blue; record the per-scope classifier
       result (boundary? combined? labels?) for use in Step 2.c.
    2. For each pair of Blue nodes connected by a White-edge chain that
       passes through no other Blue node, add a directed Blue edge between
       them in the same direction as the underlying chain.
    3. Mark each Blue node whose classifier said is_boundary as a boundary
       (no color change — boundary-ness is a marker, not a color). Cross-entity
       calls are not drawn here; they are materialised in Step 2.c as routes
       through an inferred Teal server node.
    """
    # 1. Blue nodes from agentic scopes.
    for node in graph.nodes:
        if not is_agentic_scope(node.scope):
            continue
        node.color = BLUE
        span = spans_by_id.get(node.span_id)
        if span is None:
            continue
        classifier = get_agentic_classifier(node.scope)
        if classifier is None:
            continue
        result: AgenticClassification = classifier(span)
        # role / kind / boundary-ness for an observed node are re-derived from
        # its span's facts by the `_node_*` accessors — not stored on the node.
        if result.label and not node.label:
            node.label = result.label
        # Stash the classification on the node attributes for Step 2.c.
        if result.is_combined:
            node.attributes["_combined"] = True
            if result.target_label:
                node.attributes["_target_label"] = result.target_label

    # 2. Blue edges between consecutive Blue nodes.
    fwd, _rev = _white_adjacency(graph)
    nodes_by_id = {n.id: n for n in graph.nodes}

    for src in graph.nodes:
        if src.color != BLUE:
            continue
        # BFS forward through White edges, stopping at the next Blue node.
        visited: set[str] = {src.id}
        queue: deque[str] = deque(fwd[src.id])
        for nid in fwd[src.id]:
            visited.add(nid)
        while queue:
            cur_id = queue.popleft()
            cur = nodes_by_id[cur_id]
            if cur.color == TEAL:
                # A Teal (transport) node is an entity boundary — two Blue nodes
                # separated by a transport hop are NOT the same entity, so do not
                # link them and do not traverse past the Teal node. Otherwise the
                # Blue chain edge would bridge the transport region and defeat the
                # Step 3.a Teal cut (ADR-0026 Step 3.a/3.b: the Teal chain is the
                # entity boundary, reconstructed as a cross-entity interaction).
                continue
            if cur.color == BLUE:
                # Reached a Blue node — add a Blue edge from src → cur.
                graph.edges.append(Edge.make(src.id, cur.id, BLUE))
                continue  # do not traverse past a Blue node
            for nxt in fwd[cur_id]:
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)

    # 3. Mark boundary Blue nodes (no color change).
    #    (is_boundary was already set from the classifier in step 1.)
    #
    # No cross-entity edge promotion here: every cross-entity call is
    # materialised in Step 2.c as a source→server→target route through an
    # inferred Teal server node. Adjacent same-entity boundaries (e.g. an
    # agent's `query` span and its own dispatch span) simply stay connected by
    # the plain Blue chain edge above and fuse into one entity.


# ---------------------------------------------------------------------------
# Step 2.c — Combined source-and-target spans
# ---------------------------------------------------------------------------


def duplicate_combined_nodes(graph: BaseGraph, spans_by_id: dict[str, Span]) -> None:
    """For each boundary node flagged combined (`_combined` attribute), create
    a duplicate Blue node referencing the same span (the target side of the
    call). The duplicate has no traceparent neighbours. Route the FORWARD call
    through an inferred Teal server: original(source) → server → duplicate(target)
    (request only). The response leg (duplicate → original) is formed
    structurally at Step 3.b (ADR-0026 Step 2.c: forward legs only)."""
    new_nodes: list[Node] = []
    new_edges: list[Edge] = []
    for node in graph.nodes:
        if node.color != BLUE:
            continue
        # `_combined` is only set on combined *boundary* nodes in color_agentic.
        if not node.attributes.get("_combined"):
            continue

        dup = Node.make(span_id=node.span_id, scope=node.scope, color=BLUE)
        dup.is_target_duplicate = True
        # Pool attributes so the target entity has the span's payload too.
        dup.attributes = dict(node.attributes)
        # The duplicate is the target side of the combined span; it shares the
        # original's entity-kind and plays the TARGET role. Stamp them (the dup
        # has no span-derivable role/kind of its own).
        # INVARIANT: these stamps MUST be set *after* the attribute copy above,
        # or the copied stamp wins — they are load-bearing for the
        # `_node_role`/`_node_kind`/`_node_is_boundary` accessors.
        dup.attributes["_is_boundary"] = True
        dup.attributes["_role"] = _ROLE_TARGET
        dup.attributes["_kind"] = _node_kind(node, spans_by_id)
        target_label = node.attributes.get("_target_label")
        dup.label = target_label if isinstance(target_label, str) else None
        # Strip the markers; only the original carries them.
        dup.attributes.pop("_combined", None)
        dup.attributes.pop("_target_label", None)
        new_nodes.append(dup)

        server, edges = _insert_teal_server(
            node, dup, call_order=_CALL_ORDER, scope=node.scope
        )
        new_nodes.append(server)
        new_edges.extend(edges)

    graph.nodes.extend(new_nodes)
    graph.edges.extend(new_edges)


# ---------------------------------------------------------------------------
# Step 2.c case 3 — Tool nodes inferred from an LLM span's tool_calls attribute
# ---------------------------------------------------------------------------


def infer_tool_calls_from_attributes(
    graph: BaseGraph, spans_by_id: dict[str, Span]
) -> None:
    """ADR-0026 Step 2.c case 3 — materialise inferred tool nodes from an LLM
    span's `tool_calls`, both output- and input-side.

    Some agentic spans evidence a *tool the model asked to invoke* in an
    attribute rather than as a separate span (the raw Anthropic client
    instrumentation does exactly this — see `openinference_anthropic_v1.0.6_…`).
    The adapter surfaces those on `SpanFacts.tool_calls` (output side, what the
    model asked for *as a result of* this call) and `SpanFacts.input_tool_calls`
    (input side, a prior turn's tool use replayed back into the request). For
    each tool call we infer the three nodes and three FORWARD edges the current
    spec mandates (ADR-0026 Step 2.c case 3 — forward legs only):

      * a **tool-call node** (the source — the act of calling, inside the LLM's
        turn): Blue, role=SOURCE, kind=TOOL;
      * a **tool node** (the target — the tool itself): Blue, role=TARGET,
        kind=TOOL, carrying `peer_match_key=tool:<name>` so repeated calls to
        the same tool converge in Step 3.a (semantic combine);
      * edge (a) current span → tool-call node — **Blue** (so the tool-call
        node folds into the LLM's entity in Step 3.a, exactly like a dispatch
        span folding into its agent);
      * edge (b) tool-call node → server → tool node — the FORWARD call chain
        (the cross-entity request, routed through an inferred Teal server).

    The response leg (tool → tool-call) is NOT minted here; it is formed
    structurally at Step 3.b (ordered by the traceparent-nesting LIFO rule).

    **Ordering (ADR-0026 Step 2.c "Inferred call-chain ordering").** Output-
    derived tools are ordered *after* the LLM call chain (positive band);
    input-derived tools *before* it (negative band). The call-before-response
    ordering has moved to Step 3.b. The Blue fold edge (a) carries no
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

        tool_call_node = Node.make(span_id=node.span_id, scope=node.scope, color=BLUE)
        tool_call_node.is_inferred = True
        tool_call_node.attributes["_is_boundary"] = True
        tool_call_node.attributes["_kind"] = _KIND_TOOL
        tool_call_node.attributes["_role"] = _ROLE_SOURCE
        # Deliberately NO label: the tool-call node folds (via the Blue edge)
        # into the caller's entity, and `EntityNode.absorb` takes the first
        # non-empty label it sees. A label here would race the caller's own
        # identity (e.g. relabel the agent/LLM entity `tool:database`). The
        # tool identity lives on the tool node below.
        tool_call_node.attributes["_tool_call_name"] = name
        tool_call_node.attributes["_tool_call_arguments"] = call.get("arguments")
        # The framework's tool_call id (when present) lets Step 2.d edge merge
        # recognise the *same* logical call replayed across spans (e.g. an
        # output-side call later fed back on the input side).
        if call.get("id") is not None:
            tool_call_node.attributes["_tool_call_id"] = call["id"]

        tool_node = Node.make(span_id=node.span_id, scope=node.scope, color=BLUE)
        tool_node.is_inferred = True
        tool_node.attributes["_is_boundary"] = True
        tool_node.attributes["_kind"] = _KIND_TOOL
        tool_node.attributes["_role"] = _ROLE_TARGET
        tool_node.label = natural_key
        tool_node.peer_match_key = natural_key

        new_nodes.append(tool_call_node)
        new_nodes.append(tool_node)
        # (a) Blue fold edge: folds the tool-call node into the LLM span's
        # entity (NOT an interaction — no server hop).
        new_edges.append(Edge.make(node.id, tool_call_node.id, BLUE))
        # (b) the FORWARD cross-entity call, routed through a Teal server:
        # tool-call(source) → server → tool(target) (call band). The response
        # leg (tool → tool-call) is formed at Step 3.b, not here.
        server, edges = _insert_teal_server(
            tool_call_node, tool_node, call_order=call_order, scope=node.scope
        )
        server.is_inferred = True
        new_nodes.append(server)
        new_edges.extend(edges)

    for node in graph.nodes:
        if node.color != BLUE:
            continue
        span = spans_by_id.get(node.span_id)
        if span is None:
            continue
        facts = extract_facts(span)
        # Input-derived tools first (negative band — ordered before the LLM).
        for k, call in enumerate(facts.input_tool_calls or ()):
            _materialise(node, call, call_order=_INPUT_TOOL_BASE + 2 * k)
        # Output-derived tools (positive band — ordered after the LLM). The
        # 3*k stride keeps the bands spaced apart.
        for k, call in enumerate(facts.tool_calls or ()):
            _materialise(node, call, call_order=_OUTPUT_TOOL_BASE + 3 * k)

    graph.nodes.extend(new_nodes)
    graph.edges.extend(new_edges)


# ---------------------------------------------------------------------------
# Between-boundaries flag
# ---------------------------------------------------------------------------


def flag_between_boundaries(graph: BaseGraph, spans_by_id: dict[str, Span]) -> None:
    """Annotate every Blue (non-boundary) node that lies on a Blue chain
    between two boundary nodes. Surfaced in the UI; informational only.

    A Blue node is flagged iff both:
      - some boundary node has a directed Blue-edge path to it, AND
      - it has a directed Blue-edge path to some boundary node.

    This catches plumbing-tagged spans that are actually transmitting a call
    between boundaries — likely missing classifier entries.
    """
    nodes_by_id = {n.id: n for n in graph.nodes}

    # Build Blue adjacency (directed).
    blue_fwd: dict[str, list[str]] = defaultdict(list)
    blue_rev: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if BLUE not in edge.colors:
            continue
        blue_fwd[edge.from_node_id].append(edge.to_node_id)
        blue_rev[edge.to_node_id].append(edge.from_node_id)

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

    boundaries = [n.id for n in graph.nodes if _node_is_boundary(n, spans_by_id)]
    downstream = _reachable(boundaries, blue_fwd) - set(boundaries)
    upstream = _reachable(boundaries, blue_rev) - set(boundaries)

    flagged_ids = downstream & upstream

    for nid in flagged_ids:
        n = nodes_by_id.get(nid)
        if n is not None and n.color == BLUE and not _node_is_boundary(n, spans_by_id):
            n.flagged = True


# ---------------------------------------------------------------------------
# Step 2.c — Synthesize missing peers (one-sided observation stubs)
# ---------------------------------------------------------------------------


def synthesize_missing_peers(graph: BaseGraph, spans_by_id: dict[str, Span]) -> None:
    """For every boundary node with no call-chain edges, create an inferred
    peer (is_inferred=True) referencing the same span and route the FORWARD
    call between them through an inferred Teal server (source→server→target). The
    response leg is formed structurally at Step 3.b (ADR-0026 Step 2.c: forward
    legs only).

    A boundary node with no interaction edges indicates that the peer side of
    the call was not observed. Step 2.b will already have linked any pair of
    boundaries that share a Blue chain in the trace, so an isolated boundary is
    a reliable signal for unobserved peer (not "peer present but unlinked").

    The inferred node copies the original node's pooled attributes so the
    resulting entity carries something to display, and inverts the role
    label. Step 3.a's entity graph propagates the inferred marker onto the
    entity node; the Step 3.a semantic combine then combines inferred entities
    whose source-span attributes match.
    """
    # A boundary "already has a peer" iff it is wired to a Teal server (as the
    # server's source or target endpoint) — i.e. Step 2.c already routed a call
    # through it. Index those endpoints.
    server_ids = _server_ids(graph)
    peered: set[str] = set()
    for edge in graph.edges:
        if edge.from_node_id in server_ids:
            peered.add(edge.to_node_id)
        if edge.to_node_id in server_ids:
            peered.add(edge.from_node_id)

    new_nodes: list[Node] = []
    new_edges: list[Edge] = []
    for node in graph.nodes:
        if not _node_is_boundary(node, spans_by_id):
            continue
        if node.id in peered:
            continue
        # Skip combined-span participants — Step 2.c already paired them.
        # (Defensive: a combined-span original always has a server from
        # duplicate_combined_nodes, so it would already be peered. The
        # duplicate too.)
        if node.is_target_duplicate or node.attributes.get("_combined"):
            continue

        node_role = _node_role(node, spans_by_id)
        peer = Node.make(span_id=node.span_id, scope=node.scope, color=BLUE)
        peer.is_inferred = True
        # The Step 3.a semantic combine uses this key to combine inferred peers
        # stubbing the same real callee from multiple sources. The adapter computes it
        # from the boundary span's identifying attribute, normalised by kind.
        peer.peer_match_key = _peer_match_key(node, spans_by_id)
        # The inferred peer represents the callee, not the emitter. The
        # natural-key (e.g. `tool:get_weather`) IS the callee's identity,
        # so it's the right thing to show. Fall back to the generic
        # "unobserved peer of X" only when no key was extractable —
        # those cases stay distinct in the 3.a semantic combine too, so a generic label
        # is honest about the missing information.
        peer.label = peer.peer_match_key or (
            f"(unobserved peer of {node.label})" if node.label else "(unobserved peer)"
        )
        # Pool attributes so the inferred entity has something to display.
        peer.attributes = dict(node.attributes)
        peer.attributes.pop("_combined", None)
        peer.attributes.pop("_target_label", None)
        # The inferred peer is the callee: opposite role of the observed
        # boundary, same entity-kind. Stamped (the peer has no span-derivable
        # role/kind of its own). Set *after* the attribute copy so it wins.
        # INVARIANT: these stamps MUST be set *after* the attribute copy above,
        # or the copied stamp wins — they are load-bearing for the
        # `_node_role`/`_node_kind`/`_node_is_boundary` accessors.
        peer.attributes["_is_boundary"] = True
        peer.attributes["_kind"] = _node_kind(node, spans_by_id)
        peer.attributes["_role"] = (
            _ROLE_TARGET if node_role in (_ROLE_SOURCE, _ROLE_BOTH) else _ROLE_SOURCE
        )
        new_nodes.append(peer)

        # Route the one-sided call through a Teal server: the observed boundary
        # is the source, the inferred peer the target (or vice-versa when the
        # observed side is itself the callee).
        if node_role == _ROLE_TARGET:
            source, target = peer, node
        else:
            source, target = node, peer
        server, edges = _insert_teal_server(
            source, target, call_order=_CALL_ORDER, scope=node.scope
        )
        new_nodes.append(server)
        new_edges.extend(edges)

    graph.nodes.extend(new_nodes)
    graph.edges.extend(new_edges)


# ---------------------------------------------------------------------------
# Step 2.c case 4 — Inferred agent from bare leaf LLM spans
# ---------------------------------------------------------------------------


def infer_agent_from_bare_leaf_llms(
    graph: BaseGraph, spans_by_id: dict[str, Span]
) -> None:
    """ADR-0026 Step 2.c case 4 — infer an agent node when the framework emits
    only bare leaf LLM spans (no run/agent/wrapper span).

    Pattern (raw-anthropic): a transport-scope (Teal) parent whose direct Blue
    children are *all* LLM spans, and none of those LLM spans has a Blue
    ancestor (no agent/run wrapper anywhere above). The LLM calls are being made
    directly under the HTTP-server span, so the calling *agent* was never
    instrumented.

        POST /            (Teal transport)
         ├─ messages.create   (Blue LLM)
         └─ messages.create   (Blue LLM)

    The algorithm infers a single Blue **agent** node and, per the spec:
      1. adds inferred edges transport→agent and agent→each LLM span;
      2. **disconnects** the original transport→LLM edges, keeping a
         back-reference (`Edge.replaces`) from the new agent→LLM edges to the
         originals.

    The agent node folds together with its LLM spans into one entity at the
    fuse (the agent→LLM edges are plain Blue, not server hops). The agent has no
    span of its own → `is_inferred=True`; it is named from the LLM spans'
    `service.name` (the process that made the calls). This produces the
    single-agent bare-leaf entity upstream, at inference time, rather than by any
    same-service node grouping during the Step 2.d merge.
    """
    # Directed traceparent adjacency (White edges: parent → child) and its
    # reverse (child → parents). `rev` is computed ONCE here and closed over by
    # `_has_blue_ancestor` — rebuilding it per BFS step scanned every edge on
    # every dequeue inside a per-Teal-parent loop.
    fwd, rev = _white_adjacency(graph)
    nodes_by_id = {n.id: n for n in graph.nodes}

    # Any Blue node with a Blue ancestor means there *is* an agentic wrapper;
    # the bare-leaf pattern requires no Blue node above the LLM spans. Since the
    # only Blue nodes in this shape are the LLM spans themselves (all siblings
    # under the Teal parent), it suffices to require the transport parent's Blue
    # children to be LLMs and to have no Blue node anywhere on the path above the
    # transport parent. We check the simpler sufficient condition: the LLM spans'
    # common parent is Teal and no Blue node is an ancestor of that parent.

    def _has_blue_ancestor(node_id: str) -> bool:
        seen: set[str] = set()
        queue: deque[str] = deque(rev.get(node_id, []))
        while queue:
            nid = queue.popleft()
            if nid in seen:
                continue
            seen.add(nid)
            n = nodes_by_id.get(nid)
            if n is not None and n.color == BLUE:
                return True
            queue.extend(rev.get(nid, []))
        return False

    new_nodes: list[Node] = []
    new_edges: list[Edge] = []
    drop_edge_ids: set[str] = set()

    # Index White parent→child edges by parent for rewrite/back-reference.
    white_edges_by_parent: dict[str, list[Edge]] = defaultdict(list)
    for edge in graph.edges:
        if WHITE in edge.colors:
            white_edges_by_parent[edge.from_node_id].append(edge)

    for parent in list(graph.nodes):
        if parent.color != TEAL:
            continue
        child_ids = fwd.get(parent.id, [])
        blue_children = [
            nodes_by_id[c] for c in child_ids
            if nodes_by_id.get(c) is not None and nodes_by_id[c].color == BLUE
        ]
        if not blue_children:
            continue
        # Every Blue child must be an LLM span (bare leaf), and the transport
        # parent must have no Blue ancestor.
        if any(_node_kind(c, spans_by_id) != _KIND_LLM for c in blue_children):
            continue
        if _has_blue_ancestor(parent.id):
            continue

        # Name from the LLM spans' service.name.
        svc = None
        for c in blue_children:
            span = spans_by_id.get(c.span_id)
            if span is not None and span.service_name:
                svc = span.service_name
                break

        agent = Node.make(span_id="", scope=parent.scope, color=BLUE)
        agent.is_inferred = True
        # Typed `agent:` prefix (ADR-0026 natural-key prefixes) — this case-4
        # agent has no `agent.name` attribute, so its identity is the service
        # name; prefix it so the Step 3.a semantic combine keys on it (its guard
        # requires a typed prefix) and the natural_key is consistent with the
        # observed-agent path in `build_entity_graph`.
        agent.label = f"agent:{svc}" if svc else None
        agent.attributes["_is_boundary"] = False
        agent.attributes["_kind"] = _KIND_AGENT
        # Force the fused entity inferred even though it absorbs observed LLM
        # spans — the *agent* is what was inferred (ADR-0026 Step 2.c case 4).
        agent.attributes["_inferred_agent"] = True
        new_nodes.append(agent)

        # transport → agent (inferred).
        new_edges.append(Edge.make(parent.id, agent.id, WHITE))
        # agent → each LLM span (inferred), disconnecting the original
        # transport → LLM edge and keeping a back-reference to it.
        for edge in white_edges_by_parent.get(parent.id, []):
            child = nodes_by_id.get(edge.to_node_id)
            if (
                child is None or child.color != BLUE
                or _node_kind(child, spans_by_id) != _KIND_LLM
            ):
                continue
            drop_edge_ids.add(edge.id)
            new_edge = Edge.make(agent.id, child.id, WHITE)
            new_edge.replaces = [edge.id]
            new_edges.append(new_edge)

    if new_nodes:
        graph.nodes.extend(new_nodes)
        graph.edges = [e for e in graph.edges if e.id not in drop_edge_ids]
        graph.edges.extend(new_edges)


# ---------------------------------------------------------------------------
# Step 2.d — Merge identical interactions (execution graph)
# ---------------------------------------------------------------------------


_KIND_PREFIX = {_KIND_TOOL: "tool", _KIND_LLM: "llm", _KIND_AGENT: "agent"}


def _typed_callee_key(node: Node, spans_by_id: dict[str, Span]) -> str | None:
    """Return the typed identity of a node that *is* an entity (a callee), or
    None. The discriminator: the node's identifying key prefix must match its
    own `kind` (a `tool:` key on a TOOL node, an `llm:` key on an LLM node).

    This separates genuine callee peers / observed callees — `tool:get_weather`
    on a TOOL node, `llm:…` on an LLM node — from the Blue-folded tool-call
    SOURCE nodes, which carry the *caller's* `llm:` key on a TOOL-kind node
    (prefix `llm` ≠ kind TOOL) and must NOT be treated as a tool entity. It also
    excludes an observed LLM-SOURCE caller whose label is a bare service name
    (no typed key of its own), so a caller is never fused into its callee.
    """
    key = node.peer_match_key or _peer_match_key(node, spans_by_id)
    if not key:
        return None
    prefix = key.split(":", 1)[0]
    if prefix != _KIND_PREFIX.get(_node_kind(node, spans_by_id) or ""):
        return None
    return key


def _same_processing_signature(
    node: Node, spans_by_id: dict[str, Span]
) -> tuple | None:
    """A conservative "same processing at the same time" signature for an
    *observed* callee node, used by the Step 2.d observed↔observed merge.

    The signature draws directly on the spec's Step 2.d heuristics — same tool
    name, same execution time, same input argument and output result, nodes
    from the same scope. Two observed nodes with an equal signature are asserted
    to be the SAME processing and merged. It is deliberately strict: a
    difference in the callee identity, arguments, output, scope, or time makes
    the signatures differ, so genuinely-distinct calls are never over-merged.

    Returns None for a node that carries no typed callee identity — it is not an
    entity/callee (e.g. a Blue-folded tool-call SOURCE node, whose prefix ≠
    kind), so such a node never participates.

    Components:
      * the node's typed identity — same tool/LLM/agent *name* AND kind
        (`_typed_callee_key`, which also filters out non-callee nodes);
      * its scope (nodes from the same scope);
      * its input argument and output result (`request_*` / `response_*` off
        `SpanFacts` — the same payload the extractor derives interactions from);
      * its execution-time window (`(started_at, ended_at)` of the source span)
        — "same execution time".
    """
    typed = _typed_callee_key(node, spans_by_id)
    if typed is None:
        return None
    span = spans_by_id.get(node.span_id)
    if span is None:
        return None
    facts = extract_facts(span)
    req = (
        repr(facts.request_messages) if facts.request_messages is not None
        else repr(facts.request_value)
    )
    resp = (
        repr(facts.response_messages) if facts.response_messages is not None
        else repr(facts.response_value)
    )
    return (
        typed,
        _node_kind(node, spans_by_id),
        node.scope,
        req,
        resp,
        span.started_at,
        span.ended_at,
    )


def _rewire_edges(graph: BaseGraph, redirect: dict[str, str]) -> None:
    """Rewrite every edge endpoint through `redirect`; drop self-loops."""
    rewritten: list[Edge] = []
    for edge in graph.edges:
        edge.from_node_id = redirect.get(edge.from_node_id, edge.from_node_id)
        edge.to_node_id = redirect.get(edge.to_node_id, edge.to_node_id)
        if edge.from_node_id == edge.to_node_id:
            continue
        rewritten.append(edge)
    graph.edges = rewritten


def merge_identical_interactions(
    graph: BaseGraph, spans_by_id: dict[str, Span]
) -> tuple[int, int]:
    """ADR-0026 / spec **Step 2.d** — node-and-edge merge on the execution-flow
    (base) graph, before the Step 3.a entity grouping.

    The spec states Step 2.d as one heuristic-driven merge over any
    same-interaction nodes/edges; the code realises it as four operations — two
    node merges, a Teal-chain (server) merge, then the A2A shared-root-node merge
    (`_absorb_inferred_call_into_observed_agent`).

    **Node merge — inferred peer into its observed twin.** Fold an inferred peer
    into an *independently observed* node for the same entity — matching typed
    key + kind, in a *different* White+Blue component (so it is the observed
    callee, not a sibling caller). An observed survivor is preferred over an
    inferred one. No-op unless the callee emitted its own spans (split-graph
    case).

    (The convergence of repeatedly-called typed callee peers happens in
    `combine_identical_entities` (Step 3.a semantic combine) on the entity graph, not here. The
    single-agent bare-leaf same-service case is produced upstream by the
    Step 2.c case 4 inferred agent; no observed↔observed grouping runs here.)

    **Teal-chain (server) merge.** Collapse Teal servers that represent the
    *same* interaction: same connected entity (component) pair AND same logical
    call (equal request arguments, else equal `_tool_call_id`). Time is *not*
    required to match — a tool call replayed onto a later span's input is the
    same logical call even though the two spans don't overlap. Genuinely
    distinct calls differ in arguments, so they are preserved (the canonical
    trace's repeated tool/LLM calls all have distinct arguments). The survivor
    keeps the originating call's `order` on each leg.

    **A2A shared-root-node merge (rule 4).** Absorb an inferred one-sided call
    into an observed downstream agent when the call-site span roots both the
    inferred chain and an observed transport chain reaching that agent — see
    `_absorb_inferred_call_into_observed_agent`. Turns a delegation from a call
    to a synthetic `tool:` peer into an `agent → agent` interaction.

    Returns `(nodes_merged, servers_merged)`.
    """
    nodes_merged = 0

    def _collapse(groups: dict, *, observed_survivor: bool) -> None:
        nonlocal nodes_merged
        redirect: dict[str, str] = {}
        drop_ids: set[str] = set()
        for members in groups.values():
            if len(members) <= 1:
                continue
            survivor = (
                next((m for m in members if not m.is_inferred), members[0])
                if observed_survivor else members[0]
            )
            for m in members:
                if m.id == survivor.id:
                    continue
                for k, v in m.attributes.items():
                    survivor.attributes.setdefault(k, v)
                if m.label and not survivor.label:
                    survivor.label = m.label
                if m.peer_match_key and not survivor.peer_match_key:
                    survivor.peer_match_key = m.peer_match_key
                redirect[m.id] = survivor.id
                drop_ids.add(m.id)
        if drop_ids:
            _rewire_edges(graph, redirect)
            graph.nodes = [n for n in graph.nodes if n.id not in drop_ids]
            nodes_merged += len(drop_ids)

    # --- Node merge: inferred peer into observed twin ---------------------
    # Requires the observed twin to live in a *different* White+Blue component
    # than the inferred peer's source (an independent observation of the callee,
    # not a sibling caller on the same chain).
    comp = _white_blue_components(graph)
    source_comp_for_span: dict[str, int] = {}
    for node in graph.nodes:
        if not node.is_inferred and node.span_id:
            source_comp_for_span.setdefault(node.span_id, comp[node.id])
    observed_by_key: dict[tuple[str, str | None], list[Node]] = defaultdict(list)
    for node in graph.nodes:
        if node.is_inferred or not _node_is_boundary(node, spans_by_id):
            continue
        key = _peer_match_key(node, spans_by_id) or node.label
        if key:
            observed_by_key[(key, _node_kind(node, spans_by_id))].append(node)
    def _independent_observation(inferred_peer: Node, observed: Node) -> bool:
        """The observed twin is a genuinely independent observation of the
        callee iff it was emitted by a *different service* than the inferred
        peer's caller. A same-service "twin" is the caller's own boundary span
        (e.g. google_adk's `execute_tool <name>`, emitted under the calling
        agent's service), not a separately-deployed callee — folding into it
        would collapse the tool into its agent. When either service is unknown,
        fail safe: do NOT fold (keep the inferred peer distinct).

        An **LLM-kind** twin is never an independent observation of the callee.
        An observed LLM boundary span is always the *caller's* own record of
        its outbound API call (the model is remote and emits no spans of its
        own); a same-key LLM span in another service is therefore a sibling
        *caller* of the same model, not the model observed executing. Folding
        into it would swallow every agent's inferred `llm:` peer into whichever
        agent happens to own the matched span — so in a multi-agent trace where
        N agents call one model, the shared LLM entity vanishes entirely. Only
        a genuine callee-side observation (a real tool-execution span) is a
        valid fold target here; per the spec's Step 2.d this fold is the
        split-graph "callee emitted its own spans" case, which LLMs never are."""
        if _node_kind(observed, spans_by_id) == _KIND_LLM:
            return False
        isp = spans_by_id.get(inferred_peer.span_id)
        osp = spans_by_id.get(observed.span_id)
        isvc = _service(isp) if isp else None
        osvc = _service(osp) if osp else None
        if isvc is None or osvc is None:
            return False
        return osvc != isvc

    a1_redirect: dict[str, str] = {}
    a1_drop: set[str] = set()
    if observed_by_key:
        for node in graph.nodes:
            if not node.is_inferred:
                continue
            key = node.peer_match_key or node.label
            candidates = (
                observed_by_key.get((key, _node_kind(node, spans_by_id)))
                if key else None
            )
            if not candidates:
                continue
            src_comp = source_comp_for_span.get(node.span_id, comp.get(node.id))
            best = next(
                (o for o in candidates
                 if o.id not in a1_drop
                 and comp[o.id] != src_comp
                 and _independent_observation(node, o)),
                None,
            )
            if best is None:
                continue
            for k, v in node.attributes.items():
                best.attributes.setdefault(k, v)
            if node.label and not best.label:
                best.label = node.label
            a1_redirect[node.id] = best.id
            a1_drop.add(node.id)
    if a1_drop:
        _rewire_edges(graph, a1_redirect)
        graph.nodes = [n for n in graph.nodes if n.id not in a1_drop]
        nodes_merged += len(a1_drop)

    # (Convergence of repeatedly-called typed callee peers happens in Step 3.a
    # (semantic combine, `combine_identical_entities`) on the entity graph, per the spec's two-graph
    # split — not here. Only the inferred-peer-into-observed-twin fold is an
    # execution-graph node merge.)

    # --- Node merge: observed ↔ observed same interaction -----------------
    # The spec's Step 2.d also merges two *observed* node sets that represent
    # the SAME interaction — the same processing at the same time (e.g. a tool
    # call span the trace records twice from two instrumentation sources). We
    # only merge observed callee nodes we can assert are the same processing:
    # matching `_same_processing_signature` (same typed tool/LLM name + kind,
    # same scope, same input argument AND output result, same execution time).
    # This is strict by construction — distinct calls differ in arguments,
    # output, or time and so never share a signature. Edges are preserved:
    # incident edges rewire onto the survivor and self-loops drop (via
    # `_rewire_edges`), consistent with the inferred→observed fold above.
    obs_groups: dict[tuple, list[Node]] = defaultdict(list)
    for node in graph.nodes:
        if node.is_inferred or not _node_is_boundary(node, spans_by_id):
            continue
        sig = _same_processing_signature(node, spans_by_id)
        if sig is not None:
            obs_groups[sig].append(node)
    _collapse(obs_groups, observed_survivor=True)

    # (Convergence of repeatedly-called typed callee peers happens in Step 3.a
    # (semantic combine, `combine_identical_entities`) on the entity graph, per the spec's two-graph
    # split. The single-agent bare-leaf case is produced upstream by the Step
    # 2.c case 4 inferred-agent construction — see ADR-0026 Step 2.c case 4.)

    # --- Teal-chain (server) merge ----------------------------------------
    # Two Teal servers are the same call chain when they connect the same two
    # *entities* (= White+Blue components, since the grouping will collapse each
    # component to one entity) AND describe the same logical call. The call's
    # identity lives on the server's SOURCE endpoint (its `_tool_call_id`, else
    # its `_tool_call_arguments`). Duplicate servers collapse into one survivor;
    # the survivor's forward edges take the *originating* call's order band.
    nodes_by_id = {n.id: n for n in graph.nodes}
    comp = _white_blue_components(graph)
    server_ids = _server_ids(graph)

    def _call_identity(node: Node | None) -> tuple | None:
        # Same logical call ⇔ same request arguments (the replayed call carries
        # identical args whether it appears on a span's output or a later span's
        # input). Args are the primary key; the framework tool_call id is only a
        # fallback when arguments are absent. Keying on the id *instead of* args
        # would split a replay (output side has no id, input side carries one)
        # into two interactions — exactly what Step 2.d should collapse.
        if node is None:
            return None
        args = node.attributes.get("_tool_call_arguments")
        if args is not None:
            return ("args", repr(args))
        tcid = node.attributes.get("_tool_call_id")
        if tcid is not None:
            return ("id", tcid)
        return None

    def _originating_order(a: int, b: int) -> int:
        """Pick the order of the *originating* call when two same-call servers
        merge. A tool call is "created" on the span where it appears as LLM
        output (positive band, ordered AFTER the LLM); the same call later
        replayed on an input message (negative band, ordered before that span's
        LLM) is the *same* interaction echoed back as history. Per the spec's
        timing note, the merged interaction takes the time/order of the span
        that *created* the call — so an output (positive) order wins over an
        input-replay (negative) one. A plain min() would wrongly let the replay's
        negative band drag the merged call ahead of its own originating LLM.
        When both are the same sign (e.g. a call only ever seen as input
        replay), the earlier (min) band is kept."""
        if a >= 0 and b < 0:
            return a
        if b >= 0 and a < 0:
            return b
        return min(a, b)

    # Group each server's incident (forward) edges and identify its
    # source/target endpoints (by edge direction — see `_server_endpoints`).
    incident: dict[str, list[Edge]] = defaultdict(list)
    for edge in graph.edges:
        if edge.from_node_id in server_ids:
            incident[edge.from_node_id].append(edge)
        if edge.to_node_id in server_ids:
            incident[edge.to_node_id].append(edge)

    def _server_key(server_id: str):
        src, tgt, _, _ = _server_endpoints(server_id, incident[server_id], nodes_by_id)
        if src is None or tgt is None:
            return None
        ident = _call_identity(src)
        if ident is None:
            return None
        # Discriminate the target by its *typed identity* (e.g. `tool:database`),
        # not its component: a call replayed on a later span's input produces its
        # own inferred target peer in a separate component, but the same tool
        # key — so keying on the key (not the component) still merges the replay.
        # Same-entity convergence of those peers is deferred to the Step 3.a semantic combine.
        tgt_key = tgt.peer_match_key or _typed_callee_key(tgt, spans_by_id) or comp.get(tgt.id)
        return (comp.get(src.id), tgt_key, ident)

    seen: dict[tuple, str] = {}
    drop_servers: set[str] = set()
    edges_merged = 0
    for server_id in list(incident.keys()):
        key = _server_key(server_id)
        if key is None:
            continue
        first = seen.get(key)
        if first is None:
            seen[key] = server_id
            continue
        # Same logical call — fold this server into the survivor, taking the
        # *originating* call band (output band wins over input replay). Both of
        # a server's forward edges (source→server, server→target) carry the same
        # call band (the response leg is no longer minted at 2.c — it is formed
        # at Step 3.b), so we take one representative order per server and stamp
        # the merged band onto every surviving forward edge.
        s_edges = incident[first]
        d_edges = incident[server_id]
        if s_edges and d_edges:
            merged = _originating_order(s_edges[0].order, d_edges[0].order)
            for e in s_edges:
                e.order = merged
        drop_servers.add(server_id)
        edges_merged += 1

    if drop_servers:
        graph.nodes = [n for n in graph.nodes if n.id not in drop_servers]
        graph.edges = [
            e for e in graph.edges
            if e.from_node_id not in drop_servers and e.to_node_id not in drop_servers
        ]

    # --- Inferred call site absorbed by an observed transport chain (A2A ---
    # delegation).
    # ADR-0026 Step 2.d rule 4 / spec example III. An agent's call site (an
    # openinference TOOL span, e.g. `delegate_to_research_agent`) has an inferred
    # one-sided callee chain — `source Blue → inferred Teal server → inferred Blue
    # callee peer` (from `synthesize_missing_peers`). The SAME call-site span is
    # also the traceparent ROOT of an OBSERVED transport chain (httpx/starlette,
    # colored Teal in Step 2.a) that descends through transport until it reaches an
    # OBSERVED agentic (Blue) node in a DIFFERENT White+Blue component — the
    # downstream agent. These are the same interaction: the call was actually
    # served by that observed agent. The observed side WINS — drop the inferred
    # callee peer in favour of the observed agentic node (the inferred Teal server
    # then simply connects the call site to the observed agent, collapsing onto the
    # observed transport chain at the Step 3.a Teal cut). Result: `agent → agent`,
    # not a call to a synthetic `tool:` peer.
    #
    # SHARED ROOT NODE is a sufficient signal on its own (per the spec): the
    # inferred server's source span is the traceparent parent of the observed
    # transport chain's first Teal node. Regular one-sided tools (`get_flights`,
    # …) also root an observed httpx chain, but that chain reaches no downstream
    # agentic node, so they are left as `tool:` peers — the descent-reaches-an-
    # observed-agent test is the discriminator.
    nodes_merged += _absorb_inferred_call_into_observed_agent(graph, spans_by_id)

    return nodes_merged, edges_merged


def _absorb_inferred_call_into_observed_agent(
    graph: BaseGraph, spans_by_id: dict[str, Span]
) -> int:
    """ADR-0026 Step 2.d rule 4 — see the block comment at the call site.

    For each inferred one-sided call (an inferred Teal server whose source is an
    observed call-site node and whose target is an inferred callee peer), test
    whether the source span is the traceparent root of an observed transport
    chain that descends to an observed agentic node in another component. If so,
    redirect the inferred callee peer onto that observed agentic node and drop
    the peer (the server survives, now bridging the call site and the observed
    agent). Returns the number of inferred peers absorbed.
    """
    nodes_by_id = {n.id: n for n in graph.nodes}
    comp = _white_blue_components(graph)
    server_ids = _server_ids(graph)

    # Map span_id → observed (real-span) node.
    node_for_span: dict[str, Node] = {}
    for n in graph.nodes:
        if n.span_id and not n.is_inferred:
            node_for_span.setdefault(n.span_id, n)

    # Child span-ids per parent span-id, from the trace's traceparent links —
    # the observed transport chain is a run of real spans linked parent→child.
    children_of: dict[str, list[str]] = defaultdict(list)
    for span in spans_by_id.values():
        if span.parent_id:
            children_of[span.parent_id].append(span.span_id)

    def _first_observed_agent_below(root_span_id: str, source_node: Node) -> Node | None:
        """Descend the traceparent tree below `root_span_id`, entering only
        observed transport (Teal) / unscoped (White plumbing, e.g. a2a) spans,
        and return the first observed agentic (Blue) node whose service is the
        **server-side ingress service** of the transport chain — i.e. the callee
        agent co-located with the starlette/asgi server that received the
        delegated request.

        The service check is the discriminator that separates a genuine A2A
        delegation from a plain tool call whose callee happens to make its own
        onward agent call deeper in the trace. For a delegation
        (`delegate_to_research_agent`) the transport chain's server side is the
        callee's own service (`research-agent`) and the first agentic node under
        it is that same service — a match. For a plain tool call
        (`execute_tool create_booking`, whose MCP service in turn delegates to
        `payment-agent`) the chain's server side is the tool's service
        (`create_booking`, non-agentic) while the deeper agentic node belongs to
        a *different* service (`payment-agent`) reached through the tool's OWN
        onward call — not this call's transport region — so it is rejected and
        the tool stays an inferred `tool:` peer.

        The descent must *start* on an observed transport child (the shared-root
        signal — the call site roots a real transport chain). `server_svc` is the
        first transport span in a service other than the call site's own — the
        request's ingress at the callee. It stops at (does not pass through) the
        first agentic node it finds on any branch.
        """
        src_comp = comp.get(source_node.id)
        src_span = spans_by_id.get(source_node.span_id)
        caller_svc = _service(src_span) if src_span else None
        # Seed with the root's direct children that are observed transport nodes.
        queue: deque[str] = deque()
        for csid in children_of.get(root_span_id, ()):
            node = node_for_span.get(csid)
            if node is not None and _is_observed_transport(node):
                queue.append(csid)
        if not queue:
            return None  # no observed transport chain rooted here
        server_svc: str | None = None
        seen: set[str] = set()
        while queue:
            sid = queue.popleft()
            if sid in seen:
                continue
            seen.add(sid)
            node = node_for_span.get(sid)
            span = spans_by_id.get(sid)
            if node is None:
                # Unmapped span (rare) — keep descending its children.
                for csid in children_of.get(sid, ()):
                    queue.append(csid)
                continue
            # Record the transport chain's server-side ingress service: the
            # first Teal span in a service other than the caller's.
            if server_svc is None and _is_observed_transport(node):
                svc = _service(span) if span else None
                if svc is not None and svc != caller_svc:
                    server_svc = svc
            if node.color == BLUE:
                svc = _service(span) if span else None
                # Only the agent co-located with the transport chain's server
                # ingress, in a different component, is this call's callee (see
                # the docstring). A Blue node that fails either test is not this
                # call's callee — stop descending it (do not pass through an
                # agentic node), but keep searching other branches.
                if (
                    comp.get(node.id) != src_comp
                    and server_svc is not None
                    and svc == server_svc
                ):
                    return node
                continue
            # Transport / plumbing node — keep descending.
            for csid in children_of.get(sid, ()):
                queue.append(csid)
        return None

    # Index each inferred server's endpoints.
    incident: dict[str, list[Edge]] = defaultdict(list)
    for edge in graph.edges:
        if edge.from_node_id in server_ids:
            incident[edge.from_node_id].append(edge)
        if edge.to_node_id in server_ids:
            incident[edge.to_node_id].append(edge)

    redirect: dict[str, str] = {}
    drop: set[str] = set()
    for server_id in server_ids:
        src, tgt, _, _ = _server_endpoints(
            server_id, incident.get(server_id, []), nodes_by_id
        )
        if src is None or tgt is None:
            continue
        # A one-sided inferred call: observed source, inferred callee peer.
        if src.is_inferred or not tgt.is_inferred:
            continue
        if not src.span_id:
            continue
        observed = _first_observed_agent_below(src.span_id, src)
        if observed is None:
            continue
        # Observed side wins: redirect the inferred callee peer onto the observed
        # agentic node and drop the peer. The server now connects the observed
        # call site to the observed downstream agent.
        redirect[tgt.id] = observed.id
        drop.add(tgt.id)

    if drop:
        _rewire_edges(graph, redirect)
        graph.nodes = [n for n in graph.nodes if n.id not in drop]
    return len(drop)
