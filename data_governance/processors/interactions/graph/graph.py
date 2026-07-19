"""Graph data structures for the p_interactions graph prototype. THROWAWAY.

The algorithm builds a single base graph for the whole trace and overlays
scope semantics by *adding* color to nodes and edges. Coloring is additive: a
White edge stays White when a Blue edge is added between the same endpoints. We
model that by tracking each edge's full color set (a subset of
"white" / "blue" / "teal").

Node color is one of White (unassigned scope), Blue (agentic scope), or Teal
(transport scope). A boundary flag marks nodes that participate in an agentic
protocol call. A cross-entity interaction is modelled as a route through an
inferred Teal server node (Step 2.c), not as a distinct edge kind.

Step 3.a creates the EntityGraph nodes: connected components over Blue/White
nodes (Teal nodes dropped) become groups → EntityNodes, then same-entity groups
are combined (`combine_identical_entities`). Step 3.b creates the EntityGraph
edges: each Teal transport chain between two Blue components becomes one
directed interaction (EntityEdge). Entities are named from their subgraph
(service.name, else the natural-key suffix, else 'unknown'; hostname-based
naming is deferred — see ADR-0025).
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any


# ---------------------------------------------------------------------------
# Color constants
# ---------------------------------------------------------------------------

WHITE = "white"
BLUE = "blue"    # agentic scope (was GRAY in the prior spec revision)
TEAL = "teal"    # transport scope (communication / proxy)

# Node coloring: one of White (unassigned scope) / Blue (agentic) / Teal
# (transport). Highest applied color wins for edge display.
NodeColor = str  # WHITE | BLUE | TEAL


# ---------------------------------------------------------------------------
# Base graph (Step 1 + Step 2 output)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Node:
    """One node in the base graph. One node per span (plus duplicates from
    Step 2.c for combined source-and-target spans, plus inferred peers/servers
    from Step 2.c for one-sided observations).

    `color` is the node's scope color: WHITE (unassigned) / BLUE (agentic) /
    TEAL (transport).
    `is_target_duplicate` marks the duplicate node created for a combined
    source-and-target span (the original keeps its parent/child chains; the
    duplicate stands alone).
    `is_inferred` marks a node materialised by Step 2.c to represent a peer or
    transport server the algorithm knows should exist but has no span for —
    e.g. the unobserved peer of a one-sided protocol call. Inferred nodes
    reference the same span as their observed peer (or no span, for servers)
    and propagate the marker to the resulting entity.
    `flagged` is the between-boundaries annotation: True iff this Blue node
    sits between two boundaries on a Blue chain.
    `peer_match_key` is set on inferred nodes and used by the Step 3.a semantic
    combine to group entity nodes whose source-span identifying attributes
    match. The
    key is the originating boundary's classifier label (e.g. 'tool:foo',
    'llm:gpt-4'), so two inferred peers stubbing the same real callee from two
    different sources end up with the same key.

    The node's call-role / entity-kind / boundary-ness are NOT stored as
    dedicated fields. For an *observed* node they are re-derived from the
    span's `SpanFacts` (the adapter is the single source of truth); for an
    *inferred* node they are stamped on `attributes` as `_role` / `_kind` /
    `_is_boundary` at construction (the node has no span to derive them from).
    See the `_node_role` / `_node_kind` / `_node_is_boundary` accessors in
    builder.py.
    """

    id: str
    span_id: str          # the OTel span this node represents
    scope: str
    color: NodeColor
    is_target_duplicate: bool = False
    is_inferred: bool = False
    flagged: bool = False
    label: str | None = None
    peer_match_key: str | None = None
    attributes: dict[str, Any] = dataclasses.field(default_factory=dict)

    @staticmethod
    def make(span_id: str, scope: str, color: NodeColor = WHITE) -> Node:
        return Node(id=str(uuid.uuid4()), span_id=span_id, scope=scope, color=color)


@dataclasses.dataclass
class Edge:
    """Directed edge in the base graph.

    `colors` is a set of applied colorings: {"white"}, {"white", "blue"},
    {"teal"}, etc. White is always present for parent-child edges from Step 1.
    Blue is added by Step 2.b. Inferred edges added by Step 2.c
    (request/response transport hops through a Teal server) carry the color of
    the endpoints they connect. A cross-entity interaction is *not* a distinct
    edge kind: it is a route through a Teal server node, cut at the Step 3.a
    fuse and reconstructed into entity edges there.

    `order` is the intra-turn ordering band carried by the FORWARD (request)
    edges of a Teal server, per ADR-0025 Step 2.c "Inferred call-chain ordering".
    Several forward call chains derived from a *single* span share that span's
    `started_at`, so `order` breaks the tie. The band encodes the spec's 2.c
    rules: input-derived tool calls sit in a negative band (before the LLM), the
    LLM/agent/one-sided call at 0, and output-derived tool calls in a positive
    band (after the LLM). Both forward edges of a server (source→server,
    server→target) carry the same band. Plain (non-server) edges leave it at 0.
    `build_entity_graph` reads it back onto the request `EntityEdge` and derives
    the response `EntityEdge.order` from it (Step 3.b `call + 1`, refined by the
    traceparent-nesting LIFO rule) — the response band is no longer stamped here.
    """

    id: str
    from_node_id: str
    to_node_id: str
    colors: set[str] = dataclasses.field(default_factory=set)
    order: int = 0
    # `replaces` holds the ids of original edges this edge disconnected, kept as
    # a back-reference. Step 2.c case 4 (inferred agent) rewires transport→LLM
    # edges through a new inferred agent node and records the originals here.
    replaces: list[str] = dataclasses.field(default_factory=list)

    @staticmethod
    def make(from_id: str, to_id: str, color: str, order: int = 0) -> Edge:
        return Edge(
            id=str(uuid.uuid4()), from_node_id=from_id, to_node_id=to_id,
            colors={color}, order=order,
        )

    def add_color(self, color: str) -> None:
        self.colors.add(color)

    @property
    def kind(self) -> str:
        """Highest applied color for display purposes: TEAL > BLUE > WHITE."""
        if TEAL in self.colors:
            return TEAL
        if BLUE in self.colors:
            return BLUE
        return WHITE


@dataclasses.dataclass
class BaseGraph:
    """Single base graph for the whole trace."""

    nodes: list[Node] = dataclasses.field(default_factory=list)
    edges: list[Edge] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# Entity graph (Step 3 output)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class EntityNode:
    """One entity node — a connected component of Blue/White base-graph nodes
    (Teal dropped) reached via non-interaction edges. Attributes pooled from
    every contributing span.

    `inferred` is True iff every contributing base-graph node was produced as
    an inferred (e.g. unobserved-peer or case-4 agent) node. Inferred entities
    have no observed spans of their own; their span_ids reference the observed
    peer's span(s).
    `peer_match_key` is the combine key for the Step 3.d semantic combine —
    two inferred entities with the same key represent the same unobserved real
    peer and collapse into one.
    """

    id: str
    label: str | None = None
    attributes: dict[str, Any] = dataclasses.field(default_factory=dict)
    span_ids: list[str] = dataclasses.field(default_factory=list)
    contains_boundary: bool = False
    inferred: bool = False
    peer_match_key: str | None = None
    # Colors of contributing nodes (for display). Teal nodes are dropped before
    # fusing, so `contains_teal` is normally False on entities.
    contains_blue: bool = False
    contains_teal: bool = False
    _absorbed_any: bool = False
    _all_absorbed_inferred: bool = True
    _forced_inferred: bool = False

    @staticmethod
    def make() -> EntityNode:
        return EntityNode(id=str(uuid.uuid4()))

    def absorb(self, node: Node, *, is_boundary: bool = False) -> None:
        if node.span_id and node.span_id not in self.span_ids:
            self.span_ids.append(node.span_id)
        for k, v in node.attributes.items():
            self.attributes.setdefault(k, v)
        if node.label and not self.label:
            self.label = node.label
        if node.peer_match_key and not self.peer_match_key:
            self.peer_match_key = node.peer_match_key
        if is_boundary:
            self.contains_boundary = True
        if node.color == BLUE:
            self.contains_blue = True
        elif node.color == TEAL:
            self.contains_teal = True
        self._absorbed_any = True
        if not node.is_inferred:
            self._all_absorbed_inferred = False
        # A Step 2.c case-4 inferred agent node forces its whole entity inferred
        # even though it absorbs observed LLM spans: the *agent* is what was
        # inferred (the framework emitted no agent span), which is the entity's
        # identity. See ADR-0025 Step 2.c case 4 ("produces an entity marked
        # inferred").
        if node.attributes.get("_inferred_agent"):
            self._forced_inferred = True
        # An entity is inferred iff a forced-inferred (case-4 agent) node was
        # absorbed, or at least one node was absorbed and every absorbed node is
        # inferred.
        self.inferred = self._forced_inferred or (
            self._absorbed_any and self._all_absorbed_inferred
        )


@dataclasses.dataclass
class EntityEdge:
    """Directed edge between EntityNodes (one per interaction in the base graph
    whose endpoints fall in different entities).

    `req_payload` is an optional `(content_kind, content)` override for the
    interaction this edge produces. It is set for tool calls inferred from an
    LLM span's `tool_calls` attribute (ADR-0025 Step 2.c case 3): the tool's
    request payload is the call's arguments, carried on the inferred tool-call
    node rather than re-derivable from the LLM span's own facts. When None the
    extractor derives the payload from the anchor span's `SpanFacts` as usual.

    `order` is seeded from the originating interaction base-graph edge (see
    `Edge.order`, the Step 2.c intra-turn band) and then OVERWRITTEN by
    `_order_execution_walk` with a TRUE GLOBAL ORDINAL (ADR-0025 Step 3.b point 2):
    a single monotonic sequence across the whole trace — chronological across
    turns, LIFO within a nested delegation. Consumers sort by `order` ALONE.
    """

    id: str
    from_node_id: str
    to_node_id: str
    span_ids: list[str] = dataclasses.field(default_factory=list)
    req_payload: tuple[str, Any] | None = None
    order: int = 0

    @staticmethod
    def make(from_id: str, to_id: str, order: int = 0) -> EntityEdge:
        return EntityEdge(
            id=str(uuid.uuid4()), from_node_id=from_id, to_node_id=to_id, order=order,
        )


@dataclasses.dataclass
class EntityGraph:
    """Step 3 output."""

    nodes: list[EntityNode] = dataclasses.field(default_factory=list)
    edges: list[EntityEdge] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)
