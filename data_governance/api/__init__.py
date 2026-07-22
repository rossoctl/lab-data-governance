"""UI backend REST API — thin Starlette wrapper over the retrieval library.

The surface is resource-oriented and namespaced (ADR-0017): JSON resources
under ``/api/``, the React single-page app under ``/ui/``, bare ``/`` a 302 to
``/ui/``, and ``/healthz`` un-prefixed at the root for infra probes.

The ``/ui/`` namespace serves a Vite-built React SPA baked into the image
(ADR-0019): a ``StaticFiles`` mount at ``/ui/assets`` serves Vite's
content-hashed bundles, and a catch-all ``/ui`` / ``/ui/{path:path}`` returns
the SPA ``index.html`` so React Router (``basename="/ui"``) resolves deep links
like ``/ui/traces/{tid}`` client-side. The former hand-written-shell handlers
and the ``_UI_ASSETS`` whitelist are gone — content-hashed filenames can't be
enumerated ahead of time, so the mount replaces the whitelist.

The span reads are a trace/span resource tree (ADR-0018, which retired the
former single ``GET /spans`` pass-through):

- ``GET /api/traces`` — recent-traces feed, ``{"traces": [TraceListingEntry]}``
- ``GET /api/traces/{tid}`` — one ``TraceListingEntry`` (cold-open seed)
- ``GET /api/traces/{tid}/spans`` — whole trace, flat, paginated
- ``GET /api/traces/{tid}/spans/{sid}`` — one ``Span``
- ``GET /api/traces/{tid}/spans/{sid}/children`` — direct children, keyset-paginated

The P-interactions execution-flow resources (``.../interactions``,
``.../entities``, their ``/spans`` sub-resources) and ``GET /api/payloads/{hash}``
live under the same ``/api/`` namespace. Every handler calls the retrieval
library ``get_spans`` (unchanged — its ``root_only`` / ``parent_id`` / ``cursor``
parameters and compatibility raises are reached only through these routes) or
the ``db`` module underneath.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import json
import threading
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from data_governance import db, retrieval

__all__ = ["SpansApiServer", "build_app"]

_UI_DIR = Path(__file__).parent / "ui"

DEFAULT_PORT = 8080


# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------


def _json_default(obj: object) -> object:
    if isinstance(obj, dt.datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _counts_to_jsonable(
    counts: dict[str, retrieval.TraceCounts] | None,
) -> dict[str, dict[str, int]] | None:
    if counts is None:
        return None
    return {tid: dataclasses.asdict(c) for tid, c in counts.items()}


def _result_to_dict(result: retrieval.GetSpansResult) -> dict:
    return {
        "spans": [dataclasses.asdict(s) for s in result.spans],
        "counts": _counts_to_jsonable(result.counts),
    }


def _json_ok(payload: object) -> Response:
    """Encode ``payload`` as a 200 ``application/json`` response.

    The one place the span-read handlers' success encoding lives — they emit
    dataclass-bearing payloads that need ``_json_default`` for datetimes, so a
    plain ``JSONResponse`` won't do.
    """
    body = json.dumps(payload, default=_json_default)
    return Response(content=body, media_type="application/json", status_code=200)


def _trace_listing_entry(
    span: retrieval.Span, counts: retrieval.TraceCounts | None
) -> dict:
    """Map a listing-root ``Span`` + its ``TraceCounts`` to a TraceListingEntry.

    CONTEXT.md **TraceListingEntry**: trace-shaped (identity ``trace_id``) with
    the anchor **Listing root** nested and the per-trace **Trace counts**
    inline — not a bare span with a sidecar counts map. This is the element of
    the ``GET /api/traces`` collection and the body of ``GET /api/traces/{tid}``
    (ADR-0018); the two shapes are identical.
    """
    return {
        "trace_id": span.trace_id,
        "listing_root": dataclasses.asdict(span),
        "counts": dataclasses.asdict(counts) if counts is not None else None,
        "in_time_window": span.in_time_window,
    }


# ---------------------------------------------------------------------------
# Query-parameter parsing
# ---------------------------------------------------------------------------


def _parse_int(value: str | None, name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"'{name}' must be an integer, got {value!r}")


def _pagination_kwargs(params) -> tuple[int | None, dict]:
    """Parse the shared ``cursor`` / ``limit`` query params.

    Returns ``(cursor, kwargs)`` where ``kwargs`` carries ``limit`` only when
    the caller supplied it — omitting it lets ``get_spans`` apply its own
    default rather than being handed ``None``. Raises ``ValueError`` on a
    non-integer value (handlers turn that into a 400).
    """
    cursor = _parse_int(params.get("cursor"), "cursor")
    limit_raw = _parse_int(params.get("limit"), "limit")
    kwargs: dict = {}
    if limit_raw is not None:
        kwargs["limit"] = limit_raw
    return cursor, kwargs


def _parse_iso_datetime(value: str | None, name: str) -> dt.datetime | None:
    """Parse an ISO-8601 datetime; reject naive (no Z, no explicit offset).

    ``datetime.fromisoformat`` in Python 3.11+ accepts ``Z`` as a synonym
    for ``+00:00``, so this is the same parser the rest of the stdlib
    uses. The naive-datetime rejection is enforced after parsing rather
    than via a regex so we preserve all the format variants
    ``fromisoformat`` already accepts (microseconds, fractional seconds,
    space separator, etc.).
    """
    if value is None or value == "":
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"'{name}' must be ISO-8601 with Z or explicit offset, got {value!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(
            f"'{name}' must be ISO-8601 with Z or explicit offset; "
            f"naive datetimes are rejected"
        )
    return parsed


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------


async def _traces_handler(request: Request) -> Response:
    """``GET /api/traces`` — the recent-traces feed (ADR-0018).

    Was ``GET /spans?root_only=true&time_from&time_to``. Returns
    ``{"traces": [TraceListingEntry]}`` — the trace-shaped feed the
    recent-traces view reads. ``time_from`` / ``time_to`` still window the
    listing-root selection; the library ``get_spans`` is unchanged underneath.
    """
    params = request.query_params
    try:
        cursor, kwargs = _pagination_kwargs(params)
        time_from = _parse_iso_datetime(params.get("time_from"), "time_from")
        time_to = _parse_iso_datetime(params.get("time_to"), "time_to")
        result = retrieval.get_spans(
            cursor=cursor,
            time_from=time_from,
            time_to=time_to,
            root_only=True,
            **kwargs,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    counts = result.counts or {}
    traces = [
        _trace_listing_entry(span, counts.get(span.trace_id))
        for span in result.spans
    ]
    return _json_ok({"traces": traces})


async def _trace_handler(request: Request) -> Response:
    """``GET /api/traces/{tid}`` — one **TraceListingEntry** (cold-open seed).

    Was ``GET /spans?root_only=true&trace_id``. Returns the identical
    TraceListingEntry shape as one element of ``GET /api/traces`` (ADR-0018:
    collection and singular are symmetric), not wrapped in ``{"traces": ...}``.
    404 when the trace has no spans.
    """
    tid = request.path_params.get("tid")
    try:
        result = retrieval.get_spans(root_only=True, trace_id=tid)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if not result.spans:
        return JSONResponse({"error": "not found"}, status_code=404)

    counts = result.counts or {}
    span = result.spans[0]
    return _json_ok(_trace_listing_entry(span, counts.get(span.trace_id)))


async def _trace_spans_handler(request: Request) -> Response:
    """``GET /api/traces/{tid}/spans`` — the whole trace, flat, paginated.

    Was ``GET /spans?trace_id``. The root of the span resource tree. Ships
    without a current UI caller (the tree UI seeds from the listing root and
    expands children-by-parent), included per ADR-0018 as the obvious
    collection root and a future export target. Returns the raw ``get_spans``
    shape ``{"spans": [...], "counts": null}``.
    """
    params = request.query_params
    tid = request.path_params.get("tid")
    try:
        cursor, kwargs = _pagination_kwargs(params)
        order = params.get("order") or None
        result = retrieval.get_spans(
            cursor=cursor, trace_id=tid, order=order, **kwargs
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return _json_ok(_result_to_dict(result))


async def _span_children_handler(request: Request) -> Response:
    """``GET /api/traces/{tid}/spans/{sid}/children`` — direct children.

    Was ``GET /spans?trace_id&parent_id&cursor&limit``. Keyset-paginated by
    ``seq`` (ADR-0001) — the tree UI's lazy-expansion source. Returns the raw
    ``get_spans`` shape ``{"spans": [...], "counts": null}``.
    """
    params = request.query_params
    tid = request.path_params.get("tid")
    sid = request.path_params.get("sid")
    try:
        cursor, kwargs = _pagination_kwargs(params)
        result = retrieval.get_spans(
            cursor=cursor, trace_id=tid, parent_id=sid, **kwargs
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return _json_ok(_result_to_dict(result))


async def _single_span_handler(request: Request) -> Response:
    """``GET /api/traces/{tid}/spans/{sid}`` — one **Span** (ADR-0018).

    Was ``GET /spans?trace_id&span_id``. Returns the full-row ``Span`` object
    (ADR-0006 "one shape, one contract" — identical to a collection element),
    not wrapped in ``{"spans": ...}``. 404 when the span is absent.
    """
    tid = request.path_params.get("tid")
    sid = request.path_params.get("sid")
    try:
        result = retrieval.get_spans(trace_id=tid, span_id=sid)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if not result.spans:
        return JSONResponse({"error": "not found"}, status_code=404)

    return _json_ok(dataclasses.asdict(result.spans[0]))


def _probe_postgres() -> None:
    """Blocking ``SELECT 1`` against Postgres for :func:`_healthz_handler`.

    Pulled out so the handler can dispatch it via :func:`asyncio.to_thread`
    — ``db.transaction`` does synchronous libpq round-trips. Mirrors the
    receiver's ``server._probe_postgres`` for symmetry; the UI backend's job
    is to serve the ``/api/`` resource tree, which fans out to Postgres, so
    "Postgres reachable" is the right liveness signal.
    """
    with db.transaction() as tx:
        tx.execute("SELECT 1")


async def _healthz_handler(_request: Request) -> Response:
    """Liveness/readiness endpoint for the k8s manifests in ``deploy/k8s/``.

    Returns 200 when Postgres is reachable, 503 otherwise. The probe path
    is on the §3.1 ingest blocklist (issue #9), so probe-shaped spans
    never reach ``spans``.
    """
    try:
        await asyncio.to_thread(_probe_postgres)
    except Exception:  # noqa: BLE001 — broad on purpose
        return Response("not ready", status_code=503, media_type="text/plain")
    return Response("ok", status_code=200, media_type="text/plain")


async def _root_redirect_handler(_request: Request) -> Response:
    """``GET /`` → 302 to ``/ui/`` (ADR-0017).

    Bare ``/`` is not a resource; the UI namespace is ``/ui/``. ``/healthz``
    stays un-prefixed at the root for infra probes.
    """
    return RedirectResponse(url="/ui/", status_code=302)


async def _spa_index(_request: Request) -> Response:
    """Serve the React SPA shell ``index.html`` for any ``/ui/*`` path (ADR-0019).

    Backs both ``/ui`` (the shell) and the catch-all ``/ui/{path:path}``. Every
    client-side route (``/ui/traces/{tid}`` and deeper) returns the same built
    ``index.html``; React Router (``basename="/ui"``) resolves the path in the
    browser. Content-hashed bundles are served separately by the ``/ui/assets``
    StaticFiles mount, so this handler never sees an asset request.

    Read on every request intentionally — cheap, and lets a rebuilt ``dist/``
    be picked up without a process restart during development.

    Returns 503 with a plain-text hint when the SPA build is absent (a
    build-less checkout / image), symmetrical with the ``check_dir=False`` on
    the ``/ui/assets`` mount — a missing build is a deploy problem to surface
    cleanly, not an opaque 500 from an unhandled ``FileNotFoundError``.
    """
    index = _UI_DIR / "index.html"
    try:
        return HTMLResponse(content=index.read_text())
    except FileNotFoundError:
        return Response(
            "UI build not found — run the Vite build (see ADR-0019)",
            status_code=503,
            media_type="text/plain",
        )


# Row-mapper for the span-evidence the /spans sub-resources return. Shared by
# the interaction- and entity-scoped handlers: both join their link table to
# ``spans`` for the same provenance shape (ADR-0013), selecting
# ``s.span_id, <link>.role, s.parent_id, s.kind, s.service_name`` in order.
def _span_evidence_row(row: tuple) -> dict:
    span_id, role, parent_id, kind, service_name = row
    return {
        "span_id": span_id,
        "role": role,
        "parent_id": parent_id,
        "kind": kind,
        "service_name": service_name,
    }


def _request_occurred_at(legs: list[dict]) -> str | None:
    """The request leg's ``occurred_at`` (ISO string), or None."""
    for leg in legs:
        if leg["leg_type"] == "request":
            return leg["occurred_at"]
    return None


def _leg_duration(legs: list[dict]) -> float | None:
    """Seconds between the request and response legs' ``occurred_at``, or None
    when either leg is absent (ADR-0025: null duration = "response in flight").
    Computed on read, never stored, so a later-finalizing response leg can never
    leave a stale value behind."""
    by_type = {leg["leg_type"]: leg["occurred_at"] for leg in legs}
    req, resp = by_type.get("request"), by_type.get("response")
    if req is None or resp is None:
        return None
    return (
        dt.datetime.fromisoformat(resp) - dt.datetime.fromisoformat(req)
    ).total_seconds()


def _legs_any_error(legs: list[dict]) -> bool | None:
    """Aggregate error across the legs: True if any leg errored, False if any
    leg is a definite success and none errored, else None (unknown)."""
    errs = [leg["error"] for leg in legs]
    if any(e is True for e in errs):
        return True
    if any(e is False for e in errs):
        return False
    return None


def _derived_tables_exist(tx) -> bool:
    """Whether the interactions migration has run on this DB.

    The derived tables (``interactions``, ``entities``, and their link
    tables) all land in the same migration, so probing ``interactions`` is
    sufficient. Handlers short-circuit to their empty shape when absent so a
    fresh DB serves 200s rather than 500s.
    """
    return (
        tx.fetch_one(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'interactions'"
        )
        is not None
    )


async def _interactions_handler(request: Request) -> Response:
    """``GET /api/traces/{tid}/interactions`` — derived interactions.

    Returns the interactions the in-cluster processor materialised for a
    trace (parent identity row + nested request/response legs, ADR-0025). Each
    row carries ``span_count`` / ``anchor_count`` so the flow table can show
    evidence sizing without pulling every span; the spans themselves are fetched
    per-row from ``/api/traces/{tid}/interactions/{iid}/spans``. ``legs`` (one
    or two, request first) carry the per-leg ``occurred_at`` / ``payload_hash``
    / ``error``; ``duration_seconds`` = response − request occurrence (null when
    the response leg is absent — the "response in flight" signal); ``any_error``
    aggregates the legs.
    """
    trace_id = request.path_params.get("tid")
    if not trace_id:
        return JSONResponse({"error": "trace_id required"}, status_code=400)

    def _query() -> dict:
        with db.transaction() as tx:
            if not _derived_tables_exist(tx):
                return {"interactions": []}
            interactions = tx.fetch_all(
                "SELECT id::text, caller_entity_id::text, callee_entity_id::text, "
                "summary, parent_interaction_id::text "
                "FROM interactions WHERE trace_id = %s",
                (trace_id,),
            )
            # Legs for this trace's interactions — one scan, request leg first
            # so the sequence diagram draws request-then-response.
            leg_rows = tx.fetch_all(
                "SELECT l.interaction_id::text, l.leg_type::text, l.occurred_at, "
                "l.payload_hash, l.error, l.seq "
                "FROM interaction_legs l "
                "JOIN interactions i ON i.id = l.interaction_id "
                "WHERE i.trace_id = %s "
                "ORDER BY l.interaction_id, l.leg_type",
                (trace_id,),
            )
            legs_by_ix: dict[str, list[dict]] = {}
            for iid, leg_type, occurred_at, payload_hash, error, seq in leg_rows:
                legs_by_ix.setdefault(iid, []).append(
                    {
                        "leg_type": leg_type,
                        "occurred_at": occurred_at.isoformat() if occurred_at else None,
                        "payload_hash": payload_hash,
                        "error": error,
                        "seq": seq,
                    }
                )
            # Per-interaction span aggregate — one grouped scan of the link
            # table, so the row count stays O(1) fetches regardless of trace
            # size.
            count_rows = tx.fetch_all(
                "SELECT interaction_id::text, COUNT(*) AS span_count, "
                "COUNT(*) FILTER (WHERE role = 'anchor') AS anchor_count "
                "FROM interaction_spans WHERE trace_id = %s "
                "GROUP BY interaction_id",
                (trace_id,),
            )
            counts = {r[0]: (r[1], r[2]) for r in count_rows}

            rows = [
                {
                    "id": r[0], "caller_entity_id": r[1], "callee_entity_id": r[2],
                    "summary": r[3], "parent_interaction_id": r[4],
                    "legs": legs_by_ix.get(r[0], []),
                    "duration_seconds": _leg_duration(legs_by_ix.get(r[0], [])),
                    "any_error": _legs_any_error(legs_by_ix.get(r[0], [])),
                    "span_count": counts.get(r[0], (0, 0))[0],
                    "anchor_count": counts.get(r[0], (0, 0))[1],
                }
                for r in interactions
            ]
            # Order by the request leg's occurrence so the flow list stays
            # chronological (the parent no longer carries started_at).
            rows.sort(key=lambda ix: _request_occurred_at(ix["legs"]) or "")
            return {"interactions": rows}

    try:
        data = await asyncio.to_thread(_query)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(data)


async def _entities_handler(request: Request) -> Response:
    """``GET /api/traces/{tid}/entities`` — derived entities.

    Productized entities are cross-trace-stable (ADR-0013): no trace_id
    column. Scope them to this trace indirectly via ``entity_spans`` (which
    is trace-scoped) — the entities the processor recorded provenance for in
    this trace. No ``retracted_at`` either (ADR-0012 emit-once-final, no
    tombstone). Span provenance is fetched per-row from
    ``/api/traces/{tid}/entities/{eid}/spans``.
    """
    trace_id = request.path_params.get("tid")
    if not trace_id:
        return JSONResponse({"error": "trace_id required"}, status_code=400)

    def _query() -> dict:
        with db.transaction() as tx:
            if not _derived_tables_exist(tx):
                return {"entities": []}
            entities = tx.fetch_all(
                "SELECT id::text, kind, natural_key, display_name, detected_from "
                "FROM entities "
                "WHERE id IN (SELECT DISTINCT entity_id FROM entity_spans "
                "WHERE trace_id = %s) "
                "ORDER BY kind, display_name",
                (trace_id,),
            )
            return {
                "entities": [
                    {
                        "id": r[0], "kind": r[1], "natural_key": r[2],
                        "display_name": r[3], "detected_from": r[4],
                    }
                    for r in entities
                ],
            }

    try:
        data = await asyncio.to_thread(_query)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(data)


async def _interaction_spans_handler(request: Request) -> Response:
    """``GET /api/traces/{tid}/interactions/{iid}/spans``.

    The span-evidence for one interaction (backs the detail panel's Spans
    table). Returns ``{"spans": []}`` for an unknown id or before the
    interactions migration has run — an empty table is the right UI state,
    not a 404.
    """
    trace_id = request.path_params.get("tid")
    interaction_id = request.path_params.get("iid")
    if not trace_id or not interaction_id:
        return JSONResponse(
            {"error": "trace_id and interaction_id required"}, status_code=400
        )

    def _query() -> dict:
        with db.transaction() as tx:
            if not _derived_tables_exist(tx):
                return {"spans": []}
            rows = tx.fetch_all(
                "SELECT s.span_id, pis.role, s.parent_id, s.kind, s.service_name, "
                "pis.leg_type::text "
                "FROM interaction_spans pis "
                "LEFT JOIN spans s "
                "  ON s.trace_id = pis.trace_id AND s.span_id = pis.span_id "
                "WHERE pis.trace_id = %s AND pis.interaction_id = %s",
                (trace_id, interaction_id),
            )
            # ADR-0025: a span attributes to a specific leg. Extend the shared
            # evidence-row shape with leg_type (entity_spans have no leg).
            return {
                "spans": [
                    {**_span_evidence_row(r[:5]), "leg_type": r[5]} for r in rows
                ]
            }

    try:
        data = await asyncio.to_thread(_query)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(data)


async def _entity_spans_handler(request: Request) -> Response:
    """``GET /api/traces/{tid}/entities/{eid}/spans``.

    The span-evidence for one entity. Same shape and empty-on-unknown
    convention as :func:`_interaction_spans_handler`.
    """
    trace_id = request.path_params.get("tid")
    entity_id = request.path_params.get("eid")
    if not trace_id or not entity_id:
        return JSONResponse(
            {"error": "trace_id and entity_id required"}, status_code=400
        )

    def _query() -> dict:
        with db.transaction() as tx:
            if not _derived_tables_exist(tx):
                return {"spans": []}
            rows = tx.fetch_all(
                "SELECT s.span_id, pes.role, s.parent_id, s.kind, s.service_name "
                "FROM entity_spans pes "
                "LEFT JOIN spans s "
                "  ON s.trace_id = pes.trace_id AND s.span_id = pes.span_id "
                "WHERE pes.trace_id = %s AND pes.entity_id = %s",
                (trace_id, entity_id),
            )
            return {"spans": [_span_evidence_row(r) for r in rows]}

    try:
        data = await asyncio.to_thread(_query)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(data)


def _classification_json(row: tuple) -> dict | None:
    """Shape a ``payload_classifications`` LEFT JOIN slice into the nullable
    ``classification`` field (ADR-0024).

    The join columns are all-NULL when P-classification has not yet written a
    verdict for the payload (its PK ``content_hash`` is NOT NULL, so a present
    row always has one) — that is the eventual-consistency window, surfaced as
    ``null``. Once the row exists, the document-level verdict + **Findings** +
    ``model_version`` are returned verbatim (a clean payload is a real
    ``PUBLIC`` / zero-**Findings** verdict, never a null).
    """
    if row[0] is None:  # no payload_classifications row (LEFT JOIN miss)
        return None
    return {
        "sensitivity_level": row[0],
        "regulatory_tags": list(row[1]) if row[1] is not None else [],
        "contains_identity_bundle": row[2],
        "is_personalized": row[3],
        "primary_domain": row[4],
        "findings": row[5],
        "model_version": row[6],
    }


async def _payload_handler(request: Request) -> Response:
    """``GET /api/payloads/{hash}`` — a payload by content hash.

    Backs the flow view's Req/Resp cells. Carries the P-classification
    **Classification** verdict inline as a nullable ``classification`` field
    (ADR-0024): ``null`` until P-classification has processed the payload,
    populated with the verdict object once its ``payload_classifications`` row
    exists.
    """
    h = request.path_params.get("hash")
    if not h:
        return JSONResponse({"error": "content_hash required"}, status_code=400)

    def _query() -> dict | None:
        with db.transaction() as tx:
            row = tx.fetch_one(
                "SELECT p.content_hash, p.content_kind, p.content, p.byte_size, "
                "       c.sensitivity_level, c.regulatory_tags, "
                "       c.contains_identity_bundle, c.is_personalized, "
                "       c.primary_domain, c.findings, c.model_version "
                "FROM interaction_payloads p "
                "LEFT JOIN payload_classifications c "
                "  ON c.content_hash = p.content_hash "
                "WHERE p.content_hash = %s",
                (h,),
            )
            if row is None:
                return None
            return {
                "content_hash": row[0], "content_kind": row[1],
                "content": row[2], "byte_size": row[3],
                "classification": _classification_json(row[4:]),
            }

    try:
        data = await asyncio.to_thread(_query)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    if data is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(data)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app() -> Starlette:
    """Build and return the Starlette application."""
    routes: list = [
        Route("/healthz", endpoint=_healthz_handler, methods=["GET"]),
        Route("/api/traces", endpoint=_traces_handler, methods=["GET"]),
        Route("/api/traces/{tid:str}", endpoint=_trace_handler, methods=["GET"]),
        Route(
            "/api/traces/{tid:str}/spans",
            endpoint=_trace_spans_handler,
            methods=["GET"],
        ),
        Route(
            "/api/traces/{tid:str}/spans/{sid:str}/children",
            endpoint=_span_children_handler,
            methods=["GET"],
        ),
        Route(
            "/api/traces/{tid:str}/spans/{sid:str}",
            endpoint=_single_span_handler,
            methods=["GET"],
        ),
        # React SPA under /ui/ (ADR-0019). Order matters: the /ui/assets mount
        # is listed BEFORE the catch-all so Vite's content-hashed bundles are
        # served by StaticFiles (a genuine 404 for a missing asset), and only
        # non-asset paths fall through to the catch-all that returns index.html
        # for React Router to resolve client-side. check_dir=False so a fresh
        # checkout without a Vite build still imports; a real request for a
        # missing dir just 404s.
        Mount(
            "/ui/assets",
            app=StaticFiles(directory=_UI_DIR / "assets", check_dir=False),
            name="ui-assets",
        ),
        Route("/ui", endpoint=_spa_index, methods=["GET"]),
        Route("/ui/{path:path}", endpoint=_spa_index, methods=["GET"]),
        # P-interactions execution-flow endpoints. Lists are lean; span
        # provenance is a per-id sub-resource, fetched lazily on drill-in.
        Route(
            "/api/traces/{tid:str}/interactions",
            endpoint=_interactions_handler,
            methods=["GET"],
        ),
        Route(
            "/api/traces/{tid:str}/entities",
            endpoint=_entities_handler,
            methods=["GET"],
        ),
        Route(
            "/api/traces/{tid:str}/interactions/{iid:str}/spans",
            endpoint=_interaction_spans_handler,
            methods=["GET"],
        ),
        Route(
            "/api/traces/{tid:str}/entities/{eid:str}/spans",
            endpoint=_entity_spans_handler,
            methods=["GET"],
        ),
        Route(
            "/api/payloads/{hash:str}",
            endpoint=_payload_handler,
            methods=["GET"],
        ),
        Route("/", endpoint=_root_redirect_handler, methods=["GET"]),
    ]
    return Starlette(routes=routes)


# ---------------------------------------------------------------------------
# Server lifecycle wrapper
# ---------------------------------------------------------------------------


class SpansApiServer:
    """Lifecycle wrapper around the Spans API HTTP server.

    Runs uvicorn in a background thread so callers can start and stop it
    without blocking. Mirrors the pattern used by ``HttpOtlpServer``.
    """

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
    ) -> None:
        self.host = host
        self.port = port
        self._server: object | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        config = uvicorn.Config(
            app=build_app(),
            host=self.host,
            port=self.port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        self._server = server
        self._thread = thread

    def stop(self, *, grace: float = 1.0) -> None:
        if self._server is not None:
            self._server.should_exit = True  # type: ignore[attr-defined]
        if self._thread is not None:
            self._thread.join(timeout=grace)
            self._thread = None
        self._server = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
