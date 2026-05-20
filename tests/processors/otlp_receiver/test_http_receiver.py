"""Tests for the OTLP HTTP/protobuf receiver on 4318 (issue #3).

Sends OTLP via the canonical client-side SDK
(``opentelemetry-exporter-otlp-proto-http``) and asserts rows landed in
``spans``. No mocking of the wire format — per the issue's testing
constraints, we use a real OTLP exporter against a real Postgres.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator

import psycopg
import pytest

from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from data_governance.processors.otlp_receiver.server import HttpOtlpServer


# --- helpers (duplicated from gRPC tests on purpose; small + clear) ---------


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


def _make_tracer_provider(port: int) -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    exporter = OTLPSpanExporter(
        endpoint=f"http://127.0.0.1:{port}/v1/traces",
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


def _all_span_rows(dsn: str) -> list[dict[str, object]]:
    cols = (
        "trace_id, span_id, parent_id, name, started_at, attributes, "
        "seq, arrival_seq"
    )
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(f"SELECT {cols} FROM spans").fetchall()
    keys = [c.strip() for c in cols.split(",")]
    return [dict(zip(keys, r, strict=False)) for r in rows]


# --- fixtures ----------------------------------------------------------------


@pytest.fixture()
def http_server(configured_db: str) -> Iterator[HttpOtlpServer]:
    port = _free_port()
    server = HttpOtlpServer(host="127.0.0.1", port=port)
    server.start()
    try:
        _wait_port_open("127.0.0.1", port)
        yield server
    finally:
        server.stop(grace=1.0)


# --- tests -------------------------------------------------------------------


class TestHttpExportPersists:
    def test_single_span_lands_in_spans_table(
        self, configured_db: str, http_server: HttpOtlpServer
    ) -> None:
        provider = _make_tracer_provider(http_server.port)
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("hello-http"):
            pass
        provider.shutdown()

        rows = _all_span_rows(configured_db)
        assert len(rows) == 1
        assert rows[0]["name"] == "hello-http"

    def test_seq_equals_arrival_seq(
        self, configured_db: str, http_server: HttpOtlpServer
    ) -> None:
        provider = _make_tracer_provider(http_server.port)
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("seq-http"):
            pass
        provider.shutdown()

        rows = _all_span_rows(configured_db)
        assert len(rows) == 1
        assert rows[0]["seq"] == rows[0]["arrival_seq"]

    def test_attributes_round_trip(
        self, configured_db: str, http_server: HttpOtlpServer
    ) -> None:
        provider = _make_tracer_provider(http_server.port)
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("with-attrs") as span:
            span.set_attribute("k", "v")
            span.set_attribute("flag", True)
        provider.shutdown()

        rows = _all_span_rows(configured_db)
        attrs = rows[0]["attributes"]
        assert isinstance(attrs, dict)
        assert attrs.get("k") == "v"
        assert attrs.get("flag") is True

    def test_attributes_default_to_empty_object(
        self, configured_db: str, http_server: HttpOtlpServer
    ) -> None:
        provider = _make_tracer_provider(http_server.port)
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("no-attrs"):
            pass
        provider.shutdown()

        rows = _all_span_rows(configured_db)
        assert rows[0]["attributes"] == {}

    def test_duplicate_export_is_idempotent_at_wire(
        self, configured_db: str, http_server: HttpOtlpServer
    ) -> None:
        # Build one OTLP HTTP request, send it twice via httpx.
        import httpx
        from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
        from opentelemetry.proto.common.v1 import common_pb2
        from opentelemetry.proto.trace.v1 import trace_pb2

        span = trace_pb2.Span(
            trace_id=bytes(range(16)),
            span_id=bytes(range(8)),
            name="dup-http",
            start_time_unix_nano=1_700_000_000_000_000_000,
            end_time_unix_nano=1_700_000_000_001_000_000,
        )
        request = trace_service_pb2.ExportTraceServiceRequest(
            resource_spans=[
                trace_pb2.ResourceSpans(
                    scope_spans=[
                        trace_pb2.ScopeSpans(
                            scope=common_pb2.InstrumentationScope(name="t"),
                            spans=[span],
                        )
                    ]
                )
            ]
        )
        body = request.SerializeToString()
        url = f"http://127.0.0.1:{http_server.port}/v1/traces"
        headers = {"Content-Type": "application/x-protobuf"}

        r1 = httpx.post(url, content=body, headers=headers)
        r2 = httpx.post(url, content=body, headers=headers)
        assert r1.status_code == 200
        assert r2.status_code == 200

        rows = _all_span_rows(configured_db)
        assert len(rows) == 1
        assert rows[0]["name"] == "dup-http"
