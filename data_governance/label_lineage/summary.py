"""What one leg inherited (:class:`LegSummary`) and what one trace derived
(:class:`Derivation`): the edges, the per-leg summaries and the trace's status.

An :class:`Edge` joins two occurrences of one unit through one hop: the
occurrence at the carrier (``came_from``, on the from leg) and the occurrence it
was found as at the output (``to``, on the to leg). The origin rides on both, so a
reader never walks a path to learn where a label came from.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence

from .units import LegKey, Occurrence, Unit

#: Sensitivity levels, lowest first (the classifier's ladder).
LEVELS: tuple[str, ...] = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED")


@dataclasses.dataclass(frozen=True, slots=True)
class Edge:
    """One unit reaching one payload through one hop. ``from_trace_id`` is set when
    the carrier is a leg of another trace (never, in one conversation);
    ``recombined`` when fragments of the origin united at the destination (never,
    for whole values)."""

    to: Occurrence
    came_from: Occurrence
    from_trace_id: str | None = None
    recombined: bool = False

    @property
    def unit(self) -> Unit:
        return self.to.unit

    @property
    def to_key(self) -> LegKey:
        return self.to.at.leg

    @property
    def from_key(self) -> LegKey:
        return self.came_from.at.leg

    @property
    def match_kind(self) -> str:
        return self.to.match_kind

    @property
    def key(self) -> tuple:
        """The edge's natural key: the to leg, the origin, the from leg, the run
        held and the span reached. Two carriers of one origin are two edges; one
        carrier reaching one span twice is one."""
        return (self.to.at.trace_id, self.to_key, self.unit.key, self.from_key,
                self.to.held_from, self.to.held_to, self.to.span)


@dataclasses.dataclass(frozen=True, slots=True)
class LegSummary:
    """What reached one payload-bearing leg: the inherited tags by kind, the highest
    inherited level, how many origins and edges reached it, whether it was
    classified, and how many of its own findings could not be anchored."""

    key: LegKey
    payload_hash: str
    seq: int
    classified: bool
    inherited_tags_whole: tuple[str, ...] = ()
    inherited_tags_fragment: tuple[str, ...] = ()
    max_inherited_level: str | None = None
    origin_count: int = 0
    recombined: bool = False
    recombined_units: tuple = ()
    edge_count: int = 0
    unanchored: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class TraceStatus:
    """``complete`` when every payload-bearing leg was classified at derivation."""

    status: str
    unclassified_legs: int
    unanchored_findings: int
    derived_legs: int
    edge_count: int


@dataclasses.dataclass(frozen=True, slots=True)
class Derivation:
    trace_id: str
    edges: tuple[Edge, ...]
    legs: Mapping[LegKey, LegSummary]
    occurrences: Mapping[LegKey, tuple[Occurrence, ...]]
    status: TraceStatus


def highest(levels: Sequence[str]) -> str | None:
    """The highest of *levels* on the ladder; an unknown level ranks lowest."""
    rank = {lv: i for i, lv in enumerate(LEVELS)}
    return max(levels, key=lambda lv: rank.get(lv, -1), default=None)


def summarize(key: LegKey, payload_hash: str, seq: int, *, classified: bool,
              edges: Sequence[Edge], unanchored: int = 0, trace_id: str | None = None) -> LegSummary:
    """The summary of one leg from the edges into it. With *trace_id*, only the
    edges whose unit was born in that conversation count: what crossed from
    another one is lineage information for following a value's path, not
    something this leg inherited."""
    if trace_id is not None:
        edges = [e for e in edges if e.unit.origin.trace_id == trace_id]
    whole = [e for e in edges if e.match_kind != "fragment"]
    return LegSummary(
        key=key,
        payload_hash=payload_hash,
        seq=seq,
        classified=classified,
        inherited_tags_whole=tuple(sorted({t for e in whole for t in e.unit.regulatory_tags})),
        inherited_tags_fragment=tuple(sorted({
            t for e in edges if e.match_kind == "fragment" for t in e.unit.regulatory_tags})),
        max_inherited_level=highest([e.unit.sensitivity_level for e in whole]),
        origin_count=len({e.unit.key for e in edges}),
        edge_count=len(edges),
        unanchored=unanchored,
    )


def status_of(legs: Mapping[LegKey, LegSummary], edge_count: int) -> TraceStatus:
    unclassified = sum(1 for leg in legs.values() if not leg.classified)
    return TraceStatus(
        status="complete" if unclassified == 0 else "partial",
        unclassified_legs=unclassified,
        unanchored_findings=sum(leg.unanchored for leg in legs.values()),
        derived_legs=len(legs),
        edge_count=edge_count,
    )
