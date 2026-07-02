"""UI backend REST API — thin Starlette wrapper over the retrieval library.

The single endpoint ``GET /spans`` accepts every ``get_spans`` parameter as
a query parameter and returns the JSON encoding of ``GetSpansResult``.

Issue #4 shipped the tracer bullet (``cursor``, ``limit``, ``trace_id``,
``span_id``, ``order``). Issue #12 extended the surface with
``time_from`` / ``time_to`` (ISO-8601, naive datetimes rejected per
PROJECT.md §6), ``root_only``, and ``parent_id`` (the latter only
honoured for the parameter-compatibility raises until #13 lands).
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


# Static asset names allowed under ``/ui/`` — kept narrow on purpose so
# this route never functions as a generic file-server. Add new entries
# here as the UI grows.
_UI_ASSETS: frozenset[str] = frozenset(
    {
        "recent_traces_logic.js",
        "trace_tree_logic.js",
        "execution_flow_logic.js",  # P-interactions execution-flow view
    }
)


async def _proto_interactions_handler(request: Request) -> Response:
    """P-interactions endpoint — reads the derived interaction graph.

    Serves the entities/interactions the in-cluster interactions processor
    materialised for a trace. The route path is kept as ``/proto/...`` for
    compatibility with existing UI links; the schema it reads is the
    productized one (ADR-0013).
    """
    trace_id = request.path_params.get("trace_id")
    if not trace_id:
        return JSONResponse({"error": "trace_id required"}, status_code=400)

    def _query() -> dict:
        with db.transaction() as tx:
            # The derived tables may not exist yet on a fresh DB (before the
            # interactions migration has run).
            exists = tx.fetch_one(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'interactions'"
            )
            if exists is None:
                return {"entities": [], "interactions": [], "spans_by_interaction": {}, "spans_by_entity": {}}
            # Productized entities are cross-trace-stable (ADR-0013): no
            # trace_id column. Scope them to this trace indirectly via
            # entity_spans (which is trace-scoped) — the entities the
            # processor recorded provenance for in this trace. No
            # retracted_at either (ADR-0012 emit-once-final, no tombstone).
            entities = tx.fetch_all(
                "SELECT id::text, kind, natural_key, display_name, detected_from "
                "FROM entities "
                "WHERE id IN (SELECT DISTINCT entity_id FROM entity_spans "
                "WHERE trace_id = %s) "
                "ORDER BY kind, display_name",
                (trace_id,),
            )
            interactions = tx.fetch_all(
                "SELECT id::text, caller_entity_id::text, callee_entity_id::text, "
                "started_at, ended_at, error, request_payload_hash, "
                "response_payload_hash, summary, parent_interaction_id::text "
                "FROM interactions WHERE trace_id = %s "
                "ORDER BY started_at",
                (trace_id,),
            )
            ev = tx.fetch_all(
                "SELECT pis.interaction_id::text, pis.span_id, pis.role, "
                "s.parent_id, s.kind, s.service_name "
                "FROM interaction_spans pis "
                "LEFT JOIN spans s "
                "  ON s.trace_id = pis.trace_id AND s.span_id = pis.span_id "
                "WHERE pis.trace_id = %s",
                (trace_id,),
            )
            spans_by_ix: dict[str, list[dict]] = {}
            for ix_id, span_id, role, parent_id, kind, service_name in ev:
                spans_by_ix.setdefault(ix_id, []).append(
                    {
                        "span_id": span_id,
                        "role": role,
                        "parent_id": parent_id,
                        "kind": kind,
                        "service_name": service_name,
                    }
                )
            ent_ev = tx.fetch_all(
                "SELECT pes.entity_id::text, pes.span_id, pes.role, "
                "s.parent_id, s.kind, s.service_name "
                "FROM entity_spans pes "
                "LEFT JOIN spans s "
                "  ON s.trace_id = pes.trace_id AND s.span_id = pes.span_id "
                "WHERE pes.trace_id = %s",
                (trace_id,),
            )
            spans_by_entity: dict[str, list[dict]] = {}
            for ent_id, span_id, role, parent_id, kind, service_name in ent_ev:
                spans_by_entity.setdefault(ent_id, []).append(
                    {
                        "span_id": span_id,
                        "role": role,
                        "parent_id": parent_id,
                        "kind": kind,
                        "service_name": service_name,
                    }
                )
            return {
                "entities": [
                    {
                        "id": r[0], "kind": r[1], "natural_key": r[2],
                        "display_name": r[3], "detected_from": r[4],
                    }
                    for r in entities
                ],
                "interactions": [
                    {
                        "id": r[0], "caller_entity_id": r[1], "callee_entity_id": r[2],
                        "started_at": r[3].isoformat() if r[3] else None,
                        "ended_at": r[4].isoformat() if r[4] else None,
                        "error": r[5],
                        "request_payload_hash": r[6],
                        "response_payload_hash": r[7],
                        "summary": r[8],
                        "parent_interaction_id": r[9],
                    }
                    for r in interactions
                ],
                "spans_by_interaction": spans_by_ix,
                "spans_by_entity": spans_by_entity,
            }

    try:
        data = await asyncio.to_thread(_query)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(data)


async def _proto_payload_handler(request: Request) -> Response:
    """Fetch a payload by content_hash (backs the flow view's Req/Resp cells)."""
    h = request.path_params.get("content_hash")
    if not h:
        return JSONResponse({"error": "content_hash required"}, status_code=400)

    def _query() -> dict | None:
        with db.transaction() as tx:
            row = tx.fetch_one(
                "SELECT content_hash, content_kind, content, byte_size "
                "FROM interaction_payloads WHERE content_hash = %s",
                (h,),
            )
            if row is None:
                return None
            return {
                "content_hash": row[0], "content_kind": row[1],
                "content": row[2], "byte_size": row[3],
            }

    try:
        data = await asyncio.to_thread(_query)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    if data is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(data)


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
        Route("/ui/{name:str}", endpoint=_ui_asset_handler, methods=["GET"]),
        Route(
            "/trace/{trace_id:str}",
            endpoint=_trace_tree_handler,
            methods=["GET"],
        ),
        # P-interactions execution-flow endpoints (path kept as /proto/*)
        Route(
            "/proto/interactions/{trace_id:str}",
            endpoint=_proto_interactions_handler,
            methods=["GET"],
        ),
        Route(
            "/proto/payload/{content_hash:str}",
            endpoint=_proto_payload_handler,
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
