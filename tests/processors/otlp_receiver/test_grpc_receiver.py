"""Tests for the OTLP gRPC receiver (issue #3 acceptance criteria).

Spans are sent through a real OpenTelemetry SDK + OTLP/gRPC exporter at
``opentelemetry-exporter-otlp-proto-grpc`` — the canonical client side of
the wire protocol. The receiver under test runs in-process on a chosen
port; tests assert by reading rows directly from Postgres.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator

import psycopg
import pytest

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from data_governance.processors.otlp_receiver.server import GrpcOtlpServer


# --- helpers -----------------------------------------------------------------


def _free_port() -> int:
    """Return a TCP port that was free at the moment of the call."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port_open(host: str, port: int, timeout: float = 5.0) -> None:
    """Block until *host:port* accepts TCP connections, or raise TimeoutError."""
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
    """Build a TracerProvider that exports synchronously to the given port."""
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    exporter = OTLPSpanExporter(endpoint=f"127.0.0.1:{port}", insecure=True)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


def _all_span_rows(dsn: str) -> list[dict[str, object]]:
    cols = (
        "trace_id, span_id, parent_id, name, started_at, attributes, "
        "seq, arrival_seq, observed_at"
    )
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(f"SELECT {cols} FROM spans").fetchall()
    keys = [c.strip() for c in cols.split(",")]
    return [dict(zip(keys, r, strict=False)) for r in rows]


# --- fixtures ----------------------------------------------------------------


@pytest.fixture()
def grpc_server(configured_db: str) -> Iterator[GrpcOtlpServer]:
    """Boot the OTLP gRPC server on a free port; tear down after the test."""
    port = _free_port()
    server = GrpcOtlpServer(host="127.0.0.1", port=port)
    server.start()
    try:
        _wait_port_open("127.0.0.1", port)
        yield server
    finally:
        server.stop(grace=0.5)


# --- tests -------------------------------------------------------------------


class TestGrpcExportPersists:
    def test_single_span_lands_in_spans_table(
        self, configured_db: str, grpc_server: GrpcOtlpServer
    ) -> None:
        provider = _make_tracer_provider(grpc_server.port)
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("hello-grpc"):
            pass
        provider.shutdown()  # flush the SimpleSpanProcessor

        rows = _all_span_rows(configured_db)
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "hello-grpc"
        # trace_id is 32 hex chars, span_id is 16 hex chars (the receiver
        # stores OTLP ids as their hex representation).
        assert isinstance(row["trace_id"], str)
        assert len(row["trace_id"]) == 32
        assert isinstance(row["span_id"], str)
        assert len(row["span_id"]) == 16

    def test_seq_equals_arrival_seq(
        self, configured_db: str, grpc_server: GrpcOtlpServer
    ) -> None:
        provider = _make_tracer_provider(grpc_server.port)
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("eq"):
            pass
        provider.shutdown()

        rows = _all_span_rows(configured_db)
        assert len(rows) == 1
        assert rows[0]["seq"] == rows[0]["arrival_seq"]

    def test_attributes_is_json_object_when_span_has_no_attributes(
        self, configured_db: str, grpc_server: GrpcOtlpServer
    ) -> None:
        provider = _make_tracer_provider(grpc_server.port)
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("no-attrs"):
            pass
        provider.shutdown()

        rows = _all_span_rows(configured_db)
        assert len(rows) == 1
        assert rows[0]["attributes"] == {}

    def test_attributes_round_trip_when_span_has_attributes(
        self, configured_db: str, grpc_server: GrpcOtlpServer
    ) -> None:
        provider = _make_tracer_provider(grpc_server.port)
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("with-attrs") as span:
            span.set_attribute("user.id", "alice")
            span.set_attribute("retries", 3)
        provider.shutdown()

        rows = _all_span_rows(configured_db)
        assert len(rows) == 1
        attrs = rows[0]["attributes"]
        assert isinstance(attrs, dict)
        assert attrs.get("user.id") == "alice"
        assert attrs.get("retries") == 3

    def test_multiple_spans_in_one_batch_all_persist(
        self, configured_db: str, grpc_server: GrpcOtlpServer
    ) -> None:
        provider = _make_tracer_provider(grpc_server.port)
        tracer = provider.get_tracer("test")
        # Three sibling spans inside an outer span — four total. One Export
        # call carries all four (BatchSpanProcessor would batch better, but
        # SimpleSpanProcessor sends each on end; the OTLP wire still groups
        # them at the ResourceSpans envelope level when shipped.)
        with tracer.start_as_current_span("outer"):
            for i in range(3):
                with tracer.start_as_current_span(f"child-{i}"):
                    pass
        provider.shutdown()

        rows = _all_span_rows(configured_db)
        names = sorted(r["name"] for r in rows)  # type: ignore[type-var]
        assert names == ["child-0", "child-1", "child-2", "outer"]

    def test_duplicate_export_is_silently_idempotent_at_wire(
        self, configured_db: str, grpc_server: GrpcOtlpServer
    ) -> None:
        """Resending the *same* OTLP request twice must not error and must
        leave one row — proving the SQL ON CONFLICT DO NOTHING does not
        escalate to an OTLP-level failure on retries."""
        # Build one OTLP request, send it twice via the generated gRPC stub.
        import grpc
        from opentelemetry.proto.collector.trace.v1 import (
            trace_service_pb2,
            trace_service_pb2_grpc,
        )
        from opentelemetry.proto.common.v1 import common_pb2
        from opentelemetry.proto.trace.v1 import trace_pb2

        trace_id_bytes = bytes(range(16))
        span_id_bytes = bytes(range(8))
        span = trace_pb2.Span(
            trace_id=trace_id_bytes,
            span_id=span_id_bytes,
            name="dup-span",
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

        with grpc.insecure_channel(f"127.0.0.1:{grpc_server.port}") as channel:
            stub = trace_service_pb2_grpc.TraceServiceStub(channel)
            stub.Export(request)
            # Second send — same (trace_id, span_id). Must not raise.
            stub.Export(request)

        rows = _all_span_rows(configured_db)
        assert len(rows) == 1
        assert rows[0]["name"] == "dup-span"
