"""Cross-step shared helpers for the graph interactions builder.

These helpers are used by more than one top-level step of the algorithm
(Step 2 enrichment and Step 3 entity-graph construction), so they live here to
keep the per-step modules acyclic. See `builder.py` for the facade that
re-exports every symbol under its historical `builder.<name>` path.
"""

from __future__ import annotations

from collections import defaultdict, deque

from data_governance.retrieval import Span

from .adapters import Role, extract_facts
from .graph import BLUE, BaseGraph, Edge, Node, TEAL


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
    spans (Step 2.a). This is the full set the Step 3.a fuse drops: ADR-0026
    Step 3.a forms entity subgraphs by "dropping the Teal nodes", so a Teal node
    of either provenance is the boundary between two entities, and the Teal chain
    between two Blue components becomes one interaction (Step 3.b).
    """
    return {n.id for n in graph.nodes if n.color == TEAL}


# ---------------------------------------------------------------------------
# Forward call-chain order bands (ADR-0026 Step 2.c "Inferred call-chain
# ordering" — the input/output tool banding on the FORWARD edges only)
# ---------------------------------------------------------------------------
#
# Several forward call chains derived from one span share that span's
# `started_at`, so an explicit `order` breaks the tie. Per the current spec the
# ONLY ordering carried at Step 2.c is the input/output tool banding on the
# forward (request) edges — the call-before-response and LIFO response-unwind
# ordering has moved to Step 3.b (the traceparent-nesting rule):
#
#   spec 2.c rule (input) — input-derived tools BEFORE the LLM   → negative band
#   spec 2.c rule (output) — output-derived tools AFTER the LLM  → positive band
#
# `_INPUT_TOOL_BASE` + 2*k gives the k-th input tool's call slot; the
# LLM/agent/one-sided call sits at 0; `_OUTPUT_TOOL_BASE` + 3*k gives the k-th
# output tool's call slot (the 3-stride leaves room around each band). The bands
# are spaced far apart so they never interleave for realistic tool counts.
#
# `_RESPONSE_ORDER` (= `_CALL_ORDER` + 1) is retained as the *base* response
# offset Step 3.b applies to a call chain's response leg: response = call + 1
# keeps every response immediately after its own call, and — because an inner
# call chain always carries a lower call band than the outer one waiting on it —
# the resulting responses already unwind LIFO by band (spec Step 3.b). The
# traceparent-nesting rule in `build_entity_graph` refines this only when two
# chains genuinely nest on the *same* anchor span.
_INPUT_TOOL_BASE = -40   # input tool k: call = -40+2k
_CALL_ORDER = 0          # the agent↔LLM / combined / one-sided call
_RESPONSE_ORDER = 1      # base response offset applied at Step 3.b (call + 1)
_OUTPUT_TOOL_BASE = 40   # output tool k: call = 40+3k


# ---------------------------------------------------------------------------
# Teal server insertion (ADR-0026 Step 2.c — route the FORWARD call through a
# transport node)
# ---------------------------------------------------------------------------
#
# Every inferred source→target call routes *through* an inferred Teal "server"
# node modelling the transport hop. Per Assumption #3 / the current spec, Step
# 2.c mints only the FORWARD (request-direction) legs — the call-chain
# structure: source→server→target, both at the call band. The matching response
# leg (target→source) is NOT created here; it is formed structurally at Step 3.b
# (`build_entity_graph`), ordered LIFO by traceparent nesting. The two forward
# edges carry the call band so Step 3.b can read it back. Edges are Blue (they
# connect a Blue endpoint to the server); the call-chain identity is the
# *presence of the server on the path*, not an edge color.


def _insert_teal_server(
    source: Node,
    target: Node,
    *,
    call_order: int,
    scope: str,
) -> tuple[Node, list[Edge]]:
    """Create an inferred Teal server node between `source` and `target` and
    return it plus the TWO forward (request-direction) edges:

        source →(call) server →(call) target      (the outgoing request)

    Per ADR-0026 Step 2.c (Assumption #3), Step 2.c mints the FORWARD legs only
    — the call-chain structure. The response leg (target→source) is formed
    structurally at Step 3.b and ordered by the traceparent-nesting LIFO rule,
    not minted here. The server has `span_id=""` (it references no real span, so
    it never becomes an interaction anchor). Both forward edges carry
    `call_order` so `_server_endpoints` / `build_entity_graph` can recover the
    request band.
    """
    server = Node.make(span_id="", scope=scope, color=TEAL)
    server.is_inferred = True
    server.attributes["_server"] = True

    # Forward request only: source → server → target (call band). No response
    # legs — Step 3.b forms the response structurally (ADR-0026 Step 3.b).
    e_src_srv = Edge.make(source.id, server.id, BLUE, order=call_order)
    e_srv_tgt = Edge.make(server.id, target.id, BLUE, order=call_order)
    return server, [e_src_srv, e_srv_tgt]


def _server_endpoints(
    server_id: str, incident: list[Edge], nodes_by_id: dict[str, Node]
) -> tuple[Node | None, Node | None, int, int]:
    """Identify a Teal server's external source and target endpoints and its
    call (request) order band from its incident FORWARD edges.

    Direction, not order, is authoritative: with the forward-only redesign the
    server carries just the two request legs source→server→target. So the
    **source** is the endpoint with an OUTBOUND edge INTO the server
    (source→server); the **target** is the endpoint the server points TO
    (server→target). (The old order-based signal is gone — there are no longer
    two inbound edges at distinct call/response bands to sort on.) The call band
    is read off either forward edge; the response band is derived at Step 3.b
    (`call_order + 1`, refined by the traceparent-nesting LIFO rule), so the
    fourth return value is the conventional `call_order + 1` base offset.

    Returns `(source, target, call_order, resp_order)`; source/target are None
    when the server is malformed (e.g. an endpoint was dropped as a self-loop).
    """
    source: Node | None = None
    target: Node | None = None
    call_order = _CALL_ORDER
    for e in incident:
        # source → server (inbound to the server): the external end is the source.
        if e.to_node_id == server_id:
            ext = nodes_by_id.get(e.from_node_id)
            if ext is not None and not _is_server(ext):
                source = ext
                call_order = e.order
        # server → target (outbound from the server): the external end is target.
        elif e.from_node_id == server_id:
            ext = nodes_by_id.get(e.to_node_id)
            if ext is not None and not _is_server(ext):
                target = ext
                call_order = e.order
    return source, target, call_order, call_order + 1


# ---------------------------------------------------------------------------
# Peer match key (Step 2.c / Step 3.a semantic combine)
# ---------------------------------------------------------------------------


def _peer_match_key(node: Node, spans_by_id: dict[str, Span]) -> str | None:
    """Identifying attribute of the boundary span — used as the combine key in
    the Step 3.a semantic combine. Two inferred peers stubbing the same real
    callee from different sources end up with the same key.

    Per ADR-0026, the key is "the source-span identifying attribute used by
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


# ---------------------------------------------------------------------------
# White+Blue components (Teal is the entity cut) — used by Step 2.d and Step 3.a
# ---------------------------------------------------------------------------


def _white_blue_neighbors(graph: BaseGraph) -> dict[str, set[str]]:
    """Undirected adjacency over edges that do NOT touch a Teal node — the
    same connectivity Step 3.a uses to form entities. Teal is the entity cut
    (ADR-0026 Step 3.a drops all Teal): both an inferred `source→server→target`
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
