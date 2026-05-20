"""Issue #7 acceptance: ADR-0004 span finalization via the harness from #5.

Each test corresponds to one row of the truth table in the issue body /
ADR-0004:

1. Fresh insert, ``ended_at IS NULL``           -> INSERT, ``seq == arrival_seq``.
2. Fresh insert, ``ended_at IS NOT NULL``       -> INSERT (finalized at first sight).
3. Partial then completion                       -> UPDATE; new ``seq`` from sequence;
                                                   ``arrival_seq`` unchanged; preserved
                                                   columns intact; mutable columns
                                                   overwritten.
4. Completion then partial-retry                 -> DO NOTHING.
5. Partial then partial                          -> DO NOTHING (the niche §3.2 case).
6. End-version retry of finalized row            -> DO NOTHING; idempotent;
                                                   no ``seq`` advance.
7. ``arrival_seq IS NOT NULL`` enforced by schema; never updated by app code.

Tests drive OTLP-in via the harness's hand-built ``ExportTraceServiceRequest``
escape hatch (``send_grpc_request``) so partial vs. completion is controlled
exactly via ``end_time_unix_nano``. Assertions read the row through
``SpanRowInspector.fetch_all_columns`` so columns beyond ``_DEFAULT_COLUMNS``
(``ended_at``, ``kind``, ``status_message``, etc.) are visible.
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
    parent_span_id_hex: str = "",
    start_time_unix_nano: int = 1_700_000_000_000_000_000,
    end_time_unix_nano: int = 0,
    kind: int = trace_pb2.Span.SPAN_KIND_UNSPECIFIED,
    status_code: int = trace_pb2.Status.STATUS_CODE_UNSET,
    status_message: str = "",
    span_attributes: list[common_pb2.KeyValue] | None = None,
    events: list[trace_pb2.Span.Event] | None = None,
    links: list[trace_pb2.Span.Link] | None = None,
    resource_attributes: list[common_pb2.KeyValue] | None = None,
) -> trace_service_pb2.ExportTraceServiceRequest:
    parent_bytes = bytes.fromhex(parent_span_id_hex) if parent_span_id_hex else b""
    span = trace_pb2.Span(
        trace_id=_trace_id(trace_id_hex),
        span_id=_span_id(span_id_hex),
        parent_span_id=parent_bytes,
        name=name,
        start_time_unix_nano=start_time_unix_nano,
        end_time_unix_nano=end_time_unix_nano,
        kind=kind,
        status=trace_pb2.Status(code=status_code, message=status_message),
        attributes=span_attributes or [],
        events=events or [],
        links=links or [],
    )
    scope_spans = trace_pb2.ScopeSpans(spans=[span])
    resource = resource_pb2.Resource(attributes=resource_attributes or [])
    rs = trace_pb2.ResourceSpans(resource=resource, scope_spans=[scope_spans])
    return trace_service_pb2.ExportTraceServiceRequest(resource_spans=[rs])


# A canonical "partial" payload: rich enough to verify that the receiver writes
# the *original* values for preserved columns, and that they survive the
# finalization. Different from the "completion" payload below in every mutable
# column so the UPDATE branch can be exercised properly.
_TRACE = "ee" * 16
_PARTIAL_PARENT = "aa" * 8
_COMPLETION_PARENT = "bb" * 8
_PARTIAL_START_NS = 1_700_000_000_000_000_000
_COMPLETION_START_NS = 1_700_000_000_999_000_000  # later than partial — should be ignored
_COMPLETION_END_NS = 1_700_000_001_000_000_000


def _partial_request(
    span_id_hex: str, *, name: str = "partial-name"
) -> trace_service_pb2.ExportTraceServiceRequest:
    return _build_request(
        trace_id_hex=_TRACE,
        span_id_hex=span_id_hex,
        name=name,
        parent_span_id_hex=_PARTIAL_PARENT,
        start_time_unix_nano=_PARTIAL_START_NS,
        end_time_unix_nano=0,  # partial
        kind=trace_pb2.Span.SPAN_KIND_INTERNAL,
        status_code=trace_pb2.Status.STATUS_CODE_UNSET,
        status_message="",
        span_attributes=[_kv("phase", _string("partial"))],
        resource_attributes=[_kv("service.name", _string("partial-svc"))],
    )


def _completion_request(span_id_hex: str) -> trace_service_pb2.ExportTraceServiceRequest:
    return _build_request(
        trace_id_hex=_TRACE,
        span_id_hex=span_id_hex,
        name="completion-name",
        # Different parent on the wire so we can prove app code preserves the
        # partial's parent_id rather than letting EXCLUDED.parent_id win.
        parent_span_id_hex=_COMPLETION_PARENT,
        start_time_unix_nano=_COMPLETION_START_NS,
        end_time_unix_nano=_COMPLETION_END_NS,
        kind=trace_pb2.Span.SPAN_KIND_SERVER,
        status_code=trace_pb2.Status.STATUS_CODE_ERROR,
        status_message="finalized",
        span_attributes=[_kv("phase", _string("completion"))],
        # Different service.name so we can prove the *partial*'s service_name
        # is the one preserved, not the completion's.
        resource_attributes=[_kv("service.name", _string("completion-svc"))],
    )


# --- truth-table tests -------------------------------------------------------


class TestRow1FreshPartialInsert:
    """Row 1: fresh insert with ``ended_at IS NULL`` -> seq == arrival_seq."""

    def test_partial_first_arrival_seq_equals_arrival_seq(
        self, otlp_harness: OtlpHarness
    ) -> None:
        otlp_harness.client.send_grpc_request(_partial_request("01" * 8))
        row = otlp_harness.rows.fetch_all_columns(_TRACE, "01" * 8)
        assert row is not None
        assert row["ended_at"] is None
        assert row["seq"] == row["arrival_seq"]


class TestRow2FreshFinalizedInsert:
    """Row 2: fresh insert with ``ended_at IS NOT NULL`` -> seq == arrival_seq."""

    def test_completion_first_arrival_seq_equals_arrival_seq(
        self, otlp_harness: OtlpHarness
    ) -> None:
        otlp_harness.client.send_grpc_request(_completion_request("02" * 8))
        row = otlp_harness.rows.fetch_all_columns(_TRACE, "02" * 8)
        assert row is not None
        assert row["ended_at"] is not None
        assert row["seq"] == row["arrival_seq"]


class TestRow3PartialThenCompletion:
    """Row 3: partial then completion -> UPDATE applied with the surgical rules."""

    def _send_and_read(
        self, otlp_harness: OtlpHarness, span_id_hex: str
    ) -> tuple[dict, dict]:
        otlp_harness.client.send_grpc_request(_partial_request(span_id_hex))
        before = otlp_harness.rows.fetch_all_columns(_TRACE, span_id_hex)
        assert before is not None
        otlp_harness.client.send_grpc_request(_completion_request(span_id_hex))
        after = otlp_harness.rows.fetch_all_columns(_TRACE, span_id_hex)
        assert after is not None
        return before, after

    def test_seq_advances_to_a_fresh_value_from_the_sequence(
        self, otlp_harness: OtlpHarness
    ) -> None:
        before, after = self._send_and_read(otlp_harness, "03" * 8)
        # Fresh seq must be strictly greater than the original (drawn from
        # nextval('spans_seq')).
        assert after["seq"] > before["seq"]

    def test_arrival_seq_unchanged(self, otlp_harness: OtlpHarness) -> None:
        before, after = self._send_and_read(otlp_harness, "04" * 8)
        assert after["arrival_seq"] == before["arrival_seq"]
        # And the new seq has diverged from arrival_seq — the watermark moved.
        assert after["seq"] != after["arrival_seq"]

    def test_started_at_preserved_from_partial(
        self, otlp_harness: OtlpHarness
    ) -> None:
        before, after = self._send_and_read(otlp_harness, "05" * 8)
        assert after["started_at"] == before["started_at"]
        assert after["started_at"] == dt.datetime(
            2023, 11, 14, 22, 13, 20, 0, tzinfo=dt.timezone.utc
        )

    def test_parent_id_preserved_from_partial(
        self, otlp_harness: OtlpHarness
    ) -> None:
        before, after = self._send_and_read(otlp_harness, "06" * 8)
        assert after["parent_id"] == _PARTIAL_PARENT
        assert after["parent_id"] == before["parent_id"]

    def test_service_name_preserved_from_partial(
        self, otlp_harness: OtlpHarness
    ) -> None:
        before, after = self._send_and_read(otlp_harness, "07" * 8)
        assert after["service_name"] == "partial-svc"
        assert after["service_name"] == before["service_name"]

    def test_observed_at_preserved_from_partial(
        self, otlp_harness: OtlpHarness
    ) -> None:
        before, after = self._send_and_read(otlp_harness, "08" * 8)
        assert after["observed_at"] == before["observed_at"]

    def test_mutable_columns_overwritten_by_completion(
        self, otlp_harness: OtlpHarness
    ) -> None:
        _, after = self._send_and_read(otlp_harness, "09" * 8)
        # Mutable columns: kind, name, attributes, events, links, otlp,
        # scope, resource_attributes, error, status_message, ended_at.
        assert after["name"] == "completion-name"
        assert after["kind"] == "SERVER"
        assert after["attributes"] == {"phase": "completion"}
        assert after["error"] is True
        assert after["status_message"] == "finalized"
        assert after["ended_at"] == dt.datetime(
            2023, 11, 14, 22, 13, 21, 0, tzinfo=dt.timezone.utc
        )


class TestRow4CompletionThenPartialRetry:
    """Row 4: completion then partial-retry -> DO NOTHING; finalized row untouched."""

    def test_finalized_row_untouched_by_partial_retry(
        self, otlp_harness: OtlpHarness
    ) -> None:
        span_id = "0a" * 8
        otlp_harness.client.send_grpc_request(_completion_request(span_id))
        first = otlp_harness.rows.fetch_all_columns(_TRACE, span_id)
        assert first is not None
        assert first["name"] == "completion-name"

        # A late-arriving "partial" version with the same PK must not move
        # anything, including not advancing seq. The retry carries a *distinct*
        # mutable payload (``name="late-partial-name"``) to make the no-leak
        # property concrete: any bug that fires the SET clause on this conflict
        # — an unconditional ``DO UPDATE``, swapped ``EXCLUDED``/``spans``
        # references, etc. — would overwrite ``name`` to ``late-partial-name``.
        # The spec's predicate evaluates false here (``spans.ended_at IS NOT
        # NULL`` after the completion landed), so DO NOTHING fires and the
        # row's ``name`` stays ``completion-name``.
        otlp_harness.client.send_grpc_request(
            _partial_request(span_id, name="late-partial-name")
        )
        second = otlp_harness.rows.fetch_all_columns(_TRACE, span_id)
        assert second is not None

        # Every column unchanged — the retry's mutable payload did not leak in.
        assert second == first
        assert second["name"] == "completion-name"


class TestRow5PartialThenPartial:
    """Row 5: partial then partial -> DO NOTHING (the §3.2 niche case)."""

    def test_second_partial_is_dropped(self, otlp_harness: OtlpHarness) -> None:
        span_id = "0b" * 8
        otlp_harness.client.send_grpc_request(_partial_request(span_id))
        first = otlp_harness.rows.fetch_all_columns(_TRACE, span_id)
        assert first is not None
        # A different partial — different name to prove no overwrite happens
        # if the rule were misimplemented.
        second_req = _build_request(
            trace_id_hex=_TRACE,
            span_id_hex=span_id,
            name="second-partial-name",
            end_time_unix_nano=0,
            kind=trace_pb2.Span.SPAN_KIND_INTERNAL,
        )
        otlp_harness.client.send_grpc_request(second_req)
        second = otlp_harness.rows.fetch_all_columns(_TRACE, span_id)
        assert second is not None
        assert second == first


class TestRow6EndVersionRetryOfFinalized:
    """Row 6: end-version retry of an already-finalized row -> idempotent.

    Easy to fail with a naive ON CONFLICT DO UPDATE: this test locks in the
    conditional clause's "existing.ended_at IS NULL" predicate.
    """

    def test_no_seq_advance_on_completion_retry(
        self, otlp_harness: OtlpHarness
    ) -> None:
        span_id = "0c" * 8
        otlp_harness.client.send_grpc_request(_completion_request(span_id))
        first = otlp_harness.rows.fetch_all_columns(_TRACE, span_id)
        assert first is not None

        # Re-send the *exact same* completion. A naive UPDATE branch (no
        # "existing.ended_at IS NULL" guard) would advance seq again here.
        otlp_harness.client.send_grpc_request(_completion_request(span_id))
        second = otlp_harness.rows.fetch_all_columns(_TRACE, span_id)
        assert second is not None

        assert second == first
        assert second["seq"] == first["seq"]


class TestRow7ArrivalSeqInvariants:
    """Row 7: ``arrival_seq IS NOT NULL`` enforced by schema; never updated."""

    def test_arrival_seq_is_not_null_constraint_in_information_schema(
        self, otlp_harness: OtlpHarness
    ) -> None:
        is_nullable = otlp_harness.rows.fetch_one_value(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            ("spans", "arrival_seq"),
        )
        assert is_nullable == "NO"

    def test_arrival_seq_never_advances_on_finalization(
        self, otlp_harness: OtlpHarness
    ) -> None:
        span_id = "0d" * 8
        otlp_harness.client.send_grpc_request(_partial_request(span_id))
        before = otlp_harness.rows.fetch_all_columns(_TRACE, span_id)
        assert before is not None
        original_arrival_seq = before["arrival_seq"]

        # Finalize.
        otlp_harness.client.send_grpc_request(_completion_request(span_id))
        after = otlp_harness.rows.fetch_all_columns(_TRACE, span_id)
        assert after is not None
        assert after["arrival_seq"] == original_arrival_seq

    def test_arrival_seq_diverges_from_seq_after_finalization(
        self, otlp_harness: OtlpHarness
    ) -> None:
        # The point of #7: this is the slice where the two columns first
        # diverge in production. Schema-completion (#6) left them equal.
        span_id = "0e" * 8
        otlp_harness.client.send_grpc_request(_partial_request(span_id))
        # Drive at least one other write so the next nextval() differs from
        # the partial's allocated value.
        otlp_harness.client.send_grpc_request(_partial_request("ff" * 8))
        otlp_harness.client.send_grpc_request(_completion_request(span_id))

        row = otlp_harness.rows.fetch_all_columns(_TRACE, span_id)
        assert row is not None
        assert row["seq"] != row["arrival_seq"]
        assert row["seq"] > row["arrival_seq"]
