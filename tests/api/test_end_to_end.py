"""End-to-end test for issue #4:

Send an OTLP span via gRPC → call GET /spans → assert the span is in the response.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator

import httpx
import pytest

from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from data_governance.api import SpansApiServer
from data_governance.processors.otlp_receiver.server import GrpcOtlpServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.connect((host, port))
                return
            except OSError:
                time.sleep(0.05)
    raise TimeoutError(f"port {host}:{port} did not open")


def test_otlp_span_visible_via_get_spans(configured_db: str) -> None:
    """Send span via OTLP gRPC, read it back through GET /spans."""
    grpc_port = _free_port()
    api_port = _free_port()

    grpc_server = GrpcOtlpServer(host="127.0.0.1", port=grpc_port)
    api_server = SpansApiServer(host="127.0.0.1", port=api_port)

    grpc_server.start()
    api_server.start()

    try:
        _wait_port("127.0.0.1", grpc_port)
        _wait_port("127.0.0.1", api_port)

        provider = TracerProvider(resource=Resource.create({"service.name": "e2e-test"}))
        provider.add_span_processor(
            SimpleSpanProcessor(
                OTLPSpanExporter(
                    endpoint=f"127.0.0.1:{grpc_port}", insecure=True
                )
            )
        )
        with provider.get_tracer("e2e").start_as_current_span("e2e-read-path"):
            pass
        provider.shutdown()

        resp = httpx.get(f"http://127.0.0.1:{api_port}/spans")
        assert resp.status_code == 200

        spans = resp.json()["spans"]
        names = [s["name"] for s in spans]
        assert "e2e-read-path" in names

    finally:
        grpc_server.stop(grace=0.5)
        api_server.stop(grace=1.0)
