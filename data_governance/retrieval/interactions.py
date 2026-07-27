"""Retrieval submodule — **Interaction retrieval**, the typed read path over
the derived **Interaction** / **Entity** forest for one **Trace**.

Part of the :mod:`data_governance.retrieval` package. Sibling to the span-only
:mod:`.spans`; where that reads the raw ``spans`` table, this reads what
``P-interactions`` has materialised into ``interactions`` / ``interaction_legs``
/ ``interaction_spans`` / ``entities`` / ``entity_spans``.

The public surface is four functions and their frozen-dataclass return shapes.
All flow-read *logic* lives here, behind the interface — not in the REST layer:

- the request/response **Interaction leg**s nested on each interaction, request
  first (ADR-0025);
- leg **Duration** computed on read (``response.occurred_at −
  request.occurred_at``), **null when the response leg is absent** — the
  "response in flight" signal, never stored;
- the aggregated ``any_error`` roll-up over the legs;
- chronological ordering by the request leg's ``occurred_at`` (the parent row
  carries no ``started_at``);
- the not-yet-migrated **empty typed result**: before the interactions
  migration has run these return empty, never raise, so a fresh DB serves an
  empty flow rather than a 500.

Reads are trace-scoped and eventually consistent — they reflect whatever
``P-interactions`` has materialised so far.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from data_governance import db

__all__ = [
    "EntityView",
    "GetEntitiesResult",
    "GetEntitySpansResult",
    "GetInteractionSpansResult",
    "GetInteractionsResult",
    "InteractionLegView",
    "InteractionView",
    "SpanEvidenceView",
    "get_entities",
    "get_entity_spans",
    "get_interaction_spans",
    "get_interactions",
]


# ---------------------------------------------------------------------------
# Return types — field names serialize verbatim to the flow-view wire shape
# (the React SPA's TS types in ui/src/lib/flow.ts).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InteractionLegView:
    """One request/response leg of an **Interaction** (ADR-0025).

    ``occurred_at`` is an ISO-8601 string (the request leg's is the call-start
    time, the response leg's the completion time); ``None`` if the leg's
    timestamp is absent. ``payload_hash`` references the leg's **Payload**
    (request vs response body), ``None`` when the leg carries no body.
    ``error`` is the leg's tri-state error (``True`` / ``False`` / ``None``).
    """

    leg_type: str
    occurred_at: str | None
    payload_hash: str | None
    error: bool | None
    seq: int


@dataclass(frozen=True)
class InteractionView:
    """One derived **Interaction** — the parent identity row plus its nested
    legs and read-time derivations.

    ``duration_seconds`` = response − request occurrence, **None** when the
    response leg is absent. ``any_error`` aggregates the legs. ``span_count`` /
    ``anchor_count`` summarise the evidence so the **Flow view** table can size
    it without pulling every span (the rows are a per-id sub-read).
    """

    id: str
    caller_entity_id: str | None
    callee_entity_id: str | None
    summary: str | None
    parent_interaction_id: str | None
    legs: list[InteractionLegView]
    duration_seconds: float | None
    any_error: bool | None
    span_count: int
    anchor_count: int


@dataclass(frozen=True)
class EntityView:
    """One derived **Entity** the processor recorded provenance for in a trace.

    Cross-trace stable (ADR-0013): no ``trace_id`` of its own; scoped to the
    trace via ``entity_spans``. No ``retracted_at`` (ADR-0012 emit-once-final,
    no tombstone).
    """

    id: str
    kind: str
    natural_key: str
    display_name: str | None
    detected_from: str | None


@dataclass(frozen=True)
class SpanEvidenceView:
    """One span-evidence row for an **Interaction** or **Entity** — the joined
    span fields plus its role and (for interaction evidence) the leg it
    evidences. ``leg_type`` is ``None`` for entity evidence (``entity_spans``
    have no leg).
    """

    span_id: str
    role: str
    parent_id: str | None
    kind: str | None
    service_name: str | None
    leg_type: str | None = None


@dataclass(frozen=True)
class GetInteractionsResult:
    interactions: list[InteractionView] = field(default_factory=list)


@dataclass(frozen=True)
class GetEntitiesResult:
    entities: list[EntityView] = field(default_factory=list)


@dataclass(frozen=True)
class GetInteractionSpansResult:
    spans: list[SpanEvidenceView] = field(default_factory=list)


@dataclass(frozen=True)
class GetEntitySpansResult:
    spans: list[SpanEvidenceView] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Read-time derivations (moved behind the seam from the REST layer)
# ---------------------------------------------------------------------------


def _leg_duration(legs: list[InteractionLegView]) -> float | None:
    """Seconds between the request and response legs' ``occurred_at``, or None
    when either leg is absent (ADR-0025: null duration = "response in flight").
    Computed on read, never stored, so a later-finalizing response leg can never
    leave a stale value behind."""
    by_type = {leg.leg_type: leg.occurred_at for leg in legs}
    req, resp = by_type.get("request"), by_type.get("response")
    if req is None or resp is None:
        return None
    return (dt.datetime.fromisoformat(resp) - dt.datetime.fromisoformat(req)).total_seconds()


def _legs_any_error(legs: list[InteractionLegView]) -> bool | None:
    """Aggregate error across the legs: True if any leg errored, False if any
    leg is a definite success and none errored, else None (unknown)."""
    errs = [leg.error for leg in legs]
    if any(e is True for e in errs):
        return True
    if any(e is False for e in errs):
        return False
    return None


def _request_occurred_at(legs: list[InteractionLegView]) -> str | None:
    """The request leg's ``occurred_at`` (ISO string), or None."""
    for leg in legs:
        if leg.leg_type == "request":
            return leg.occurred_at
    return None


def _derived_tables_exist(tx: db.Transaction) -> bool:
    """Whether the interactions migration has run on this DB.

    The derived tables (``interactions``, ``entities``, and their link tables)
    all land in the same migration, so probing ``interactions`` is sufficient.
    Reads short-circuit to their empty shape when absent so a fresh DB serves an
    empty flow rather than an error.
    """
    return (
        tx.fetch_one(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'interactions'"
        )
        is not None
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def get_interactions(trace_id: str) -> GetInteractionsResult:
    """Read the derived **Interaction**s for a trace — parent identity rows with
    their nested legs, read-time **Duration** / ``any_error``, and span/anchor
    counts — ordered by the request leg's ``occurred_at``.

    Empty when the interactions migration has not run. Trace-scoped and
    eventually consistent (reflects what ``P-interactions`` has materialised).
    """
    with db.transaction() as tx:
        if not _derived_tables_exist(tx):
            return GetInteractionsResult()

        interactions = tx.fetch_all(
            "SELECT id::text, caller_entity_id::text, callee_entity_id::text, "
            "summary, parent_interaction_id::text "
            "FROM interactions WHERE trace_id = %s",
            (trace_id,),
        )
        # Legs for this trace's interactions — one scan, request leg first so
        # the sequence diagram draws request-then-response.
        leg_rows = tx.fetch_all(
            "SELECT l.interaction_id::text, l.leg_type::text, l.occurred_at, "
            "l.payload_hash, l.error, l.seq "
            "FROM interaction_legs l "
            "JOIN interactions i ON i.id = l.interaction_id "
            "WHERE i.trace_id = %s "
            "ORDER BY l.interaction_id, l.leg_type",
            (trace_id,),
        )
        legs_by_ix: dict[str, list[InteractionLegView]] = {}
        for iid, leg_type, occurred_at, payload_hash, error, seq in leg_rows:
            legs_by_ix.setdefault(iid, []).append(
                InteractionLegView(
                    leg_type=leg_type,
                    occurred_at=occurred_at.isoformat() if occurred_at else None,
                    payload_hash=payload_hash,
                    error=error,
                    seq=seq,
                )
            )
        # Per-interaction span aggregate — one grouped scan of the link table,
        # so the row count stays O(1) fetches regardless of trace size.
        count_rows = tx.fetch_all(
            "SELECT interaction_id::text, COUNT(*) AS span_count, "
            "COUNT(*) FILTER (WHERE role = 'anchor') AS anchor_count "
            "FROM interaction_spans WHERE trace_id = %s "
            "GROUP BY interaction_id",
            (trace_id,),
        )
        counts = {r[0]: (r[1], r[2]) for r in count_rows}

        views = [
            InteractionView(
                id=r[0],
                caller_entity_id=r[1],
                callee_entity_id=r[2],
                summary=r[3],
                parent_interaction_id=r[4],
                legs=legs_by_ix.get(r[0], []),
                duration_seconds=_leg_duration(legs_by_ix.get(r[0], [])),
                any_error=_legs_any_error(legs_by_ix.get(r[0], [])),
                span_count=counts.get(r[0], (0, 0))[0],
                anchor_count=counts.get(r[0], (0, 0))[1],
            )
            for r in interactions
        ]
        # Order by the request leg's occurrence so the flow list stays
        # chronological (the parent no longer carries started_at).
        views.sort(key=lambda ix: _request_occurred_at(ix.legs) or "")
        return GetInteractionsResult(interactions=views)


def get_entities(trace_id: str) -> GetEntitiesResult:
    """Read the derived **Entities** the processor recorded provenance for in a
    trace, reached via the trace-scoped ``entity_spans``. Empty when the
    interactions migration has not run.
    """
    with db.transaction() as tx:
        if not _derived_tables_exist(tx):
            return GetEntitiesResult()
        rows = tx.fetch_all(
            "SELECT id::text, kind, natural_key, display_name, detected_from "
            "FROM entities "
            "WHERE id IN (SELECT DISTINCT entity_id FROM entity_spans "
            "WHERE trace_id = %s) "
            "ORDER BY kind, display_name",
            (trace_id,),
        )
        return GetEntitiesResult(
            entities=[
                EntityView(
                    id=r[0], kind=r[1], natural_key=r[2],
                    display_name=r[3], detected_from=r[4],
                )
                for r in rows
            ]
        )


def get_interaction_spans(trace_id: str, interaction_id: str) -> GetInteractionSpansResult:
    """Read the span evidence for one **Interaction** (backs the detail panel's
    Spans table). Empty for an unknown id or before the migration — an empty
    table is the right **Flow view** state, not a 404. Each row carries the leg
    it evidences (ADR-0025).
    """
    with db.transaction() as tx:
        if not _derived_tables_exist(tx):
            return GetInteractionSpansResult()
        rows = tx.fetch_all(
            "SELECT s.span_id, pis.role, s.parent_id, s.kind, s.service_name, "
            "pis.leg_type::text "
            "FROM interaction_spans pis "
            "LEFT JOIN spans s "
            "  ON s.trace_id = pis.trace_id AND s.span_id = pis.span_id "
            "WHERE pis.trace_id = %s AND pis.interaction_id = %s",
            (trace_id, interaction_id),
        )
        return GetInteractionSpansResult(
            spans=[
                SpanEvidenceView(
                    span_id=r[0], role=r[1], parent_id=r[2],
                    kind=r[3], service_name=r[4], leg_type=r[5],
                )
                for r in rows
            ]
        )


def get_entity_spans(trace_id: str, entity_id: str) -> GetEntitySpansResult:
    """Read the span evidence for one **Entity**. Same shape and
    empty-on-unknown convention as :func:`get_interaction_spans`, but
    ``entity_spans`` have no leg, so ``leg_type`` is ``None``.
    """
    with db.transaction() as tx:
        if not _derived_tables_exist(tx):
            return GetEntitySpansResult()
        rows = tx.fetch_all(
            "SELECT s.span_id, pes.role, s.parent_id, s.kind, s.service_name "
            "FROM entity_spans pes "
            "LEFT JOIN spans s "
            "  ON s.trace_id = pes.trace_id AND s.span_id = pes.span_id "
            "WHERE pes.trace_id = %s AND pes.entity_id = %s",
            (trace_id, entity_id),
        )
        return GetEntitySpansResult(
            spans=[
                SpanEvidenceView(
                    span_id=r[0], role=r[1], parent_id=r[2],
                    kind=r[3], service_name=r[4], leg_type=None,
                )
                for r in rows
            ]
        )
