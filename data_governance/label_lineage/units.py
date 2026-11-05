"""The nouns of label lineage, and the units of one payload.

- :class:`Site` — one payload where it sits: a leg of a trace and the hash it carries.
- :class:`Unit` — an origin: ONE classifier finding (after the containment fold),
  where it was found, its labels and its :class:`~.value.Value`. Immutable; the same
  object everywhere the unit travels.
- :class:`Occurrence` — a unit present at one payload: the span it occupies there
  (or none), the run ``[held_from, held_to)`` of the canonical value this payload
  holds, how it was found and with what score. At its origin a unit is an
  occurrence too (span = the value's extent, holding the whole value).

:func:`units_of` turns one payload's findings into units: it anchors each finding
in the projected text, reads its value, and folds a finding whose value lies inside
a same-type finding's value of the same payload into that one (the record's ``123``
and ``6789`` are one SSN, ``123-45-6789``; equal values fold too).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from typing import Literal

from .value import Value, find_all, read_value

#: How an occurrence was found. All five are declared (the edge table's CHECK
#: carries the same list); the exact finder writes ``whole`` only.
MatchKind = Literal["whole", "normalized", "structural", "fragment", "semantic"]
MATCH_KINDS: tuple[str, ...] = ("whole", "normalized", "structural", "fragment", "semantic")

LegKey = tuple[str, str]


@dataclasses.dataclass(frozen=True, slots=True)
class Site:
    """One payload in place: the leg that carries it and the trace it belongs to."""

    trace_id: str
    interaction_id: str
    leg_type: str
    payload_hash: str

    @property
    def leg(self) -> LegKey:
        return (self.interaction_id, self.leg_type)


@dataclasses.dataclass(frozen=True, slots=True)
class Unit:
    """One classifier finding as an origin of labels.

    ``text`` is what the classifier marked; ``value`` is what it denotes. A finding
    whose offsets do not match the text is *relocated* when its text occurs exactly
    once, and *unanchored* (``anchored=False``) otherwise — as is a finding with no
    text at all: an unanchored unit has no place in its payload, so it is counted
    and never travels.
    """

    entity_type: str
    regulatory_tags: tuple[str, ...]
    sensitivity_level: str
    category: str
    text: str
    value: Value
    origin: Site
    anchored: bool = True
    relocated: bool = False

    @property
    def key(self) -> tuple:
        """The unit's identity: where it was found, its extent there, its type."""
        o = self.origin
        return (o.trace_id, o.interaction_id, o.leg_type, o.payload_hash,
                self.value.start, self.value.end, self.entity_type)


@dataclasses.dataclass(frozen=True, slots=True)
class Occurrence:
    """A unit present at one payload. ``source`` is the occurrence at the input it
    was found from (``None`` at the unit's origin); it is the edge's other end and
    takes no part in equality."""

    unit: Unit
    at: Site
    span: tuple[int, int] | None
    held_from: int
    held_to: int
    match_kind: MatchKind = "whole"
    score: float = 1.0
    matched_text: str | None = None
    source: Occurrence | None = dataclasses.field(default=None, compare=False, repr=False)

    @property
    def held(self) -> str:
        """The run of the unit's canonical value this payload holds — all that it
        can pass on."""
        return self.unit.value.canonical[self.held_from:self.held_to]

    @property
    def key(self) -> tuple:
        """One occurrence per (unit, payload position, span, held run)."""
        return (self.unit.key, self.at.leg, self.span, self.held_from, self.held_to)


@dataclasses.dataclass(frozen=True, slots=True)
class ProjectedPayload:
    """A payload's projected text, in place — what a finder searches."""

    site: Site
    text: str


def at_origin(unit: Unit) -> Occurrence:
    """The unit at the payload it was found in: its extent, the whole value."""
    v = unit.value
    return Occurrence(unit=unit, at=unit.origin, span=(v.start, v.end), held_from=0,
                      held_to=len(v.canonical), matched_text=v.canonical)


def units_of(findings: Iterable[Mapping], text: str, origin: Site) -> tuple[Unit, ...]:
    """The units of the payload at *origin*, from its classifier *findings* and its
    projected *text*, ordered by position (unanchored ones last)."""
    units = [_unit(f, text, origin) for f in findings]
    anchored = _fold_contained([u for u in units if u.anchored])
    return tuple(sorted(anchored, key=lambda u: u.value.start)) + tuple(u for u in units if not u.anchored)


def _unit(finding: Mapping, text: str, origin: Site) -> Unit:
    found = str(finding.get("text") or "")
    start, end = int(finding.get("start", 0)), int(finding.get("end", 0))
    anchored, relocated = bool(found), False
    if found and text[start:end] != found:
        spots = find_all(found, text, bounded=False)
        anchored = relocated = len(spots) == 1
        start, end = spots[0] if relocated else (start, end)
    entity_type = str(finding.get("entity_type") or "")
    value = read_value(entity_type, text, start, end) if anchored else Value(found, start, end)
    return Unit(
        entity_type=entity_type,
        regulatory_tags=tuple(finding.get("regulatory_tags") or ()),
        sensitivity_level=str(finding.get("sensitivity_level") or "PUBLIC"),
        category=str(finding.get("category") or ""),
        text=found,
        value=value,
        origin=origin,
        anchored=anchored,
        relocated=relocated,
    )


def _fold_contained(units: list[Unit]) -> list[Unit]:
    """Keep the longest value of each type; a same-type unit whose value occurs
    inside a kept one (bounded, so ``12`` is not inside ``123``) is that unit."""
    kept: list[Unit] = []
    for u in sorted(units, key=lambda u: (-len(u.value.canonical), u.value.start)):
        if u.value.canonical and any(
            k.entity_type == u.entity_type and find_all(u.value.canonical, k.value.canonical)
            for k in kept
        ):
            continue
        kept.append(u)
    return kept
