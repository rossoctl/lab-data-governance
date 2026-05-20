"""Function-scoped harness API: OTLP client + row inspector + per-test reset.

The :class:`OtlpHarness` bundles the three things every harness consumer
needs:

- :attr:`OtlpHarness.client` — send spans via OTLP gRPC or HTTP/protobuf.
- :attr:`OtlpHarness.rows` — inspect ``spans`` rows by
  ``(trace_id, span_id)`` or by ``trace_id``.
- :attr:`OtlpHarness.dsn` — libpq DSN of the harness database, for tests
  that need to drop down to raw SQL (e.g. asserting on
  ``blocked_span_counts``).

The ``otlp_harness`` fixture is **function-scoped**. On entry it
re-configures the Layer 1 pool against the harness DSN (defending against
unrelated fixtures having stomped on the singleton) and truncates the
``spans`` and ``blocked_span_counts`` tables — cheaper than re-running
Alembic per test.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest

import grpc
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as _GrpcExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as _HttpExporter,
)
from opentelemetry.proto.collector.trace.v1 import (
    trace_service_pb2,
    trace_service_pb2_grpc,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from data_governance.processors.otlp_receiver.server import (
    GrpcOtlpServer,
    HttpOtlpServer,
)


# Columns the row-inspector returns. Kept narrow on purpose — issue #5 only
# needs to assert "OTLP request in, correct rows out" round-trip semantics;
# tests that need the full spans schema reach for ``otlp_harness.dsn`` and
# the inspector's :meth:`SpanRowInspector.fetch_all_columns` escape hatch.
_DEFAULT_COLUMNS: tuple[str, ...] = (
    "trace_id",
    "span_id",
    "parent_id",
    "name",
    "started_at",
    "attributes",
    "seq",
    "arrival_seq",
    "observed_at",
)


# --- OTLP client -------------------------------------------------------------


class OtlpClient:
    """Sends OTLP spans via gRPC and HTTP/protobuf to the harness servers.

    The client builds OTLP requests through the canonical OpenTelemetry SDK
    exporters — never hand-rolled protobuf bytes — so what hits the
    receiver is identical in shape to what an SDK-instrumented service
    would emit.
    """

    def __init__(self, *, grpc_endpoint: str, http_endpoint: str) -> None:
        self._grpc_endpoint = grpc_endpoint
        self._http_endpoint = http_endpoint

    def send_grpc_span(
        self,
        *,
        name: str,
        trace_id: str | None = None,
        service_name: str = "harness-grpc",
        attributes: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Send one span via OTLP gRPC. Returns ``(trace_id, span_id)`` hex.

        ``trace_id`` is honoured when supplied — passing the same value to
        successive calls groups the spans into one trace, which is what
        :meth:`SpanRowInspector.by_trace_id` tests rely on.
        """
        return self._send_via_provider(
            name=name,
            trace_id=trace_id,
            service_name=service_name,
            attributes=attributes,
            exporter=_GrpcExporter(endpoint=self._grpc_endpoint, insecure=True),
        )

    def send_grpc_request(
        self,
        request: trace_service_pb2.ExportTraceServiceRequest,
    ) -> None:
        """Send a hand-built ``ExportTraceServiceRequest`` via OTLP gRPC.

        Escape hatch for tests that need precise control over OTLP fields
        the SDK exporter abstracts away — span ``kind``, ``Status`` (including
        ``UNSET``), ``events``, ``links``, ``InstrumentationScope`` shape,
        and the envelope-level ``trace_state`` / ``flags`` /
        ``dropped_*_count`` fields. Issue #6's NULL-vs-empty acceptance
        criteria are easiest to assert when the wire payload is built
        directly rather than going through the OTel SDK's defaults.
        """
        with grpc.insecure_channel(self._grpc_endpoint) as channel:
            stub = trace_service_pb2_grpc.TraceServiceStub(channel)
            stub.Export(request)

    def send_http_span(
        self,
        *,
        name: str,
        trace_id: str | None = None,
        service_name: str = "harness-http",
        attributes: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Send one span via OTLP HTTP/protobuf. Returns ``(trace_id, span_id)`` hex."""
        return self._send_via_provider(
            name=name,
            trace_id=trace_id,
            service_name=service_name,
            attributes=attributes,
            exporter=_HttpExporter(endpoint=f"{self._http_endpoint}/v1/traces"),
        )

    @staticmethod
    def _send_via_provider(
        *,
        name: str,
        trace_id: str | None,
        service_name: str,
        attributes: dict[str, Any] | None,
        exporter: Any,
    ) -> tuple[str, str]:
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name})
        )
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        try:
            tracer = provider.get_tracer("tests.harness")
            kwargs: dict[str, Any] = {}
            if attributes is not None:
                kwargs["attributes"] = attributes
            # If the caller pinned a trace_id, splice it into a fresh
            # SpanContext so successive sends share the same trace.
            ctx = None
            if trace_id is not None:
                from opentelemetry.trace import (
                    NonRecordingSpan,
                    SpanContext,
                    TraceFlags,
                    set_span_in_context,
                )

                # Splicing a synthetic remote-parent SpanContext is the canonical OTel
                # cross-process propagation pattern — there's no public SDK API for
                # "give me a span with this exact trace_id".
                fake_parent_ctx = SpanContext(
                    trace_id=int(trace_id, 16),
                    span_id=1,
                    is_remote=True,
                    trace_flags=TraceFlags(0x01),
                )
                ctx = set_span_in_context(NonRecordingSpan(fake_parent_ctx))

            with tracer.start_as_current_span(name, context=ctx, **kwargs) as span:
                span_context = span.get_span_context()
                returned_trace_id = f"{span_context.trace_id:032x}"
                returned_span_id = f"{span_context.span_id:016x}"
        finally:
            provider.shutdown()

        return returned_trace_id, returned_span_id


# --- Row inspector -----------------------------------------------------------


class SpanRowInspector:
    """Reads back rows from the harness ``spans`` table.

    Returns dict-shaped rows so test assertions can use column names rather
    than positional indices. Columns returned are the
    ``write_span``-populated set (issue #3); future slices that promote
    further columns extend the inspector or use
    :meth:`fetch_all_columns` directly.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def get(self, trace_id: str, span_id: str) -> dict[str, Any] | None:
        """Return the row for ``(trace_id, span_id)`` or ``None`` if absent."""
        sql = (
            f"SELECT {', '.join(_DEFAULT_COLUMNS)} FROM spans "
            "WHERE trace_id = %s AND span_id = %s"
        )
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(sql, (trace_id, span_id)).fetchone()
        if row is None:
            return None
        return dict(zip(_DEFAULT_COLUMNS, row))

    def by_trace_id(self, trace_id: str) -> list[dict[str, Any]]:
        """Return every row whose ``trace_id`` matches, ordered by ``seq``."""
        sql = (
            f"SELECT {', '.join(_DEFAULT_COLUMNS)} FROM spans "
            "WHERE trace_id = %s ORDER BY seq"
        )
        with psycopg.connect(self._dsn) as conn:
            rows = conn.execute(sql, (trace_id,)).fetchall()
        return [dict(zip(_DEFAULT_COLUMNS, r)) for r in rows]

    def row_count(self) -> int:
        """Return the total number of rows in ``spans``."""
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute("SELECT COUNT(*) FROM spans").fetchone()
        assert row is not None  # COUNT(*) always returns one row
        return int(row[0])

    def fetch_all_columns(self, trace_id: str, span_id: str) -> dict[str, Any] | None:
        """Escape hatch: return every column on the row, keyed by name.

        Used by tests that need columns beyond the issue-#3 write set
        (e.g. ``ended_at`` for ADR-0004 finalization tests in later slices).
        """
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM spans WHERE trace_id = %s AND span_id = %s",
                    (trace_id, span_id),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                colnames = [d.name for d in cur.description]
        return dict(zip(colnames, row))

    def fetch_one_value(
        self, sql: str, params: tuple[Any, ...] | None = None
    ) -> Any | None:
        """Run a single-column, single-row SELECT and return the value.

        Generic escape hatch for tests that need to assert on schema metadata
        (e.g. ``information_schema.columns``) or other DB-level facts that
        live outside the ``spans`` table. Routing such queries through the
        inspector keeps test files homogeneous in how they reach the DB —
        tests should prefer this over opening their own raw
        ``psycopg.connect`` against ``otlp_harness.dsn``.
        """
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(sql, params or ()).fetchone()
        if row is None:
            return None
        return row[0]


# --- OtlpHarness bundle ------------------------------------------------------


@dataclass(frozen=True)
class OtlpHarness:
    """The function-scoped harness handle yielded by :func:`otlp_harness`.

    Attributes:
        client: :class:`OtlpClient` for sending OTLP requests.
        rows: :class:`SpanRowInspector` for reading back ``spans`` rows.
        dsn: libpq DSN of the harness database (escape hatch for tests
            that need raw SQL, e.g. asserting on ``blocked_span_counts``).
        grpc_endpoint: ``host:port`` of the OTLP gRPC server.
        http_endpoint: Base URL of the OTLP HTTP/protobuf server (no
            ``/v1/traces`` suffix).
        servers: The underlying server lifecycle handles, in case a test
            needs to restart or interrogate them. Provided as an escape
            hatch — most tests should not touch this.
    """

    client: OtlpClient
    rows: SpanRowInspector
    dsn: str
    grpc_endpoint: str
    http_endpoint: str
    servers: tuple[GrpcOtlpServer, HttpOtlpServer]


# --- the function-scoped fixture --------------------------------------------


@pytest.fixture()
def otlp_harness(
    _harness_pg_dsn: str,
    _harness_servers: tuple[GrpcOtlpServer, HttpOtlpServer],
    _harness_grpc_endpoint: str,
    _harness_http_endpoint: str,
) -> Iterator[OtlpHarness]:
    """Per-test OTLP harness: pool reconfigured + tables truncated, then yield.

    Re-configures the Layer 1 ``data_governance.db`` singleton against the
    session-scoped harness DSN. This defends against earlier tests in the
    session having *closed* the singleton pool — the legacy
    ``configured_db`` fixture's teardown calls ``db.close_pool()``, which
    leaves the global pool as ``None``. Without the reopen here, the next
    receiver write would hit the "data_governance.db is not configured"
    ``RuntimeError`` from ``_require_pool``. The reconfigure is correctness
    insurance, not just hygiene.

    Truncates ``spans`` and ``blocked_span_counts`` so the test starts
    against an empty schema. ``RESTART IDENTITY`` resets ``spans_seq`` so
    consecutive tests see ``seq`` starting from 1, which makes assertions
    on absolute ``seq`` values stable across runs.
    """
    from data_governance import db

    # Reconfigure the pool against the harness DSN every test. Cheap (the
    # pool's open=True warm-up is one connection) and defensive.
    db.close_pool()
    db.configure(_harness_pg_dsn)

    # Truncate state tables; re-running migrations would be far more
    # expensive than this (issue #5 acceptance criterion).
    with psycopg.connect(_harness_pg_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE TABLE spans, blocked_span_counts "
                "RESTART IDENTITY CASCADE"
            )
        conn.commit()

    grpc_server, http_server = _harness_servers
    yield OtlpHarness(
        client=OtlpClient(
            grpc_endpoint=_harness_grpc_endpoint,
            http_endpoint=_harness_http_endpoint,
        ),
        rows=SpanRowInspector(_harness_pg_dsn),
        dsn=_harness_pg_dsn,
        grpc_endpoint=_harness_grpc_endpoint,
        http_endpoint=_harness_http_endpoint,
        servers=(grpc_server, http_server),
    )


@pytest.fixture()
def otlp_client(otlp_harness: OtlpHarness) -> OtlpClient:
    """Convenience alias for ``otlp_harness.client``."""
    return otlp_harness.client


@pytest.fixture()
def span_rows(otlp_harness: OtlpHarness) -> SpanRowInspector:
    """Convenience alias for ``otlp_harness.rows``."""
    return otlp_harness.rows
