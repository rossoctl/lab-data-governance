"""Inherited labels as the risk engine sees them: the label-lineage edges into an
interaction's legs, which of them reach OPA, and the shape they reach it in.

One function decides what is forwarded (:func:`forwarded`); ``utils`` and
``compute`` only call this module. With ``RISK_INHERITED_LABELS=off`` the evidence
carries no :class:`Inherited` at all and nothing here runs.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from data_governance.label_lineage.summary import highest


@dataclasses.dataclass(frozen=True)
class InheritedEdge:
    """One ``payload_label_edges`` row into a leg: the origin unit's type and
    labels, how and where it was found in this leg's payload."""

    trace_id: str
    entity_type: str
    regulatory_tags: tuple[str, ...]
    sensitivity_level: str
    match_kind: str
    matched_text: str | None = None
    matched_start: int | None = None
    matched_end: int | None = None
    recombined: bool = False
    origin_trace_id: str | None = None


@dataclasses.dataclass(frozen=True)
class Inherited:
    """The edges into each leg of one interaction (keyed by leg type) and the mode
    they were read under."""

    mode: str
    by_leg: Mapping[str, Sequence[InheritedEdge]]

    def forwarded(self, leg_type: str) -> list[InheritedEdge]:
        return [e for e in self.by_leg.get(leg_type, ()) if forwarded(e, self.mode)]


def forwarded(edge: InheritedEdge, mode: str) -> bool:
    """Whether *edge* reaches the decision under *mode*: in one conversation, a
    whole value whose origin is in the same trace."""
    if mode != "on":
        return False
    return edge.match_kind == "whole" and edge.origin_trace_id in (None, edge.trace_id)


def summary(edges: Sequence[InheritedEdge]) -> dict[str, Any]:
    """The order-free view of forwarded edges that joins the fingerprint and the
    record: tags, highest level, kinds, whether any recombined, how many."""
    return {
        "regulatory_tags": sorted({t for e in edges for t in e.regulatory_tags}),
        "max_level": highest([e.sensitivity_level for e in edges]),
        "kinds": sorted({e.match_kind for e in edges}),
        "recombined": any(e.recombined for e in edges),
        "count": len(edges),
    }


def data_item(edges: Sequence[InheritedEdge]) -> dict[str, Any] | None:
    """Forwarded edges as ONE document-level ``dataItem`` — the shape of the
    classifier's own verdict: the union of the tags, the highest level, every
    matched span an ``entities[]`` entry. One item per flow, not per unit, so a
    rule keyed on a level sees the grain it was written against (a DOB's own
    INTERNAL must not fire an internal-level rule the document never did).
    ``None`` when nothing is forwarded."""
    if not edges:
        return None
    entities: dict[tuple, dict[str, Any]] = {}
    for e in edges:
        entity: dict[str, Any] = {"entity_type": e.entity_type, "regulatory_tags": list(e.regulatory_tags),
                                  "classification_level": e.sensitivity_level}
        if e.matched_text is not None:
            entity["text"] = e.matched_text
        if e.matched_start is not None:
            entity["start"], entity["end"] = e.matched_start, e.matched_end
        entities.setdefault((e.entity_type, e.matched_start, e.matched_end), entity)
    return {
        "entities": list(entities.values()),
        "list_of_entities": [entity["entity_type"] for entity in entities.values()],
        "classification_level": highest([e.sensitivity_level for e in edges]),
        "regulatory_tags": sorted({t for e in edges for t in e.regulatory_tags}),
    }
