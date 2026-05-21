"""UI backend REST API — thin Starlette wrapper over the retrieval library.

Issue #4 tracer-bullet: one endpoint ``GET /spans`` and a served UI shell.
All ``get_spans`` parameters are accepted as query parameters; the response
body is the JSON encoding of ``GetSpansResult``.
"""

from __future__ import annotations

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

from data_governance import retrieval

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


def _result_to_dict(result: retrieval.GetSpansResult) -> dict:
    return {
        "spans": [dataclasses.asdict(s) for s in result.spans],
        "counts": result.counts,
    }


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------


def _parse_int(value: str | None, name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"'{name}' must be an integer, got {value!r}")


async def _spans_handler(request: Request) -> Response:
    """``GET /spans`` — pass-through to ``get_spans``."""
    params = request.query_params

    try:
        cursor = _parse_int(params.get("cursor"), "cursor")
        limit_raw = _parse_int(params.get("limit"), "limit")
        trace_id = params.get("trace_id") or None
        span_id = params.get("span_id") or None
        order = params.get("order") or None

        kwargs: dict = {}
        if limit_raw is not None:
            kwargs["limit"] = limit_raw
        result = retrieval.get_spans(
            cursor=cursor,
            trace_id=trace_id,
            span_id=span_id,
            order=order,
            **kwargs,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    body = json.dumps(_result_to_dict(result), default=_json_default)
    return Response(content=body, media_type="application/json", status_code=200)


async def _ui_handler(_request: Request) -> Response:
    """Serve the UI shell ``index.html``."""
    index = _UI_DIR / "index.html"
    # Read on every request intentionally — enables hot-reload during development.
    return HTMLResponse(content=index.read_text())


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app() -> Starlette:
    """Build and return the Starlette application."""
    routes: list = [
        Route("/spans", endpoint=_spans_handler, methods=["GET"]),
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
