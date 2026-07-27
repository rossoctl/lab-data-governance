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
live under the same ``/api/`` namespace. Every handler is a thin adapter over
the retrieval library: the span reads call ``get_spans``; the flow reads call
**Interaction retrieval** (``get_interactions`` / ``get_entities`` /
``get_interaction_spans`` / ``get_entity_spans``) and ``get_payload``. All the
read logic — nested legs, computed duration, error roll-up, chronological
ordering, the nullable-classification and not-yet-migrated shapes — lives behind
those seams; the handler only parses ids, dispatches to a worker thread, and
encodes the returned dataclasses to the wire (ADR-0005).
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
async def _interactions_handler(request: Request) -> Response:
    """``GET /api/traces/{tid}/interactions`` — derived interactions.

    Thin adapter over :func:`retrieval.get_interactions`: the derived
    interactions the processor materialised for a trace (parent identity row +
    nested request/response legs, computed ``duration_seconds`` / ``any_error``,
    span/anchor counts — ADR-0025), ordered by request-leg occurrence. All that
    logic lives behind the **Interaction retrieval** seam; the handler only
    parses the id and encodes the result.
    """
    trace_id = request.path_params.get("tid")
    if not trace_id:
        return JSONResponse({"error": "trace_id required"}, status_code=400)
    try:
        result = await asyncio.to_thread(retrieval.get_interactions, trace_id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    return _json_ok(
        {"interactions": [dataclasses.asdict(ix) for ix in result.interactions]}
    )


async def _entities_handler(request: Request) -> Response:
    """``GET /api/traces/{tid}/entities`` — derived entities.

    Thin adapter over :func:`retrieval.get_entities` (entities scoped to the
    trace via ``entity_spans``, ADR-0013).
    """
    trace_id = request.path_params.get("tid")
    if not trace_id:
        return JSONResponse({"error": "trace_id required"}, status_code=400)
    try:
        result = await asyncio.to_thread(retrieval.get_entities, trace_id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    return _json_ok(
        {"entities": [dataclasses.asdict(e) for e in result.entities]}
    )


async def _interaction_spans_handler(request: Request) -> Response:
    """``GET /api/traces/{tid}/interactions/{iid}/spans``.

    Thin adapter over :func:`retrieval.get_interaction_spans` — the span
    evidence for one interaction, each row carrying the leg it evidences
    (ADR-0025). Empty for an unknown id or before the migration.
    """
    trace_id = request.path_params.get("tid")
    interaction_id = request.path_params.get("iid")
    if not trace_id or not interaction_id:
        return JSONResponse(
            {"error": "trace_id and interaction_id required"}, status_code=400
        )
    try:
        result = await asyncio.to_thread(
            retrieval.get_interaction_spans, trace_id, interaction_id
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    return _json_ok({"spans": [dataclasses.asdict(s) for s in result.spans]})


async def _entity_spans_handler(request: Request) -> Response:
    """``GET /api/traces/{tid}/entities/{eid}/spans``.

    Thin adapter over :func:`retrieval.get_entity_spans`. Same shape and
    empty-on-unknown convention as :func:`_interaction_spans_handler`;
    ``leg_type`` is ``None`` for entity evidence.
    """
    trace_id = request.path_params.get("tid")
    entity_id = request.path_params.get("eid")
    if not trace_id or not entity_id:
        return JSONResponse(
            {"error": "trace_id and entity_id required"}, status_code=400
        )
    try:
        result = await asyncio.to_thread(
            retrieval.get_entity_spans, trace_id, entity_id
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    return _json_ok({"spans": [dataclasses.asdict(s) for s in result.spans]})


async def _payload_handler(request: Request) -> Response:
    """``GET /api/payloads/{hash}`` — a payload by content hash.

    Thin adapter over :func:`retrieval.get_payload`. Backs the flow view's
    Req/Resp cells; carries the P-classification **Classification** verdict
    inline as a nullable ``classification`` field (ADR-0024). A missing payload
    is a 404.
    """
    h = request.path_params.get("hash")
    if not h:
        return JSONResponse({"error": "content_hash required"}, status_code=400)
    try:
        view = await asyncio.to_thread(retrieval.get_payload, h)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    if view is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return _json_ok(dataclasses.asdict(view))


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
