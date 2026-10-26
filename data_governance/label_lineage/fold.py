"""The fold: one trace's legs in wire order, the units at each payload carried on
to the next by a finder. Pure: a :class:`TraceInput` in, a
:class:`~.summary.Derivation` out.

At each payload-bearing leg, in :func:`wire_order`:

1. **inputs** — the leg's inbound legs (``traversal.inbound_sets`` with responses
   paired to their own request); each contributes the occurrences it holds after
   its own fold. A leg with no payload is routed but holds nothing.
2. **find** — the finder, once per input, returns candidate occurrences here.
3. **attribute** — one owner per span (:func:`attribute`).
4. **summarize** — the leg's edges become its :class:`~.summary.LegSummary`.

Across conversations (slice 2): a :class:`Prior` is what an entity that keeps
state (an agent, a declared source) was handed or said in an EARLIER trace. It
joins the inputs of every leg that entity emits in this trace, provided it was on
the wire before that leg. A unit that crosses keeps its origin, so every edge after
the crossing names ``origin_trace_id``; the crossing edge itself names
``from_trace_id``. What crossed is lineage information for following a value's
path, never inheritance: a foreign unit never claims a finding of this
conversation, and the leg summaries count same-conversation origins only.

What the fold never does: look inside a finder, guess a parent, read a seq
threshold, order by ``seq`` alone, or stop at an absent payload.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Mapping, Sequence

from data_governance.processors.classification.projection import project
from data_governance.processors.data_lineage.traversal import Entity, Leg, inbound_sets

from .find import Finder
from .summary import Derivation, Edge, LegSummary, status_of, summarize
from .units import LegKey, Occurrence, ProjectedPayload, Site, Unit, at_origin, units_of
from .value import find_all


@dataclasses.dataclass(frozen=True, slots=True)
class WireLeg(Leg):
    """A leg with the time it was on the wire — the fold's order key."""

    occurred_at: dt.datetime | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Prior:
    """One payload of an earlier conversation as *entity_id* holds it: the
    occurrences at it (its own units and what reached it there) and the time it
    was on the wire. An untimed prior joins nothing."""

    at: Site
    occurrences: tuple[Occurrence, ...]
    entity_id: str
    occurred_at: dt.datetime | None


@dataclasses.dataclass(frozen=True)
class TraceInput:
    """One trace as the fold reads it. *payloads* maps a content hash to the stored
    content; *findings* maps the hash of every CLASSIFIED payload to its findings (a
    hash absent there is unclassified). *priors* — payloads of other conversations
    as the entities that keep state hold them — is how a later conversation joins."""

    trace_id: str
    legs: Sequence[WireLeg]
    entities: Mapping[str, Entity]
    payloads: Mapping[str, object]
    findings: Mapping[str, Sequence[Mapping]]
    priors: Sequence[Prior] = ()


def wire_order(legs: Sequence[WireLeg]) -> list[WireLeg]:
    """Legs in the order they were on the wire: ``occurred_at``, then ``seq``.

    ``seq`` alone is not causal: the interactions processor re-keys a tool entity
    after its echo and mints new legs, so on the lab traces the egress request's
    ``seq`` is lower than the store read that fed it, while its ``occurred_at`` is
    later. A leg with no wire time sorts after the timed ones, by ``seq``.
    """
    def key(leg: WireLeg) -> tuple:
        if leg.occurred_at is None:
            return (1, leg.seq, leg.interaction_id, leg.leg_type)
        return (0, leg.occurred_at, leg.seq, leg.interaction_id, leg.leg_type)

    return sorted(legs, key=key)


def derive(trace_input: TraceInput, finder: Finder) -> Derivation:
    """Derive the label lineage of one trace."""
    ordered = wire_order(trace_input.legs)
    inbound = inbound_sets(ordered, dict(trace_input.entities), pair_responses=True)
    texts = {h: project(c) for h, c in trace_input.payloads.items()}
    priors = priors_by_entity(trace_input)
    held: dict[LegKey, tuple[Occurrence, ...]] = {}
    summaries: dict[LegKey, LegSummary] = {}
    edges: list[Edge] = []
    for leg in ordered:
        if leg.payload_hash is None:
            continue
        key = (leg.interaction_id, leg.leg_type)
        site = Site(trace_input.trace_id, leg.interaction_id, leg.leg_type, leg.payload_hash)
        output = ProjectedPayload(site, texts.get(leg.payload_hash, ""))
        findings = trace_input.findings.get(leg.payload_hash)
        own = units_of(findings or (), output.text, site)
        candidates = [c for k in inbound.get(key, ()) for c in finder(held.get(k, ()), output)]
        candidates += [c for p in earlier_priors(priors, leg) for c in finder(p.occurrences, output)]
        held[key], leg_edges = attribute(candidates, own)
        edges.extend(leg_edges)
        summaries[key] = summarize(key, leg.payload_hash, leg.seq, classified=findings is not None,
                                   edges=leg_edges, unanchored=sum(not u.anchored for u in own),
                                   trace_id=trace_input.trace_id)
    return Derivation(trace_id=trace_input.trace_id, edges=tuple(edges), legs=summaries,
                      occurrences=held, status=status_of(summaries, len(edges)))


def priors_by_entity(trace_input: TraceInput) -> dict[str, list[Prior]]:
    """The trace's priors keyed by the entity that holds them, timed ones only, and
    never carrying this conversation's own units back: what this trace said that
    came through another conversation is not lineage information about this one."""
    by_entity: dict[str, list[Prior]] = {}
    for p in trace_input.priors:
        if p.occurred_at is None:
            continue
        foreign = tuple(o for o in p.occurrences if o.unit.origin.trace_id != trace_input.trace_id)
        if foreign:
            by_entity.setdefault(p.entity_id, []).append(dataclasses.replace(p, occurrences=foreign))
    return by_entity


def producer_of(leg: Leg) -> str:
    """The entity that put *leg*'s payload on the wire: the caller of a request,
    the callee of a response."""
    return leg.caller_entity_id if leg.leg_type == "request" else leg.callee_entity_id


def earlier_priors(priors: Mapping[str, Sequence[Prior]], leg: WireLeg) -> list[Prior]:
    """The priors held by the entity that emits *leg*, from before it was on the
    wire — each unit once, at the entity's LATEST holding of it: that payload is
    the carrier into this conversation, and the unit's earlier holdings are
    reachable from it inside its own trace. (An agent that held a record at ten
    payloads of an earlier conversation is one carrier, not ten.)"""
    if leg.occurred_at is None:
        return []
    timed = [p for p in priors.get(producer_of(leg), ()) if p.occurred_at is not None and p.occurred_at < leg.occurred_at]
    seen: set[tuple] = set()
    latest: list[Prior] = []
    for p in reversed(sorted(timed, key=lambda p: p.occurred_at)):  # latest first; on one instant, the later given
        fresh = tuple(o for o in p.occurrences if o.unit.key not in seen)
        seen.update(o.unit.key for o in fresh)
        if fresh:
            latest.append(dataclasses.replace(p, occurrences=fresh))
    return latest


def attribute(candidates: Sequence[Occurrence], own: Sequence[Unit]) -> tuple[tuple[Occurrence, ...], list[Edge]]:
    """One owner per span: the occurrences at this payload and the edges into it.

    The clauses, in order:

    (a) a classifier finding of the same type as a candidate, whose extent lies
        inside the candidate's span or whose value occurs inside the value the
        candidate holds, is that candidate's unit confirmed, not a new origin:
        carriers are re-classified and often mis-read (a group of a number, the
        suffix field of a record whose whole number reached it);
    (b) among candidates of one type whose spans nest, the longer value owns the
        span (an ``Alice`` inside ``Alice Moreno`` is the name);
    (c) one origin reaching one span through two carriers is two edges and one
        occurrence: an occurrence is per (origin, payload, span);
    (d) every finding not claimed is a new unit, with its own occurrence.

    Only a candidate that keeps its span under (b) can claim a finding under (a).

    Ambiguity is recorded, never resolved: candidates of DIFFERENT types on one span
    (the same four digits read as an SSN suffix in one place and a card code in
    another) both stay, two edges and two labels. A later change may add a context
    tie-break (the same JSON key path) as a further clause; none is removed.

    Across conversations (slice 2): a candidate whose unit was born in ANOTHER
    trace takes no part in (a) or (b) against this conversation's own — it never
    claims a finding here (the finding stays this conversation's origin, the
    foreign occurrence sits beside it) and never suppresses a shorter own value.
    What a conversation inherited is therefore the same with or without priors;
    the foreign occurrences are the path, recorded beside it. The crossing is
    recorded where the value ENTERS this conversation: a candidate carried in from
    a prior is dropped when this conversation's own legs already bring the same
    occurrence here (the path through them is the record; a second crossing edge
    would say nothing new).
    """
    spanned = [c for c in candidates if c.span is not None]
    survivors = [c for c in candidates if not any(_nests_in(c, d) for d in spanned)]
    carried_in = {c.key for c in survivors if _from_trace(c) is None}
    survivors = [c for c in survivors if _from_trace(c) is None or c.key not in carried_in]
    claimed = {u.key for u in own if u.anchored
               and any(_inside(u, c) for c in survivors if c.span is not None and _native(c))}
    occurrences: dict[tuple, Occurrence] = {}
    edges: dict[tuple, Edge] = {}
    for c in survivors:  # a unit's key names its origin trace: own and foreign never share an occurrence
        occurrence = occurrences.setdefault(c.key, dataclasses.replace(c, source=None))
        edge = Edge(to=occurrence, came_from=c.source, from_trace_id=_from_trace(c))
        edges.setdefault(edge.key, edge)
    for u in own:
        if u.anchored and u.key not in claimed:
            occ = at_origin(u)
            occurrences.setdefault(occ.key, occ)
    return tuple(occurrences.values()), list(edges.values())


def _inside(unit: Unit, candidate: Occurrence) -> bool:
    if candidate.unit.entity_type != unit.entity_type:
        return False
    start, end = candidate.span
    return (start <= unit.value.start and unit.value.end <= end) or bool(
        find_all(unit.value.canonical, candidate.held))


def _nests_in(c: Occurrence, d: Occurrence) -> bool:
    """*c*'s span lies strictly inside *d*'s, both are of one type, and both are of
    one conversation class (own or foreign)."""
    if c.span is None or c.span == d.span or c.unit.entity_type != d.unit.entity_type:
        return False
    if _native(c) != _native(d):
        return False
    return d.span[0] <= c.span[0] and c.span[1] <= d.span[1]


def _native(c: Occurrence) -> bool:
    """The candidate's unit was born in the conversation it is found in."""
    return c.unit.origin.trace_id == c.at.trace_id


def _from_trace(c: Occurrence) -> str | None:
    """The carrier's trace when it is another one: the crossing edge names it."""
    if c.source is None or c.source.at.trace_id == c.at.trace_id:
        return None
    return c.source.at.trace_id
