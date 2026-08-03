"""Agentic-scope classifier facade for the graph interactions algorithm.

Per ADR-0026, the current algorithm covers the **openinference** agentic
scope only. a2a and mcp are deferred — their adapters do not exist yet.

This module is now a thin facade over `adapters.py`: every per-framework
attribute lookup happens there. The classifier here only translates the
typed `SpanFacts` produced by an adapter into the older
`AgenticClassification` shape the builder consumes.

When you need to support a new framework or version, edit `adapters.py` —
not this file.
"""

from __future__ import annotations

import dataclasses

from data_governance.retrieval import Span

from .adapters import (
    Kind,
    Role,
    SpanFacts,
    extract_facts,
    is_agentic_scope,  # re-exported below
)


__all__ = [
    "AgenticClassification",
    "classify_openinference",
    "is_agentic_scope",
    "get_agentic_classifier",
]


@dataclasses.dataclass
class AgenticClassification:
    """Result of classifying an agentic span — algorithm-facing shape.

    is_boundary:  True iff this span is a protocol boundary (caller or
                  callee side of an agentic protocol call). Informational for
                  the builder (which re-derives boundary-ness from the span's
                  facts via `_node_is_boundary`); False means a plain Blue node.
    is_combined:  True iff the boundary span carries BOTH the source and
                  the target of the same call (Step 2.c duplication).
                  Implies is_boundary.
    label:        Entity label to attach to the source node. May be None.
    target_label: For combined spans, the label for the duplicate (target)
                  node. Ignored if is_combined is False.

    Built from `SpanFacts` (see `adapters.py`) via `_from_facts`. Kept as a
    distinct dataclass so the builder's existing call sites do not need to
    learn about adapters. Call-role and entity-kind are NOT carried here — the
    builder reads them from `SpanFacts` directly at the point of use.
    """

    is_boundary: bool
    is_combined: bool = False
    label: str | None = None
    target_label: str | None = None

    @classmethod
    def _from_facts(cls, facts: SpanFacts) -> "AgenticClassification":
        # The adapter's role assignment is the single source of truth for
        # whether this span is a boundary. A wrapper AGENT span carries
        # kind=AGENT, role=NONE → is_boundary=False.
        is_boundary = facts.role is not Role.NONE
        return cls(
            is_boundary=is_boundary,
            is_combined=facts.is_combined,
            label=facts.display_label,
            target_label=facts.target_label,
        )


def classify_openinference(span: Span) -> AgenticClassification:
    """Classify an openinference span. Backed by the (scope, framework,
    version) adapter dispatch in `adapters.py`."""
    return AgenticClassification._from_facts(extract_facts(span))


def get_agentic_classifier(scope_name: str):
    """Return the classifier for a scope, or None if non-agentic.

    The single classifier function is sufficient because dispatch happens
    inside `adapters.extract_facts(span)` based on the span's own scope name
    — the caller never needs to pick a framework-specific classifier.
    """
    if not is_agentic_scope(scope_name):
        return None
    return classify_openinference
