"""OTLP receiver servers — gRPC on 4317 and HTTP/protobuf on 4318.

This is the **tracer-bullet stage** of the receiver introduced in issue #3:
both transports decode an OTLP ``ExportTraceServiceRequest`` and write
each span via the minimal :func:`write_span` Layer 2 function. The
``GET /healthz`` endpoint, served alongside the HTTP/protobuf path on
4318, returns 200 iff the OTLP HTTP server is accepting AND Postgres is
reachable, 503 otherwise.

**Library choice.** We bind the gRPC service directly using the
``opentelemetry-proto``-generated ``TraceService`` skeleton on top of
``grpcio``'s synchronous server, and we serve the HTTP/protobuf path with
Starlette behind ``uvicorn``. The alternatives — pulling in the full
OpenTelemetry Collector or its Python contrib bindings — would dwarf the
tracer bullet in code volume and dependency surface, with no behaviour we
need at this stage that the direct generated skeletons don't already give
us. Future slices that grow the receiver (blocklist #9, metrics #8,
SQLSTATE→OTLP mapping #8) sit on top of these two thin handlers.

**Out of scope here, by design:**

- The §3.1 blocklist (issue #9).
- The ADR-0003 SQLSTATE→OTLP error mapping (issue #8). An exception
  escaping ``write_span`` aborts the whole batch with whatever the
  underlying server library reports by default.
- Prometheus ``/metrics`` (issue #8).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import grpc
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from data_governance import db

from .translate import request_to_span_rows
from .write_span import write_span


# Default OTLP wire-side limits (PROJECT.md §1).
DEFAULT_MAX_RECV_BYTES = 4 * 1024 * 1024  # 4 MiB
DEFAULT_GRPC_PORT = 4317
DEFAULT_HTTP_PORT = 4318


_log = logging.getLogger(__name__)


# --- gRPC OTLP server --------------------------------------------------------


class _TraceServicer(trace_service_pb2_grpc.TraceServiceServicer):
    """Implements ``opentelemetry.proto.collector.trace.v1.TraceService.Export``.

    For every span in the request, calls :func:`write_span` in its own
    Layer 1 transaction (per PROJECT.md §3 "Transactional unit"). At the
    tracer-bullet stage, exceptions escape — there is no per-span
    SQLSTATE→OTLP mapping yet (that arrives in issue #8).
    """

    def Export(  # noqa: N802 — gRPC method casing is fixed by the proto
        self,
        request: trace_service_pb2.ExportTraceServiceRequest,
        context: grpc.ServicerContext,
    ) -> trace_service_pb2.ExportTraceServiceResponse:
        for row in request_to_span_rows(request):
            write_span(row)
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


# --- HTTP/protobuf OTLP server + healthz ------------------------------------


def _starlette_app() -> Starlette:
    """Build the Starlette app serving OTLP HTTP/protobuf + ``/healthz``."""
    return Starlette(
        routes=[
            Route("/healthz", endpoint=_healthz_handler, methods=["GET"]),
            Route("/v1/traces", endpoint=_traces_handler, methods=["POST"]),
        ]
    )


def _decode_and_write_spans(body: bytes) -> None:
    """Blocking work for :func:`_traces_handler`: decode + Layer 1 writes.

    Pulled out so :func:`_traces_handler` can dispatch it to the default
    threadpool via :func:`asyncio.to_thread` — :func:`write_span` does
    synchronous libpq round-trips and would otherwise stall uvicorn's
    event loop under load.
    """
    req = trace_service_pb2.ExportTraceServiceRequest()
    req.ParseFromString(body)
    for row in request_to_span_rows(req):
        write_span(row)


async def _traces_handler(request: Request) -> Response:
    """OTLP/HTTP trace export endpoint.

    Reads a serialised :class:`ExportTraceServiceRequest` protobuf body,
    decodes it, and writes each span via :func:`write_span`. The decode
    and DB writes are synchronous (libpq round-trips block), so they run
    on the default asyncio threadpool to keep uvicorn's event loop free.

    The request body is currently read in full with no size enforcement:
    a real OTLP wire-side body cap (PROJECT.md §1's 4 MiB limit) is
    deferred to a follow-up slice. ``h11_max_incomplete_event_size`` on
    the uvicorn config bounds h11's header parser, not request bodies.
    """
    body = await request.body()
    await asyncio.to_thread(_decode_and_write_spans, body)
    # OTLP/HTTP success response: serialised empty
    # ``ExportTraceServiceResponse`` with content-type application/x-protobuf.
    resp = trace_service_pb2.ExportTraceServiceResponse()
    return Response(
        content=resp.SerializeToString(),
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
        # Late import keeps uvicorn out of the import path of the gRPC-only
        # use cases (and keeps the cold-start cost off ``import server``).
        import uvicorn  # local import on purpose

        config = uvicorn.Config(
            app=_starlette_app(),
            host=self.host,
            port=self.port,
            log_level="warning",
            limit_max_requests=None,
            # No request-body size cap is enforced at this stage. The OTLP
            # wire-side 4 MiB cap (PROJECT.md §1) is deferred to a follow-up
            # slice; ``self.max_recv_bytes`` is currently only consulted by
            # the gRPC transport via ``grpc.max_receive_message_length``.
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        self._server = server
        self._thread = thread

    def stop(self, *, grace: float = 1.0) -> None:
        """Stop the uvicorn server, waiting up to *grace* seconds."""
        if self._server is not None:
            # uvicorn.Server.should_exit is the documented stop signal.
            self._server.should_exit = True  # type: ignore[attr-defined]
        if self._thread is not None:
            self._thread.join(timeout=grace)
            self._thread = None
        self._server = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
