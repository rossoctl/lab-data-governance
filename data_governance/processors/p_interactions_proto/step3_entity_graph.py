"""Step 3 — build the entity graph (Step 3.a / 3.b / 3.d). THROWAWAY.

ADR-0025:
  Step 3.a — structural grouping (nodes) + Step 3.b (edges):
             `build_entity_graph` (with `_observed_transport_chains`).
  Step 3.d — semantic combine of same-entity groups: `combine_identical_entities`.
"""

from __future__ import annotations

from collections import defaultdict, deque

from data_governance.retrieval import Span

from .adapters import extract_facts
from .graph import (
    BLUE,
    BaseGraph,
    Edge,
    EntityEdge,
    EntityGraph,
    EntityNode,
    Node,
)

from ._shared import (
    _CALL_ORDER,
    _KIND_AGENT,
    _RESPONSE_ORDER,
    _ROLE_BOTH,
    _ROLE_SOURCE,
    _is_observed_transport,
    _node_is_boundary,
    _node_kind,
    _node_role,
    _peer_match_key,
    _server_endpoints,
    _server_ids,
    _teal_ids,
)


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


def _reconstruct_two_component_transport_chains(
    graph: BaseGraph,
    entity_graph: EntityGraph,
    nodes_by_id: dict[str, Node],
    node_to_entity: dict[str, str],
    spans_by_id: dict[str, Span],
    emit,
) -> None:
    """ADR-0025 Step 3.b for observed transport chains — reconstruct one
    interaction per observed Teal chain that bridges **two distinct Blue
    components** (a cross-service `agent→httpx→starlette→agent` hop).

    Only a chain touching *exactly two* Blue components is a cross-entity call.
    The Blue endpoints are the chain's neighbours that landed in an entity
    (`node_to_entity`); the caller is the endpoint whose span *starts first*
    (observed transport chains carry no order band, so timing is the only
    direction signal). A chain touching only ONE component is a one-sided chain
    — no longer materialised as an interaction here (the spec removed the
    terminal node). A chain touching more than two components is out of scope and
    recorded as a note.
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
            # One-sided observed chain — dropped here. The spec removed the
            # terminal node.
            continue
        if len(endpoint_by_entity) > 2:
            entity_graph.notes.append(
                f"observed transport chain touches {len(endpoint_by_entity)} "
                "entities; >2-way transport reconstruction is deferred (ADR-0025)"
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
    pooled from every contributing span. Step 3.b is the SOLE place a
    bidirectional interaction is formed: each dropped Teal transport chain
    **between two Blue components** yields a directed S→D request entity edge
    (call band, read off the forward legs) AND a D→S response entity edge that is
    formed HERE structurally (Step 2.c minted only the forward legs — the
    response is NOT read back from a 2.c-minted edge). Response legs are ordered
    LIFO by traceparent nesting (`_order_execution_walk`). One interaction per
    chain; distinct calls survive as distinct interactions (Step 2.d's Teal-chain
    merge already merged the genuinely-duplicate ones). Both kinds of chain
    reconstruct: an inferred server (one Teal node with forward-only edges) and
    an observed transport hop (a run of real httpx/starlette Teal spans between
    two Blue components). A one-sided chain (only one Blue component) is not
    turned into an interaction here — the spec removed the terminal node.
    (The Step 3.d semantic combine of same-entity groups is a separate pass,
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
    # Dropping Teal is the entity cut (ADR-0025 Step 3.a "drop the Teal nodes"):
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
        # the Step 3.d semantic combine (which requires a typed prefix) skips
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
        # Pool the two endpoints' spans; `span_ids[0]` is the anchor driving the
        # interaction's absolute timing (Step 3.b point 2) and its evidence span. The
        # anchor is **direction-aware**: it prefers *this leg's source endpoint*
        # (`from_id` — the endpoint doing the emitting on this leg) when that
        # endpoint is observed, then any observed endpoint, then an inferred one.
        # The server itself has span_id="" and never anchors.
        #
        # Direction matters because a call (S→D) and its response (D→S) are two
        # legs with swapped from/to that pool the *same* spans. Anchoring each on
        # its own source keeps a **response** on the responding (observed
        # downstream) endpoint's span rather than the caller's call-site span —
        # Step 3.b point 2 / Step 2.d example III: the `research-agent → travel-advisor`
        # A2A-delegation response must reflect research-agent's own span, not the
        # travel-advisor `delegate_to_*` call-site TOOL span both legs pool.
        #
        # The ordinary inferred-server case still anchors on the observed
        # endpoint: there the response leg's *source* IS the inferred peer, whose
        # `is_inferred` sorts it last, so the observed endpoint stays the anchor.
        # `is_inferred` dominates the source preference, so the source tiebreak
        # only bites when **both** endpoints are observed — exactly the A2A
        # delegation (call-site TOOL span + downstream agent wrapper span), the
        # one case whose two legs must split their anchors.
        #
        # When BOTH endpoints are inferred (an inferred↔inferred interaction, e.g.
        # an inferred LLM peer calling an inferred tool peer), all endpoints tie on
        # `is_inferred=True` and the anchor lands on the deriving observed span only
        # *incidentally* — those inferred peers share the deriving span's `span_id`,
        # so whichever the sort happens to pick still references that same span. The
        # sort key does not target it.
        endpoints = [
            n for n in (nodes_by_id.get(from_id), nodes_by_id.get(to_id))
            if n is not None and n.span_id
        ]
        # `_has_payload` is the final tiebreak: absent a source-preference
        # difference, a payload-bearing observed span wins over a payload-less
        # one. It also keeps the delegation's call-site span pooled so payloads
        # survive even when the timing anchor is the payload-less responding
        # wrapper — the extractor derives its payload shapes from the first
        # payload-bearing pooled span, not necessarily the timing anchor.
        # `_has_payload` is False for an inferred peer sharing the observed span,
        # so the ordering is unchanged for the ordinary inferred-server case.
        def _has_payload(n: Node) -> bool:
            if n.is_inferred:
                return False
            span = spans_by_id.get(n.span_id)
            if span is None:
                return False
            facts = extract_facts(span)
            return (
                facts.request_messages is not None
                or facts.response_messages is not None
                or facts.request_value is not None
                or facts.response_value is not None
            )
        endpoints.sort(
            key=lambda n: (n.is_inferred, n.id != from_id, not _has_payload(n))
        )  # stable
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

    # Each entry records one reconstructed chain: its request (call) edge and its
    # response edge, the chain's anchor span (which drives traceparent nesting),
    # and the Step 2.c call band. `_order_execution_walk` consumes these after all
    # chains are emitted to assign the structural request/response `order` (spec
    # Step 3.b point 2 — order derived from STRUCTURE, not timestamps).
    chains: list[dict] = []

    def _emit_interaction(
        src: Node, tgt: Node, src_eid: str, dst_eid: str,
        *, call_order: int, resp_order: int,
    ) -> None:
        """Emit the FORWARD request (S→D, call band) and form the RESPONSE
        (D→S) entity edge for one dropped Teal chain, anchoring each on its
        observed endpoint. Shared by the inferred-server loop and the
        observed-transport chain loop below.

        Step 2.c minted only the forward legs (`_insert_teal_server`), so the
        response edge is created HERE, not read back from a 2.c-minted edge. Both
        `order` fields are SEEDED here (the request to its Step 2.c call band, the
        response to `call_order + 1`) and then OVERWRITTEN with a global ordinal by
        `_order_execution_walk` after all chains are emitted — every chain is
        assigned a globally-unique `order`, so the seed is only a transient
        placeholder (it never survives to a consumer)."""
        call_edge = EntityEdge.make(src_eid, dst_eid, order=call_order)
        _anchor_and_payload(call_edge, src.id, tgt.id)
        entity_graph.edges.append(call_edge)
        # Response leg — formed structurally at Step 3.b (not carried from 2.c).
        resp_edge = EntityEdge.make(dst_eid, src_eid, order=resp_order)
        _anchor_and_payload(resp_edge, tgt.id, src.id)
        entity_graph.edges.append(resp_edge)
        # The call chain's anchor span (the source endpoint's observed span, else
        # the call edge's own anchor) drives traceparent nesting for the LIFO
        # response ordering.
        anchor = src.span_id or (call_edge.span_ids[0] if call_edge.span_ids else None)
        chains.append(
            {
                "call": call_edge,
                "resp": resp_edge,
                "call_order": call_order,
                "anchor": anchor,
            }
        )

    # --- Inferred servers (Step 2.c) — one interaction per server ---------
    # Index each server's incident edges.
    incident: dict[str, list[Edge]] = defaultdict(list)
    for edge in graph.edges:
        if edge.from_node_id in server_ids:
            incident[edge.from_node_id].append(edge)
        if edge.to_node_id in server_ids:
            incident[edge.to_node_id].append(edge)

    for server_id in server_ids:
        # Identify the server's external source and target by edge DIRECTION
        # (source→server, server→target — see `_server_endpoints`) and its call
        # band, then emit one S→D (call band) request edge and form one D→S
        # response edge (ordered by the Step 3.b LIFO pass below).
        src, tgt, call_order, resp_order = _server_endpoints(
            server_id, incident.get(server_id, []), nodes_by_id
        )
        if src is None or tgt is None:
            continue  # malformed server (both endpoints absent); fail safe
        src_eid = node_to_entity.get(src.id)
        dst_eid = node_to_entity.get(tgt.id)
        # An inferred server ALWAYS bridges two Blue components by construction:
        # each of the three Step 2.c creation cases (`duplicate_combined_nodes`,
        # `infer_tool_calls_from_attributes`, `synthesize_missing_peers`) produces
        # an inferred *peer* that itself becomes a Blue node grouped into its own
        # entity in Step 3.a — so the server endpoint is entities-by-construction,
        # never truly entity-less. Here we only fail safe: drop a chain whose
        # endpoint(s) somehow did not group.
        if src_eid is None or dst_eid is None:
            continue  # fail safe — an endpoint did not form an entity
        if src_eid == dst_eid:
            continue  # same entity — internal, no interaction
        _emit_interaction(
            src, tgt, src_eid, dst_eid, call_order=call_order, resp_order=resp_order
        )

    # --- Observed transport chains (Step 2.a / ADR-0025 Step 3.b) ---------
    # An observed transport chain is a maximal connected run of observed Teal
    # nodes (real httpx/starlette/asgi spans colored Teal in Step 2.a). Unlike an
    # inferred server it has no order bands and no request/response edge split —
    # it is just a parent→child run of real spans. When such a chain connects
    # *two distinct Blue components* (a cross-service `agent→httpx→starlette→
    # agent` hop), it is the transport region between two entities and, per the
    # ADR, becomes ONE interaction: a call from the earlier-starting Blue end to
    # the later, plus its response. A chain touching only ONE component is a
    # one-sided chain — dropped here (the spec removed the terminal node). A
    # chain touching more than two components is out of scope — recorded as a
    # note rather than guessed.
    _reconstruct_two_component_transport_chains(
        graph, entity_graph, nodes_by_id, node_to_entity, spans_by_id,
        _emit_interaction,
    )

    # Step 3.b edge ordering — structural (LIFO by traceparent nesting).
    _order_execution_walk(chains, spans_by_id)

    return entity_graph


def _order_execution_walk(
    chains: list[dict], spans_by_id: dict[str, Span]
) -> None:
    """ADR-0025 / spec **Step 3.b point 2** — assign each interaction edge a
    `order` that is a TRUE GLOBAL ORDINAL: a single monotonic integer sequence
    across the ENTIRE trace such that sorting the interactions by `order` ALONE
    yields the correct display. `order` is derived from **both structure and
    timing** — structure governs nesting (which chain's anchor span is a
    traceparent descendant of which) while `started_at` sequences siblings among
    themselves and sequences the top-level/independent roots. It is globally
    unique per edge, so consumers sort by `order` alone (not by
    `(started_at, order)`).

    Timestamps stay a SEPARATE concern: each edge keeps its anchor span and that
    span's `started_at` / `ended_at` unchanged (set in `build_entity_graph`).
    Only `order` changes meaning here — from a local band to a global ordinal.

    Algorithm — a **recursive execution-order walk** of the chain nesting forest
    (spec Step 3.b point 2). It replaces the OLD batch-LIFO rule (emit ALL of a
    subtree's requests then ALL its responses):

    1. **Nesting forest.** Chain X is a CHILD of chain Y iff Y's anchor span is
       the NEAREST chain-anchor ancestor of X's anchor span when walking
       `parent_id` links up from X (Y is the closest enclosing chain). Roots are
       chains with no enclosing chain.

    2. **Order each node's children** by their (request) anchor span's
       `started_at`, tiebroken by the Step 2.c intra-turn call band and then the
       anchor span_id — a stable, wall-clock-free key.

    3. **DFS in execution order.** For each child in sorted order: assign the
       next ordinal to its **request** edge, recurse into its subtree, then
       assign the next ordinal to its **response** edge. The root chains are
       walked the same way (as children of a virtual root), producing one global
       monotonic counter.

    Consequences: sibling leaf chains INTERLEAVE — each returns immediately
    (request then response) and siblings are sequenced by `started_at`. A sibling
    with its own nested subtree is FULLY UNWOUND (recurse) before its response,
    and therefore before the next sibling starts. Example 1 (A→B→C): A→B, B→C,
    C→B, B→A. Example 2 (B calls L, T, L): A→B, B→L, L→B, B→T, T→B, B→L, L→B,
    B→A.

    Determinism. Every ordering key ends in ``(anchor started_at, call band,
    anchor span_id)`` so no step depends on dict/set/list iteration order and no
    step uses wall-clock time; repeated runs on the same input produce identical
    `order` values.
    """
    if not chains:
        return

    parent_of: dict[str, str | None] = {
        sid: getattr(s, "parent_id", None) for sid, s in spans_by_id.items()
    }

    def _tiebreak(c: dict) -> tuple:
        # (started_at, Step 2.c intra-turn band, span id). `call_order` is the
        # forward call-chain band Step 2.c stamped (input tools < LLM < output
        # tools within one turn — `_INPUT_TOOL_BASE` < 0 < `_OUTPUT_TOOL_BASE`);
        # it is the ONLY signal separating several sibling chains derived from a
        # *single* LLM span (they share `started_at` and anchor span_id), so it
        # sits in the key between `started_at` and the span-id fallback. It never
        # overrides chronology across turns (bands are ±40, per-turn) — a later
        # turn's chain has a later `started_at` and sorts after regardless.
        # Deterministic and independent of dict order.
        span = spans_by_id.get(c["anchor"]) if c["anchor"] else None
        return (
            span.started_at if span is not None else None,
            c.get("call_order", 0),
            c["anchor"] or "",
        )

    n = len(chains)
    anchors = [chains[i]["anchor"] for i in range(n)]

    # --- Move 1: nesting forest — each chain's parent is the chain whose anchor
    # is the NEAREST chain-anchor ancestor of this chain's anchor (walk parent_id
    # up until we hit a span that is another chain's anchor). Several chains may
    # share an anchor span (e.g. input/output tool calls derived from one LLM
    # span); such a chain's parent is the ENCLOSING chain, never a sibling on the
    # same anchor — so we skip spans that only match this chain's own anchor and
    # resolve same-anchor sibling ordering via the `_tiebreak` call band instead.
    anchor_to_chains: dict[str, list[int]] = defaultdict(list)
    for i in range(n):
        if anchors[i]:
            anchor_to_chains[anchors[i]].append(i)

    children: dict[int, list[int]] = defaultdict(list)
    roots: list[int] = []
    for i in range(n):
        a = anchors[i]
        parent_idx: int | None = None
        seen: set[str] = set()
        cur = parent_of.get(a) if a else None
        while cur and cur not in seen:
            if cur in anchor_to_chains and cur != a:
                # Nearest enclosing chain — pick a deterministic representative
                # (the one that sorts first among chains sharing that anchor).
                parent_idx = min(
                    anchor_to_chains[cur], key=lambda j: _tiebreak(chains[j])
                )
                break
            seen.add(cur)
            cur = parent_of.get(cur)
        if parent_idx is None:
            roots.append(i)
        else:
            children[parent_idx].append(i)

    # --- Move 2 + 3: recursive execution-order DFS over the forest, assigning a
    # single running global ordinal. For each child (sorted by started_at / band
    # / span_id): emit its request, recurse, then emit its response.
    counter = 0

    def _walk(node_children: list[int]) -> None:
        nonlocal counter
        for i in sorted(node_children, key=lambda j: _tiebreak(chains[j])):
            chains[i]["call"].order = counter
            counter += 1
            _walk(children.get(i, []))
            chains[i]["resp"].order = counter
            counter += 1

    _walk(roots)


# ---------------------------------------------------------------------------
# Step 3.d (semantic combine) — combine identical entities (entity graph)
# ---------------------------------------------------------------------------


def combine_identical_entities(
    entity_graph: EntityGraph, spans_by_id: dict[str, Span] | None = None
) -> int:
    """ADR-0025 / spec **Step 3.d semantic combine** — combine entity-graph nodes
    (groups) representing the *same* entity, maintaining edges (re-pointing
    source/target to the survivor).

    An execution graph may materialise multiple groups for one real entity —
    e.g. repeated tool calls to one file-system tool from different call sites
    each yield their own inferred `tool:<name>` peer (the structural grouping
    turns each into its own group), or two observed spans of the same callee land
    in separate (traceparent-broken) components. They all represent one entity
    and are combined here.

    The combine keys per the spec's Step 3.d semantic heuristics: the entity's typed
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
        # Scope distinguishes only OBSERVED entities, where the scope is the
        # entity's own instrumentation. An INFERRED peer has no scope of its
        # own — its pooled scope is borrowed from the *caller's* span, so it
        # reflects which framework called the callee, not the callee's
        # identity. Keeping caller-scope in the key over-splits a single real
        # callee reached from several frameworks (e.g. one LLM called by four
        # agents under openai_agents / langchain / google_adk becomes three
        # `llm:` entities). Per the spec's Step 3.d the shared callee is ONE
        # entity ("all those tools represent in reality a single … entity"), so
        # scope drops out of the key for inferred peers and they combine on
        # typed identity alone.
        if node.inferred or not spans_by_id:
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
