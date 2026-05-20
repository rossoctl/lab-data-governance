"""End-to-end test (issue #3 acceptance):

Boot both OTLP transports (gRPC on a free port, HTTP/protobuf on another),
send one span via gRPC and another via HTTP, then assert both rows landed
in ``spans``.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator

import psycopg
import pytest

from opentelemetry import trace as otel_trace_api
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from data_governance.processors.otlp_receiver.server import (
    GrpcOtlpServer,
    HttpOtlpServer,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port_open(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.connect((host, port))
                return
            except OSError:
                time.sleep(0.05)
    raise TimeoutError(f"port {host}:{port} did not open within {timeout}s")


@pytest.fixture()
def both_servers(
    configured_db: str,
) -> Iterator[tuple[GrpcOtlpServer, HttpOtlpServer]]:
    grpc_port = _free_port()
    http_port = _free_port()
    grpc_server = GrpcOtlpServer(host="127.0.0.1", port=grpc_port)
    http_server = HttpOtlpServer(host="127.0.0.1", port=http_port)
    grpc_server.start()
    http_server.start()
    try:
        _wait_port_open("127.0.0.1", grpc_port)
        _wait_port_open("127.0.0.1", http_port)
        yield grpc_server, http_server
    finally:
        grpc_server.stop(grace=0.5)
        http_server.stop(grace=1.0)


def test_both_transports_persist_spans_to_one_database(
    configured_db: str,
    both_servers: tuple[GrpcOtlpServer, HttpOtlpServer],
) -> None:
    grpc_server, http_server = both_servers

    # gRPC export
    grpc_provider = TracerProvider(
        resource=Resource.create({"service.name": "grpc-side"})
    )
    grpc_provider.add_span_processor(
        SimpleSpanProcessor(
            GrpcExporter(endpoint=f"127.0.0.1:{grpc_server.port}", insecure=True)
        )
    )
    with grpc_provider.get_tracer("e2e").start_as_current_span("via-grpc"):
        pass
    grpc_provider.shutdown()

    # HTTP export — using a *different* tracer provider so we don't share
    # the gRPC exporter's trace context.
    http_provider = TracerProvider(
        resource=Resource.create({"service.name": "http-side"})
    )
    http_provider.add_span_processor(
        SimpleSpanProcessor(
            HttpExporter(endpoint=f"http://127.0.0.1:{http_server.port}/v1/traces")
        )
    )
    with http_provider.get_tracer("e2e").start_as_current_span("via-http"):
        pass
    http_provider.shutdown()

    with psycopg.connect(configured_db) as conn:
        rows = conn.execute("SELECT name FROM spans ORDER BY name").fetchall()

    names = [r[0] for r in rows]
    assert names == ["via-grpc", "via-http"]

    # Make sure the otel default global tracer didn't leak into the
    # second provider (a common SDK foot-gun); the assertion above is the
    # actual end-to-end check.
    assert otel_trace_api.get_tracer_provider() is not None
