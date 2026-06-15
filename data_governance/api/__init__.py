"""UI backend REST API — thin Starlette wrapper over the retrieval library.

The single endpoint ``GET /spans`` accepts every ``get_spans`` parameter as
a query parameter and returns the JSON encoding of ``GetSpansResult``.

Issue #4 shipped the tracer bullet (``cursor``, ``limit``, ``trace_id``,
``span_id``, ``order``). Issue #12 extended the surface with
``time_from`` / ``time_to`` (ISO-8601, naive datetimes rejected per
PROJECT.md §6), ``root_only``, and ``parent_id`` (the latter only
honoured for the parameter-compatibility raises until #13 lands).

Issue #58 adds ``GET /graph`` — the derived entity/edge graph (ADR-0007),
a thin pass-through to ``get_entities`` + ``get_edges`` returning
``{"entities": [...], "edges": [...]}``.

ADR-0009 adds ``GET /forest/{trace_id}`` — the execution-forest UI shell. The
unified per-invocation forest (``U -> A -> {L, tools}``) is derived client-side
by ``ui/forest_logic.js`` over the same ``GET /spans`` read path; no new
endpoint data and no schema change.
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
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route

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


def _graph_to_dict(
    entities: retrieval.GetEntitiesResult,
    edges: retrieval.GetEdgesResult,
) -> dict:
    """JSON-able body for ``GET /graph``: the graph's nodes and links.

    ``entities`` are the nodes and ``edges`` the links; the UI (issue #59)
    aggregates them client-side (``GROUP BY from_entity, to_entity``). Each
    list element is the ``asdict`` of its retrieval dataclass, so the wire
    shape tracks :class:`retrieval.Entity` / :class:`retrieval.Edge` exactly.
    """
    return {
        "entities": [dataclasses.asdict(e) for e in entities.entities],
        "edges": [dataclasses.asdict(e) for e in edges.edges],
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


def _parse_bool(value: str | None, name: str) -> bool:
    """Lenient bool parser for query strings: ``"true"`` / ``"1"`` → True."""
    if value is None:
        return False
    lowered = value.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no", ""):
        return False
    raise ValueError(f"'{name}' must be a boolean, got {value!r}")


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


async def _spans_handler(request: Request) -> Response:
    """``GET /spans`` — pass-through to ``get_spans``."""
    params = request.query_params

    try:
        cursor = _parse_int(params.get("cursor"), "cursor")
        limit_raw = _parse_int(params.get("limit"), "limit")
        trace_id = params.get("trace_id") or None
        span_id = params.get("span_id") or None
        parent_id = params.get("parent_id") or None
        order = params.get("order") or None
        root_only = _parse_bool(params.get("root_only"), "root_only")
        time_from = _parse_iso_datetime(params.get("time_from"), "time_from")
        time_to = _parse_iso_datetime(params.get("time_to"), "time_to")

        kwargs: dict = {}
        if limit_raw is not None:
            kwargs["limit"] = limit_raw
        result = retrieval.get_spans(
            cursor=cursor,
            trace_id=trace_id,
            span_id=span_id,
            parent_id=parent_id,
            time_from=time_from,
            time_to=time_to,
            root_only=root_only,
            order=order,
            **kwargs,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    body = json.dumps(_result_to_dict(result), default=_json_default)
    return Response(content=body, media_type="application/json", status_code=200)


async def _graph_handler(request: Request) -> Response:
    """``GET /graph`` — the derived entity/edge graph (issue #58).

    Thin pass-through to ``get_entities`` + ``get_edges``, returning
    ``{"entities": [...], "edges": [...]}``. Query params:

    - ``cursor`` / ``limit`` / ``trace_id`` / ``order`` scope the **edges**
      (``cursor`` is an ``edge_seq``; ``trace_id`` restricts to one trace).
    - ``limit`` / ``semantic_kind`` / ``order`` scope the **entities**
      (entity pagination is not exposed in v1 — the node set is small).
    """
    params = request.query_params

    try:
        cursor = _parse_int(params.get("cursor"), "cursor")
        limit_raw = _parse_int(params.get("limit"), "limit")
        trace_id = params.get("trace_id") or None
        order = params.get("order") or None
        semantic_kind = params.get("semantic_kind") or None

        kwargs: dict = {}
        if limit_raw is not None:
            kwargs["limit"] = limit_raw
        entities = retrieval.get_entities(
            semantic_kind=semantic_kind, order=order, **kwargs
        )
        edges = retrieval.get_edges(
            cursor=cursor, trace_id=trace_id, order=order, **kwargs
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    body = json.dumps(_graph_to_dict(entities, edges), default=_json_default)
    return Response(content=body, media_type="application/json", status_code=200)


def _probe_postgres() -> None:
    """Blocking ``SELECT 1`` against Postgres for :func:`_healthz_handler`.

    Pulled out so the handler can dispatch it via :func:`asyncio.to_thread`
    — ``db.transaction`` does synchronous libpq round-trips. Mirrors the
    receiver's ``server._probe_postgres`` for symmetry; the v1 UI backend's
    only job is to serve ``GET /spans``, which fans out to Postgres, so
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


async def _ui_handler(_request: Request) -> Response:
    """Serve the UI shell ``index.html``."""
    index = _UI_DIR / "index.html"
    # Read on every request intentionally — enables hot-reload during development.
    return HTMLResponse(content=index.read_text())


async def _trace_tree_handler(_request: Request) -> Response:
    """Serve the trace-tree UI shell (issue #14).

    Path is ``/trace/{trace_id}``. The trace_id is consumed by the
    in-page JS (it reads ``window.location.pathname`` to learn the
    target trace), so the server-side handler is the same static HTML
    for any trace_id.
    """
    page = _UI_DIR / "trace_tree.html"
    return HTMLResponse(content=page.read_text())


async def _forest_handler(_request: Request) -> Response:
    """Serve the execution-forest UI shell (ADR-0009).

    Path is ``/forest/{trace_id}``. Like the trace-tree shell, the trace_id is
    consumed by the in-page JS (it reads ``window.location.pathname``), so the
    server-side handler returns the same static HTML for any trace_id.
    """
    page = _UI_DIR / "forest.html"
    return HTMLResponse(content=page.read_text())


# Static asset names allowed under ``/ui/`` — kept narrow on purpose so
# this route never functions as a generic file-server. Add new entries
# here as the UI grows.
_UI_ASSETS: frozenset[str] = frozenset(
    {"recent_traces_logic.js", "trace_tree_logic.js", "forest_logic.js"}
)


async def _ui_asset_handler(request: Request) -> Response:
    """Serve a whitelisted static asset under ``/ui/<file>``."""
    name = request.path_params.get("name", "")
    if name not in _UI_ASSETS:
        return Response(status_code=404)
    asset = _UI_DIR / name
    if not asset.is_file():
        return Response(status_code=404)
    media_type = "application/javascript" if name.endswith(".js") else "text/plain"
    return Response(content=asset.read_text(), media_type=media_type, status_code=200)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app() -> Starlette:
    """Build and return the Starlette application."""
    routes: list = [
        Route("/healthz", endpoint=_healthz_handler, methods=["GET"]),
        Route("/spans", endpoint=_spans_handler, methods=["GET"]),
        Route("/graph", endpoint=_graph_handler, methods=["GET"]),
        Route("/ui/{name:str}", endpoint=_ui_asset_handler, methods=["GET"]),
        Route(
            "/trace/{trace_id:str}",
            endpoint=_trace_tree_handler,
            methods=["GET"],
        ),
        Route(
            "/forest/{trace_id:str}",
            endpoint=_forest_handler,
            methods=["GET"],
        ),
        Route("/", endpoint=_ui_handler, methods=["GET"]),
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
