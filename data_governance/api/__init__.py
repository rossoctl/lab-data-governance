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


# UI assets are read fresh on every request (hot-reload) and the served
# image is rebuilt in place under the same ``:latest`` tag, so the bytes
# behind a given URL change without the URL changing. Without a cache
# directive, browsers apply heuristic caching and keep serving a stale
# copy after a rebuild (the "ran the CLI but the Flow panel says it
# hasn't" confusion). ``no-cache`` lets the browser cache but forces a
# revalidation every time, so a rebuilt asset is always picked up.
_NO_CACHE_HEADERS = {"Cache-Control": "no-cache"}


async def _ui_handler(_request: Request) -> Response:
    """Serve the UI shell ``index.html``."""
    index = _UI_DIR / "index.html"
    # Read on every request intentionally — enables hot-reload during development.
    return HTMLResponse(content=index.read_text(), headers=_NO_CACHE_HEADERS)


async def _trace_tree_handler(_request: Request) -> Response:
    """Serve the trace-tree UI shell (issue #14).

    Path is ``/trace/{trace_id}``. The trace_id is consumed by the
    in-page JS (it reads ``window.location.pathname`` to learn the
    target trace), so the server-side handler is the same static HTML
    for any trace_id.
    """
    page = _UI_DIR / "trace_tree.html"
    return HTMLResponse(content=page.read_text(), headers=_NO_CACHE_HEADERS)


# Static asset names allowed under ``/ui/`` — kept narrow on purpose so
# this route never functions as a generic file-server. Add new entries
# here as the UI grows.
_UI_ASSETS: frozenset[str] = frozenset(
    {
        "recent_traces_logic.js",
        "trace_tree_logic.js",
        "execution_flow_logic.js",  # P-interactions prototype (throwaway)
        "graph_view_logic.js",      # P-interactions graph prototype (throwaway)
    }
)


async def _proto_interactions_handler(request: Request) -> Response:
    """P-interactions prototype endpoint — reads scratch tables. THROWAWAY."""
    trace_id = request.path_params.get("trace_id")
    if not trace_id:
        return JSONResponse({"error": "trace_id required"}, status_code=400)

    def _query() -> dict:
        with db.transaction() as tx:
            # Tables may not exist yet if the CLI hasn't run.
            exists = tx.fetch_one(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'proto_interactions'"
            )
            if exists is None:
                return {"entities": [], "interactions": [], "spans_by_interaction": {}}
            entities = tx.fetch_all(
                "SELECT id::text, natural_key, display_name, detected_from, "
                "scope_name, anchor_span_id, inferred "
                "FROM proto_entities WHERE trace_id = %s "
                "ORDER BY scope_name, natural_key",
                (trace_id,),
            )
            interactions = tx.fetch_all(
                "SELECT id::text, caller_entity_id::text, callee_entity_id::text, "
                "started_at, ended_at, error, request_payload_hash, "
                'response_payload_hash, summary, "order" '
                'FROM proto_interactions WHERE trace_id = %s ORDER BY "order"',
                (trace_id,),
            )
            ev = tx.fetch_all(
                "SELECT pis.interaction_id::text, pis.span_id, pis.is_anchor, "
                "s.parent_id, s.kind, s.service_name "
                "FROM proto_interaction_spans pis "
                "LEFT JOIN spans s "
                "  ON s.trace_id = pis.trace_id AND s.span_id = pis.span_id "
                "WHERE pis.trace_id = %s",
                (trace_id,),
            )
            spans_by_ix: dict[str, list[dict]] = {}
            for ix_id, span_id, is_anchor, parent_id, kind, service_name in ev:
                spans_by_ix.setdefault(ix_id, []).append(
                    {
                        "span_id": span_id,
                        "is_anchor": is_anchor,
                        "parent_id": parent_id,
                        "kind": kind,
                        "service_name": service_name,
                    }
                )
            return {
                "entities": [
                    {
                        "id": r[0], "natural_key": r[1],
                        "display_name": r[2], "detected_from": r[3],
                        "scope_name": r[4], "anchor_span_id": r[5],
                        "inferred": bool(r[6]),
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
                        "order": r[9],
                    }
                    for r in interactions
                ],
                "spans_by_interaction": spans_by_ix,
            }

    try:
        data = await asyncio.to_thread(_query)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(data)


async def _proto_payload_handler(request: Request) -> Response:
    """Fetch a payload by content_hash. THROWAWAY."""
    h = request.path_params.get("content_hash")
    if not h:
        return JSONResponse({"error": "content_hash required"}, status_code=400)

    def _query() -> dict | None:
        with db.transaction() as tx:
            row = tx.fetch_one(
                "SELECT content_hash, content_kind, content, byte_size "
                "FROM proto_interaction_payloads WHERE content_hash = %s",
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


async def _proto_graphs_handler(request: Request) -> Response:
    """Graph prototype endpoint — reads graph scratch tables. THROWAWAY.

    Returns three graph stages from the new algorithm:
      - base:    Step 1 white base graph (one node per span; traceparent edges)
      - colored: Step 2 colored execution graph, before the fuse (Blue/Teal,
                 additive edge colors, inferred nodes/edges, combined-span
                 duplicates, the Step 2.d node+edge merge, between-boundary flags)
      - entity:  Step 3 entity graph, after the fuse (one node per fused component)
    """
    trace_id = request.path_params.get("trace_id")
    if not trace_id:
        return JSONResponse({"error": "trace_id required"}, status_code=400)

    def _query() -> dict:
        empty = {
            "base": {"nodes": [], "edges": []},
            "colored": {"nodes": [], "edges": []},
            "entity": {"nodes": [], "edges": []},
        }
        with db.transaction() as tx:
            exists = tx.fetch_one(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'proto_base_nodes'"
            )
            if exists is None:
                return empty

            # --- Step 1 base graph ---
            base_node_rows = tx.fetch_all(
                "SELECT id, span_id, scope, attributes "
                "FROM proto_base_nodes WHERE trace_id = %s "
                "ORDER BY scope, span_id",
                (trace_id,),
            )
            base_nodes = [
                {
                    "id": r[0],
                    "scope": r[2],
                    "node_type": "span",
                    "color": "white",
                    "label": None,
                    "attributes": r[3],
                    "span_ids": [r[1]] if r[1] else [],
                    "is_boundary": False,
                    "is_target_duplicate": False,
                    "is_inferred": False,
                    "flagged": False,
                }
                for r in base_node_rows
            ]
            base_edge_rows = tx.fetch_all(
                "SELECT id, from_node_id, to_node_id "
                "FROM proto_base_edges WHERE trace_id = %s",
                (trace_id,),
            )
            base_edges = [
                {"id": r[0], "from": r[1], "to": r[2], "kind": "white", "colors": "white"}
                for r in base_edge_rows
            ]

            # --- Step 2 colored execution graph (before fuse) ---
            colored_node_rows = tx.fetch_all(
                "SELECT id, span_id, scope, color, is_boundary, is_target_duplicate, "
                "       is_inferred, flagged, label, attributes "
                "FROM proto_colored_nodes WHERE trace_id = %s "
                "ORDER BY color, scope, span_id",
                (trace_id,),
            )
            colored_nodes = [
                {
                    "id": r[0],
                    "scope": r[2],
                    "node_type": "span",
                    "color": r[3],
                    "is_boundary": r[4],
                    "is_target_duplicate": r[5],
                    "is_inferred": r[6],
                    "flagged": r[7],
                    "label": r[8],
                    "attributes": r[9],
                    "span_ids": [r[1]] if r[1] else [],
                }
                for r in colored_node_rows
            ]
            colored_edge_rows = tx.fetch_all(
                "SELECT id, from_node_id, to_node_id, colors, kind "
                "FROM proto_colored_edges WHERE trace_id = %s ORDER BY kind",
                (trace_id,),
            )
            colored_edges = [
                {
                    "id": r[0], "from": r[1], "to": r[2],
                    "colors": r[3], "kind": r[4],
                }
                for r in colored_edge_rows
            ]

            # --- Step 3 entity graph (after fuse) ---
            entity_node_rows = tx.fetch_all(
                "SELECT n.id, n.label, n.attributes, n.contains_boundary, "
                "       n.contains_blue, n.contains_teal, n.inferred, "
                "       n.scopes, "
                "       array_agg(ns.span_id ORDER BY ns.span_id) "
                "         FILTER (WHERE ns.span_id IS NOT NULL) "
                "FROM proto_entity_nodes n "
                "LEFT JOIN proto_entity_node_spans ns ON ns.node_id = n.id "
                "WHERE n.trace_id = %s "
                "GROUP BY n.id, n.label, n.attributes, n.contains_boundary, "
                "         n.contains_blue, n.contains_teal, n.inferred, "
                "         n.scopes "
                "ORDER BY n.label",
                (trace_id,),
            )
            entity_nodes = [
                {
                    "id": r[0],
                    "scope": r[7],
                    "node_type": "entity",
                    # Teal nodes are dropped at the fuse, so an entity is Blue
                    # when it carries any agentic node, else White.
                    "color": "blue" if r[4] else ("teal" if r[5] else "white"),
                    "label": r[1],
                    "attributes": r[2],
                    "contains_boundary": r[3],
                    "span_ids": r[8] or [],
                    "is_boundary": r[3],
                    "is_target_duplicate": False,
                    # Step 3.a marker: surfaced as `is_inferred` on the wire so
                    # the UI uses one boolean shape across base / colored /
                    # entity graphs. Source column on the entity row is
                    # `inferred` (no `is_` prefix) — see ADR-0007.
                    "is_inferred": r[6],
                    "flagged": False,
                }
                for r in entity_node_rows
            ]
            entity_edge_rows = tx.fetch_all(
                "SELECT id, from_node_id, to_node_id "
                "FROM proto_entity_edges WHERE trace_id = %s",
                (trace_id,),
            )
            entity_edges = [
                {"id": r[0], "from": r[1], "to": r[2], "kind": "interaction", "colors": "interaction"}
                for r in entity_edge_rows
            ]

            return {
                "base": {"nodes": base_nodes, "edges": base_edges},
                "colored": {"nodes": colored_nodes, "edges": colored_edges},
                "entity": {"nodes": entity_nodes, "edges": entity_edges},
            }

    try:
        data = await asyncio.to_thread(_query)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(data)


async def _proto_process_handler(request: Request) -> Response:
    """Run the P-interactions extractor for a trace, on demand. THROWAWAY.

    Backs the "Process this trace" button: runs the exact same extraction
    the CLI runs (single-trace semantics — drops & recreates the proto_*
    scratch tables, writes only this trace). Synchronous: the extraction
    runs in a worker thread and the response carries the resulting counts.
    """
    trace_id = request.path_params.get("trace_id")
    if not trace_id:
        return JSONResponse({"error": "trace_id required"}, status_code=400)

    def _process() -> dict:
        # Import inside the handler — the proto prototype is throwaway and
        # kept off the API's import-time surface (same isolation as the
        # other /proto/* routes).
        from data_governance.processors.p_interactions_proto import process_trace

        result = process_trace(trace_id)
        return {
            "entities": len(result.entities),
            "interactions": len(result.interactions),
            "spans": len(result.base_graph.nodes),
        }

    try:
        counts = await asyncio.to_thread(_process)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(counts)


async def _ui_asset_handler(request: Request) -> Response:
    """Serve a whitelisted static asset under ``/ui/<file>``."""
    name = request.path_params.get("name", "")
    if name not in _UI_ASSETS:
        return Response(status_code=404)
    asset = _UI_DIR / name
    if not asset.is_file():
        return Response(status_code=404)
    media_type = "application/javascript" if name.endswith(".js") else "text/plain"
    return Response(
        content=asset.read_text(),
        media_type=media_type,
        status_code=200,
        headers=_NO_CACHE_HEADERS,
    )


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
        # P-interactions prototype (throwaway)
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
        Route(
            "/proto/graphs/{trace_id:str}",
            endpoint=_proto_graphs_handler,
            methods=["GET"],
        ),
        Route(
            "/proto/process/{trace_id:str}",
            endpoint=_proto_process_handler,
            methods=["POST"],
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
