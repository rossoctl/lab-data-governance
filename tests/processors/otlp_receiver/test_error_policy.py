"""Issue #8 acceptance tests: ADR-0003 SQLSTATE→OTLP error policy.

Each test class covers one ADR-0003 mapping row:

1. Connection-class failure → batch fails retryable (gRPC UNAVAILABLE / HTTP 503).
2. PK conflict on (trace_id, span_id) → OTLP success; spans_duplicate_total increments.
3. Integrity / too-large → span in rejected_spans; OTLP partial-success.
4. Unknown error class → batch fails retryable.
5. Pool acquire-timeout → kind=connection, retryable batch failure.

Tests induce errors by:
- Connection failure: reconfigure pool to an unreachable DSN.
- PK duplicate: send the same span twice; the second arrives as DO NOTHING.
- Integrity violation: send a span whose trace_id is too long to fit the column
  (right-truncation / 22001) by patching write_span to raise the error.
- Other error: patch write_span to raise psycopg.ProgrammingError.
- Pool timeout: reconfigure pool with max_size=1, timeout=0.001 and hold the
  only connection while sending a span.

All tests use the real Postgres via the harness; no mocking of the DB layer
itself — consistent with ADR-0005.  For errors we cannot induce via SQL
alone (integrity on a valid-looking span) we monkeypatch write_span.
"""

from __future__ import annotations

import threading
import unittest.mock as mock

import grpc
import httpx
import psycopg
import psycopg.errors
import pytest

from opentelemetry.proto.collector.trace.v1 import (
    trace_service_pb2,
    trace_service_pb2_grpc,
)
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.resource.v1 import resource_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

from data_governance import db
from data_governance.processors.otlp_receiver import metrics as _metrics
from tests.harness.fixtures import OtlpHarness


# ---------------------------------------------------------------------------
# Protobuf helpers (copied from test_finalization for self-containment)
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


def _grpc_export(
    endpoint: str,
    request: trace_service_pb2.ExportTraceServiceRequest,
) -> tuple[trace_service_pb2.ExportTraceServiceResponse, grpc.RpcError | None]:
    """Send request, return (response, rpc_error). rpc_error is None on success."""
    with grpc.insecure_channel(endpoint) as channel:
        stub = trace_service_pb2_grpc.TraceServiceStub(channel)
        try:
            resp = stub.Export(request)
            return resp, None
        except grpc.RpcError as exc:
            return trace_service_pb2.ExportTraceServiceResponse(), exc


# ---------------------------------------------------------------------------
# Helper to read rejected_spans table
# ---------------------------------------------------------------------------


def _rejected_rows(dsn: str) -> list[dict]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT trace_id, span_id, error_message FROM rejected_spans ORDER BY id"
        ).fetchall()
    return [{"trace_id": r[0], "span_id": r[1], "error_message": r[2]} for r in rows]


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


_TRACE_A = "aa" * 16
_TRACE_B = "bb" * 16


class TestConnectionClassFailure:
    """ADR-0003 row 1: connection-class error → retryable batch failure."""

    def test_grpc_returns_unavailable_on_bad_dsn(
        self, otlp_harness: OtlpHarness
    ) -> None:
        # Reset metrics so we get a clean read for this test.
        registry = _metrics.make_registry()

        db.close_pool()
        db.configure("postgresql://nobody:badpass@127.0.0.1:9999/noexist", timeout=1)
        try:
            req = _make_request(_TRACE_A, "01" * 8, name="conn-fail-grpc")
            _, rpc_err = _grpc_export(otlp_harness.grpc_endpoint, req)
            assert rpc_err is not None, "Expected RPC error but got none"
            assert rpc_err.code() == grpc.StatusCode.UNAVAILABLE
        finally:
            db.close_pool()
            db.configure(otlp_harness.dsn)

        count = _metrics.db_errors_total.labels(kind="connection")._value.get()
        assert count >= 1

    def test_http_returns_503_on_bad_dsn(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        db.close_pool()
        db.configure("postgresql://nobody:badpass@127.0.0.1:9999/noexist", timeout=1)
        try:
            req = _make_request(_TRACE_A, "02" * 8, name="conn-fail-http")
            body = req.SerializeToString()
            resp = httpx.post(
                f"{otlp_harness.http_endpoint}/v1/traces",
                content=body,
                headers={"Content-Type": "application/x-protobuf"},
            )
            assert resp.status_code == 503
        finally:
            db.close_pool()
            db.configure(otlp_harness.dsn)

        count = _metrics.db_errors_total.labels(kind="connection")._value.get()
        assert count >= 1


class TestPkDuplicate:
    """ADR-0003 row 2: PK conflict on (trace_id, span_id) → OTLP success."""

    def test_second_send_returns_success_not_error(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        req = _make_request(_TRACE_B, "03" * 8, name="dup-span")
        # First send: inserts.
        _, rpc_err = _grpc_export(otlp_harness.grpc_endpoint, req)
        assert rpc_err is None

        # Second send: duplicate — should still succeed (OTLP success, no error).
        resp, rpc_err = _grpc_export(otlp_harness.grpc_endpoint, req)
        assert rpc_err is None
        assert not resp.HasField("partial_success") or resp.partial_success.rejected_spans == 0

    def test_duplicate_increments_spans_duplicate_total(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        req = _make_request(_TRACE_B, "04" * 8, name="dup-metric")
        _grpc_export(otlp_harness.grpc_endpoint, req)  # insert
        _grpc_export(otlp_harness.grpc_endpoint, req)  # duplicate

        dup_count = _metrics.spans_duplicate_total._value.get()
        assert dup_count >= 1

    def test_duplicate_does_not_increment_db_errors_total(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        req = _make_request(_TRACE_B, "05" * 8, name="dup-no-err")
        _grpc_export(otlp_harness.grpc_endpoint, req)
        _grpc_export(otlp_harness.grpc_endpoint, req)

        # No db_errors_total for any kind should have been incremented.
        for kind in ("connection", "integrity", "other"):
            count = _metrics.db_errors_total.labels(kind=kind)._value.get()
            assert count == 0, f"db_errors_total{{kind={kind}}} should be 0"


class TestIntegrityFailure:
    """ADR-0003 row 3: integrity violation → partial-success + rejected_spans."""

    def test_grpc_partial_success_on_integrity_error(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        from data_governance.processors.otlp_receiver import write_span as ws

        integrity_exc = psycopg.errors.NotNullViolation("simulated integrity")
        integrity_exc.sqlstate = "23502"  # type: ignore[attr-defined]

        # Batch of two spans: first fails with integrity, second succeeds.
        span_id_bad = "06" * 8
        span_id_good = "07" * 8

        call_count = {"n": 0}
        original_write_span = ws.write_span

        def patched_write_span(span):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise integrity_exc
            return original_write_span(span)

        # Build a request with two spans.
        span_bad = trace_pb2.Span(
            trace_id=bytes.fromhex(_TRACE_A),
            span_id=bytes.fromhex(span_id_bad),
            name="bad-span",
            start_time_unix_nano=1_700_000_000_000_000_000,
        )
        span_good = trace_pb2.Span(
            trace_id=bytes.fromhex(_TRACE_A),
            span_id=bytes.fromhex(span_id_good),
            name="good-span",
            start_time_unix_nano=1_700_000_000_000_000_000,
        )
        resource = resource_pb2.Resource(
            attributes=[
                common_pb2.KeyValue(
                    key="service.name",
                    value=common_pb2.AnyValue(string_value="test-svc"),
                )
            ]
        )
        scope_spans = trace_pb2.ScopeSpans(spans=[span_bad, span_good])
        rs = trace_pb2.ResourceSpans(resource=resource, scope_spans=[scope_spans])
        req = trace_service_pb2.ExportTraceServiceRequest(resource_spans=[rs])

        import data_governance.processors.otlp_receiver.server as srv

        with mock.patch.object(srv, "write_span", side_effect=patched_write_span):
            resp, rpc_err = _grpc_export(otlp_harness.grpc_endpoint, req)

        # Should be OTLP success (no gRPC error), with partial_success set.
        assert rpc_err is None
        assert resp.partial_success.rejected_spans == 1

    def test_integrity_increments_db_errors_total(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        import data_governance.processors.otlp_receiver.server as srv

        integrity_exc = psycopg.errors.ForeignKeyViolation("simulated fk")
        integrity_exc.sqlstate = "23503"  # type: ignore[attr-defined]

        req = _make_request(_TRACE_A, "08" * 8, name="fk-fail")

        with mock.patch.object(srv, "write_span", side_effect=integrity_exc):
            _grpc_export(otlp_harness.grpc_endpoint, req)

        count = _metrics.db_errors_total.labels(kind="integrity")._value.get()
        assert count >= 1

    def test_neighbour_span_commits_when_one_span_fails(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        import data_governance.processors.otlp_receiver.server as srv
        from data_governance.processors.otlp_receiver.write_span import write_span as real_write_span

        integrity_exc = psycopg.errors.NotNullViolation("simulated")
        integrity_exc.sqlstate = "23502"  # type: ignore[attr-defined]

        span_id_bad = "09" * 8
        span_id_good = "0a" * 8

        def patched(span):
            if span.span_id == span_id_bad:
                raise integrity_exc
            return real_write_span(span)

        span_bad = trace_pb2.Span(
            trace_id=bytes.fromhex(_TRACE_A),
            span_id=bytes.fromhex(span_id_bad),
            name="bad",
            start_time_unix_nano=1_700_000_000_000_000_000,
        )
        span_good = trace_pb2.Span(
            trace_id=bytes.fromhex(_TRACE_A),
            span_id=bytes.fromhex(span_id_good),
            name="good",
            start_time_unix_nano=1_700_000_000_000_000_000,
        )
        resource = resource_pb2.Resource(
            attributes=[
                common_pb2.KeyValue(
                    key="service.name",
                    value=common_pb2.AnyValue(string_value="test-svc"),
                )
            ]
        )
        scope_spans = trace_pb2.ScopeSpans(spans=[span_bad, span_good])
        rs = trace_pb2.ResourceSpans(resource=resource, scope_spans=[scope_spans])
        req = trace_service_pb2.ExportTraceServiceRequest(resource_spans=[rs])

        with mock.patch.object(srv, "write_span", side_effect=patched):
            _grpc_export(otlp_harness.grpc_endpoint, req)

        # The good span must have committed.
        good_row = otlp_harness.rows.get(_TRACE_A, span_id_good)
        assert good_row is not None, "Good neighbour span should have been written"

    def test_rejected_span_written_to_rejected_spans_table(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        import data_governance.processors.otlp_receiver.server as srv

        integrity_exc = psycopg.errors.NotNullViolation("simulated nn")
        integrity_exc.sqlstate = "23502"  # type: ignore[attr-defined]

        span_id = "0b" * 8
        req = _make_request(_TRACE_A, span_id, name="nn-fail")

        with mock.patch.object(srv, "write_span", side_effect=integrity_exc):
            _grpc_export(otlp_harness.grpc_endpoint, req)

        rows = _rejected_rows(otlp_harness.dsn)
        assert any(r["span_id"] == span_id for r in rows), (
            f"Expected {span_id} in rejected_spans, got: {rows}"
        )


class TestOtherErrorClass:
    """ADR-0003 row 4: unknown error → retryable batch failure."""

    def test_grpc_returns_unavailable_on_programming_error(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        import data_governance.processors.otlp_receiver.server as srv

        other_exc = psycopg.ProgrammingError("column does not exist")
        other_exc.sqlstate = "42703"  # type: ignore[attr-defined]

        req = _make_request(_TRACE_A, "0c" * 8, name="prog-err")

        with mock.patch.object(srv, "write_span", side_effect=other_exc):
            _, rpc_err = _grpc_export(otlp_harness.grpc_endpoint, req)

        assert rpc_err is not None
        assert rpc_err.code() == grpc.StatusCode.UNAVAILABLE

    def test_other_increments_db_errors_total(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        import data_governance.processors.otlp_receiver.server as srv

        other_exc = psycopg.ProgrammingError("syntax error")
        other_exc.sqlstate = "42601"  # type: ignore[attr-defined]

        req = _make_request(_TRACE_A, "0d" * 8, name="syntax-err")

        with mock.patch.object(srv, "write_span", side_effect=other_exc):
            _grpc_export(otlp_harness.grpc_endpoint, req)

        count = _metrics.db_errors_total.labels(kind="other")._value.get()
        assert count >= 1


class TestPoolTimeout:
    """ADR-0003 pool exhaustion: maps to kind=connection, retryable failure."""

    def test_pool_timeout_maps_to_connection_class(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _metrics.make_registry()

        # Reconfigure pool to size 1 with near-zero timeout, then hold the one
        # connection via a background thread so the next acquire times out.
        db.close_pool()
        db.configure(otlp_harness.dsn, max_size=1, timeout=0.05)

        acquired = threading.Event()
        release = threading.Event()

        def hold_connection():
            with db.transaction() as tx:
                tx.execute("SELECT pg_sleep(0)")
                acquired.set()
                release.wait(timeout=5)

        holder = threading.Thread(target=hold_connection, daemon=True)
        holder.start()
        acquired.wait(timeout=3)

        try:
            req = _make_request(_TRACE_A, "0e" * 8, name="pool-timeout")
            _, rpc_err = _grpc_export(otlp_harness.grpc_endpoint, req)
            assert rpc_err is not None
            assert rpc_err.code() == grpc.StatusCode.UNAVAILABLE
        finally:
            release.set()
            holder.join(timeout=3)
            db.close_pool()
            db.configure(otlp_harness.dsn)

        count = _metrics.db_errors_total.labels(kind="connection")._value.get()
        assert count >= 1
