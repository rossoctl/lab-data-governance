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
EntityNodes; Black edges become directed EntityEdges between entities. Step 3.b
then merges synthetic entity nodes whose source-span identifying attributes
match. Step 3.c assigns the literal ID 'unknown' to every entity (richer
naming is deferred — see ADR-0007).
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
    Step 2.b for combined source-and-target spans, plus synthetic peers from
    Step 2.c for one-sided observations).

    `color` is the highest color applied: WHITE → GRAY → BLACK.
    `is_boundary` is True iff this node was identified as an agentic-protocol
    boundary by a per-scope classifier.
    `is_target_duplicate` marks the duplicate node created for a combined
    source-and-target span (the original keeps its parent/child chains; the
    duplicate stands alone).
    `is_synthetic` marks a node materialised by Step 2.c to represent the
    unobserved peer of a one-sided protocol call. Synthetic nodes reference
    the same span as their observed peer and propagate the marker to the
    resulting entity.
    `flagged` is the between-boundaries annotation: True iff this Gray node
    sits between two Black boundaries on a Gray chain.
    `peer_match_key` is set on synthetic nodes by Step 2.c and used by Step
    3.b to merge synthetic entity nodes whose source-span identifying
    attributes match. The key is the originating boundary's classifier
    label (e.g. 'tool:foo', 'llm:gpt-4'), so two synthetic peers stubbing
    the same real callee from two different sources end up with the same
    key.
    """

    id: str
    span_id: str          # the OTel span this node represents
    scope: str
    color: NodeColor
    is_boundary: bool = False
    is_target_duplicate: bool = False
    is_synthetic: bool = False
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

    `colors` is a set of applied colorings: {"white"}, {"white", "gray"},
    {"white", "gray", "black"}, etc. White is always present for parent-child
    edges from Step 1. Gray/Black are added by Step 2.a. Edges added by
    Step 2.b (combined-span request/response) carry only {"black"} — they do
    not have a White underlying span-graph relationship.
    """

    id: str
    from_node_id: str
    to_node_id: str
    colors: set[str] = dataclasses.field(default_factory=set)

    @staticmethod
    def make(from_id: str, to_id: str, color: str) -> Edge:
        return Edge(id=str(uuid.uuid4()), from_node_id=from_id, to_node_id=to_id, colors={color})

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

    `synthetic` is True iff every contributing base-graph node was produced by
    Step 2.c as an unobserved-peer stub. Synthetic entities have no observed
    spans of their own; their span_ids reference the observed peer's span(s).
    `peer_match_key` is the merge key for Step 3.b — two synthetic entities
    with the same key represent the same unobserved real peer and collapse
    into one.
    """

    id: str
    label: str | None = None
    attributes: dict[str, Any] = dataclasses.field(default_factory=dict)
    span_ids: list[str] = dataclasses.field(default_factory=list)
    contains_boundary: bool = False
    synthetic: bool = False
    peer_match_key: str | None = None
    # Colors of contributing nodes (for display: pure-Gray entities exist in
    # the rare case a connected Gray component contains no boundary).
    contains_black: bool = False
    contains_gray: bool = False
    _absorbed_any: bool = False
    _all_absorbed_synthetic: bool = True

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
        if not node.is_synthetic:
            self._all_absorbed_synthetic = False
        # An entity is synthetic iff at least one node was absorbed and every
        # absorbed node is synthetic.
        self.synthetic = self._absorbed_any and self._all_absorbed_synthetic


@dataclasses.dataclass
class EntityEdge:
    """Directed edge between EntityNodes (one per Black edge in the base graph
    whose endpoints fall in different entities)."""

    id: str
    from_node_id: str
    to_node_id: str
    span_ids: list[str] = dataclasses.field(default_factory=list)

    @staticmethod
    def make(from_id: str, to_id: str) -> EntityEdge:
        return EntityEdge(id=str(uuid.uuid4()), from_node_id=from_id, to_node_id=to_id)


@dataclasses.dataclass
class EntityGraph:
    """Step 3 output."""

    nodes: list[EntityNode] = dataclasses.field(default_factory=list)
    edges: list[EntityEdge] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)
