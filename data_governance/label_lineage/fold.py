"""The fold: one trace's legs in wire order, the units at each payload carried on
to the next by a finder. Pure: a :class:`TraceInput` in, a
:class:`~.summary.Derivation` out.

At each payload-bearing leg, in :func:`wire_order`:

1. **inputs** — the leg's inbound legs (``traversal.inbound_sets``; a tool or an
   LLM routed by the call it is answering, :func:`with_invocation_scope`; an
   entity declared ``call`` or ``conversation`` routed as declared,
   :func:`.memory.mixes_calls`); each
   contributes the occurrences it holds after its own fold. A leg with no payload
   is routed but holds nothing.
2. **find** — the finder, once per input, returns candidate occurrences here.
3. **attribute** — one owner per span (:func:`attribute`).
4. **carry** — with a crossing, how surely each value is its unit's (:func:`carry`).
5. **summarize** — the leg's edges become its :class:`~.summary.LegSummary`.

What the fold never does: look inside a finder, guess a parent, read a seq
threshold, order by ``seq`` alone, or stop at an absent payload.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from collections.abc import Mapping, Sequence

from data_governance.interaction_order import LegOrderEvent, LegOrderKey, leg_positions
from data_governance.processors.classification.projection import is_projectable, project
from data_governance.processors.data_lineage import memory
from data_governance.processors.data_lineage.traversal import Entity, Leg, inbound_sets

from . import cross
from .find import Finder
from .summary import Derivation, Edge, LegSummary, status_of, summarize
from .units import LegKey, Occurrence, ProjectedPayload, Site, Unit, at_origin, units_of
from .value import find_all

log = logging.getLogger(__name__)


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
    hash absent there is unclassified). *priors* — what persistent entities received
    in other conversations (:class:`.cross.Holding`) — is how a later conversation
    joins; it is read only by a cross-conversation derivation. *held_elsewhere* —
    per leg a persistent entity sends, how many occurrences other conversations hold
    with it valid at the leg's time — is read only under the holdings limit."""

    trace_id: str
    legs: Sequence[WireLeg]
    entities: Mapping[str, Entity]
    payloads: Mapping[str, object]
    findings: Mapping[str, Sequence[Mapping]]
    priors: Sequence[cross.Holding] = ()
    held_elsewhere: Mapping[LegKey, int] = dataclasses.field(default_factory=dict)


def wire_order(legs: Sequence[WireLeg]) -> list[WireLeg]:
    """The shared trace-local causal order (ADR-0035), using recorded parents.

    Observed times order otherwise ready legs; ingestion ``seq`` breaks ties.
    The fold still routes absent payloads and routes a memoryless entity by the
    call each leg belongs to.
    """
    positions = leg_positions(
        LegOrderEvent(leg.interaction_id, leg.leg_type, leg.parent_interaction_id,
                      leg.occurred_at, leg.seq)
        for leg in legs
    )
    return sorted(legs, key=lambda leg: positions[LegOrderKey(leg.interaction_id, leg.leg_type)])


def derive(trace_input: TraceInput, finder: Finder,
           declared: Mapping[str, str] = memory.NO_DECLARATION,
           crossing: cross.Crossing | None = None) -> Derivation:
    """Derive the label lineage of one trace; *declared* is the memory scope declared
    per entity natural key (:func:`.memory.parse_scopes`). With *crossing* (the
    cross-conversation switch on), at each leg in wire order a payload a persistent
    entity sends is first matched against what it holds (:func:`.cross.leaving`, the
    limits applied there), and what a persistent entity receives is then registered
    with it; without, the fold is the conversation's alone."""
    if trace_input.priors and crossing is None:
        raise ValueError("priors from other conversations are read only with the cross-conversation switch on")
    ordered = wire_order(trace_input.legs)
    inbound = inbound_sets(ordered, dict(trace_input.entities), pair_responses=True,
                           accumulates=lambda e: memory.mixes_calls(e, declared))
    inbound = with_invocation_scope(ordered, inbound, trace_input.entities, declared)
    texts = {h: project(c) for h, c in trace_input.payloads.items()}
    held: dict[LegKey, tuple[Occurrence, ...]] = {}
    summaries: dict[LegKey, LegSummary] = {}
    edges: list[Edge] = []
    known = cross.Memory(trace_input.priors) if crossing else None
    levels: dict[tuple, cross.Crossed] = {}
    level_of: dict[tuple, str] = {}
    crossed_at: dict[tuple, dt.datetime] = {}     # occurrence key -> the latest crossing it rests on, in event time
    carried: dict[tuple, cross.Carried] = {}
    capped: list[cross.Capped] = []
    cuts: list[tuple[LegKey, str]] = []
    sends: list[tuple[str, LegKey, dt.datetime]] = []
    holdings: list[cross.Holding] = []

    def persistent(entity_id: str) -> bool:
        return is_persistent(trace_input.entities, entity_id, declared)

    for leg in ordered:
        if leg.payload_hash is None:
            continue
        key = (leg.interaction_id, leg.leg_type)
        site = Site(trace_input.trace_id, leg.interaction_id, leg.leg_type, leg.payload_hash)
        output = ProjectedPayload(site, texts.get(leg.payload_hash, ""))
        findings = trace_input.findings.get(leg.payload_hash)
        own = units_of(findings or (), output.text, site)
        candidates = [c for k in inbound.get(key, ()) for c in finder(held.get(k, ()), output)]
        sender, receiver = ends(leg)
        found: list[tuple[Occurrence, cross.Crossed]] = []
        if known is not None and leg.occurred_at is None and (persistent(sender) or persistent(receiver)):
            log.debug("leg %s %s has no event time: not matched, not registered", *key)
        elif known is not None and persistent(sender):
            held_now = 0 if crossing.holdings_limit is None else trace_input.held_elsewhere.get(key, 0) + sum(
                h.holder_entity_id == sender and h.valid_at(leg.occurred_at) for h in holdings)
            found, cap, cut = cross.leaving(known, crossing, sender, output, leg.occurred_at,
                                               inbound.get(key, ()), held_now)
            capped += cap
            cuts += [(key, limit) for limit in cut]
            sends.append((sender, key, leg.occurred_at))
        crossed = {Edge(to=dataclasses.replace(o, source=None), came_from=o.source).key: c for o, c in found}
        # a crossed occurrence carried on claims no finding of a later leg either
        on = [level_of.get(c.source.key, cross.CERTAIN) != cross.CERTAIN for c in candidates]
        held[key], leg_edges = attribute([c for c, x in zip(candidates, on) if not x], own,
                                         [o for o, _ in found] + [c for c, x in zip(candidates, on) if x])
        levels.update((e.key, crossed[e.key]) for e in leg_edges if e.key in crossed)
        if known is not None:
            carried.update(carry(leg_edges, crossed, level_of))
            for e in leg_edges:
                at = leg.occurred_at if e.key in crossed else crossed_at.get(e.came_from.key)
                if at is not None:
                    crossed_at[e.to.key] = max(at, crossed_at.get(e.to.key, at))
        if known is not None and leg.occurred_at is not None and persistent(receiver):
            here = _received(receiver, _every_place(held[key], finder, output), own, level_of, crossed_at,
                             leg.occurred_at, crossing, records=cross.records(output.text) if _structured(
                                 trace_input.payloads.get(leg.payload_hash)) else {})
            for h in here:
                known.add(h)
            holdings += here
        edges.extend(leg_edges)
        summaries[key] = summarize(key, leg.payload_hash, leg.seq, classified=findings is not None,
                                   edges=leg_edges, unanchored=sum(not u.anchored for u in own))
    return Derivation(trace_id=trace_input.trace_id, edges=tuple(edges), legs=summaries,
                      occurrences=held, status=status_of(summaries, len(edges)),
                      levels=levels, carried=carried, capped=tuple(capped), holdings=tuple(holdings),
                      cuts=tuple(cuts), sends=tuple(sends))


def ends(leg: Leg) -> tuple[str, str]:
    """(sender, receiver): a request is sent by its caller, a response by its callee."""
    if leg.leg_type == "request":
        return leg.caller_entity_id, leg.callee_entity_id
    return leg.callee_entity_id, leg.caller_entity_id


def is_persistent(entities: Mapping[str, Entity], entity_id: str, declared: Mapping[str, str]) -> bool:
    """Whether *entity_id* remembers across conversations (:func:`.memory.scope`)."""
    entity = entities.get(entity_id)
    return entity is not None and memory.scope(entity, declared) == "persistent"


def _structured(content: object) -> bool:
    """Whether a payload was projected as its serialised JSON: an object or an array
    with no prose projection. Only there can a value sit in a record (R4)."""
    return isinstance(content, (dict, list)) and not is_projectable(content)


def carry(edges: Sequence[Edge], crossed: Mapping[tuple, cross.Crossed],
          level_of: dict[tuple, str]) -> dict[tuple, cross.Carried]:
    """How surely each value these *edges* (into one leg) reach is its unit's, and the
    edges whose value rests on a crossing (R3).

    An origin, and whatever reaches a leg inside the conversation from one, is
    :data:`.cross.CERTAIN`. A crossing edge's step is the weakest of its rule level and
    the level its holding was registered with (a holding that was itself crossed); any
    other edge carries its carrier's level on. An occurrence takes the strongest of its
    edges' steps: of two paths of one origin the better holds, so an origin known inside
    the conversation is certain however else it also arrives. *level_of* (occurrence key
    -> level) is filled in for the legs after this one.

    Of the occurrences on one span that are not certain, the value carries the strongest
    level, and is ambiguous when they are of more than one origin unit. Every edge into
    such an occurrence maps to that."""
    for e in edges:
        if e.key in crossed:
            step = cross.step(crossed[e.key].level, crossed[e.key].holding)
        else:
            step = level_of.get(e.came_from.key, cross.CERTAIN)
        level_of[e.to.key] = cross.strongest((level_of.get(e.to.key, step), step))
    on_span: dict[tuple[int, int] | None, list[Occurrence]] = {}
    for o in {e.to.key: e.to for e in edges}.values():
        if level_of[o.key] != cross.CERTAIN:
            on_span.setdefault(o.span, []).append(o)
    value = {span: cross.Carried(cross.strongest(level_of[o.key] for o in occs), len({o.unit.key for o in occs}) > 1)
             for span, occs in on_span.items()}
    return {e.key: value[e.to.span] for e in edges if level_of[e.to.key] != cross.CERTAIN}


def _every_place(occurrences: Sequence[Occurrence], finder: Finder, output: ProjectedPayload) -> list[Occurrence]:
    """*occurrences* at one payload, and each further place of that payload where the
    finder finds the value of a unit that originates there. Equal findings of one
    payload are one unit, at its first place (:func:`.units.units_of`); what an entity
    receives is the value at every place it sits, each with the record of that place
    (R4). The unit stays one origin, and the leg's own occurrences are untouched."""
    places: dict[tuple, Occurrence] = {}
    for o in (*occurrences, *finder([o for o in occurrences if o.at == o.unit.origin], output)):
        places.setdefault(o.key, o)
    return list(places.values())


def _received(entity_id: str, occurrences: Sequence[Occurrence], own: Sequence[Unit], level_of: Mapping[tuple, str],
              crossed_at: Mapping[tuple, dt.datetime], t: dt.datetime, crossing: cross.Crossing,
              records: Mapping[tuple[int, int], tuple[int, int]]) -> list[cross.Holding]:
    """What a persistent *entity_id* holds after receiving these *occurrences* at *t*:
    every labelled value, with the record it sat in, valid for the retention, and how
    the leg knew its label (crossed, with the level it reached this leg with,
    :func:`carry`; else classified, when a finding of this leg is the value, whether it
    originates here or was confirmed here; else inherited). A value with no run of
    letters or digits cannot be found by the exact finder's boundary rule, so it is not
    held. A crossed value is valid from the later of *t* and the crossing it rests on
    (*crossed_at*), so it never begins before what fed it, whatever the clocks say; a
    value whose interval that leaves empty is not held."""
    out = []
    for occ in occurrences:
        if occ.span is None or cross.core(occ.held) is None:
            continue
        level: str | None = level_of.get(occ.key, cross.CERTAIN)
        if level != cross.CERTAIN:
            label_basis = "crossed"
        elif any(u.anchored and _inside(u, occ) for u in own):
            label_basis, level = "classified", None
        else:
            label_basis, level = "inherited", None
        start = max(t, crossed_at.get(occ.key, t)) if label_basis == "crossed" else t
        if start >= t + crossing.retention:
            continue
        out.append(cross.Holding(entity_id, dataclasses.replace(occ, source=None), records.get(occ.span),
                                 start, t + crossing.retention, label_basis, level))
    return out


def attribute(candidates: Sequence[Occurrence], own: Sequence[Unit],
              crossed: Sequence[Occurrence] = ()) -> tuple[tuple[Occurrence, ...], list[Edge]]:
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
    *crossed* — candidates from another conversation (:func:`.cross.leaving`), and
    candidates carrying a crossed label on (:func:`carry`) — take part in every clause
    but (a): a finding at the leg stays its own origin beside a crossed label on its
    span, separate evidence that is never weakened.

    Ambiguity is recorded, never resolved: candidates of DIFFERENT types on one span
    (the same four digits read as an SSN suffix in one place and a card code in
    another) both stay, two edges and two labels. A later change may add a context
    tie-break (the same JSON key path) as a further clause; none is removed.
    """
    pool = [*candidates, *crossed]
    spanned = [c for c in pool if c.span is not None]
    survivors = [c for c in pool if not any(_nests_in(c, d) for d in spanned)]
    from_elsewhere = {id(c) for c in crossed}
    claimed = {u.key for u in own if u.anchored
               and any(_inside(u, c) for c in survivors if c.span is not None and id(c) not in from_elsewhere)}
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
                          entities: Mapping[str, Entity],
                          declared: Mapping[str, str] = memory.NO_DECLARATION) -> dict[LegKey, tuple[LegKey, ...]]:
    """A tool or an LLM keeps one memory per call it receives (#348); so does any
    entity :func:`.memory.mixes_calls` says does not pool its calls under *declared*.

    What a memoryless entity sends inside a call (its requests to others and its
    answer) inherits from the request that opened the call and from the responses it
    received inside the call before it; nothing crosses from one call to another.
    The call a leg belongs to: a response's own interaction; a request's
    ``parent_interaction_id``, when that interaction's request is present and was
    received by the same entity. Of the responses inside a call, only those the
    entity itself received count (a response is inbound to its caller). ``inbound_sets`` routes a memoryless entity by time
    instead (a request inherits the latest leg it received, from whichever call; a
    response its own request only), so its sets are replaced here. A request whose
    call is unknown keeps them. Entities that pool their calls are untouched: one
    memory for all calls (#349). ``inbound_sets`` itself is untouched.
    """
    position = {(leg.interaction_id, leg.leg_type): i for i, leg in enumerate(ordered)}
    receiver = {leg.interaction_id: leg.callee_entity_id for leg in ordered if leg.leg_type == "request"}
    children: dict[str, list[tuple[LegKey, str]]] = {}
    for leg in ordered:
        if leg.leg_type == "response" and leg.parent_interaction_id:
            children.setdefault(leg.parent_interaction_id, []).append(
                ((leg.interaction_id, "response"), leg.caller_entity_id))
    out = dict(inbound)
    for leg in ordered:
        key = (leg.interaction_id, leg.leg_type)
        if leg.leg_type == "response":
            sender, call = leg.callee_entity_id, leg.interaction_id
            have = tuple(out.get(key, ()))
        else:
            sender, call = leg.caller_entity_id, leg.parent_interaction_id
            opener = (call, "request")
            if call is None or receiver.get(call) != sender or position[opener] > position[key]:
                continue
            have = (opener,)
        producer = entities.get(sender)
        if producer is None or memory.mixes_calls(producer, declared):
            continue
        out[key] = have + tuple(k for k, received_by in children.get(call, ())
                                if received_by == sender and position[k] < position[key] and k not in have)
    return out
