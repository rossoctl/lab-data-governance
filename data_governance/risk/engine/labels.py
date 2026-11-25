"""Inherited labels as the risk engine sees them: the label-lineage edges into an
interaction's legs, which of them reach OPA, and the shape they reach it in.

Two functions decide what is forwarded: :func:`forwarded` (known inside the
conversation) and :func:`crossed` (resting on a crossing, with
``LABEL_CROSS_CONVERSATION`` on); ``utils`` and ``compute`` only call this module.
:func:`tiers` says what each tier of evidence hands the policy.
With ``RISK_INHERITED_LABELS=off`` the evidence carries no :class:`Inherited` at all
and nothing here runs; with the cross switch off, :func:`crossed` forwards nothing
and :func:`forwarded` ignores how surely an edge was carried.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from data_governance.label_lineage.summary import highest


@dataclasses.dataclass(frozen=True)
class InheritedEdge:
    """One ``payload_label_edges`` row into a leg: the origin unit's type and
    labels, how and where it was found in this leg's payload, and — when the value
    rests on a crossing there (its ``label_crossed_edges`` row) — how surely it is
    the label's (``crossed_level``) and whether of several origins (``ambiguous``)."""

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
    crossed_level: str | None = None
    ambiguous: bool = False


@dataclasses.dataclass(frozen=True)
class Inherited:
    """The edges into each leg of one interaction (keyed by leg type), the mode they
    were read under and whether the cross-conversation switch is on."""

    mode: str
    by_leg: Mapping[str, Sequence[InheritedEdge]]
    cross: bool = False

    def forwarded(self, leg_type: str) -> list[InheritedEdge]:
        return [e for e in self.by_leg.get(leg_type, ()) if forwarded(e, self.mode, self.cross)]

    def crossed(self, leg_type: str) -> list[InheritedEdge]:
        return [e for e in self.by_leg.get(leg_type, ()) if crossed(e, self.mode, self.cross)]


def tiers(inherited: Inherited | None) -> list[Inherited | None]:
    """What each tier of evidence hands the policy, in order: classification alone
    (tier 0, no inherited labels); with ``RISK_INHERITED_LABELS=on``, the labels known
    inside the conversation (tier 1); with the cross switch on too, the crossed labels
    (tier 2), and tier 1 then leaves out every edge that rests on a crossing — a
    persistent entity's later call in the same conversation (R7) included."""
    if inherited is None:
        return [None]
    if not inherited.cross:
        return [None, inherited]
    within = dataclasses.replace(inherited, by_leg={
        leg: [e for e in edges if e.crossed_level is None] for leg, edges in inherited.by_leg.items()})
    return [None, within, inherited]


def forwarded(edge: InheritedEdge, mode: str, cross: bool = False) -> bool:
    """Whether *edge* reaches the decision under *mode* as known inside the
    conversation: a whole value whose origin is in the same trace — with *cross*,
    one that does not rest on a crossing (that one is :func:`crossed`)."""
    if mode != "on" or (cross and edge.crossed_level is not None):
        return False
    return edge.match_kind == "whole" and edge.origin_trace_id in (None, edge.trace_id)


def crossed(edge: InheritedEdge, mode: str, cross: bool) -> bool:
    """Whether *edge* reaches the decision as a crossed label: a whole value resting
    on a crossing, with inherited labels and the cross switch both on. Its origin may
    be in this conversation too (a persistent entity's later call, R7)."""
    return mode == "on" and cross and edge.match_kind == "whole" and edge.crossed_level is not None


def crossed_summary(edges: Sequence[InheritedEdge]) -> dict[str, Any] | None:
    """How surely the crossed labels of one flow are its values' — the flow's
    ``crossed`` field and the record's: the levels, whether any is ambiguous, how many
    edges, their tags and highest sensitivity. ``None`` when nothing crossed."""
    if not edges:
        return None
    return {
        "levels": sorted({e.crossed_level for e in edges}),
        "ambiguous": any(e.ambiguous for e in edges),
        "count": len(edges),
        "regulatory_tags": sorted({t for e in edges for t in e.regulatory_tags}),
        "max_level": highest([e.sensitivity_level for e in edges]),
    }


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
