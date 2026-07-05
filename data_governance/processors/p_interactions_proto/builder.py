"""Graph construction for the p_interactions prototype. THROWAWAY.

Implements the algorithm described in docs/adr/0007-p-interactions-graph-algorithm.md.
The spec's steps, and the functions that realise them:

  Step 1   — build the base graph: one node per span; one White directed
             parent→child edge per traceparent relationship (`build_base_graph`).
  Step 2.b — agentic scope: color openinference-scope nodes Blue, mark call
             boundaries, and add Blue edges along the agentic (grand-)parent
             chain (`color_agentic`). (Step 2.a transport/Teal coloring lands in
             `color_transport`; a2a and mcp scopes are deferred — see ADR-0007.)
  Step 2.c — extend the execution graph with inferred nodes and edges: combined
             source-and-target spans duplicate the node
             (`duplicate_combined_nodes`); LLM `tool_calls` attributes infer
             tool-call/tool nodes (`infer_tool_calls_from_attributes`); one-sided
             observations get an inferred peer (`synthesize_missing_peers`).
  Step 2.d — node-and-edge merge on the execution-flow graph
             (`merge_identical_interactions`): collapse same-entity nodes
             (inferred↔observed, inferred↔inferred, observed↔observed) then
             same-interaction edges. Runs *before* the entity grouping so the
             colored snapshot reflects every merge.
  Step 3.a — create the entity-graph *nodes*: drop Teal and group each connected
             Blue+White subgraph into a group (`build_entity_graph`), then
             combine same-entity groups (`combine_identical_entities`).
  Step 3.b — create the entity-graph *edges*: one interaction per Teal transport
             chain between two Blue components (per-chain; distinct calls stay
             distinct). Built inside `build_entity_graph` as it drops each chain.

Edge coloring is additive: an edge can carry multiple colors at once. The
underlying White connectivity is preserved when Blue/Teal are added on top.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from data_governance.retrieval import Span

from .adapters import Role, _service, extract_facts, is_transport_scope
from .classifiers import (
    AgenticClassification,
    get_agentic_classifier,
    is_agentic_scope,
)
from .graph import (
    BLUE,
    BaseGraph,
    Edge,
    EntityEdge,
    EntityGraph,
    EntityNode,
    Node,
    TEAL,
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

_KIND_TOOL = "TOOL"
_KIND_LLM = "LLM"
_KIND_AGENT = "AGENT"


# ---------------------------------------------------------------------------
# Call-role / kind / boundary accessors
# ---------------------------------------------------------------------------
#
# `Node` carries no `role` / `kind` / `is_boundary` fields. For an *observed*
# node the call-role, entity-kind, and boundary-ness are re-derived from the
# span's `SpanFacts` (the adapter is the single source of truth). For an
# *inferred* node (Teal server, inferred peer, tool node, case-4 agent) they are
# intrinsic to how it was constructed and cannot come from a span, so they are
# stamped on `node.attributes` (`_role` / `_kind` / `_is_boundary`) at creation.
# These accessors read the stamped value when present, else fall back to facts.


def _node_role(node: Node, spans_by_id: dict[str, Span]) -> str | None:
    if "_role" in node.attributes:
        return node.attributes["_role"]
    span = spans_by_id.get(node.span_id)
    if span is None:
        return None
    r = extract_facts(span).role
    return r.value if r is not None else None


def _node_kind(node: Node, spans_by_id: dict[str, Span]) -> str | None:
    if "_kind" in node.attributes:
        return node.attributes["_kind"]
    span = spans_by_id.get(node.span_id)
    if span is None:
        return None
    k = extract_facts(span).kind
    return k.value if k is not None else None


def _node_is_boundary(node: Node, spans_by_id: dict[str, Span]) -> bool:
    if "_is_boundary" in node.attributes:
        return bool(node.attributes["_is_boundary"])
    span = spans_by_id.get(node.span_id)
    if span is None:
        return False
    return extract_facts(span).role is not Role.NONE


def _is_server(node: Node) -> bool:
    """True iff `node` is an inferred Teal transport server (Step 2.c)."""
    return node.color == TEAL and bool(node.attributes.get("_server"))


def _is_observed_transport(node: Node) -> bool:
    """True iff `node` is an *observed* transport Teal node (Step 2.a) — a real
    httpx/starlette/asgi span colored Teal by `color_transport`, as opposed to
    an inferred Teal server (Step 2.c). It has a real `span_id` and no `_server`
    marker.
    """
    return node.color == TEAL and not node.attributes.get("_server")


def _server_ids(graph: BaseGraph) -> set[str]:
    return {n.id for n in graph.nodes if _is_server(n)}


def _teal_ids(graph: BaseGraph) -> set[str]:
    """Every Teal node — inferred servers (Step 2.c) *and* observed transport
    spans (Step 2.a). This is the full set the Step 3.a fuse drops: ADR-0007
    Step 3.a forms entity subgraphs by "dropping the Teal nodes", so a Teal node
    of either provenance is the boundary between two entities, and the Teal chain
    between two Blue components becomes one interaction (Step 3.b).
    """
    return {n.id for n in graph.nodes if n.color == TEAL}


# ---------------------------------------------------------------------------
# Interaction order bands (ADR-0007 "Inferred interaction ordering")
# ---------------------------------------------------------------------------
#
# Several interaction edges derived from one span share that span's
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
# the Blue fold edge which carries no order). The bands are spaced far apart so
# they never interleave for realistic tool counts.
_INPUT_TOOL_BASE = -40   # input tool k: call = -40+2k, response = -40+2k+1
_CALL_ORDER = 0          # the agent↔LLM / combined / one-sided call
_RESPONSE_ORDER = 1      # its response
_OUTPUT_TOOL_BASE = 40   # output tool k: call = 40+3k, response = 40+3k+1


# ---------------------------------------------------------------------------
# Teal server insertion (ADR-0007 Step 2.c — route calls through a transport
# node)
# ---------------------------------------------------------------------------
#
# Every inferred source↔target call routes *through* an inferred Teal "server"
# node modelling the transport hop: request source→server→target, response
# target→server→source. The four edges carry the call/response order bands and
# their direction (call vs response) so `build_entity_graph` can reconstruct the
# single S→D / D→S entity interactions after dropping the server. Edges are Blue
# (they connect a Blue endpoint to the server); the interaction identity is the
# *presence of the server on the path*, not an edge color.


def _insert_teal_server(
    source: Node,
    target: Node,
    *,
    call_order: int,
    scope: str,
) -> tuple[Node, list[Edge]]:
    """Create an inferred Teal server node between `source` and `target` and
    return it plus the four request/response edges:

        source →(call) server →(call) target      (the outgoing request)
        target →(resp) server →(resp) source       (the incoming response)

    The server has `span_id=""` (it references no real span, so it never
    becomes an interaction anchor). Each edge is tagged (`kind` attr on the
    edge via `is_call`) so the fuse pairs the two call edges and the two
    response edges. `call_order` is the request band; the response band is
    `call_order + 1`.
    """
    server = Node.make(span_id="", scope=scope, color=TEAL)
    server.is_inferred = True
    server.attributes["_server"] = True

    resp_order = call_order + 1
    # Request: source → server → target (call band).
    e_src_srv = Edge.make(source.id, server.id, BLUE, order=call_order)
    e_srv_tgt = Edge.make(server.id, target.id, BLUE, order=call_order)
    # Response: target → server → source (response band).
    e_tgt_srv = Edge.make(target.id, server.id, BLUE, order=resp_order)
    e_srv_src = Edge.make(server.id, source.id, BLUE, order=resp_order)
    return server, [e_src_srv, e_srv_tgt, e_tgt_srv, e_srv_src]


def _server_endpoints(
    server_id: str, incident: list[Edge], nodes_by_id: dict[str, Node]
) -> tuple[Node | None, Node | None, int, int]:
    """Identify a Teal server's external source and target endpoints and its
    call/response order bands from its incident edges.

    Direction, not role, is authoritative: the request is source→server→target
    at the *call* band and the response is target→server→source at the *response*
    band, and the call band is always the lower order (`call_order` <
    `call_order + 1`). So the endpoint whose **inbound** edge carries the lower
    order is the source; the endpoint whose inbound edge carries the higher
    order is the target. Role can't be used — after the Step 2.d inferred-peer
    fold an inferred TARGET peer may be replaced by an observed node that is
    itself role=SOURCE (an observed tool span), which would otherwise leave the
    target bucket empty.

    Returns `(source, target, call_order, resp_order)`; source/target are None
    when the server is malformed (e.g. an endpoint was dropped as a self-loop).
    """
    inbound: list[tuple[int, Node]] = []
    for e in incident:
        if e.to_node_id != server_id:
            continue
        ext = nodes_by_id.get(e.from_node_id)
        if ext is None or _is_server(ext):
            continue
        inbound.append((e.order, ext))
    if not inbound:
        return None, None, _CALL_ORDER, _RESPONSE_ORDER
    inbound.sort(key=lambda t: t[0])
    source = inbound[0][1]
    call_order = inbound[0][0]
    target = inbound[-1][1] if len(inbound) > 1 else None
    resp_order = inbound[-1][0] if len(inbound) > 1 else call_order + 1
    return source, target, call_order, resp_order


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
                # Step 3.a Teal cut (ADR-0007 Step 3.a/3.b: the Teal chain is the
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
    call). The duplicate has no traceparent neighbours. Route the call through
    an inferred Teal server: original(source) → server → duplicate(target)
    (request) and duplicate → server → original (response)."""
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
    """ADR-0007 Step 2.c case 3 — materialise inferred tool nodes from an LLM
    span's `tool_calls`, both output- and input-side.

    Some agentic spans evidence a *tool the model asked to invoke* in an
    attribute rather than as a separate span (the raw Anthropic client
    instrumentation does exactly this — see `openinference_anthropic_v1.0.6_…`).
    The adapter surfaces those on `SpanFacts.tool_calls` (output side, what the
    model asked for *as a result of* this call) and `SpanFacts.input_tool_calls`
    (input side, a prior turn's tool use replayed back into the request). For
    each tool call we infer the two nodes and three edges the spec mandates:

      * a **tool-call node** (the source — the act of calling, inside the LLM's
        turn): Blue, role=SOURCE, kind=TOOL;
      * a **tool node** (the target — the tool itself): Blue, role=TARGET,
        kind=TOOL, carrying `peer_match_key=tool:<name>` so repeated calls to
        the same tool converge in Step 3.a (semantic combine);
      * edge (a) current span → tool-call node — **Blue** (so the tool-call
        node folds into the LLM's entity in Step 3.a, exactly like a dispatch
        span folding into its agent);
      * edge (b) tool-call node → tool node — **interaction** (the cross-entity call);
      * edge (c) tool node → tool-call node — **interaction** (the reverse / response).

    **Ordering (ADR-0007 "Inferred interaction ordering").** Output-derived
    tools are ordered *after* the LLM interaction (positive band, rule 4);
    input-derived tools *before* it (negative band, rule 3). The call edge (b)
    always precedes its response edge (c). The Blue fold edge (a) carries no
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
        # (b) the cross-entity call, routed through a Teal server:
        # tool-call(source) → server → tool(target) (call band) and the reverse
        # (response band). `call_order + 1` is the response band, so the call
        # always precedes its response.
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
        # 3*k stride leaves the odd slot free for the response edge.
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


def _peer_match_key(node: Node, spans_by_id: dict[str, Span]) -> str | None:
    """Identifying attribute of the boundary span — used as the combine key in
    the Step 3.a semantic combine. Two inferred peers stubbing the same real
    callee from different sources end up with the same key.

    Per ADR-0007, the key is "the source-span identifying attribute used by
    the originating boundary's classifier". The actual attribute lookup
    lives in `adapters.py`, dispatched on (scope, framework, version);
    here we just ask the matching adapter for `SpanFacts.natural_key`.
    Returns None when no identifying attribute is available — the Step 3.a
    semantic combine leaves keyless inferred peers distinct.
    """
    span = spans_by_id.get(node.span_id)
    if span is None:
        return None
    return extract_facts(span).natural_key


def synthesize_missing_peers(graph: BaseGraph, spans_by_id: dict[str, Span]) -> None:
    """For every boundary node with no interaction edges, create an inferred
    peer (is_inferred=True) referencing the same span and add bidirectional
    interaction edges between them.

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
    """ADR-0007 Step 2.c case 4 — infer an agent node when the framework emits
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
        # Typed `agent:` prefix (ADR-0007 natural-key prefixes) — this case-4
        # agent has no `agent.name` attribute, so its identity is the service
        # name; prefix it so the Step 3.a semantic combine keys on it (its guard
        # requires a typed prefix) and the natural_key is consistent with the
        # observed-agent path in `build_entity_graph`.
        agent.label = f"agent:{svc}" if svc else None
        agent.attributes["_is_boundary"] = False
        agent.attributes["_kind"] = _KIND_AGENT
        # Force the fused entity inferred even though it absorbs observed LLM
        # spans — the *agent* is what was inferred (ADR-0007 Step 2.c case 4).
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


def _white_blue_neighbors(graph: BaseGraph) -> dict[str, set[str]]:
    """Undirected adjacency over edges that do NOT touch a Teal node — the
    same connectivity Step 3.a uses to form entities. Teal is the entity cut
    (ADR-0007 Step 3.a drops all Teal): both an inferred `source→server→target`
    route and an observed transport hop `agent→httpx→starlette→agent` fail to
    connect their two Blue ends here, so a caller and its callee stay in
    different components. Used by Step 2.d as a proximity guard: an inferred node
    may only fold into an observed twin that is reachable over this adjacency (a
    sibling/ancestor in the execution-flow graph), never an unrelated same-key
    node elsewhere in the trace.
    """
    teal_ids = _teal_ids(graph)
    adj: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.from_node_id in teal_ids or edge.to_node_id in teal_ids:
            continue
        adj[edge.from_node_id].add(edge.to_node_id)
        adj[edge.to_node_id].add(edge.from_node_id)
    return adj


def _white_blue_components(graph: BaseGraph) -> dict[str, int]:
    """Label every node with its non-interaction connected-component id — the
    same components Step 3.a collapses into entities. Two nodes in the same
    component are the *same* entity (a caller and its own plumbing); two in
    different components are distinct entities linked only by an interaction.
    """
    adj = _white_blue_neighbors(graph)
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
    """ADR-0007 / spec **Step 2.d** — node-and-edge merge on the execution-flow
    (base) graph, before the Step 3.a entity grouping.

    The spec states Step 2.d as one heuristic-driven merge over any
    same-interaction nodes/edges; the code implements a subset, as two
    operations — a node merge then a Teal-chain (server) merge.

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
        fail safe: do NOT fold (keep the inferred peer distinct)."""
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
    # 2.c case 4 inferred-agent construction — see ADR-0007 Step 2.c case 4.)

    # --- Teal-chain (server) merge ----------------------------------------
    # Two Teal servers are the same interaction when they connect the same two
    # *entities* (= White+Blue components, since the grouping will collapse each
    # component to one entity) AND describe the same logical call. The call's
    # identity lives on the server's SOURCE endpoint (its `_tool_call_id`, else
    # its `_tool_call_arguments`). Duplicate servers collapse into one survivor;
    # the survivor's call/response bands take the *originating* call's order.
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

    # Group each server's incident edges and identify its source/target
    # endpoints (by role) and its call/response legs.
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

    def _leg(server_id: str, want_call: bool) -> list[Edge]:
        """Return the server's two call-band (or response-band) edges. The call
        leg is source→server + server→target; the response leg is
        target→server + server→source. The source is the endpoint whose
        inbound edge carries the *lower* order (the call band); the target the
        higher (the response band). See `_server_endpoints`."""
        src, tgt, call_order, _ = _server_endpoints(
            server_id, incident[server_id], nodes_by_id
        )
        out: list[Edge] = []
        for e in incident[server_id]:
            is_call = e.order == call_order
            if is_call == want_call:
                out.append(e)
        return out

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
        # *originating* order on each leg (output band wins over input replay).
        for want_call in (True, False):
            s_edges = _leg(first, want_call)
            d_edges = _leg(server_id, want_call)
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

    return nodes_merged, edges_merged


# ---------------------------------------------------------------------------
# Step 3.a (structural grouping) + Step 3.b (edges) — build the entity graph
# ---------------------------------------------------------------------------


def _observed_transport_chains(graph: BaseGraph) -> list[set[str]]:
    """Group observed transport Teal nodes into maximal connected chains.

    Two observed Teal nodes are in the same chain iff a graph edge joins them
    directly (e.g. an httpx span whose parent is a starlette span — both Teal).
    Inferred servers (`_server`) are excluded — they are handled by their own
    reconstruction path. Returns one node-id set per chain.
    """
    obs_ids = {n.id for n in graph.nodes if _is_observed_transport(n)}
    adj: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.from_node_id in obs_ids and edge.to_node_id in obs_ids:
            adj[edge.from_node_id].add(edge.to_node_id)
            adj[edge.to_node_id].add(edge.from_node_id)

    chains: list[set[str]] = []
    seen: set[str] = set()
    for start in obs_ids:
        if start in seen:
            continue
        members: set[str] = set()
        queue: deque[str] = deque([start])
        while queue:
            nid = queue.popleft()
            if nid in members:
                continue
            members.add(nid)
            seen.add(nid)
            for nb in adj.get(nid, ()):
                if nb not in members:
                    queue.append(nb)
        chains.append(members)
    return chains


def _reconstruct_observed_transport_chains(
    graph: BaseGraph,
    entity_graph: EntityGraph,
    nodes_by_id: dict[str, Node],
    node_to_entity: dict[str, str],
    spans_by_id: dict[str, Span],
    emit,
) -> None:
    """ADR-0007 Step 3.b for observed transport chains — reconstruct one
    interaction per observed Teal chain that bridges two distinct Blue
    components (a cross-service `agent→httpx→starlette→agent` hop).

    Only a chain touching *exactly two* Blue components is a cross-entity call.
    The Blue endpoints are the chain's neighbours that landed in an entity
    (`node_to_entity`); the caller is the endpoint whose span *starts first*
    (transport chains carry no order band, so timing is the only direction
    signal). A chain touching fewer than two components is dangling plumbing and
    is dropped; one touching more than two is out of scope and recorded as a
    note.
    """
    # Undirected adjacency over ALL edges — used to find each chain's Blue
    # neighbours (the edges from a transport span to the agentic spans it sits
    # between are White traceparent edges, not cut here).
    adj: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        adj[edge.from_node_id].add(edge.to_node_id)
        adj[edge.to_node_id].add(edge.from_node_id)

    def _started_at(node: Node):
        span = spans_by_id.get(node.span_id)
        return span.started_at if span is not None else None

    for chain in _observed_transport_chains(graph):
        # Blue endpoints adjacent to the chain, grouped by their entity.
        endpoint_by_entity: dict[str, Node] = {}
        for tid in chain:
            for nb in adj.get(tid, ()):
                eid = node_to_entity.get(nb)
                if eid is None:
                    continue
                node = nodes_by_id.get(nb)
                if node is None:
                    continue
                # Keep the earliest-starting endpoint per entity as its anchor.
                cur = endpoint_by_entity.get(eid)
                if cur is None:
                    endpoint_by_entity[eid] = node
                else:
                    a, b = _started_at(node), _started_at(cur)
                    if a is not None and (b is None or a < b):
                        endpoint_by_entity[eid] = node

        if len(endpoint_by_entity) < 2:
            continue  # dangling transport leaf / all-one-entity plumbing
        if len(endpoint_by_entity) > 2:
            entity_graph.notes.append(
                f"observed transport chain touches {len(endpoint_by_entity)} "
                "entities; >2-way transport reconstruction is deferred (ADR-0007)"
            )
            continue

        # Direction from timing: the earlier-starting endpoint is the caller.
        (eid_a, node_a), (eid_b, node_b) = endpoint_by_entity.items()
        ta, tb = _started_at(node_a), _started_at(node_b)
        if ta is not None and tb is not None and tb < ta:
            (eid_a, node_a), (eid_b, node_b) = (eid_b, node_b), (eid_a, node_a)
        emit(
            node_a, node_b, eid_a, eid_b,
            call_order=_CALL_ORDER, resp_order=_RESPONSE_ORDER,
        )


def build_entity_graph(graph: BaseGraph, spans_by_id: dict[str, Span]) -> EntityGraph:
    """Step 3.a structural grouping (nodes) + Step 3.b (edges) — compute
    connected components over Blue nodes, **dropping all Teal nodes** (edges
    touching any Teal node — inferred server or observed transport span — are
    cut). Each component becomes one entity node (a group), with attributes
    pooled from every contributing span. Step 3.b: each dropped Teal transport
    chain is reconstructed into a directed S→D entity edge (call band) and D→S
    entity edge (response band) — one interaction per chain; distinct calls
    survive as distinct interactions (Step 2.d's Teal-chain merge already merged
    the genuinely-duplicate ones). Both kinds of chain reconstruct: an inferred
    server (one Teal node with order-banded edges) and an observed transport hop
    (a run of real httpx/starlette Teal spans between two Blue components).
    (The Step 3.a semantic combine of same-entity groups is a separate pass,
    `combine_identical_entities`.)

    Agent entities are relabelled to their typed `agent:` identity here: an
    observed agent's nodes carry the bare service name (the classifier's
    `display_label`), so a component containing an AGENT-kind node adopts that
    node's prefixed `natural_key` (`agent:<name>`). This gives the semantic
    combine a typed key to group same-agent components on."""
    entity_graph = EntityGraph()
    nodes_by_id = {n.id: n for n in graph.nodes}
    server_ids = _server_ids(graph)
    teal_ids = _teal_ids(graph)

    # Build undirected adjacency over edges that do NOT touch ANY Teal node.
    # Dropping Teal is the entity cut (ADR-0007 Step 3.a "drop the Teal nodes"):
    # neither an inferred `source→server→target` route nor an observed transport
    # hop `agent→httpx→starlette→agent` connects its two Blue ends here, so they
    # group into separate entities.
    adj: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if edge.from_node_id in teal_ids or edge.to_node_id in teal_ids:
            continue
        adj[edge.from_node_id].append(edge.to_node_id)
        adj[edge.to_node_id].append(edge.from_node_id)

    # Restrict to Blue nodes — White nodes contribute no entity at this
    # stage (non-agentic spans are enrichment-only later).
    coloured_ids = {n.id for n in graph.nodes if n.color == BLUE}

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
            n = nodes_by_id[nid]
            entity.absorb(n, is_boundary=_node_is_boundary(n, spans_by_id))

        # Promote an agent entity's label to its typed `agent:` identity.
        # `absorb` takes the first non-empty node label, and for observed
        # agents every node is labelled with the bare service name (the
        # classifier's `display_label`) — so the entity label lands bare and
        # the Step 3.a semantic combine (which requires a typed prefix) skips
        # it, splitting one agent invoked across broken traceparent chains into
        # several entities. When the component is an agent (contains the agent's
        # own AGENT-kind wrapper node) and its label is still bare, adopt that
        # node's prefixed `natural_key` (e.g. `agent:booking_agent` from
        # `agent.name`) so the combine keys on it.
        #
        # Only a non-dispatch AGENT node names *this* entity: the observed
        # wrapper (role NONE, e.g. google_adk `agent_run`) or the case-4
        # inferred agent (no span, role unset). A dispatch/handoff boundary is
        # also AGENT-kind but is a SOURCE/BOTH boundary whose `natural_key`
        # names the *callee* (e.g. `ClaudeAgentSDK.book_flight` →
        # `agent:book_flight`); promoting from it would mislabel the caller.
        # LLM/tool entities already carry a prefix and are left untouched.
        if entity.label and ":" not in entity.label:
            for nid in component:
                n = nodes_by_id[nid]
                if _node_kind(n, spans_by_id) != _KIND_AGENT:
                    continue
                if _node_role(n, spans_by_id) in (_ROLE_SOURCE, _ROLE_BOTH):
                    continue  # dispatch/handoff boundary — names the callee
                # Observed wrapper carries its identity on the span facts
                # (`peer_match_key`); the case-4 inferred agent carries it on
                # its own `label` (it has no span).
                agent_key = (
                    n.peer_match_key or _peer_match_key(n, spans_by_id) or n.label
                )
                if agent_key and ":" in agent_key:
                    entity.label = agent_key
                    break

        entity_graph.nodes.append(entity)
        for nid in component:
            node_to_entity[nid] = entity.id

    # Reconstruct interactions from each dropped Teal server. A server sits
    # between an external source and target; the request routes
    # source→server→target (call band) and the response target→server→source
    # (response band). For each server we emit one S→D EntityEdge (call band)
    # and one D→S EntityEdge (response band), one per distinct source×target
    # pair its edges evidence. Same-interaction collapsing already happened in
    # Step 2.d (the Teal-chain merge); distinct calls between the same two
    # entities (e.g. two separate LLM turns) survive as separate interactions.
    def _anchor_and_payload(ent_edge: EntityEdge, from_id: str, to_id: str) -> None:
        # Pool the two endpoints' spans, **observed (non-inferred) endpoint
        # first**: `span_ids[0]` is the anchor driving the interaction's timing
        # and payload. An inferred peer that Step 2.d merged across turns
        # carries a *stale* span, so anchoring on the observed endpoint keeps
        # each turn's response on its own span. The server itself has span_id=""
        # and never anchors.
        endpoints = [
            n for n in (nodes_by_id.get(from_id), nodes_by_id.get(to_id))
            if n is not None and n.span_id
        ]
        endpoints.sort(key=lambda n: n.is_inferred)  # observed (False) first; stable
        for n in endpoints:
            if n.span_id not in ent_edge.span_ids:
                ent_edge.span_ids.append(n.span_id)
        # Step 2.c case 3: the tool-call node (the call's source endpoint)
        # carries the inferred call's arguments. Surface them as the request
        # payload (otherwise the extractor would derive the LLM completion off
        # the shared span — see EntityEdge.req_payload).
        src_node = nodes_by_id.get(from_id)
        if (
            ent_edge.req_payload is None
            and src_node is not None
            and "_tool_call_arguments" in src_node.attributes
        ):
            ent_edge.req_payload = (
                "tool_call_arguments",
                src_node.attributes["_tool_call_arguments"],
            )

    def _emit_interaction(
        src: Node, tgt: Node, src_eid: str, dst_eid: str,
        *, call_order: int, resp_order: int,
    ) -> None:
        """Emit the call (S→D, call band) and response (D→S, response band)
        entity edges for one dropped Teal chain, anchoring each on its observed
        endpoint. Shared by the inferred-server loop and the observed-transport
        chain loop below."""
        call_edge = EntityEdge.make(src_eid, dst_eid, order=call_order)
        _anchor_and_payload(call_edge, src.id, tgt.id)
        entity_graph.edges.append(call_edge)
        resp_edge = EntityEdge.make(dst_eid, src_eid, order=resp_order)
        _anchor_and_payload(resp_edge, tgt.id, src.id)
        entity_graph.edges.append(resp_edge)

    # --- Inferred servers (Step 2.c) — one interaction per server ---------
    # Index each server's incident edges.
    incident: dict[str, list[Edge]] = defaultdict(list)
    for edge in graph.edges:
        if edge.from_node_id in server_ids:
            incident[edge.from_node_id].append(edge)
        if edge.to_node_id in server_ids:
            incident[edge.to_node_id].append(edge)

    for server_id in server_ids:
        # Identify the server's external source and target and its call/response
        # bands (by inbound-edge order — see `_server_endpoints`), then emit one
        # S→D (call band) and one D→S (response band) entity edge.
        src, tgt, call_order, resp_order = _server_endpoints(
            server_id, incident.get(server_id, []), nodes_by_id
        )
        if src is None or tgt is None:
            continue  # malformed server (endpoint dropped); fail safe
        src_eid = node_to_entity.get(src.id)
        dst_eid = node_to_entity.get(tgt.id)
        if src_eid is None or dst_eid is None or src_eid == dst_eid:
            continue
        _emit_interaction(
            src, tgt, src_eid, dst_eid, call_order=call_order, resp_order=resp_order
        )

    # --- Observed transport chains (Step 2.a / ADR-0007 Step 3.b) ---------
    # An observed transport chain is a maximal connected run of observed Teal
    # nodes (real httpx/starlette/asgi spans colored Teal in Step 2.a). Unlike an
    # inferred server it has no order bands and no request/response edge split —
    # it is just a parent→child run of real spans. When such a chain connects
    # *two distinct Blue components* (a cross-service `agent→httpx→starlette→
    # agent` hop), it is the transport region between two entities and, per the
    # ADR, becomes ONE interaction: a call from the earlier-starting Blue end to
    # the later, plus its response. A chain touching fewer than two components (a
    # dangling httpx leaf under a single LLM, or all-one-entity plumbing) is not
    # a cross-entity call and is dropped silently. A chain touching more than two
    # components is out of scope at this stage — recorded as a note rather than
    # guessed.
    _reconstruct_observed_transport_chains(
        graph, entity_graph, nodes_by_id, node_to_entity, spans_by_id,
        _emit_interaction,
    )

    return entity_graph


# ---------------------------------------------------------------------------
# Step 3.a (semantic combine) — combine identical entities (entity graph)
# ---------------------------------------------------------------------------


def combine_identical_entities(
    entity_graph: EntityGraph, spans_by_id: dict[str, Span] | None = None
) -> int:
    """ADR-0007 / spec **Step 3.a semantic combine** — combine entity-graph nodes
    (groups) representing the *same* entity, maintaining edges (re-pointing
    source/target to the survivor).

    An execution graph may materialise multiple groups for one real entity —
    e.g. repeated tool calls to one file-system tool from different call sites
    each yield their own inferred `tool:<name>` peer (the structural grouping
    turns each into its own group), or two observed spans of the same callee land
    in separate (traceparent-broken) components. They all represent one entity
    and are combined here.

    The combine keys per the spec's Step 3.a semantic heuristics: the entity's typed
    identity (**same tool name** — its `peer_match_key`, else its natural-key
    `label`, e.g. `tool:database`), its **argument/output types** (the typed-key
    prefix — `tool:` / `llm:` / `agent:` — is the coarse kind and so stands in
    for the payload types), its **scope** (nodes from the same scope), and
    **whether it is inferred or observed** (the `inferred` flag is part of the
    key). Both inferred peers and OBSERVED entities participate — two entities
    with an equal key collapse, while genuinely-different entities (different
    name, kind, scope) stay distinct, and an inferred peer never silently
    absorbs an observed entity (inferred-vs-observed is in the key). Every edge
    is preserved — distinct calls between the same two entities remain distinct
    interactions.

    `spans_by_id` supplies the per-entity scope; when omitted (older callers)
    scope drops out of the key and only typed identity + inferred are used,
    reproducing the pre-broadening inferred-peer convergence.

    Returns the number of entity nodes combined.
    """
    def _entity_scope(node: EntityNode) -> str:
        if not spans_by_id:
            return ""
        for sid in node.span_ids:
            s = spans_by_id.get(sid)
            if s is not None:
                sc = (s.scope or {}).get("name")
                if sc:
                    return sc
        return ""

    groups: dict[tuple, list[EntityNode]] = defaultdict(list)
    for node in entity_graph.nodes:
        # Typed identity: the inferred peer's `peer_match_key` when present, else
        # the entity's natural-key `label`. Observed and case-4 agents carry an
        # `agent:` prefix promoted in `build_entity_graph`, so they participate.
        # A truly keyless entity (an `unknown` entity, or an agent with no
        # service name) never combines — there is no name to assert sameness on.
        key = node.peer_match_key or node.label
        if not key or ":" not in key:
            continue
        # The typed-key prefix (`tool:` / `llm:` / `agent:`) doubles as the
        # coarse kind → argument/output types; keep it in the key so a tool and
        # an LLM sharing a name never combine.
        prefix = key.split(":", 1)[0]
        groups[(key, prefix, node.inferred, _entity_scope(node))].append(node)

    redirect: dict[str, str] = {}
    drop: set[str] = set()
    for members in groups.values():
        if len(members) <= 1:
            continue
        survivor = members[0]
        for m in members[1:]:
            for sid in m.span_ids:
                if sid not in survivor.span_ids:
                    survivor.span_ids.append(sid)
            for k, v in m.attributes.items():
                survivor.attributes.setdefault(k, v)
            if m.label and not survivor.label:
                survivor.label = m.label
            survivor.contains_boundary = survivor.contains_boundary or m.contains_boundary
            survivor.contains_blue = survivor.contains_blue or m.contains_blue
            survivor.contains_teal = survivor.contains_teal or m.contains_teal
            redirect[m.id] = survivor.id
            drop.add(m.id)

    if not drop:
        return 0

    for edge in entity_graph.edges:
        edge.from_node_id = redirect.get(edge.from_node_id, edge.from_node_id)
        edge.to_node_id = redirect.get(edge.to_node_id, edge.to_node_id)
    entity_graph.nodes = [n for n in entity_graph.nodes if n.id not in drop]
    return len(drop)

