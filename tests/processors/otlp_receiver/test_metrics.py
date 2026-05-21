"""Issue #8 acceptance tests: Prometheus metrics surface.

Each test class covers one required metric from PROJECT.md §5.1 / the
issue body. Every test calls ``_metrics.make_registry()`` at the start to
replace the module-global metric objects with fresh ones backed by a new
``CollectorRegistry``, so counter values are always relative to that test.

Tests that verify ``GET /metrics`` HTTP output use the session-scoped
``MetricsServer`` wired into the harness via ``otlp_harness.metrics_endpoint``.
"""

from __future__ import annotations

import httpx
import pytest

from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.resource.v1 import resource_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

from data_governance.processors.otlp_receiver import metrics as _metrics
from tests.harness.fixtures import OtlpHarness


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    trace_id_hex: str,
    span_id_hex: str,
    name: str = "test-span",
    end_time_unix_nano: int = 0,
) -> trace_service_pb2.ExportTraceServiceRequest:
    span = trace_pb2.Span(
        trace_id=bytes.fromhex(trace_id_hex),
        span_id=bytes.fromhex(span_id_hex),
        name=name,
        start_time_unix_nano=1_700_000_000_000_000_000,
        end_time_unix_nano=end_time_unix_nano,
    )
    scope_spans = trace_pb2.ScopeSpans(spans=[span])
    resource = resource_pb2.Resource(
        attributes=[
            common_pb2.KeyValue(
                key="service.name",
                value=common_pb2.AnyValue(string_value="test-svc"),
            )
        ]
    )
    rs = trace_pb2.ResourceSpans(resource=resource, scope_spans=[scope_spans])
    return trace_service_pb2.ExportTraceServiceRequest(resource_spans=[rs])


_TRACE = "cc" * 16


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSpansReceivedTotal:
    """spans_received_total{transport} increments per accepted OTLP request."""

    def test_grpc_transport_increments(self, otlp_harness: OtlpHarness) -> None:
        _metrics.make_registry()

        before = _metrics.spans_received_total.labels(transport="grpc")._value.get()
        otlp_harness.client.send_grpc_span(name="recv-grpc")
        after = _metrics.spans_received_total.labels(transport="grpc")._value.get()

        assert after == before + 1

    def test_http_transport_increments(self, otlp_harness: OtlpHarness) -> None:
        _metrics.make_registry()

        before = _metrics.spans_received_total.labels(transport="http")._value.get()
        otlp_harness.client.send_http_span(name="recv-http")
        after = _metrics.spans_received_total.labels(transport="http")._value.get()

        assert after == before + 1

    def test_grpc_and_http_are_independently_labelled(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        otlp_harness.client.send_grpc_span(name="label-grpc")
        otlp_harness.client.send_http_span(name="label-http")

        grpc_count = _metrics.spans_received_total.labels(transport="grpc")._value.get()
        http_count = _metrics.spans_received_total.labels(transport="http")._value.get()

        assert grpc_count == 1
        assert http_count == 1


class TestSpansInsertedTotal:
    """spans_inserted_total increments on new spans."""

    def test_new_span_increments(self, otlp_harness: OtlpHarness) -> None:
        _metrics.make_registry()

        before = _metrics.spans_inserted_total._value.get()
        otlp_harness.client.send_grpc_span(name="insert-grpc")
        after = _metrics.spans_inserted_total._value.get()

        assert after == before + 1


class TestSpansFinalizedTotal:
    """spans_finalized_total increments on finalization (partial→completion)."""

    def test_finalization_increments(self, otlp_harness: OtlpHarness) -> None:
        _metrics.make_registry()

        span_id = "f1" * 8

        # Send partial span (ended_at IS NULL).
        partial_req = _make_request(_TRACE, span_id, end_time_unix_nano=0)
        otlp_harness.client.send_grpc_request(partial_req)

        before = _metrics.spans_finalized_total._value.get()

        # Send completion (ended_at IS NOT NULL).
        completion_req = _make_request(
            _TRACE, span_id, end_time_unix_nano=1_700_000_001_000_000_000
        )
        otlp_harness.client.send_grpc_request(completion_req)

        after = _metrics.spans_finalized_total._value.get()
        assert after == before + 1


class TestSpansDuplicateTotal:
    """spans_duplicate_total increments on PK-duplicate (DO NOTHING) sends."""

    def test_second_partial_is_duplicate(self, otlp_harness: OtlpHarness) -> None:
        _metrics.make_registry()

        span_id = "d1" * 8
        req = _make_request(_TRACE, span_id)

        otlp_harness.client.send_grpc_request(req)  # INSERT
        before = _metrics.spans_duplicate_total._value.get()
        otlp_harness.client.send_grpc_request(req)  # DO NOTHING
        after = _metrics.spans_duplicate_total._value.get()

        assert after == before + 1


def _histogram_count(histogram) -> float:
    """Return the _count sample value from a prometheus_client Histogram."""
    for sample in histogram._child_samples():
        if sample.name == "_count":
            return sample.value
    return 0.0


class TestSpanInsertDurationSeconds:
    """span_insert_duration_seconds records observations on writes."""

    def test_histogram_has_observations_after_write(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        otlp_harness.client.send_grpc_span(name="duration-test")

        count = _histogram_count(_metrics.span_insert_duration_seconds)
        assert count >= 1

    def test_observed_duration_is_positive(self, otlp_harness: OtlpHarness) -> None:
        _metrics.make_registry()

        otlp_harness.client.send_grpc_span(name="duration-positive")

        total = _metrics.span_insert_duration_seconds._sum.get()
        assert total > 0


class TestSpanRowBytes:
    """span_row_bytes records observations on writes."""

    def test_histogram_has_observations_after_write(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        otlp_harness.client.send_grpc_span(name="bytes-test")

        count = _histogram_count(_metrics.span_row_bytes)
        assert count >= 1


class TestGetMetricsEndpoint:
    """GET /metrics returns Prometheus text exposition format."""

    def test_returns_200(self, otlp_harness: OtlpHarness) -> None:
        resp = httpx.get(f"{otlp_harness.metrics_endpoint}/metrics")
        assert resp.status_code == 200

    def test_content_type_is_prometheus(self, otlp_harness: OtlpHarness) -> None:
        resp = httpx.get(f"{otlp_harness.metrics_endpoint}/metrics")
        assert "text/plain" in resp.headers["content-type"]

    def test_required_metric_names_present(self, otlp_harness: OtlpHarness) -> None:
        # Send a span so the registry has been populated.
        otlp_harness.client.send_grpc_span(name="metrics-check")

        resp = httpx.get(f"{otlp_harness.metrics_endpoint}/metrics")
        body = resp.text

        required = [
            "spans_received_total",
            "spans_inserted_total",
            "spans_finalized_total",
            "spans_duplicate_total",
            "span_insert_duration_seconds",
            "span_row_bytes",
            "db_errors_total",
        ]
        for name in required:
            assert name in body, f"Expected '{name}' in /metrics output"
