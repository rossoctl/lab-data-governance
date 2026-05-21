"""OTLP receiver servers — gRPC on 4317, HTTP/protobuf on 4318, metrics on 9090.

Issue #3 introduced the gRPC and HTTP/protobuf transports. Issue #8 adds:

- ADR-0003 SQLSTATE→OTLP error classification: connection-class failures
  make the whole batch retryable; integrity/too-large failures push the
  offending span to ``rejected_spans`` and return OTLP partial-success;
  PK duplicates are silently dropped.
- Prometheus ``/metrics`` served on a dedicated :class:`MetricsServer` (port
  9090 by default) so infra scraping stays on a separate port from the OTLP
  and UI surfaces.

**Library choice.** We bind the gRPC service directly using the
``opentelemetry-proto``-generated ``TraceService`` skeleton on top of
``grpcio``'s synchronous server, and we serve the HTTP/protobuf path with
Starlette behind ``uvicorn``. The alternatives — pulling in the full
OpenTelemetry Collector or its Python contrib bindings — would dwarf the
tracer bullet in code volume and dependency surface.

**Out of scope here, by design:**

- The §3.1 blocklist (issue #9).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import grpc
import prometheus_client
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from data_governance import db

from . import metrics as _metrics
from .classify import classify_error
from .translate import request_to_span_rows
from .write_span import WriteOutcome, write_span


# Default OTLP wire-side limits (PROJECT.md §1).
DEFAULT_MAX_RECV_BYTES = 4 * 1024 * 1024  # 4 MiB
DEFAULT_GRPC_PORT = 4317
DEFAULT_HTTP_PORT = 4318
DEFAULT_METRICS_PORT = 9090


_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# rejected_spans writer
# ---------------------------------------------------------------------------


_REJECT_SQL = """
    INSERT INTO rejected_spans (trace_id, span_id, error_message)
    VALUES (%s, %s, %s)
"""


def _write_rejected_span(trace_id: str, span_id: str, error_message: str) -> None:
    """Persist one rejected span to the audit table in its own transaction."""
    try:
        with db.transaction() as tx:
            tx.execute(_REJECT_SQL, (trace_id, span_id, error_message))
    except Exception:
        _log.exception(
            "Failed to write rejected_span for (%s, %s)", trace_id, span_id
        )


# ---------------------------------------------------------------------------
# Per-span write with metrics and error classification
# ---------------------------------------------------------------------------


def _write_span_with_metrics(
    row_data: object,
    transport: str,
) -> tuple[WriteOutcome | None, str | None]:
    """Call write_span, record metrics, classify errors.

    Returns ``(outcome, error_kind)`` where exactly one is non-None:
    - ``(outcome, None)`` on success.
    - ``(None, error_kind)`` on failure, where ``error_kind`` is one of
      ``"connection"``, ``"integrity"``, ``"other"`` (never ``"duplicate"``
      — duplicates are returned as ``WriteOutcome.DUPLICATE``).
    """
    # Approximate serialised row size for the histogram (repr is always safe).
    row_bytes = len(repr(row_data))

    t0 = time.perf_counter()
    try:
        outcome = write_span(row_data)  # type: ignore[arg-type]
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        _metrics.span_insert_duration_seconds.observe(elapsed)
        kind = classify_error(exc)
        if kind == "duplicate":
            _metrics.spans_duplicate_total.inc()
        else:
            _metrics.db_errors_total.labels(kind=kind).inc()
        return None, kind
    elapsed = time.perf_counter() - t0
    _metrics.span_insert_duration_seconds.observe(elapsed)
    if row_bytes:
        _metrics.span_row_bytes.observe(row_bytes)

    if outcome is WriteOutcome.INSERTED:
        _metrics.spans_inserted_total.inc()
    elif outcome is WriteOutcome.FINALIZED:
        _metrics.spans_finalized_total.inc()
    elif outcome is WriteOutcome.DUPLICATE:
        _metrics.spans_duplicate_total.inc()

    return outcome, None


# ---------------------------------------------------------------------------
# gRPC OTLP server
# ---------------------------------------------------------------------------


class _TraceServicer(trace_service_pb2_grpc.TraceServiceServicer):
    """Implements ``opentelemetry.proto.collector.trace.v1.TraceService.Export``.

    Per ADR-0003:
    - Connection / other errors → gRPC UNAVAILABLE (retryable batch failure).
    - Integrity errors → span written to ``rejected_spans``; OTLP partial-success.
    - Duplicates → silently skipped; OTLP success.
    """

    def Export(  # noqa: N802 — gRPC method casing is fixed by the proto
        self,
        request: trace_service_pb2.ExportTraceServiceRequest,
        context: grpc.ServicerContext,
    ) -> trace_service_pb2.ExportTraceServiceResponse:
        _metrics.spans_received_total.labels(transport="grpc").inc()

        rejected = 0
        for row in request_to_span_rows(request):
            outcome, error_kind = _write_span_with_metrics(row, transport="grpc")
            if error_kind in ("connection", "other"):
                context.set_code(grpc.StatusCode.UNAVAILABLE)
                context.set_details(f"db error: {error_kind}")
                return trace_service_pb2.ExportTraceServiceResponse()
            if error_kind == "integrity":
                rejected += 1
                _write_rejected_span(row.trace_id, row.span_id, "integrity error")

        if rejected:
            return trace_service_pb2.ExportTraceServiceResponse(
                partial_success=trace_service_pb2.ExportTracePartialSuccess(
                    rejected_spans=rejected,
                    error_message=f"{rejected} span(s) rejected due to integrity errors",
                )
            )
        return trace_service_pb2.ExportTraceServiceResponse()


class GrpcOtlpServer:
    """Lifecycle wrapper around the gRPC OTLP server.

    Instantiate, call :meth:`start` (non-blocking), call :meth:`stop`
    when shutting down. Designed for single-process v1 deployment and
    for in-process use by tests.
    """

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = DEFAULT_GRPC_PORT,
        max_recv_bytes: int = DEFAULT_MAX_RECV_BYTES,
        max_workers: int = 10,
    ) -> None:
        self.host = host
        self.port = port
        self.max_recv_bytes = max_recv_bytes
        self._max_workers = max_workers
        self._server: grpc.Server | None = None

    def start(self) -> None:
        """Bind the configured port and start serving in the background."""
        server = grpc.server(
            ThreadPoolExecutor(max_workers=self._max_workers),
            options=[
                ("grpc.max_receive_message_length", self.max_recv_bytes),
            ],
        )
        trace_service_pb2_grpc.add_TraceServiceServicer_to_server(
            _TraceServicer(), server
        )
        server.add_insecure_port(f"{self.host}:{self.port}")
        server.start()
        self._server = server

    def stop(self, *, grace: float = 1.0) -> None:
        """Stop the server, waiting up to *grace* seconds for in-flight RPCs."""
        if self._server is not None:
            self._server.stop(grace).wait()
            self._server = None

    def is_running(self) -> bool:
        return self._server is not None


# ---------------------------------------------------------------------------
# HTTP/protobuf OTLP server + healthz
# ---------------------------------------------------------------------------


def _starlette_app() -> Starlette:
    """Build the Starlette app serving OTLP HTTP/protobuf + ``/healthz``."""
    return Starlette(
        routes=[
            Route("/healthz", endpoint=_healthz_handler, methods=["GET"]),
            Route("/v1/traces", endpoint=_traces_handler, methods=["POST"]),
        ]
    )


class _HttpWriteResult:
    """Carries the outcome of the synchronous write loop back to the async handler."""

    __slots__ = ("rejected", "retryable_error")

    def __init__(self) -> None:
        self.rejected: int = 0
        self.retryable_error: Exception | None = None


def _decode_and_write_spans(body: bytes, result: _HttpWriteResult) -> None:
    """Blocking work for :func:`_traces_handler`: decode + per-span writes.

    Pulled out so :func:`_traces_handler` can dispatch it to the default
    threadpool via :func:`asyncio.to_thread` — :func:`write_span` does
    synchronous libpq round-trips and would otherwise stall uvicorn's event
    loop under load.

    Mutates *result* in-place with rejection count or retryable error.
    """
    req = trace_service_pb2.ExportTraceServiceRequest()
    req.ParseFromString(body)
    for row in request_to_span_rows(req):
        outcome, error_kind = _write_span_with_metrics(row, transport="http")
        if error_kind in ("connection", "other"):
            result.retryable_error = Exception(f"db error: {error_kind}")
            return
        if error_kind == "integrity":
            result.rejected += 1
            _write_rejected_span(row.trace_id, row.span_id, "integrity error")


async def _traces_handler(request: Request) -> Response:
    """OTLP/HTTP trace export endpoint.

    Reads a serialised :class:`ExportTraceServiceRequest` protobuf body,
    decodes it, and writes each span via :func:`write_span`. The decode
    and DB writes are synchronous (libpq round-trips block), so they run
    on the default asyncio threadpool to keep uvicorn's event loop free.

    Returns 503 on connection/other DB errors (retryable), or 200 with an
    OTLP partial-success body when some spans were rejected for integrity
    reasons.
    """
    _metrics.spans_received_total.labels(transport="http").inc()

    body = await request.body()
    result = _HttpWriteResult()
    await asyncio.to_thread(_decode_and_write_spans, body, result)

    resp_proto = trace_service_pb2.ExportTraceServiceResponse()
    if result.retryable_error is not None:
        return Response(
            content=resp_proto.SerializeToString(),
            media_type="application/x-protobuf",
            status_code=503,
        )
    if result.rejected:
        resp_proto.partial_success.rejected_spans = result.rejected
        resp_proto.partial_success.error_message = (
            f"{result.rejected} span(s) rejected due to integrity errors"
        )
    return Response(
        content=resp_proto.SerializeToString(),
        media_type="application/x-protobuf",
        status_code=200,
    )


def _probe_postgres() -> None:
    """Blocking ``SELECT 1`` against Postgres for :func:`_healthz_handler`.

    Pulled out so the handler can dispatch it via :func:`asyncio.to_thread`
    — :func:`db.transaction` does synchronous libpq round-trips.
    """
    with db.transaction() as tx:
        tx.execute("SELECT 1")


async def _healthz_handler(_request: Request) -> Response:
    """Per PROJECT.md §5.1: 200 iff OTLP is open AND Postgres is reachable.

    The fact that this handler ran proves the HTTP/protobuf transport is
    open. We separately probe Postgres via the Layer 1 pool, off the
    event loop via :func:`asyncio.to_thread` (libpq is synchronous).
    Connection errors surface as 503.
    """
    try:
        await asyncio.to_thread(_probe_postgres)
    except Exception:  # noqa: BLE001 — broad on purpose
        return Response("not ready", status_code=503, media_type="text/plain")
    return Response("ok", status_code=200, media_type="text/plain")


class HttpOtlpServer:
    """Lifecycle wrapper around the HTTP/protobuf OTLP + ``/healthz`` server.

    Runs uvicorn in a background thread so callers can start and stop it
    without blocking. Test fixtures rely on this; production wires the
    same lifecycle into the receiver entrypoint.
    """

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = DEFAULT_HTTP_PORT,
        max_recv_bytes: int = DEFAULT_MAX_RECV_BYTES,
    ) -> None:
        self.host = host
        self.port = port
        self.max_recv_bytes = max_recv_bytes
        self._server: object | None = None  # uvicorn.Server, late-imported
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Bind the configured port and start serving in a background thread."""
        import uvicorn  # local import on purpose

        config = uvicorn.Config(
            app=_starlette_app(),
            host=self.host,
            port=self.port,
            log_level="warning",
            limit_max_requests=None,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        self._server = server
        self._thread = thread

    def stop(self, *, grace: float = 1.0) -> None:
        """Stop the uvicorn server, waiting up to *grace* seconds."""
        if self._server is not None:
            self._server.should_exit = True  # type: ignore[attr-defined]
        if self._thread is not None:
            self._thread.join(timeout=grace)
            self._thread = None
        self._server = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# ---------------------------------------------------------------------------
# Prometheus metrics server
# ---------------------------------------------------------------------------


def _metrics_app(registry: prometheus_client.CollectorRegistry) -> Starlette:
    """Build the Starlette app serving ``GET /metrics``."""

    async def _metrics_handler(_request: Request) -> Response:
        output = prometheus_client.generate_latest(registry)
        return Response(
            content=output,
            media_type=prometheus_client.CONTENT_TYPE_LATEST,
            status_code=200,
        )

    return Starlette(
        routes=[Route("/metrics", endpoint=_metrics_handler, methods=["GET"])]
    )


class MetricsServer:
    """Lifecycle wrapper around the Prometheus metrics HTTP server.

    Serves ``GET /metrics`` in Prometheus text exposition format on a
    dedicated port (default 9090) so infrastructure scrapers have a clean,
    separate endpoint from the OTLP and UI surfaces.

    The *registry* argument defaults to the module-level singleton from
    :mod:`metrics`. Tests pass a fresh registry from
    :func:`metrics.make_registry` for isolation.
    """

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = DEFAULT_METRICS_PORT,
        registry: prometheus_client.CollectorRegistry | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._registry = registry if registry is not None else _metrics._registry
        self._server: object | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Bind the configured port and start serving in a background thread."""
        import uvicorn  # local import on purpose

        config = uvicorn.Config(
            app=_metrics_app(self._registry),
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
        """Stop the uvicorn server, waiting up to *grace* seconds."""
        if self._server is not None:
            self._server.should_exit = True  # type: ignore[attr-defined]
        if self._thread is not None:
            self._thread.join(timeout=grace)
            self._thread = None
        self._server = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
