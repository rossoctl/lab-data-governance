"""Graph data structures for the p_interactions graph prototype. THROWAWAY.

The algorithm builds a single base graph for the whole trace and overlays
agentic-scope semantics by *adding* color to nodes and edges. Coloring is
additive: a White edge stays White when a Gray edge is added between the same
endpoints; a Gray edge stays Gray when a Black edge is added. We model that by
tracking each edge's full color set (a set of "white" / "gray" / "black").

A node carries its single coloring (white / gray / black) plus a boundary flag
for Black nodes that participate in an agentic protocol.

Step 3.a reduces the colored base graph to an EntityGraph: connected components
over Gray/Black nodes via White and Gray edges (Black edges ignored) become
EntityNodes; Black edges become directed EntityEdges between entities. Step 3.a
phase 2 then combines inferred entity nodes whose source-span identifying
attributes match. Step 3.b names each entity from its subgraph (service.name,
else the natural-key suffix, else 'unknown'; hostname-based naming is deferred
— see ADR-0007).
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any


# ---------------------------------------------------------------------------
# Color constants
# ---------------------------------------------------------------------------

WHITE = "white"
GRAY = "gray"
BLACK = "black"

# Node coloring: highest applied color wins for display, but we store the
# explicit value because "Gray-but-not-Black" is a real, common state.
NodeColor = str  # WHITE | GRAY | BLACK


# ---------------------------------------------------------------------------
# Base graph (Step 1 + Step 2.a + Step 2.b output)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Node:
    """One node in the base graph. One node per span (plus duplicates from
    Step 2.b for combined source-and-target spans, plus inferred peers from
    Step 2.c for one-sided observations).

    `color` is the highest color applied: WHITE → GRAY → BLACK.
    `is_boundary` is True iff this node was identified as an agentic-protocol
    boundary by a per-scope classifier.
    `is_target_duplicate` marks the duplicate node created for a combined
    source-and-target span (the original keeps its parent/child chains; the
    duplicate stands alone).
    `is_inferred` marks a node materialised by Step 2.b/2.c to represent a
    peer the algorithm knows should exist but has no span for — e.g. the
    unobserved peer of a one-sided protocol call. Inferred nodes reference
    the same span as their observed peer and propagate the marker to the
    resulting entity.
    `flagged` is the between-boundaries annotation: True iff this Gray node
    sits between two Black boundaries on a Gray chain.
    `peer_match_key` is set on inferred nodes and used by the Step 3.a
    phase-2 combine to fuse entity nodes whose source-span identifying
    attributes match. The key is the originating boundary's classifier
    label (e.g. 'tool:foo', 'llm:gpt-4'), so two inferred peers stubbing
    the same real callee from two different sources end up with the same
    key.
    `role` / `kind` are the adapter's call-side ("SOURCE" / "TARGET" /
    "BOTH" / "NONE") and entity-kind ("LLM" / "TOOL" / "AGENT" / "OTHER")
    for this node, stored as plain strings to avoid an import cycle on
    adapters.py. Step 2.d uses them to decide which Gray edges become
    Black: a Gray edge is promoted only between a SOURCE node and a
    same-kind TARGET node (tool→tool, llm→llm, agent→agent).
    """

    id: str
    span_id: str          # the OTel span this node represents
    scope: str
    color: NodeColor
    is_boundary: bool = False
    is_target_duplicate: bool = False
    is_inferred: bool = False
    flagged: bool = False
    label: str | None = None
    peer_match_key: str | None = None
    role: str | None = None
    kind: str | None = None
    attributes: dict[str, Any] = dataclasses.field(default_factory=dict)

    @staticmethod
    def make(span_id: str, scope: str, color: NodeColor = WHITE) -> Node:
        return Node(id=str(uuid.uuid4()), span_id=span_id, scope=scope, color=color)


@dataclasses.dataclass
class Edge:
    """Directed edge in the base graph.

    `colors` is a set of applied colorings: {"white"}, {"white", "gray"},
    {"white", "gray", "black"}, etc. White is always present for parent-child
    edges from Step 1. Gray/Black are added by Step 2.a. Edges added by
    Step 2.b (combined-span request/response) carry only {"black"} — they do
    not have a White underlying span-graph relationship.

    `order` is the intra-turn ordering band carried by Black (interaction)
    edges, per ADR-0007 "Inferred interaction ordering". Several Black edges
    derived from a *single* span share that span's `started_at`, so `order`
    breaks the tie. The band encodes the spec rules: input-derived tool calls
    sit in a negative band (before the LLM), the LLM/agent call+response in
    {0, 1}, and output-derived tool calls in a positive band (after the LLM);
    within any call/response pair the call's `order` is one less than its
    response's. White/Gray-only edges leave it at 0 (never read — only Black
    edges become interactions). `build_entity_graph` copies it onto the
    resulting `EntityEdge`.
    """

    id: str
    from_node_id: str
    to_node_id: str
    colors: set[str] = dataclasses.field(default_factory=set)
    order: int = 0

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
        """Highest applied color for display purposes."""
        if BLACK in self.colors:
            return BLACK
        if GRAY in self.colors:
            return GRAY
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
    """One entity node — a connected component of Gray/Black base-graph nodes
    reached via White+Gray edges. Attributes pooled from every contributing
    span.

    `inferred` is True iff every contributing base-graph node was produced as
    an inferred (e.g. unobserved-peer) stub. Inferred entities have no observed
    spans of their own; their span_ids reference the observed peer's span(s).
    `peer_match_key` is the combine key for the Step 3.a inferred↔inferred
    combine — two inferred entities with the same key represent the same
    unobserved real peer and collapse into one.
    """

    id: str
    label: str | None = None
    attributes: dict[str, Any] = dataclasses.field(default_factory=dict)
    span_ids: list[str] = dataclasses.field(default_factory=list)
    contains_boundary: bool = False
    inferred: bool = False
    peer_match_key: str | None = None
    # Colors of contributing nodes (for display: pure-Gray entities exist in
    # the rare case a connected Gray component contains no boundary).
    contains_black: bool = False
    contains_gray: bool = False
    _absorbed_any: bool = False
    _all_absorbed_inferred: bool = True

    @staticmethod
    def make() -> EntityNode:
        return EntityNode(id=str(uuid.uuid4()))

    def absorb(self, node: Node) -> None:
        if node.span_id and node.span_id not in self.span_ids:
            self.span_ids.append(node.span_id)
        for k, v in node.attributes.items():
            self.attributes.setdefault(k, v)
        if node.label and not self.label:
            self.label = node.label
        if node.peer_match_key and not self.peer_match_key:
            self.peer_match_key = node.peer_match_key
        if node.is_boundary:
            self.contains_boundary = True
        if node.color == BLACK:
            self.contains_black = True
        elif node.color == GRAY:
            self.contains_gray = True
        self._absorbed_any = True
        if not node.is_inferred:
            self._all_absorbed_inferred = False
        # An entity is inferred iff at least one node was absorbed and every
        # absorbed node is inferred.
        self.inferred = self._absorbed_any and self._all_absorbed_inferred


@dataclasses.dataclass
class EntityEdge:
    """Directed edge between EntityNodes (one per Black edge in the base graph
    whose endpoints fall in different entities).

    `req_payload` is an optional `(content_kind, content)` override for the
    interaction this edge produces. It is set for tool calls inferred from an
    LLM span's `tool_calls` attribute (ADR-0007 Step 2.b case 3): the tool's
    request payload is the call's arguments, carried on the inferred tool-call
    node rather than re-derivable from the LLM span's own facts. When None the
    extractor derives the payload from the anchor span's `SpanFacts` as usual.

    `order` is copied from the originating Black base-graph edge (see
    `Edge.order`). It is the deterministic intra-turn tiebreak for the derived
    interaction; the extractor sorts by `(started_at, order)` so interactions
    sharing a span's timestamp keep their spec-mandated order.
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
