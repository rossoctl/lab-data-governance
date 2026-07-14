"""Step 1 — build the base graph. THROWAWAY.

ADR-0007 Step 1: one node per span; one White directed parent→child edge per
traceparent relationship (`build_base_graph`).
"""

from __future__ import annotations

from typing import Any

from data_governance.retrieval import Span

from .graph import BaseGraph, Edge, Node, WHITE


def _attrs(span: Span) -> dict[str, Any]:
    return dict(span.attributes or {})


def _scope_name(span: Span) -> str:
    return (span.scope or {}).get("name") or "(unknown)"


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
