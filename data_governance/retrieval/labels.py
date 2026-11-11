"""Retrieval submodule — the per-leg **Labels** read: what the classifier found in
an interaction's payloads and what label lineage carried into them.

Part of the :mod:`data_governance.retrieval` package. Two sources, read side by
side and never confused:

- **Found here** — the classifier's own findings on the leg's payload
  (``payload_classifications``, keyed by content hash, ADR-0024).
- **Carried in** — the ``payload_label_edges`` into the leg (migration 0024): the
  same text carried that label earlier in the trace. Each edge names its origin
  unit; the origin's ``seq`` and caller/callee come from joins, because the edge's
  own ``seq`` is the *to* leg's.

**Pinned to the leg's current payload.** Edges are read at the leg's
``(leg_type, payload_hash)`` now, the rule the risk engine applies
(``risk/engine/evidence.py`` ``_INHERITED_SQL``): an edge derived from a payload
the leg was rewritten away from is not shown.

**Chains: the lineage of each carried-in value.** Every origin on a label carries
``chains``, routes back along ``from_*`` from this leg to the origin, one hop
per payload the value sat on, in ``seq`` order from the origin (``role``
``origin``) through the carriers (``hop``) to this leg (``here``), and
``route_count``, how many such routes exist. A value reaches a leg along many
routes: the edges form a DAG with fan-in (an agent keeps every earlier holding
of a value, every LLM round trip echoes it back), and the number of routes is a
product over that fan-in while the number of edges is not. So this read never
enumerates routes. It reads the **sub-graph** once — every edge reachable
backwards from the edges into the leg, each once, a set union linear in edges —
and derives from it one shortest route per edge into the leg (each distinct
previous hop), the route count by a sum over parents, and ``carriers``, every
leg on any route (both ends of every reachable edge): the chains are previews,
the carriers are the full set a tracked value lights. Each next hop is read
at the payload the edge was derived from (``from_payload_hash``), so a chain
is a chain of payloads, not of legs as they stand now; only the *to* leg is
pinned to its current payload. The walk follows ``from_trace_id`` /
``origin_trace_id`` into other traces, so this read is no longer scoped to one
trace; every hop names its ``trace_id``. A cycle (the edges run from earlier to
later ``seq``, so none should exist) ends where the walk meets itself; a route
that ends before the origin (an edge whose source has no edge of its own) is
kept as a chain whose first hop is a ``hop``, never dressed up as an origin. The
origin hop carries an ``excerpt`` of the origin payload's projected text around
the unit, so the reader sees the value in the company it was born in.

**A fact, not a decision.** Nothing here says whether a carried label reached a
risk decision — that depends on ``RISK_INHERITED_LABELS``, which this pod does not
read. The risk record's ``classification_summary.<leg>.inherited`` is the only
place that says so. ``match_kind`` and ``origin_trace_id`` ride on every origin
so a later finder's kinds and cross-trace origins can be told apart without a
shape change.

:func:`merge` pairs the two sources per ``(span, entity_type)``, pure and unit
tested without a DB. Absences are shapes, never errors: an unknown interaction (or
one of another trace) has no legs, an unclassified leg has ``found=None``, and
before migration 0024 the legs carry found-here rows only with
``lineage_status=None`` (*not derived*, never ``complete``).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from data_governance import db
from data_governance.label_lineage.summary import LEVELS, highest
from data_governance.processors.classification.projection import project

__all__ = [
    "CarriedLabelsView",
    "CarrierLeg",
    "FoundLabelsView",
    "GetLegLabelsResult",
    "HopExcerpt",
    "LabelEdge",
    "LabelOriginView",
    "LabelView",
    "LegLabelsView",
    "LineageHop",
    "get_leg_labels",
    "merge",
]

#: Characters of the origin's projected text kept on each side of the unit.
EXCERPT_MARGIN = 80


# ---------------------------------------------------------------------------
# Return types — field names serialize verbatim to the wire shape the UI reads
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HopExcerpt:
    """A window of a payload's projected text around the hop's span, with the
    span re-based into it; an ellipsis stands where the window cut the text."""

    text: str
    start: int
    end: int


@dataclass(frozen=True)
class LineageHop:
    """One payload a value sat on, on its way from the origin to the leg read.
    ``role`` is ``origin`` (the classifier found it here; ``text``/``start``/
    ``end`` are the unit), ``hop`` (a carrier; the span the finder matched at
    that payload, ``None`` when the edge has none) or ``here`` (the leg read).
    ``seq`` and caller/callee are ``None`` when the leg is gone. ``trace_id`` is
    always set; a hop in another trace names that trace. ``excerpt`` is set on
    the origin hop when its payload is still stored."""

    interaction_id: str
    leg_type: str
    seq: int | None
    trace_id: str
    caller: str | None
    callee: str | None
    text: str | None
    start: int | None
    end: int | None
    role: str
    excerpt: HopExcerpt | None = None


@dataclass(frozen=True, order=True)
class CarrierLeg:
    """One leg a value sat on, on some route from its origin to the leg read."""

    trace_id: str
    interaction_id: str
    leg_type: str


@dataclass(frozen=True)
class LabelOriginView:
    """Where one carried-in label was found by the classifier: the origin leg,
    its ``seq`` and caller/callee (``None`` when the origin leg is gone), what the
    classifier marked there (``unit_text``) and the value it denotes
    (``unit_value``; the two differ when the extent rule widened a number).
    ``origin_trace_id`` is ``None`` for an origin in this trace. ``chains`` is
    one shortest route from this origin per edge into the leg (see the module
    docstring), shortest first, and ``route_count`` the number of routes there
    are in all. ``carriers`` is every leg on any of those routes, the origin and
    the leg read included: the chains are previews, the carriers are what a
    tracked value lights. ``chains`` and ``carriers`` are empty and
    ``route_count`` ``None`` only when the read was made without the walk
    (:func:`merge` called bare)."""

    origin_interaction_id: str
    origin_leg_type: str
    origin_seq: int | None
    caller: str | None
    callee: str | None
    unit_text: str
    unit_value: str
    match_kind: str
    origin_trace_id: str | None
    chains: list[list[LineageHop]] = field(default_factory=list)
    route_count: int | None = None
    carriers: list[CarrierLeg] = field(default_factory=list)


@dataclass(frozen=True)
class LabelView:
    """One label on one span of the leg's payload: ``found_here`` when the
    classifier found it on this payload, ``carried_in`` the origins label lineage
    reached it from. ``start``/``end`` index the projected text the classifier
    read; ``None`` for a carried-in match that has no span, or a finding without
    offsets. A row both found here and carried in has the highest level of the
    two sources and the union of their tags."""

    text: str
    start: int | None
    end: int | None
    entity_type: str
    sensitivity_level: str | None
    regulatory_tags: list[str]
    found_here: bool
    carried_in: list[LabelOriginView]


@dataclass(frozen=True)
class FoundLabelsView:
    """The classifier's document-level verdict on the leg's payload."""

    sensitivity_level: str
    regulatory_tags: list[str]


@dataclass(frozen=True)
class CarriedLabelsView:
    """What label lineage carried into the leg: the highest level, the tag union,
    and how many distinct origin legs it came from (not the edge count)."""

    max_level: str | None
    regulatory_tags: list[str]
    origin_count: int


@dataclass(frozen=True)
class LegLabelsView:
    """One payload-bearing leg. ``found`` is ``None`` while the payload is not
    classified; ``carried`` is ``None`` when nothing was carried in."""

    leg_type: str
    payload_hash: str
    seq: int
    found: FoundLabelsView | None
    carried: CarriedLabelsView | None
    labels: list[LabelView]


@dataclass(frozen=True)
class GetLegLabelsResult:
    """The interaction's payload-bearing legs in ``seq`` order, plus the trace's
    label-lineage status: ``"complete"`` | ``"partial"`` | ``None`` (not derived,
    or migration 0024 not run)."""

    legs: list[LegLabelsView] = field(default_factory=list)
    lineage_status: str | None = None


# ---------------------------------------------------------------------------
# The merge — pure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelEdge:
    """One ``payload_label_edges`` row into a leg, with its origin leg's ``seq``
    and caller/callee joined on — :func:`merge`'s input."""

    entity_type: str
    regulatory_tags: tuple[str, ...]
    sensitivity_level: str
    matched_text: str | None
    matched_start: int | None
    matched_end: int | None
    origin_interaction_id: str
    origin_leg_type: str
    origin_payload_hash: str
    unit_start: int
    unit_end: int
    unit_text: str
    unit_value: str
    match_kind: str
    origin_trace_id: str | None = None
    origin_seq: int | None = None
    caller: str | None = None
    callee: str | None = None
    edge_id: str | None = None


Chains = dict[str, list[list[LineageHop]]]
"""The chains that end at each edge into the leg, keyed by ``edge_id``."""

RouteCounts = dict[str, int]
"""How many routes end at each edge into the leg, keyed by ``edge_id``."""

Carriers = dict[str, frozenset[CarrierLeg]]
"""Every leg on any route that ends at each edge into the leg, keyed by ``edge_id``."""


def _rank(level: str | None) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> int:
    return min(a[1], b[1]) - max(a[0], b[0])


def _span(finding: dict[str, Any]) -> tuple[int, int] | None:
    """A finding's ``(start, end)``, or ``None`` when either offset is missing
    or not an integer."""
    start, end = finding.get("start"), finding.get("end")
    return (start, end) if isinstance(start, int) and isinstance(end, int) else None


def _chain_key(chain: Sequence[LineageHop]) -> tuple:
    return (len(chain), tuple((h.seq is None, h.seq or 0, h.interaction_id, h.leg_type) for h in chain))


def _origins(edges: Sequence[LabelEdge], chains: Chains | None = None,
             route_counts: RouteCounts | None = None,
             carriers: Carriers | None = None) -> list[LabelOriginView]:
    """One origin per (origin leg, unit) — two carriers of one origin are two
    edges but one origin — in origin ``seq`` order. The origin's chains are the
    chains of every edge in its group, shortest first, each once; its route
    count the sum over those edges, its carriers their union."""
    by_unit: dict[tuple, list[LabelEdge]] = {}
    for e in edges:
        by_unit.setdefault((e.origin_interaction_id, e.origin_leg_type, e.origin_payload_hash,
                            e.unit_start, e.unit_end), []).append(e)
    ordered = sorted(by_unit.values(), key=lambda g: (
        g[0].origin_seq is None, g[0].origin_seq or 0, g[0].origin_interaction_id, g[0].unit_start))
    views = []
    for group in ordered:
        e = group[0]
        seen: dict[tuple, list[LineageHop]] = {}
        for edge in group:
            for chain in (chains or {}).get(edge.edge_id or "", []):
                seen.setdefault(_chain_key(chain), chain)
        views.append(LabelOriginView(
            origin_interaction_id=e.origin_interaction_id, origin_leg_type=e.origin_leg_type,
            origin_seq=e.origin_seq, caller=e.caller, callee=e.callee,
            unit_text=e.unit_text, unit_value=e.unit_value, match_kind=e.match_kind,
            origin_trace_id=e.origin_trace_id,
            chains=[seen[k] for k in sorted(seen)],
            route_count=None if route_counts is None else sum(
                route_counts.get(edge.edge_id or "", 0) for edge in group),
            carriers=sorted(set().union(*((carriers or {}).get(edge.edge_id or "", ()) for edge in group))),
        ))
    return views


def merge(findings: Sequence[dict[str, Any]], edges: Sequence[LabelEdge],
          chains: Chains | None = None, route_counts: RouteCounts | None = None,
          carriers: Carriers | None = None) -> list[LabelView]:
    """Pair the classifier's *findings* with the carried-in *edges* per
    ``(span, entity_type)``. *chains*, *route_counts* and *carriers* (by
    ``edge_id``) ride onto each origin; without them every origin's ``chains``
    and ``carriers`` are empty and its ``route_count`` ``None``.

    Edges are grouped by ``(entity_type, matched span)``. A finding claims each
    same-type group whose span overlaps its own — overlap, not equality, because
    the extent rule can widen a number past what the classifier marked here — and
    a group overlapping several findings goes to the one it overlaps most. A
    claimed group makes the finding's row both found here and carried in; an
    unclaimed group is a carried-in row; an unclaimed finding a found-here row.
    A claimed row's level is the highest of the finding's and its edges', its
    tags the union. Edges without a span group by value and are rows without a
    span; a finding without integer offsets is a found-here row without a span
    and claims nothing. Two labels are never folded into one row, so none is
    lost.

    Rows are ordered by start (spanless last), then highest level first, then
    type.
    """
    groups: dict[tuple, list[LabelEdge]] = {}
    for e in edges:
        key = ((e.entity_type, e.matched_start, e.matched_end) if e.matched_start is not None
               else (e.entity_type, None, None, e.unit_value))
        groups.setdefault(key, []).append(e)

    spans = [_span(f) for f in findings]
    claimed: dict[int, list[LabelEdge]] = {i: [] for i in range(len(findings))}
    unclaimed: list[list[LabelEdge]] = []
    for key, group in groups.items():
        best: tuple[int, int] | None = None  # (overlap, finding index)
        if key[1] is not None:
            for i, f in enumerate(findings):
                span = spans[i]
                if span is None or f.get("entity_type") != key[0]:
                    continue
                overlap = _overlaps(span, (key[1], key[2]))
                if overlap > 0 and (best is None or overlap > best[0]):
                    best = (overlap, i)
        if best is None:
            unclaimed.append(group)
        else:
            claimed[best[1]].extend(group)

    rows: list[LabelView] = []
    for i, f in enumerate(findings):
        carried = claimed[i]
        own_tags = list(f.get("regulatory_tags") or [])
        levels = [lv for lv in [f.get("sensitivity_level"), *(e.sensitivity_level for e in carried)] if lv]
        span = spans[i]
        rows.append(LabelView(
            text=str(f.get("text", "")), start=None if span is None else span[0],
            end=None if span is None else span[1],
            entity_type=str(f.get("entity_type", "")),
            sensitivity_level=highest(levels),
            regulatory_tags=sorted({*own_tags, *(t for e in carried for t in e.regulatory_tags)}),
            found_here=True, carried_in=_origins(carried, chains, route_counts, carriers),
        ))
    for group in unclaimed:
        head = group[0]
        rows.append(LabelView(
            text=head.matched_text if head.matched_text is not None else head.unit_value,
            start=head.matched_start, end=head.matched_end, entity_type=head.entity_type,
            sensitivity_level=highest([e.sensitivity_level for e in group]),
            regulatory_tags=sorted({t for e in group for t in e.regulatory_tags}),
            found_here=False, carried_in=_origins(group, chains, route_counts, carriers),
        ))
    rows.sort(key=lambda r: (r.start is None, r.start or 0, -_rank(r.sensitivity_level), r.entity_type))
    return rows


def _carried_summary(edges: Sequence[LabelEdge]) -> CarriedLabelsView | None:
    if not edges:
        return None
    return CarriedLabelsView(
        max_level=highest([e.sensitivity_level for e in edges]),
        regulatory_tags=sorted({t for e in edges for t in e.regulatory_tags}),
        origin_count=len({(e.origin_interaction_id, e.origin_leg_type) for e in edges}),
    )


# ---------------------------------------------------------------------------
# The read
# ---------------------------------------------------------------------------


def _tables_exist(tx: db.Transaction, names: tuple[str, ...]) -> bool:
    """Whether every table in *names* exists (``COUNT(DISTINCT)``: one name can
    sit in more than one schema on the search path)."""
    row = tx.fetch_one(
        "SELECT COUNT(DISTINCT table_name) FROM information_schema.tables "
        "WHERE table_name = ANY(%s)",
        (list(names),),
    )
    return row is not None and row[0] == len(names)


_LEGS_SQL = (
    "SELECT l.leg_type::text, l.payload_hash, l.seq, "
    "       c.sensitivity_level, c.regulatory_tags, c.findings "
    "FROM interaction_legs l "
    "JOIN interactions i ON i.id = l.interaction_id "
    "LEFT JOIN payload_classifications c ON c.content_hash = l.payload_hash "
    "WHERE l.interaction_id = %s AND i.trace_id = %s AND l.payload_hash IS NOT NULL "
    "ORDER BY l.seq ASC"
)

# The edges into the legs at their current trace and payloads; the origin's seq and
# caller/callee are LEFT JOINs, so a missing origin leg reads as nulls rather
# than dropping the edge.
_EDGES_SQL = (
    "SELECT e.to_leg_type::text, e.entity_type, e.regulatory_tags, e.sensitivity_level, "
    "       e.matched_text, e.matched_start, e.matched_end, "
    "       e.origin_interaction_id, e.origin_leg_type::text, e.origin_payload_hash, "
    "       e.unit_start, e.unit_end, e.unit_text, e.unit_value, e.match_kind, "
    "       e.origin_trace_id, ol.seq, ce.display_name, ke.display_name, e.edge_id "
    "FROM payload_label_edges e "
    "JOIN interactions current_i "
    "  ON current_i.id = e.to_interaction_id AND current_i.trace_id = e.trace_id "
    "JOIN unnest(%s::text[], %s::text[]) AS cur(leg_type, payload_hash) "
    "  ON cur.leg_type = e.to_leg_type::text AND cur.payload_hash = e.to_payload_hash "
    "LEFT JOIN interaction_legs ol "
    "  ON ol.interaction_id = e.origin_interaction_id AND ol.leg_type = e.origin_leg_type "
    "LEFT JOIN interactions oi ON oi.id = e.origin_interaction_id "
    "LEFT JOIN entities ce ON ce.id = oi.caller_entity_id "
    "LEFT JOIN entities ke ON ke.id = oi.callee_entity_id "
    "WHERE e.to_interaction_id = %s "
    "ORDER BY e.to_leg_type, e.matched_start NULLS LAST, e.entity_type, e.edge_id"
)

# The sub-graph: from the edges into the leg (the anchors), each step reads the
# edges into the current edge's FROM side at the payload the edge was derived
# from, for the same origin unit and type. UNION (set semantics) returns each
# reachable edge once, however many routes lead to it, so the read is linear in
# edges and a cycle ends by itself. The routes and their count are derived in
# Python from the sub-graph; nothing here enumerates them.
_ORIGIN_MATCH = (
    "n.origin_interaction_id = w.origin_interaction_id "
    "AND n.origin_leg_type::text = w.origin_leg_type "
    "AND n.origin_payload_hash = w.origin_payload_hash "
    "AND n.unit_start = w.unit_start AND n.unit_end = w.unit_end "
    "AND n.entity_type = w.entity_type"
)
_AT_ORIGIN = ("w.from_interaction_id = w.origin_interaction_id "
              "AND w.from_leg_type = w.origin_leg_type")
_SUBGRAPH_SQL = (
    "WITH RECURSIVE walk(edge_id, from_interaction_id, from_leg_type, from_payload_hash, "
    "                    from_trace_id, origin_interaction_id, origin_leg_type, origin_payload_hash, "
    "                    unit_start, unit_end, entity_type) AS ("
    "  SELECT e.edge_id, e.from_interaction_id, e.from_leg_type::text, e.from_payload_hash, "
    "         COALESCE(e.from_trace_id, e.trace_id), e.origin_interaction_id, "
    "         e.origin_leg_type::text, e.origin_payload_hash, e.unit_start, e.unit_end, "
    "         e.entity_type "
    "  FROM payload_label_edges e WHERE e.edge_id = ANY(%s) "
    "  UNION "
    "  SELECT n.edge_id, n.from_interaction_id, n.from_leg_type::text, n.from_payload_hash, "
    "         COALESCE(n.from_trace_id, n.trace_id), w.origin_interaction_id, w.origin_leg_type, "
    "         w.origin_payload_hash, w.unit_start, w.unit_end, w.entity_type "
    "  FROM walk w JOIN payload_label_edges n "
    "    ON n.to_interaction_id = w.from_interaction_id "
    "   AND n.to_leg_type::text = w.from_leg_type "
    "   AND n.to_payload_hash = w.from_payload_hash "
    "   AND n.trace_id = w.from_trace_id "
    f"   AND {_ORIGIN_MATCH} "
    f"  WHERE NOT ({_AT_ORIGIN})"
    ") "
    "SELECT w.edge_id, e.trace_id, e.to_interaction_id, e.to_leg_type::text, e.to_payload_hash, "
    "       w.from_trace_id, w.from_interaction_id, w.from_leg_type, w.from_payload_hash, "
    "       w.origin_interaction_id, w.origin_leg_type, w.origin_payload_hash, "
    "       w.unit_start, w.unit_end, w.entity_type, "
    f"      ({_AT_ORIGIN.replace('w.', 'e.')}) AS at_origin "
    "FROM walk w JOIN payload_label_edges e ON e.edge_id = w.edge_id"
)

# Both ends of every edge a chain walked: the to side (its trace is the edge's)
# and the from side (its trace is `from_trace_id`, or the edge's when null).
_HOPS_SQL = (
    "SELECT e.edge_id, e.trace_id, "
    "       e.to_interaction_id, e.to_leg_type::text, tl.seq, tc.display_name, tk.display_name, "
    "       e.from_interaction_id, e.from_leg_type::text, e.from_payload_hash, "
    "       COALESCE(e.from_trace_id, e.trace_id), fl.seq, fc.display_name, fk.display_name, "
    "       e.matched_text, e.matched_start, e.matched_end, e.unit_text, e.unit_start, e.unit_end "
    "FROM payload_label_edges e "
    "LEFT JOIN interaction_legs tl ON tl.interaction_id = e.to_interaction_id AND tl.leg_type = e.to_leg_type "
    "LEFT JOIN interactions ti ON ti.id = e.to_interaction_id "
    "LEFT JOIN entities tc ON tc.id = ti.caller_entity_id "
    "LEFT JOIN entities tk ON tk.id = ti.callee_entity_id "
    "LEFT JOIN interaction_legs fl ON fl.interaction_id = e.from_interaction_id AND fl.leg_type = e.from_leg_type "
    "LEFT JOIN interactions fi ON fi.id = e.from_interaction_id "
    "LEFT JOIN entities fc ON fc.id = fi.caller_entity_id "
    "LEFT JOIN entities fk ON fk.id = fi.callee_entity_id "
    "WHERE e.edge_id = ANY(%s)"
)

_PAYLOADS_SQL = "SELECT content_hash, content FROM interaction_payloads WHERE content_hash = ANY(%s)"


@dataclass(frozen=True)
class _Side:
    interaction_id: str
    leg_type: str
    seq: int | None
    trace_id: str
    caller: str | None
    callee: str | None


@dataclass(frozen=True)
class _EdgeEnds:
    to: _Side
    frm: _Side
    from_payload_hash: str
    matched_text: str | None
    matched_start: int | None
    matched_end: int | None
    unit_text: str
    unit_start: int
    unit_end: int


def _excerpt(text: str, start: int, end: int) -> HopExcerpt | None:
    """*text* cut to :data:`EXCERPT_MARGIN` around ``[start, end)``, the span
    re-based; ``None`` when the span does not fit the text (a payload rewritten
    since the derivation)."""
    if not (0 <= start <= end <= len(text)):
        return None
    lo, hi = max(0, start - EXCERPT_MARGIN), min(len(text), end + EXCERPT_MARGIN)
    head = "\u2026" if lo > 0 else ""
    tail = "\u2026" if hi < len(text) else ""
    return HopExcerpt(text=head + text[lo:hi] + tail,
                      start=start - lo + len(head), end=end - lo + len(head))


def _hop(side: _Side, role: str, text: str | None, start: int | None, end: int | None,
         excerpt: HopExcerpt | None = None) -> LineageHop:
    return LineageHop(interaction_id=side.interaction_id, leg_type=side.leg_type, seq=side.seq,
                      trace_id=side.trace_id, caller=side.caller, callee=side.callee,
                      text=text, start=start, end=end, role=role, excerpt=excerpt)


def _route_counts(anchors: Sequence[str], parents: dict[str, Sequence[str]]) -> RouteCounts:
    """Routes ending at each anchor edge: the sum over its parents, an edge with
    no parent (the origin, or a dead end) counting one. Memoised over the
    sub-graph, iterative, so a route of any length costs its edges once; an
    edge met again while its own count is pending (a cycle) counts nothing."""
    memo: RouteCounts = {}
    pending: set[str] = set()
    for start in anchors:
        stack = [(start, False)]
        while stack:
            eid, ready = stack.pop()
            if ready:
                memo[eid] = max(1, sum(memo.get(p, 0) for p in parents[eid]))
                pending.discard(eid)
            elif eid not in memo and eid not in pending:
                pending.add(eid)
                stack.append((eid, True))
                stack.extend((p, False) for p in parents[eid] if p not in memo and p not in pending)
    return {a: memo[a] for a in anchors}


def _shortest_route(start: str, parents: dict[str, Sequence[str]], at_origin: dict[str, bool]
                    ) -> tuple[list[str], bool]:
    """The edge ids of one shortest route from *start* back to an origin edge
    (BFS over the sub-graph), and whether it reached one. When no origin is
    reachable, the route ends at the nearest dead end — an edge with no parent
    at all; only when there is none (every route runs in a circle) does it end
    at the first edge whose every parent was already walked, which is how a
    cycle ends. Branches that converge also meet already-walked parents, so
    that test alone would end a route early."""
    prev: dict[str, str | None] = {start: None}
    queue = deque([start])
    end = dead_end = closes_cycle = None
    while queue:
        eid = queue.popleft()
        if at_origin[eid]:
            end = eid
            break
        if not parents[eid]:
            dead_end = dead_end or eid
            continue
        fresh = [p for p in parents[eid] if p not in prev]
        if not fresh:
            closes_cycle = closes_cycle or eid
        for p in fresh:
            prev[p] = eid
            queue.append(p)
    end = end or dead_end or closes_cycle or start
    route = []
    cur: str | None = end
    while cur is not None:
        route.append(cur)
        cur = prev[cur]
    route.reverse()  # start first, the origin (or dead end) last
    return route, at_origin[end]


def _carriers(start: str, parents: dict[str, Sequence[str]], legs: dict[str, tuple[CarrierLeg, CarrierLeg]]
              ) -> frozenset[CarrierLeg]:
    """Both ends of every edge on any route from *start*: the edges reachable
    from it in the sub-graph, each once."""
    seen, stack = {start}, [start]
    while stack:
        for p in parents[stack.pop()]:
            if p not in seen:
                seen.add(p)
                stack.append(p)
    return frozenset(leg for eid in seen for leg in legs[eid])


def _read_chains(tx: db.Transaction, edge_ids: list[str]) -> tuple[Chains, RouteCounts, Carriers]:
    """Read the sub-graph behind *edge_ids* once and derive, per edge, one
    shortest route to its origin, the number of routes and the legs on any of
    them (see the module docstring); build the hops of each route."""
    if not edge_ids:
        return {}, {}, {}
    # The sub-graph: each reachable edge once. An edge's parents are the edges of
    # the same origin unit into the payload it was derived from.
    into: dict[tuple, list[str]] = {}
    frm: dict[str, tuple] = {}
    at_origin: dict[str, bool] = {}
    legs: dict[str, tuple[CarrierLeg, CarrierLeg]] = {}
    for r in tx.fetch_all(_SUBGRAPH_SQL, (edge_ids,)):
        unit = tuple(r[9:15])
        into.setdefault((r[1], r[2], r[3], r[4], unit), []).append(r[0])
        frm[r[0]] = (r[5], r[6], r[7], r[8], unit)
        at_origin[r[0]] = bool(r[15])
        legs[r[0]] = (CarrierLeg(r[1], r[2], r[3]), CarrierLeg(r[5], r[6], r[7]))
    parents = {eid: () if at_origin[eid] else tuple(sorted(into.get(frm[eid], ()))) for eid in frm}
    anchors = [eid for eid in edge_ids if eid in frm]  # an anchor deleted since the edge read is skipped
    counts = _route_counts(anchors, parents)
    routes = {a: _shortest_route(a, parents, at_origin) for a in anchors}
    carriers = {a: _carriers(a, parents, legs) for a in anchors}

    walked = sorted({eid for path, _ in routes.values() for eid in path})
    ends: dict[str, _EdgeEnds] = {}
    for r in tx.fetch_all(_HOPS_SQL, (walked,)):
        ends[r[0]] = _EdgeEnds(
            to=_Side(r[2], r[3], None if r[4] is None else int(r[4]), r[1], r[5], r[6]),
            frm=_Side(r[7], r[8], None if r[11] is None else int(r[11]), r[10], r[12], r[13]),
            from_payload_hash=r[9], matched_text=r[14], matched_start=r[15], matched_end=r[16],
            unit_text=r[17], unit_start=r[18], unit_end=r[19],
        )
    origin_hashes = sorted({ends[path[-1]].from_payload_hash for path, complete in routes.values()
                            if complete and path[-1] in ends})
    texts: dict[str, str] = {}
    if origin_hashes:
        for h, content in tx.fetch_all(_PAYLOADS_SQL, (origin_hashes,)):
            texts[h] = project(content)

    chains: Chains = {}
    for start, (path, complete) in routes.items():
        if any(eid not in ends for eid in path):
            continue  # an edge deleted between the two reads: a re-derivation in flight
        last = ends[path[-1]]
        if complete:
            text = texts.get(last.from_payload_hash)
            excerpt = None if text is None else _excerpt(text, last.unit_start, last.unit_end)
            hops = [_hop(last.frm, "origin", last.unit_text, last.unit_start, last.unit_end, excerpt)]
        else:
            hops = [_hop(last.frm, "hop", None, None, None)]
        for eid in reversed(path):
            e = ends[eid]
            hops.append(_hop(e.to, "here" if eid == start else "hop",
                             e.matched_text, e.matched_start, e.matched_end))
        chains.setdefault(start, []).append(hops)
    return chains, counts, carriers


def get_leg_labels(trace_id: str, interaction_id: str) -> GetLegLabelsResult:
    """Read the found-here and carried-in labels of *interaction_id*'s
    payload-bearing legs, each carried-in origin with the chains the value
    travelled (see the module docstring). Empty for an unknown interaction, one
    of another trace, or a DB without the interactions migration."""
    with db.transaction() as tx:
        if not _tables_exist(tx, ("interactions", "interaction_legs", "payload_classifications")):
            return GetLegLabelsResult()
        legs = tx.fetch_all(_LEGS_SQL, (interaction_id, trace_id))
        if not legs:
            return GetLegLabelsResult()

        edges: dict[str, list[LabelEdge]] = {}
        chains: Chains = {}
        counts: RouteCounts = {}
        carriers: Carriers = {}
        status: str | None = None
        if _tables_exist(tx, ("payload_label_edges", "label_lineage_trace_status")):
            for r in tx.fetch_all(_EDGES_SQL, ([r[0] for r in legs], [r[1] for r in legs],
                                              interaction_id)):
                edges.setdefault(r[0], []).append(LabelEdge(
                    entity_type=r[1], regulatory_tags=tuple(r[2] or ()), sensitivity_level=r[3],
                    matched_text=r[4], matched_start=r[5], matched_end=r[6],
                    origin_interaction_id=r[7], origin_leg_type=r[8], origin_payload_hash=r[9],
                    unit_start=r[10], unit_end=r[11], unit_text=r[12], unit_value=r[13],
                    match_kind=r[14], origin_trace_id=r[15],
                    origin_seq=None if r[16] is None else int(r[16]),
                    caller=r[17], callee=r[18], edge_id=r[19],
                ))
            chains, counts, carriers = _read_chains(tx, [e.edge_id for into in edges.values() for e in into
                                                         if e.edge_id is not None])
            row = tx.fetch_one(
                "SELECT status FROM label_lineage_trace_status WHERE trace_id = %s",
                (trace_id,),
            )
            status = None if row is None else row[0]

        views = []
        for leg_type, payload_hash, seq, level, tags, findings in legs:
            into = edges.get(leg_type, [])
            views.append(LegLabelsView(
                leg_type=leg_type, payload_hash=payload_hash, seq=int(seq),
                # sensitivity_level is NOT NULL (migration 0008), so a null here
                # is exactly the LEFT JOIN miss: not classified yet.
                found=None if level is None else FoundLabelsView(
                    sensitivity_level=level, regulatory_tags=list(tags or [])),
                carried=_carried_summary(into),
                labels=merge(findings or [], into, chains, counts, carriers),
            ))
        return GetLegLabelsResult(legs=views, lineage_status=status)
