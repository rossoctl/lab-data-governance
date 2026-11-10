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

What the fold never does: look inside a finder, guess a parent, read a seq
threshold, order by ``seq`` alone, or stop at an absent payload.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Mapping, Sequence

from data_governance.interaction_order import LegOrderEvent, LegOrderKey, leg_positions
from data_governance.processors.classification.projection import project
from data_governance.processors.data_lineage import memory
from data_governance.processors.data_lineage.traversal import Entity, Leg, inbound_sets

from .find import Finder
from .summary import Derivation, Edge, LegSummary, status_of, summarize
from .units import LegKey, Occurrence, ProjectedPayload, Site, Unit, at_origin, units_of
from .value import find_all


@dataclasses.dataclass(frozen=True, slots=True)
class WireLeg(Leg):
    """A leg with its observed time and recorded parent, for causal ordering.

    Keep the time as the seventh positional argument accepted before the shared
    ``Leg`` grew its parent field; new parent identity is keyword-only here.
    """

    parent_interaction_id: str | None = dataclasses.field(default=None, kw_only=True)
    occurred_at: dt.datetime | None = None


@dataclasses.dataclass(frozen=True)
class TraceInput:
    """One trace as the fold reads it. *payloads* maps a content hash to the stored
    content; *findings* maps the hash of every CLASSIFIED payload to its findings (a
    hash absent there is unclassified). *priors* — occurrences at other traces'
    payloads — is how a later conversation joins; it must be empty in one."""

    trace_id: str
    legs: Sequence[WireLeg]
    entities: Mapping[str, Entity]
    payloads: Mapping[str, object]
    findings: Mapping[str, Sequence[Mapping]]
    priors: Sequence[Occurrence] = ()


def wire_order(legs: Sequence[WireLeg]) -> list[WireLeg]:
    """The shared trace-local causal order (ADR-0035), using recorded parents.

    Observed times order otherwise ready legs; ingestion ``seq`` breaks ties.
    The fold still routes absent payloads and pairs memoryless responses with
    their own requests.
    """
    positions = leg_positions(
        LegOrderEvent(leg.interaction_id, leg.leg_type, leg.parent_interaction_id,
                      leg.occurred_at, leg.seq)
        for leg in legs
    )
    return sorted(legs, key=lambda leg: positions[LegOrderKey(leg.interaction_id, leg.leg_type)])


def derive(trace_input: TraceInput, finder: Finder) -> Derivation:
    """Derive the label lineage of one trace."""
    if trace_input.priors:
        raise ValueError("priors from other conversations are not read in one conversation")
    ordered = wire_order(trace_input.legs)
    inbound = inbound_sets(ordered, dict(trace_input.entities), pair_responses=True)
    inbound = with_invocation_scope(ordered, inbound, trace_input.entities)
    texts = {h: project(c) for h, c in trace_input.payloads.items()}
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
        held[key], leg_edges = attribute(candidates, own)
        edges.extend(leg_edges)
        summaries[key] = summarize(key, leg.payload_hash, leg.seq, classified=findings is not None,
                                   edges=leg_edges, unanchored=sum(not u.anchored for u in own))
    return Derivation(trace_id=trace_input.trace_id, edges=tuple(edges), legs=summaries,
                      occurrences=held, status=status_of(summaries, len(edges)))


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
    """
    spanned = [c for c in candidates if c.span is not None]
    survivors = [c for c in candidates if not any(_nests_in(c, d) for d in spanned)]
    claimed = {u.key for u in own if u.anchored
               and any(_inside(u, c) for c in survivors if c.span is not None)}
    occurrences: dict[tuple, Occurrence] = {}
    edges: dict[tuple, Edge] = {}
    for c in survivors:
        occurrence = occurrences.setdefault(c.key, dataclasses.replace(c, source=None))
        edge = Edge(to=occurrence, came_from=c.source)
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
    """*c*'s span lies strictly inside *d*'s, and both are of one type."""
    if c.span is None or c.span == d.span or c.unit.entity_type != d.unit.entity_type:
        return False
    return d.span[0] <= c.span[0] and c.span[1] <= d.span[1]


def with_invocation_scope(ordered: Sequence[WireLeg], inbound: Mapping[LegKey, tuple[LegKey, ...]],
                          entities: Mapping[str, Entity]) -> dict[LegKey, tuple[LegKey, ...]]:
    """A memoryless responder's answer may carry what it received from the services
    it called while answering.

    ``inbound_sets(pair_responses=True)`` answers a memoryless entity's response with
    its own request only, so a tool that calls a service and returns what the service
    said has no inbound for that value, although every leg is on the wire (teaching
    flag 2026-10-24; the paper's "invocation scope", P14). Here the response of every
    child interaction (``parent_interaction_id`` = this interaction) that arrived
    before the answer is added to the answer's inbound set. Accumulating entities are
    untouched: their memory node already holds every prior. Data lineage is untouched:
    this runs on label lineage's copy of the sets only.
    """
    position = {(leg.interaction_id, leg.leg_type): i for i, leg in enumerate(ordered)}
    children: dict[str, list[LegKey]] = {}
    for leg in ordered:
        if leg.leg_type == "response" and leg.parent_interaction_id:
            children.setdefault(leg.parent_interaction_id, []).append((leg.interaction_id, "response"))
    out = dict(inbound)
    for leg in ordered:
        if leg.leg_type != "response":
            continue
        producer = entities.get(leg.callee_entity_id)
        if producer is None or memory.accumulates(producer):
            continue
        key = (leg.interaction_id, "response")
        have = tuple(out.get(key, ()))
        extra = tuple(k for k in children.get(leg.interaction_id, ())
                      if position[k] < position[key] and k not in have)
        if extra:
            out[key] = have + extra
    return out
