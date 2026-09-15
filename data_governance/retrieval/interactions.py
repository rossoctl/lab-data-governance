"""Retrieval submodule — **Interaction retrieval**, the typed read path over
the derived **Interaction** / **Entity** forest for one **Trace**.

Part of the :mod:`data_governance.retrieval` package. Sibling to the span-only
:mod:`.spans`; where that reads the raw ``spans`` table, this reads what
``P-interactions`` has materialised into ``interactions`` / ``interaction_legs``
/ ``interaction_spans`` / ``entities`` / ``entity_spans``.

The public surface is five functions and their frozen-dataclass return shapes.
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

Two entry points share one derivation: :func:`get_interactions` reads one
trace's interactions for the **Flow view**, :func:`get_interactions_feed`
cursors the same interactions across traces on the ``interaction_legs.seq``
stream (ADR-0007) for a downstream governance service. Both assemble their rows
through :func:`_assemble_interactions`, so a field derived for one is derived
for the other.

Reads are eventually consistent — they reflect whatever ``P-interactions`` has
materialised so far.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from typing import Any

from data_governance import db
from data_governance.sidecar_facts import classify_attrs

__all__ = [
    "DestinationView",
    "EntityView",
    "GetEntitiesResult",
    "GetEntitySpansResult",
    "GetInteractionSpansResult",
    "GetInteractionsFeedResult",
    "GetInteractionsResult",
    "HttpView",
    "InteractionKindsView",
    "InteractionLegView",
    "InteractionView",
    "SpanEvidenceView",
    "get_entities",
    "get_entity_spans",
    "get_interaction_spans",
    "get_interactions",
    "get_interactions_feed",
]

# Feed defaults — a cursor page is capped so one call can never ask the DB for
# the whole stream (the consumer advances ``next_seq`` instead).
_FEED_DEFAULT_LIMIT = 100
_FEED_MAX_LIMIT = 1000


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
class InteractionKindsView:
    """The sidecar classification of an interaction, re-derived at read time
    from its anchor (request) span's stored attributes through the shared
    vocabulary (:mod:`data_governance.sidecar_facts`) — so content kinds follow
    the table's *current* vocabulary rather than a write-once snapshot, and
    bodyless interactions (no payload rows at all) still classify.

    ``None`` on the parent view when the anchor span carries no sidecar lineage
    facts (streaming/graph-derived interactions): kinds are never fabricated,
    and a ``null`` renders as not-infrastructure.
    """

    protocol: str
    mcp_method: str | None
    request_content_kind: str | None
    response_content_kind: str | None


@dataclass(frozen=True)
class DestinationView:
    """Where an **Interaction**'s exchange was addressed, re-derived at read
    time from the anchor span's stored location facts (``lineage.peer.host``,
    ``url.path``, ``url.scheme`` — wire contract v1.5.1).

    ``url`` is composed (``scheme://host + path``) only when the scheme fact is
    present — spans stored before v1.5.1 lack it, and an absent fact yields an
    absent URL, never a guessed one. ``internal`` is consumer-side vocabulary
    (the producer emits facts only): True for cluster-local authorities.
    """

    url: str | None
    host: str | None
    path: str | None
    internal: bool | None


@dataclass(frozen=True)
class HttpView:
    """The HTTP event of an **Interaction**, re-derived at read time from the
    span pair's stored facts: ``method`` from the anchor (request) span,
    ``status_code`` and ``outcome`` (``ok`` | ``denied`` | ``error`` |
    ``abandoned``) from the paired response span.

    Every field is independently nullable — a request still in flight has no
    status yet, and listeners that did not supply the method leave it absent.
    ``None`` on the parent view when the anchor carries no sidecar lineage facts
    or when not one of the three facts is present: never fabricated.
    """

    method: str | None
    status_code: int | None
    outcome: str | None


@dataclass(frozen=True)
class InteractionView:
    """One derived **Interaction** — the parent identity row plus its nested
    legs and read-time derivations.

    ``duration_seconds`` = response − request occurrence, **None** when the
    response leg is absent. ``any_error`` aggregates the legs. ``span_count`` /
    ``anchor_count`` summarise the evidence so the **Flow view** table can size
    it without pulling every span (the rows are a per-id sub-read).

    ``kinds`` / ``destination`` / ``http`` / ``principal_sub`` / ``session_id``
    are all re-derived from the same winning anchor span (and, for the response
    half of ``http``, its paired response span), so every read-time field
    describes one exchange. ``principal_sub`` is the validated JWT subject —
    absent for an unauthenticated caller, never reverse-engineered from the
    caller entity; ``session_id`` is the a2a session, absent on every other
    protocol.
    """

    id: str
    trace_id: str
    caller_entity_id: str | None
    callee_entity_id: str | None
    summary: str | None
    parent_interaction_id: str | None
    legs: list[InteractionLegView]
    duration_seconds: float | None
    any_error: bool | None
    span_count: int
    anchor_count: int
    kinds: InteractionKindsView | None
    destination: DestinationView | None
    http: HttpView | None
    principal_sub: str | None
    session_id: str | None


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
    # A pod's Kubernetes namespace (migration 0020); None when not a pod.
    namespace: str | None = None


@dataclass(frozen=True)
class SpanEvidenceView:
    """One span-evidence row for an **Interaction** — the joined span fields
    plus its role and the leg it evidences (ADR-0025). ``name`` is the span's
    own name, so a consumer holding an interaction can name its evidence spans
    without a second read.
    """

    span_id: str
    role: str
    parent_id: str | None
    kind: str | None
    name: str | None
    service_name: str | None
    leg_type: str | None


@dataclass(frozen=True)
class EntitySpanEvidenceView:
    """One span-evidence row for an **Entity**. The original five joined span
    fields, with no ``leg_type`` — ``entity_spans`` have no leg, so the
    entity-evidence wire row carries no leg key at all — and no ``name``: the
    span name was asked for on interaction evidence (issue #155) and entity
    evidence is left as it was rather than widened unasked.
    """

    span_id: str
    role: str
    parent_id: str | None
    kind: str | None
    service_name: str | None


@dataclass(frozen=True)
class GetInteractionsResult:
    interactions: list[InteractionView] = field(default_factory=list)


@dataclass(frozen=True)
class GetInteractionsFeedResult:
    """One cursor page of the cross-trace **Interaction** feed. ``next_seq`` is
    the cursor to pass back as ``since_seq``: the highest ``interaction_legs``
    seq included in this page, or the caller's own ``since_seq`` when the page
    is empty (so a caller that has caught up never rewinds).
    """

    interactions: list[InteractionView] = field(default_factory=list)
    next_seq: int = 0


@dataclass(frozen=True)
class GetEntitiesResult:
    entities: list[EntityView] = field(default_factory=list)


@dataclass(frozen=True)
class GetInteractionSpansResult:
    spans: list[SpanEvidenceView] = field(default_factory=list)


@dataclass(frozen=True)
class GetEntitySpansResult:
    spans: list[EntitySpanEvidenceView] = field(default_factory=list)


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


def _has_lineage_facts(attrs: dict[str, Any] | None) -> bool:
    """Whether a stored span carries the sidecar's lineage facts. The single
    guard every read-time derivation shares: a streaming/graph-derived span has
    none of them, and an absent fact must yield an absent derivation rather than
    a guessed one."""
    a = attrs or {}
    return "lineage.exchange.id" in a and "lineage.direction" in a


def _kinds_from_anchor_attrs(attrs: dict[str, Any] | None) -> InteractionKindsView | None:
    """Classify one anchor span's stored attributes, or None when they carry no
    sidecar lineage facts. The guard matters: ``classify_attrs`` has defaults
    for every fact, so running it on a streaming/graph anchor would fabricate
    ``user``/``agent`` kinds out of thin air — absent facts must yield an
    absent classification, not a guessed one."""
    a = attrs or {}
    if not _has_lineage_facts(a):
        return None
    try:
        kinds = classify_attrs(a)
    except ValueError:
        # A present-but-invalid direction (one malformed stored span) must not
        # poison the whole trace's read path with a 500 forever: the write path
        # raises loudly on this; the read path renders an honest absence.
        return None
    proto = str(a.get("lineage.protocol") or "http").lower()
    return InteractionKindsView(
        protocol=proto if proto in ("a2a", "mcp", "inference") else "http",
        mcp_method=str(a["mcp.method"]) if a.get("mcp.method") else None,
        request_content_kind=kinds.req_content_kind,
        response_content_kind=kinds.resp_content_kind,
    )


def _host_is_internal(host: str) -> bool:
    """Whether an authority names a cluster-local destination. Consumer-side
    vocabulary (the wire carries facts only): k8s service DNS suffixes, bare
    service names (an in-cluster Host header is typically the short service
    name), and the container-host gateway the platform's LLM sits behind."""
    hostname = host.rsplit(":", 1)[0]
    if "." not in hostname:
        return True
    return (
        hostname.endswith((".svc", ".svc.cluster.local", ".cluster.local"))
        or hostname in ("host.containers.internal", "host.docker.internal")
    )


def _destination_from_anchor_attrs(attrs: dict[str, Any]) -> DestinationView | None:
    """Compose the destination from an anchor span's location facts, or None
    when it carries neither a host nor a path. Only called for anchors that
    passed the lineage-facts guard. The URL requires the scheme fact
    (``url.scheme``, v1.5.1) — never guessed for older spans."""
    host = str(attrs.get("lineage.peer.host") or "") or None
    path = str(attrs.get("url.path") or "") or None
    if host is None and path is None:
        return None
    scheme = str(attrs.get("url.scheme") or "") or None
    url = f"{scheme}://{host}{path or ''}" if scheme and host else None
    return DestinationView(
        url=url,
        host=host,
        path=path,
        internal=_host_is_internal(host) if host is not None else None,
    )


def _status_code(value: Any) -> int | None:
    """The response span's ``http.status_code`` as an int, or None when it is
    absent or stored as something that is not a status (attributes are JSONB —
    a listener that stringified the code still reads back as a number here)."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _http_from_span_attrs(
    anchor_attrs: dict[str, Any], response_attrs: dict[str, Any] | None
) -> HttpView | None:
    """Compose the HTTP event from the winning span pair: the method off the
    anchor (request) span, the status code and outcome off its paired response
    span. None when not one of the three facts is present — an interaction whose
    listener supplied no method and whose response has not landed yet has no
    HTTP event to report, and a null is honest where an empty object is not.
    Only called for anchors that passed the lineage-facts guard."""
    resp = response_attrs or {}
    method = str(anchor_attrs.get("http.method") or "") or None
    status_code = _status_code(resp.get("http.status_code"))
    outcome = str(resp.get("lineage.outcome") or "") or None
    if method is None and status_code is None and outcome is None:
        return None
    return HttpView(method=method, status_code=status_code, outcome=outcome)


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


# The identity columns every assembled interaction starts from. Both entry
# points select exactly these, in this order, so the assembly reads one shape.
_IDENTITY_COLUMNS = (
    "id::text, trace_id, caller_entity_id::text, callee_entity_id::text, "
    "summary, parent_interaction_id::text"
)


def _assemble_interactions(
    tx: db.Transaction, identity_rows: list[tuple]
) -> list[InteractionView]:
    """Enrich identity rows into full :class:`InteractionView`s, in the order
    given. The one derivation path behind both entry points: the per-trace read
    and the cross-trace feed differ only in how they choose their rows.

    Four grouped scans keyed on the interaction ids — legs, span counts, anchor
    attributes, response-span attributes — so the fetch count stays O(1) in the
    number of interactions.
    """
    if not identity_rows:
        return []
    ids = [r[0] for r in identity_rows]

    # Legs — one scan, request leg first (the leg_type ENUM orders that way) so
    # the sequence diagram draws request-then-response.
    leg_rows = tx.fetch_all(
        "SELECT interaction_id::text, leg_type::text, occurred_at, "
        "payload_hash, error, seq "
        "FROM interaction_legs WHERE interaction_id = ANY(%s) "
        "ORDER BY interaction_id, leg_type",
        (ids,),
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
        "FROM interaction_spans WHERE interaction_id = ANY(%s) "
        "GROUP BY interaction_id",
        (ids,),
    )
    counts = {r[0]: (r[1], r[2]) for r in count_rows}

    # Sidecar facts, re-derived from the anchor spans' stored attributes (one
    # grouped scan). A streaming cross-service interaction can carry two anchor
    # rows; the first anchor bearing lineage facts wins — the guard makes any
    # pick safe (non-sidecar anchors contribute nothing), and ORDER BY span_id
    # makes the pick deterministic (without it, two fact-bearing anchors could
    # report different kinds across identical requests).
    anchor_rows = tx.fetch_all(
        "SELECT isp.interaction_id::text, s.attributes "
        "FROM interaction_spans isp "
        "JOIN spans s "
        "  ON s.trace_id = isp.trace_id AND s.span_id = isp.span_id "
        "WHERE isp.interaction_id = ANY(%s) AND isp.role = 'anchor' "
        "ORDER BY isp.span_id",
        (ids,),
    )
    kinds_of: dict[str, InteractionKindsView] = {}
    anchor_attrs_of: dict[str, dict[str, Any]] = {}
    for iid, attrs in anchor_rows:
        if iid not in kinds_of:
            kv = _kinds_from_anchor_attrs(attrs)
            if kv is not None:
                kinds_of[iid] = kv
                # Every other anchor-sourced field comes from this same winning
                # span, so kinds/destination/principal/session describe one
                # request rather than a mix of two.
                anchor_attrs_of[iid] = attrs or {}

    # The response half of the pair — the interaction_spans row the processor
    # marked leg_type = 'response' (ADR-0025). Same deterministic pick as the
    # anchor: ORDER BY span_id, first span bearing lineage facts wins.
    response_rows = tx.fetch_all(
        "SELECT isp.interaction_id::text, s.attributes "
        "FROM interaction_spans isp "
        "JOIN spans s "
        "  ON s.trace_id = isp.trace_id AND s.span_id = isp.span_id "
        "WHERE isp.interaction_id = ANY(%s) AND isp.leg_type = 'response' "
        "ORDER BY isp.span_id",
        (ids,),
    )
    response_attrs_of: dict[str, dict[str, Any]] = {}
    for iid, attrs in response_rows:
        if iid not in response_attrs_of and _has_lineage_facts(attrs):
            response_attrs_of[iid] = attrs or {}

    views = []
    for iid, trace_id, caller, callee, summary, parent_id in identity_rows:
        legs = legs_by_ix.get(iid, [])
        anchor_attrs = anchor_attrs_of.get(iid)
        views.append(
            InteractionView(
                id=iid,
                trace_id=trace_id,
                caller_entity_id=caller,
                callee_entity_id=callee,
                summary=summary,
                parent_interaction_id=parent_id,
                legs=legs,
                duration_seconds=_leg_duration(legs),
                any_error=_legs_any_error(legs),
                span_count=counts.get(iid, (0, 0))[0],
                anchor_count=counts.get(iid, (0, 0))[1],
                kinds=kinds_of.get(iid),
                destination=(
                    _destination_from_anchor_attrs(anchor_attrs)
                    if anchor_attrs is not None
                    else None
                ),
                http=(
                    _http_from_span_attrs(anchor_attrs, response_attrs_of.get(iid))
                    if anchor_attrs is not None
                    else None
                ),
                principal_sub=(
                    str(anchor_attrs.get("lineage.principal.sub") or "") or None
                    if anchor_attrs is not None
                    else None
                ),
                session_id=(
                    str(anchor_attrs.get("a2a.session_id") or "") or None
                    if anchor_attrs is not None
                    else None
                ),
            )
        )
    return views


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

        identity_rows = tx.fetch_all(
            f"SELECT {_IDENTITY_COLUMNS} FROM interactions WHERE trace_id = %s",
            (trace_id,),
        )
        views = _assemble_interactions(tx, identity_rows)
        # Order by the request leg's occurrence so the flow list stays
        # chronological (the parent no longer carries started_at).
        views.sort(key=lambda ix: _request_occurred_at(ix.legs) or "")
        return GetInteractionsResult(interactions=views)


def get_interactions_feed(
    since_seq: int, limit: int = _FEED_DEFAULT_LIMIT
) -> GetInteractionsFeedResult:
    """Read one cursor page of the cross-trace **Interaction** feed — the same
    :class:`InteractionView`s :func:`get_interactions` serves, for a downstream
    service that consumes interactions as a stream rather than per trace.

    The cursor is the ``interaction_legs.seq`` stream (ADR-0007): an interaction
    is included when ANY of its legs has ``seq > since_seq``, ordered by the
    highest such seq. A late response leg therefore re-emits its interaction —
    intended, and the only way the consumer learns the mutation (an interaction
    is mutable in place and carries no completion flag).

    ``next_seq`` is the cursor to pass back; on an empty page it is the caller's
    own ``since_seq``. Empty (with that same ``next_seq``) when the interactions
    migration has not run. Raises ``ValueError`` on a negative cursor or an
    out-of-range limit.
    """
    if since_seq < 0:
        raise ValueError(f"'since_seq' must be >= 0, got {since_seq}")
    if limit < 1 or limit > _FEED_MAX_LIMIT:
        raise ValueError(f"'limit' must be 1..{_FEED_MAX_LIMIT}, got {limit}")

    with db.transaction() as tx:
        if not _derived_tables_exist(tx):
            return GetInteractionsFeedResult(next_seq=since_seq)

        # One row per interaction with a fresh leg, carrying that leg's seq —
        # the page's order and its next cursor in a single scan of the seq index.
        cursor_rows = tx.fetch_all(
            "SELECT interaction_id::text, MAX(seq) AS max_seq "
            "FROM interaction_legs WHERE seq > %s "
            "GROUP BY interaction_id ORDER BY max_seq LIMIT %s",
            (since_seq, limit),
        )
        if not cursor_rows:
            return GetInteractionsFeedResult(next_seq=since_seq)

        order = {iid: max_seq for iid, max_seq in cursor_rows}
        identity_rows = tx.fetch_all(
            f"SELECT {_IDENTITY_COLUMNS} FROM interactions WHERE id = ANY(%s)",
            (list(order),),
        )
        # Legs outlive nothing, but a parent row can be absent for a moment
        # (the processor rewrites a trace's interactions in one transaction, and
        # this read is not in it) — assemble whatever parents exist, in cursor
        # order.
        identity_rows.sort(key=lambda r: order[r[0]])
        return GetInteractionsFeedResult(
            interactions=_assemble_interactions(tx, identity_rows),
            next_seq=max(order.values()),
        )


def get_entities(trace_id: str) -> GetEntitiesResult:
    """Read the derived **Entities** the processor recorded provenance for in a
    trace, reached via the trace-scoped ``entity_spans``. Empty when the
    interactions migration has not run.
    """
    with db.transaction() as tx:
        if not _derived_tables_exist(tx):
            return GetEntitiesResult()
        rows = tx.fetch_all(
            "SELECT id::text, kind, natural_key, display_name, detected_from, namespace "
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
                    display_name=r[3], detected_from=r[4], namespace=r[5],
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
            "SELECT s.span_id, pis.role, s.parent_id, s.kind, s.name, "
            "s.service_name, pis.leg_type::text "
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
                    kind=r[3], name=r[4], service_name=r[5], leg_type=r[6],
                )
                for r in rows
            ]
        )


def get_entity_spans(trace_id: str, entity_id: str) -> GetEntitySpansResult:
    """Read the span evidence for one **Entity**. Same empty-on-unknown
    convention as :func:`get_interaction_spans`, but ``entity_spans`` have no
    leg, so entity-evidence rows carry no ``leg_type`` field at all.
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
                EntitySpanEvidenceView(
                    span_id=r[0], role=r[1], parent_id=r[2],
                    kind=r[3], service_name=r[4],
                )
                for r in rows
            ]
        )
