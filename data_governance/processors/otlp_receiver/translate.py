"""OTLP protobuf → :class:`SpanRow` translation.

The receiver decodes incoming OTLP ``ExportTraceServiceRequest`` messages
into a sequence of :class:`SpanRow` instances suitable for the tracer-bullet
``write_span``. Translation here is intentionally narrow: only the columns
issue #3 populates are extracted, even though the OTLP message carries
much more. Issue #6 widens this when the rest of the spans schema is
written.

OTLP timestamps arrive as Unix nanoseconds; we truncate to microseconds
(Postgres ``timestamptz`` resolution) on the way in. OTLP ids are bytes;
we hex-encode them before storage so the downstream UI and SQL queries
read trace/span ids as fixed-length lowercase hex strings.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Iterator
from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1 import common_pb2

from .write_span import SpanRow


def request_to_span_rows(
    request: ExportTraceServiceRequest,
) -> Iterator[SpanRow]:
    """Yield one :class:`SpanRow` per OTLP span in *request*.

    Walks the standard ``ResourceSpans -> ScopeSpans -> Span`` envelope.
    Resource and scope metadata is not consumed at this stage (issue #6's
    job); only span-level fields the tracer-bullet ``write_span`` writes
    are translated.
    """
    for rs in request.resource_spans:
        for ss in rs.scope_spans:
            for span in ss.spans:
                yield _span_to_row(span)


def _span_to_row(span: Any) -> SpanRow:
    return SpanRow(
        trace_id=span.trace_id.hex(),
        span_id=span.span_id.hex(),
        parent_id=span.parent_span_id.hex() if span.parent_span_id else None,
        name=span.name,
        started_at=_ns_to_dt(span.start_time_unix_nano),
        attributes=_kvs_to_dict(span.attributes),
    )


def _ns_to_dt(ts_ns: int) -> dt.datetime:
    """Convert OTLP Unix nanoseconds to a microsecond-precision UTC datetime.

    Postgres ``timestamptz`` has microsecond resolution; OTLP carries
    nanoseconds. Per PROJECT.md §3 timestamps are truncated to microseconds
    on ingest, which is acceptable for v1 and below the resolution of most
    platforms' monotonic clocks.
    """
    secs, ns_remainder = divmod(ts_ns, 1_000_000_000)
    micros = ns_remainder // 1000  # truncate (don't round) to us
    return dt.datetime.fromtimestamp(secs, tz=dt.timezone.utc).replace(
        microsecond=micros
    )


def _kvs_to_dict(kvs: Iterable[common_pb2.KeyValue]) -> dict[str, Any]:
    """Translate an OTLP ``repeated KeyValue`` into a JSON-serialisable dict.

    OTLP's ``AnyValue`` is a oneof over string, bool, int, double, array,
    kvlist, and bytes. JSON has only ``number``, so int and double both
    round-trip as JSON numbers (the int-vs-double distinction is lost — see
    PROJECT.md §3 "Attribute typing"). Bytes values are hex-encoded; this
    is a v1 best-effort and the format may tighten in v1.x once a real
    bytes-shaped attribute appears.
    """
    out: dict[str, Any] = {}
    for kv in kvs:
        out[kv.key] = _any_value_to_python(kv.value)
    return out


def _any_value_to_python(value: common_pb2.AnyValue) -> Any:
    which = value.WhichOneof("value")
    if which is None:
        return None
    if which == "string_value":
        return value.string_value
    if which == "bool_value":
        return value.bool_value
    if which == "int_value":
        return value.int_value
    if which == "double_value":
        return value.double_value
    if which == "array_value":
        return [_any_value_to_python(v) for v in value.array_value.values]
    if which == "kvlist_value":
        return _kvs_to_dict(value.kvlist_value.values)
    if which == "bytes_value":
        return value.bytes_value.hex()
    # Unknown oneof variant: future-proof fallback.
    return None
