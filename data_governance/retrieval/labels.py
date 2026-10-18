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

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from data_governance import db
from data_governance.label_lineage.summary import LEVELS, highest

__all__ = [
    "CarriedLabelsView",
    "FoundLabelsView",
    "GetLegLabelsResult",
    "LabelEdge",
    "LabelOriginView",
    "LabelView",
    "LegLabelsView",
    "get_leg_labels",
    "merge",
]


# ---------------------------------------------------------------------------
# Return types — field names serialize verbatim to the wire shape the UI reads
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelOriginView:
    """Where one carried-in label was found by the classifier: the origin leg,
    its ``seq`` and caller/callee (``None`` when the origin leg is gone), what the
    classifier marked there (``unit_text``) and the value it denotes
    (``unit_value``; the two differ when the extent rule widened a number).
    ``origin_trace_id`` is ``None`` for an origin in this trace."""

    origin_interaction_id: str
    origin_leg_type: str
    origin_seq: int | None
    caller: str | None
    callee: str | None
    unit_text: str
    unit_value: str
    match_kind: str
    origin_trace_id: str | None


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


def _rank(level: str | None) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> int:
    return min(a[1], b[1]) - max(a[0], b[0])


def _span(finding: dict[str, Any]) -> tuple[int, int] | None:
    """A finding's ``(start, end)``, or ``None`` when either offset is missing
    or not an integer."""
    start, end = finding.get("start"), finding.get("end")
    return (start, end) if isinstance(start, int) and isinstance(end, int) else None


def _origins(edges: Sequence[LabelEdge]) -> list[LabelOriginView]:
    """One origin per (origin leg, unit) — two carriers of one origin are two
    edges but one origin — in origin ``seq`` order."""
    by_unit: dict[tuple, LabelEdge] = {}
    for e in edges:
        by_unit.setdefault((e.origin_interaction_id, e.origin_leg_type, e.origin_payload_hash,
                            e.unit_start, e.unit_end), e)
    ordered = sorted(by_unit.values(), key=lambda e: (
        e.origin_seq is None, e.origin_seq or 0, e.origin_interaction_id, e.unit_start))
    return [LabelOriginView(
        origin_interaction_id=e.origin_interaction_id, origin_leg_type=e.origin_leg_type,
        origin_seq=e.origin_seq, caller=e.caller, callee=e.callee,
        unit_text=e.unit_text, unit_value=e.unit_value, match_kind=e.match_kind,
        origin_trace_id=e.origin_trace_id,
    ) for e in ordered]


def merge(findings: Sequence[dict[str, Any]], edges: Sequence[LabelEdge]) -> list[LabelView]:
    """Pair the classifier's *findings* with the carried-in *edges* per
    ``(span, entity_type)``.

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
            found_here=True, carried_in=_origins(carried),
        ))
    for group in unclaimed:
        head = group[0]
        rows.append(LabelView(
            text=head.matched_text if head.matched_text is not None else head.unit_value,
            start=head.matched_start, end=head.matched_end, entity_type=head.entity_type,
            sensitivity_level=highest([e.sensitivity_level for e in group]),
            regulatory_tags=sorted({t for e in group for t in e.regulatory_tags}),
            found_here=False, carried_in=_origins(group),
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

# The edges into the legs at their current payloads; the origin's seq and
# caller/callee are LEFT JOINs, so a missing origin leg reads as nulls rather
# than dropping the edge.
_EDGES_SQL = (
    "SELECT e.to_leg_type::text, e.entity_type, e.regulatory_tags, e.sensitivity_level, "
    "       e.matched_text, e.matched_start, e.matched_end, "
    "       e.origin_interaction_id, e.origin_leg_type::text, e.origin_payload_hash, "
    "       e.unit_start, e.unit_end, e.unit_text, e.unit_value, e.match_kind, "
    "       e.origin_trace_id, ol.seq, ce.display_name, ke.display_name "
    "FROM payload_label_edges e "
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


def get_leg_labels(trace_id: str, interaction_id: str) -> GetLegLabelsResult:
    """Read the found-here and carried-in labels of *interaction_id*'s
    payload-bearing legs (see the module docstring). Empty for an unknown
    interaction, one of another trace, or a DB without the interactions
    migration."""
    with db.transaction() as tx:
        if not _tables_exist(tx, ("interactions", "interaction_legs", "payload_classifications")):
            return GetLegLabelsResult()
        legs = tx.fetch_all(_LEGS_SQL, (interaction_id, trace_id))
        if not legs:
            return GetLegLabelsResult()

        edges: dict[str, list[LabelEdge]] = {}
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
                    caller=r[17], callee=r[18],
                ))
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
                labels=merge(findings or [], into),
            ))
        return GetLegLabelsResult(legs=views, lineage_status=status)
