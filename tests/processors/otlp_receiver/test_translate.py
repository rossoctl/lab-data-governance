"""Tests for the OTLP → SpanRow translation layer — issue #19.

Issue #19 tightens the ``parent_span_id`` handling in ``_span_to_row`` so
that an all-zero 8-byte value (used by some non-conformant SDKs in place of
the empty-bytes sentinel) is treated as an absent parent and translates to
``parent_id=None``, not the hex string ``"0000000000000000"``.

Tests are split into two layers:

1. Unit tests on ``request_to_span_rows`` / ``_span_to_row`` directly —
   fast, no I/O, cover the mapping table exhaustively.
2. Wire-level integration test — sends a span with all-zero parent bytes
   through the real OTLP gRPC receiver and asserts ``parent_id IS NULL``
   in the Postgres row (per the AC).
"""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator

import grpc
import psycopg
import pytest

from opentelemetry.proto.collector.trace.v1 import (
    trace_service_pb2,
    trace_service_pb2_grpc,
)
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

from data_governance.processors.otlp_receiver.server import GrpcOtlpServer
from data_governance.processors.otlp_receiver.translate import request_to_span_rows
from data_governance.processors.otlp_receiver.write_span import SpanRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRACE_ID = bytes(range(16))      # 16 non-zero bytes → valid trace id
_SPAN_ID = bytes(range(8, 16))    # 8 non-zero bytes  → valid span id
_ZERO_PARENT = b"\x00" * 8       # all-zero parent — the subject of issue #19
_REAL_PARENT = bytes(range(1, 9)) # 8 non-zero bytes  → genuine parent id


def _minimal_request(
    *,
    parent_span_id: bytes = b"",
) -> trace_service_pb2.ExportTraceServiceRequest:
    """Build the smallest legal OTLP request with the given parent_span_id."""
    span = trace_pb2.Span(
        trace_id=_TRACE_ID,
        span_id=_SPAN_ID,
        parent_span_id=parent_span_id,
        name="test-span",
        start_time_unix_nano=1_700_000_000_000_000_000,
        end_time_unix_nano=1_700_000_001_000_000_000,
    )
    return trace_service_pb2.ExportTraceServiceRequest(
        resource_spans=[
            trace_pb2.ResourceSpans(
                scope_spans=[trace_pb2.ScopeSpans(spans=[span])]
            )
        ]
    )


def _single_row(request: trace_service_pb2.ExportTraceServiceRequest) -> SpanRow:
    rows = list(request_to_span_rows(request))
    assert len(rows) == 1
    return rows[0]


# ---------------------------------------------------------------------------
# Unit tests — request_to_span_rows (no I/O)
# ---------------------------------------------------------------------------


class TestParentIdTranslation:
    def test_empty_bytes_gives_none(self):
        """Conformant SDK: empty parent_span_id → parent_id=None."""
        row = _single_row(_minimal_request(parent_span_id=b""))
        assert row.parent_id is None

    def test_all_zero_bytes_gives_none(self):
        """Non-conformant SDK: all-zero parent_span_id → parent_id=None.

        This is the core fix from issue #19. Before the fix, b'\\x00'*8
        was truthy and was hex-encoded to '0000000000000000' — a structurally
        valid but semantically invalid parent reference.
        """
        row = _single_row(_minimal_request(parent_span_id=_ZERO_PARENT))
        assert row.parent_id is None

    def test_nonzero_parent_bytes_are_hex_encoded(self):
        """A genuine non-null, non-zero parent_span_id is preserved as hex."""
        row = _single_row(_minimal_request(parent_span_id=_REAL_PARENT))
        assert row.parent_id == _REAL_PARENT.hex()

    def test_nonzero_parent_is_16_hex_chars(self):
        """8 bytes always hex-encode to exactly 16 lowercase hex characters."""
        row = _single_row(_minimal_request(parent_span_id=_REAL_PARENT))
        assert isinstance(row.parent_id, str)
        assert len(row.parent_id) == 16

    def test_single_nonzero_byte_not_treated_as_absent(self):
        """Exactly one non-zero byte must NOT be treated as absent parent.
        Only all-zero is the sentinel; partial-zero is a real parent id."""
        one_nonzero = b"\x00" * 7 + b"\x01"
        row = _single_row(_minimal_request(parent_span_id=one_nonzero))
        assert row.parent_id == one_nonzero.hex()

    def test_all_zero_does_not_produce_zero_hex_string(self):
        """Negative: the old (broken) behaviour would yield this hex string.
        Confirm the fix means this value never appears in parent_id."""
        row = _single_row(_minimal_request(parent_span_id=_ZERO_PARENT))
        assert not isinstance(row.parent_id, str)


# ---------------------------------------------------------------------------
# Wire-level integration test — goes through the real gRPC receiver
# ---------------------------------------------------------------------------


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
    raise TimeoutError(f"{host}:{port} did not open within {timeout}s")


@pytest.fixture()
def grpc_server(configured_db: str) -> Iterator[GrpcOtlpServer]:
    port = _free_port()
    server = GrpcOtlpServer(host="127.0.0.1", port=port)
    server.start()
    try:
        _wait_port("127.0.0.1", port)
        yield server
    finally:
        server.stop(grace=0.5)


def _send_request(
    server: GrpcOtlpServer,
    request: trace_service_pb2.ExportTraceServiceRequest,
) -> None:
    with grpc.insecure_channel(f"127.0.0.1:{server.port}") as channel:
        stub = trace_service_pb2_grpc.TraceServiceStub(channel)
        resp = stub.Export(request)
    assert resp.partial_success.rejected_spans == 0, (
        f"server rejected {resp.partial_success.rejected_spans} span(s): "
        f"{resp.partial_success.error_message!r}"
    )


def test_all_zero_parent_stored_as_null_in_postgres(
    configured_db: str,
    grpc_server: GrpcOtlpServer,
) -> None:
    """Wire-level AC from issue #19.

    Send a span with an all-zero parent_span_id through the real OTLP gRPC
    receiver and assert that ``parent_id IS NULL`` in the Postgres row.
    This is the regression guard the issue explicitly requires.
    """
    _send_request(grpc_server, _minimal_request(parent_span_id=_ZERO_PARENT))

    with psycopg.connect(configured_db) as conn:
        rows = conn.execute(
            "SELECT parent_id FROM spans WHERE name = %s", ("test-span",)
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] is None, (
        f"expected parent_id=NULL for all-zero parent bytes, got {rows[0][0]!r}"
    )


def test_real_parent_stored_as_hex_in_postgres(
    configured_db: str,
    grpc_server: GrpcOtlpServer,
) -> None:
    """Wire-level sanity check: a genuine non-zero parent_span_id round-trips
    through the gRPC receiver to a 16-char hex string in Postgres."""
    span = trace_pb2.Span(
        trace_id=_TRACE_ID,
        span_id=_SPAN_ID,
        parent_span_id=_REAL_PARENT,
        name="child-span",
        start_time_unix_nano=1_700_000_000_000_000_000,
        end_time_unix_nano=1_700_000_001_000_000_000,
    )
    request = trace_service_pb2.ExportTraceServiceRequest(
        resource_spans=[
            trace_pb2.ResourceSpans(
                scope_spans=[trace_pb2.ScopeSpans(spans=[span])]
            )
        ]
    )
    _send_request(grpc_server, request)

    with psycopg.connect(configured_db) as conn:
        rows = conn.execute(
            "SELECT parent_id FROM spans WHERE name = %s", ("child-span",)
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == _REAL_PARENT.hex()
