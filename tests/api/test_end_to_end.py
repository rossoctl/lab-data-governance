"""End-to-end test for issue #4:

Send an OTLP span via gRPC → read it back through the recent-traces feed
(``GET /api/traces``, ADR-0018) → assert the span is the listing root of its
trace.
"""

from __future__ import annotations

import httpx

from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from data_governance.api import SpansApiServer
from data_governance.processors.otlp_receiver.server import GrpcOtlpServer
from tests.api.conftest import _free_port, _wait_port


def test_otlp_span_visible_via_traces_feed(configured_db: str) -> None:
    """Send span via OTLP gRPC, read it back through GET /api/traces."""
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

        resp = httpx.get(f"http://127.0.0.1:{api_port}/api/traces")
        assert resp.status_code == 200

        names = [e["listing_root"]["name"] for e in resp.json()["traces"]]
        assert "e2e-read-path" in names

    finally:
        grpc_server.stop(grace=0.5)
        api_server.stop(grace=1.0)
