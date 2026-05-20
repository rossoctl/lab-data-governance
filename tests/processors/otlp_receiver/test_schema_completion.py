"""Issue #6 acceptance: schema-completion via the harness from #5.

Each test sends a hand-built ``ExportTraceServiceRequest`` (so OTLP fields
the OTel SDK exporter abstracts away — ``SpanKind``, ``Status.Code``,
``events``, ``links``, the envelope's ``trace_state`` / ``flags`` /
``dropped_*`` counters — can be set to exactly the value the test asserts
on) and reads back the row via the harness inspector's
:meth:`SpanRowInspector.fetch_all_columns` escape hatch.

Tests are organised by acceptance bullet so the issue body's checklist
maps 1:1 onto failing tests in the red phase.
"""

from __future__ import annotations

import datetime as dt

from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.resource.v1 import resource_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

from tests.harness.fixtures import OtlpHarness


# --- protobuf builders -------------------------------------------------------


def _kv(key: str, value: common_pb2.AnyValue) -> common_pb2.KeyValue:
    return common_pb2.KeyValue(key=key, value=value)


def _string(s: str) -> common_pb2.AnyValue:
    return common_pb2.AnyValue(string_value=s)


def _int(i: int) -> common_pb2.AnyValue:
    return common_pb2.AnyValue(int_value=i)


def _double(d: float) -> common_pb2.AnyValue:
    return common_pb2.AnyValue(double_value=d)


def _bool(b: bool) -> common_pb2.AnyValue:
    return common_pb2.AnyValue(bool_value=b)


def _trace_id(hex_str: str) -> bytes:
    assert len(hex_str) == 32, hex_str
    return bytes.fromhex(hex_str)


def _span_id(hex_str: str) -> bytes:
    assert len(hex_str) == 16, hex_str
    return bytes.fromhex(hex_str)


def _build_request(
    *,
    trace_id_hex: str,
    span_id_hex: str,
    name: str = "test-span",
    start_time_unix_nano: int = 1_700_000_000_000_000_000,
    end_time_unix_nano: int = 0,
    kind: int = trace_pb2.Span.SPAN_KIND_UNSPECIFIED,
    status_code: int = trace_pb2.Status.STATUS_CODE_UNSET,
    status_message: str = "",
    span_attributes: list[common_pb2.KeyValue] | None = None,
    events: list[trace_pb2.Span.Event] | None = None,
    links: list[trace_pb2.Span.Link] | None = None,
    resource_attributes: list[common_pb2.KeyValue] | None = None,
    scope_name: str = "",
    scope_version: str = "",
    scope_attributes: list[common_pb2.KeyValue] | None = None,
    include_scope: bool = True,
    trace_state: str = "",
    flags: int = 0,
    dropped_attributes_count: int = 0,
    dropped_events_count: int = 0,
    dropped_links_count: int = 0,
) -> trace_service_pb2.ExportTraceServiceRequest:
    span = trace_pb2.Span(
        trace_id=_trace_id(trace_id_hex),
        span_id=_span_id(span_id_hex),
        name=name,
        start_time_unix_nano=start_time_unix_nano,
        end_time_unix_nano=end_time_unix_nano,
        kind=kind,
        status=trace_pb2.Status(code=status_code, message=status_message),
        attributes=span_attributes or [],
        events=events or [],
        links=links or [],
        trace_state=trace_state,
        flags=flags,
        dropped_attributes_count=dropped_attributes_count,
        dropped_events_count=dropped_events_count,
        dropped_links_count=dropped_links_count,
    )
    if include_scope:
        scope = common_pb2.InstrumentationScope(
            name=scope_name,
            version=scope_version,
            attributes=scope_attributes or [],
        )
        scope_spans = trace_pb2.ScopeSpans(scope=scope, spans=[span])
    else:
        scope_spans = trace_pb2.ScopeSpans(spans=[span])
    resource = resource_pb2.Resource(attributes=resource_attributes or [])
    rs = trace_pb2.ResourceSpans(resource=resource, scope_spans=[scope_spans])
    return trace_service_pb2.ExportTraceServiceRequest(resource_spans=[rs])


# --- promoted-column tests ---------------------------------------------------


class TestKindIsPopulated:
    def _send_kind(
        self, harness: OtlpHarness, trace_hex: str, span_hex: str, kind: int
    ) -> dict:
        req = _build_request(
            trace_id_hex=trace_hex, span_id_hex=span_hex, kind=kind
        )
        harness.client.send_grpc_request(req)
        row = harness.rows.fetch_all_columns(trace_hex, span_hex)
        assert row is not None
        return row

    def test_kind_internal(self, otlp_harness: OtlpHarness) -> None:
        row = self._send_kind(
            otlp_harness,
            "aa" * 16,
            "01" * 8,
            trace_pb2.Span.SPAN_KIND_INTERNAL,
        )
        assert row["kind"] == "INTERNAL"

    def test_kind_server(self, otlp_harness: OtlpHarness) -> None:
        row = self._send_kind(
            otlp_harness,
            "aa" * 16,
            "02" * 8,
            trace_pb2.Span.SPAN_KIND_SERVER,
        )
        assert row["kind"] == "SERVER"

    def test_kind_client(self, otlp_harness: OtlpHarness) -> None:
        row = self._send_kind(
            otlp_harness,
            "aa" * 16,
            "03" * 8,
            trace_pb2.Span.SPAN_KIND_CLIENT,
        )
        assert row["kind"] == "CLIENT"

    def test_kind_producer(self, otlp_harness: OtlpHarness) -> None:
        row = self._send_kind(
            otlp_harness,
            "aa" * 16,
            "04" * 8,
            trace_pb2.Span.SPAN_KIND_PRODUCER,
        )
        assert row["kind"] == "PRODUCER"

    def test_kind_consumer(self, otlp_harness: OtlpHarness) -> None:
        row = self._send_kind(
            otlp_harness,
            "aa" * 16,
            "05" * 8,
            trace_pb2.Span.SPAN_KIND_CONSUMER,
        )
        assert row["kind"] == "CONSUMER"


class TestEndedAt:
    def test_ended_at_truncated_to_microseconds(
        self, otlp_harness: OtlpHarness
    ) -> None:
        # 1700000000.123456789s — 789 ns must be truncated, leaving 123456 us.
        end_ns = 1_700_000_000_123_456_789
        req = _build_request(
            trace_id_hex="bb" * 16,
            span_id_hex="11" * 8,
            end_time_unix_nano=end_ns,
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("bb" * 16, "11" * 8)
        assert row is not None
        assert row["ended_at"] == dt.datetime(
            2023, 11, 14, 22, 13, 20, 123456, tzinfo=dt.timezone.utc
        )

    def test_ended_at_null_when_span_not_ended(
        self, otlp_harness: OtlpHarness
    ) -> None:
        # OTLP sends end_time_unix_nano == 0 to mean "not ended".
        req = _build_request(
            trace_id_hex="bb" * 16,
            span_id_hex="12" * 8,
            end_time_unix_nano=0,
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("bb" * 16, "12" * 8)
        assert row is not None
        assert row["ended_at"] is None


class TestErrorTriState:
    def test_status_error_maps_to_true(self, otlp_harness: OtlpHarness) -> None:
        req = _build_request(
            trace_id_hex="cc" * 16,
            span_id_hex="01" * 8,
            status_code=trace_pb2.Status.STATUS_CODE_ERROR,
            status_message="boom",
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("cc" * 16, "01" * 8)
        assert row is not None
        assert row["error"] is True
        assert row["status_message"] == "boom"

    def test_status_ok_maps_to_false(self, otlp_harness: OtlpHarness) -> None:
        req = _build_request(
            trace_id_hex="cc" * 16,
            span_id_hex="02" * 8,
            status_code=trace_pb2.Status.STATUS_CODE_OK,
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("cc" * 16, "02" * 8)
        assert row is not None
        assert row["error"] is False

    def test_status_unset_maps_to_null(self, otlp_harness: OtlpHarness) -> None:
        # The trap from the issue body: UNSET must NOT collapse to false.
        req = _build_request(
            trace_id_hex="cc" * 16,
            span_id_hex="03" * 8,
            status_code=trace_pb2.Status.STATUS_CODE_UNSET,
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("cc" * 16, "03" * 8)
        assert row is not None
        assert row["error"] is None

    def test_empty_status_message_stored_as_null(
        self, otlp_harness: OtlpHarness
    ) -> None:
        # OTLP Status.Message is a plain string proto field — when absent on
        # the wire it deserialises to "". An empty status_message has no
        # information and should not occupy a TEXT column on every span.
        req = _build_request(
            trace_id_hex="cc" * 16,
            span_id_hex="04" * 8,
            status_code=trace_pb2.Status.STATUS_CODE_ERROR,
            status_message="",
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("cc" * 16, "04" * 8)
        assert row is not None
        assert row["status_message"] is None


class TestServiceName:
    def test_service_name_promoted_from_resource(
        self, otlp_harness: OtlpHarness
    ) -> None:
        req = _build_request(
            trace_id_hex="dd" * 16,
            span_id_hex="01" * 8,
            resource_attributes=[
                _kv("service.name", _string("checkout-api")),
            ],
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("dd" * 16, "01" * 8)
        assert row is not None
        assert row["service_name"] == "checkout-api"

    def test_service_name_null_when_absent(
        self, otlp_harness: OtlpHarness
    ) -> None:
        req = _build_request(
            trace_id_hex="dd" * 16,
            span_id_hex="02" * 8,
            resource_attributes=[
                _kv("deployment.env", _string("prod")),
            ],
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("dd" * 16, "02" * 8)
        assert row is not None
        assert row["service_name"] is None


# --- JSONB envelope tests ----------------------------------------------------


class TestEvents:
    def test_events_round_trip(self, otlp_harness: OtlpHarness) -> None:
        events = [
            trace_pb2.Span.Event(
                name="cache_miss",
                time_unix_nano=1_700_000_000_500_000_000,
                attributes=[_kv("key", _string("user:42"))],
            ),
            trace_pb2.Span.Event(
                name="db_query",
                time_unix_nano=1_700_000_000_600_000_000,
            ),
        ]
        req = _build_request(
            trace_id_hex="ee" * 16,
            span_id_hex="01" * 8,
            events=events,
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("ee" * 16, "01" * 8)
        assert row is not None
        assert row["events"] == [
            {
                "name": "cache_miss",
                "time_unix_nano": 1_700_000_000_500_000_000,
                "attributes": {"key": "user:42"},
            },
            {
                "name": "db_query",
                "time_unix_nano": 1_700_000_000_600_000_000,
                "attributes": {},
            },
        ]

    def test_events_null_when_empty(self, otlp_harness: OtlpHarness) -> None:
        # Issue body trap: empty events -> SQL NULL, not [].
        req = _build_request(
            trace_id_hex="ee" * 16, span_id_hex="02" * 8, events=[]
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("ee" * 16, "02" * 8)
        assert row is not None
        assert row["events"] is None


class TestLinks:
    def test_links_round_trip(self, otlp_harness: OtlpHarness) -> None:
        links = [
            trace_pb2.Span.Link(
                trace_id=_trace_id("ab" * 16),
                span_id=_span_id("cd" * 8),
                attributes=[_kv("rel", _string("follows-from"))],
            ),
        ]
        req = _build_request(
            trace_id_hex="ff" * 16,
            span_id_hex="01" * 8,
            links=links,
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("ff" * 16, "01" * 8)
        assert row is not None
        assert row["links"] == [
            {
                "trace_id": "ab" * 16,
                "span_id": "cd" * 8,
                "attributes": {"rel": "follows-from"},
            }
        ]

    def test_links_null_when_empty(self, otlp_harness: OtlpHarness) -> None:
        req = _build_request(
            trace_id_hex="ff" * 16, span_id_hex="02" * 8, links=[]
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("ff" * 16, "02" * 8)
        assert row is not None
        assert row["links"] is None


class TestScope:
    def test_scope_round_trips_name_version_attributes(
        self, otlp_harness: OtlpHarness
    ) -> None:
        req = _build_request(
            trace_id_hex="01" * 16,
            span_id_hex="01" * 8,
            scope_name="my.lib",
            scope_version="1.2.3",
            scope_attributes=[_kv("scope.k", _string("v"))],
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("01" * 16, "01" * 8)
        assert row is not None
        assert row["scope"] == {
            "name": "my.lib",
            "version": "1.2.3",
            "attributes": {"scope.k": "v"},
        }

    def test_scope_null_when_wholly_absent(
        self, otlp_harness: OtlpHarness
    ) -> None:
        # Empty default-constructed InstrumentationScope: every field at
        # default -> SQL NULL.
        req = _build_request(
            trace_id_hex="01" * 16,
            span_id_hex="02" * 8,
            scope_name="",
            scope_version="",
            scope_attributes=[],
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("01" * 16, "02" * 8)
        assert row is not None
        assert row["scope"] is None

    def test_scope_omits_default_fields_but_keeps_set_ones(
        self, otlp_harness: OtlpHarness
    ) -> None:
        req = _build_request(
            trace_id_hex="01" * 16,
            span_id_hex="03" * 8,
            scope_name="my.lib",
            scope_version="",
            scope_attributes=[],
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("01" * 16, "03" * 8)
        assert row is not None
        assert row["scope"] == {"name": "my.lib"}


class TestResourceAttributes:
    def test_excludes_promoted_service_name(
        self, otlp_harness: OtlpHarness
    ) -> None:
        req = _build_request(
            trace_id_hex="02" * 16,
            span_id_hex="01" * 8,
            resource_attributes=[
                _kv("service.name", _string("svc")),
                _kv("deployment.env", _string("prod")),
            ],
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("02" * 16, "01" * 8)
        assert row is not None
        assert row["resource_attributes"] == {"deployment.env": "prod"}

    def test_null_when_only_service_name_present(
        self, otlp_harness: OtlpHarness
    ) -> None:
        # After removing service.name the dict is empty -> SQL NULL,
        # NOT {}.
        req = _build_request(
            trace_id_hex="02" * 16,
            span_id_hex="02" * 8,
            resource_attributes=[
                _kv("service.name", _string("svc")),
            ],
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("02" * 16, "02" * 8)
        assert row is not None
        assert row["resource_attributes"] is None

    def test_null_when_no_resource_attributes(
        self, otlp_harness: OtlpHarness
    ) -> None:
        req = _build_request(
            trace_id_hex="02" * 16,
            span_id_hex="03" * 8,
            resource_attributes=[],
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("02" * 16, "03" * 8)
        assert row is not None
        assert row["resource_attributes"] is None


class TestOtlpEnvelope:
    def test_otlp_includes_only_non_default_fields(
        self, otlp_harness: OtlpHarness
    ) -> None:
        req = _build_request(
            trace_id_hex="03" * 16,
            span_id_hex="01" * 8,
            trace_state="vendor=abc",
            flags=1,
            dropped_attributes_count=2,
            # dropped_events_count and dropped_links_count omitted (default 0).
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("03" * 16, "01" * 8)
        assert row is not None
        assert row["otlp"] == {
            "trace_state": "vendor=abc",
            "flags": 1,
            "dropped_attributes_count": 2,
        }

    def test_otlp_is_null_when_every_field_is_default(
        self, otlp_harness: OtlpHarness
    ) -> None:
        req = _build_request(
            trace_id_hex="03" * 16,
            span_id_hex="02" * 8,
            trace_state="",
            flags=0,
            dropped_attributes_count=0,
            dropped_events_count=0,
            dropped_links_count=0,
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("03" * 16, "02" * 8)
        assert row is not None
        assert row["otlp"] is None

    def test_otlp_carries_all_drop_counters(
        self, otlp_harness: OtlpHarness
    ) -> None:
        req = _build_request(
            trace_id_hex="03" * 16,
            span_id_hex="03" * 8,
            dropped_attributes_count=1,
            dropped_events_count=2,
            dropped_links_count=3,
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("03" * 16, "03" * 8)
        assert row is not None
        assert row["otlp"] == {
            "dropped_attributes_count": 1,
            "dropped_events_count": 2,
            "dropped_links_count": 3,
        }


# --- attribute typing -------------------------------------------------------


class TestAttributeTyping:
    def test_int_and_double_both_land_as_json_numbers(
        self, otlp_harness: OtlpHarness
    ) -> None:
        req = _build_request(
            trace_id_hex="04" * 16,
            span_id_hex="01" * 8,
            span_attributes=[
                _kv("count", _int(42)),
                _kv("ratio", _double(0.5)),
            ],
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("04" * 16, "01" * 8)
        assert row is not None
        # Postgres jsonb returns Python int for whole-number JSON numbers
        # and float otherwise. Both originated as JSON numbers; we assert
        # numeric equality (the int-vs-double distinction is intentionally
        # lost per PROJECT.md §3 "Attribute typing").
        attrs = row["attributes"]
        assert attrs["count"] == 42
        assert attrs["ratio"] == 0.5

    def test_attributes_remain_in_attributes_not_otlp(
        self, otlp_harness: OtlpHarness
    ) -> None:
        # Span.attributes must continue to land in the ``attributes`` column,
        # not be merged into the ``otlp`` envelope.
        req = _build_request(
            trace_id_hex="04" * 16,
            span_id_hex="02" * 8,
            span_attributes=[_kv("k", _string("v"))],
            trace_state="vendor=abc",
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("04" * 16, "02" * 8)
        assert row is not None
        assert row["attributes"] == {"k": "v"}
        assert row["otlp"] == {"trace_state": "vendor=abc"}
        # And the otlp envelope must not contain span.attributes.
        assert "attributes" not in row["otlp"]

    def test_attributes_remains_empty_object_not_null_when_absent(
        self, otlp_harness: OtlpHarness
    ) -> None:
        # Issue body invariant: ``attributes`` is always a (possibly empty)
        # JSON object — never SQL NULL — distinct from ``events``/``links``
        # whose canonical "absent" is NULL.
        req = _build_request(
            trace_id_hex="04" * 16,
            span_id_hex="03" * 8,
            span_attributes=[],
        )
        otlp_harness.client.send_grpc_request(req)
        row = otlp_harness.rows.fetch_all_columns("04" * 16, "03" * 8)
        assert row is not None
        assert row["attributes"] == {}
