"""OTLP protobuf → :class:`SpanRow` translation.

The receiver decodes incoming OTLP ``ExportTraceServiceRequest`` messages
into a sequence of :class:`SpanRow` instances suitable for the receiver's
``write_span``. Issue #6 widens this from the tracer-bullet column set
(issue #3) to the full v1 ``spans`` schema: span ``kind``, ``ended_at``,
the tri-state ``error`` projection of OTLP ``Status.Code``,
``status_message``, ``service_name``, the ``events``/``links``/``scope``
JSONB columns, ``resource_attributes`` (minus the promoted ``service.name``),
and the ``otlp`` envelope of non-promoted top-level fields.

OTLP timestamps arrive as Unix nanoseconds; we truncate to microseconds
(Postgres ``timestamptz`` resolution) on the way in. OTLP ids are bytes;
we hex-encode them before storage so the downstream UI and SQL queries
read trace/span ids as fixed-length lowercase hex strings.

The two NULL-vs-empty rules from issue #6 are encoded by *helper*: the
helpers that build optional-jsonb mappings return ``None`` (not ``{}`` or
``[]``) when their input has no information, and ``write_span`` writes
those ``None`` values as SQL ``NULL``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Iterator
from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

from .write_span import SpanRow


_SERVICE_NAME_KEY = "service.name"

# OTLP enum value → column string. ``SPAN_KIND_UNSPECIFIED`` maps to
# ``None`` so unspecified spans land as SQL ``NULL`` rather than a
# placeholder string.
_SPAN_KIND_NAMES: dict[int, str] = {
    trace_pb2.Span.SPAN_KIND_INTERNAL: "INTERNAL",
    trace_pb2.Span.SPAN_KIND_SERVER: "SERVER",
    trace_pb2.Span.SPAN_KIND_CLIENT: "CLIENT",
    trace_pb2.Span.SPAN_KIND_PRODUCER: "PRODUCER",
    trace_pb2.Span.SPAN_KIND_CONSUMER: "CONSUMER",
}


def request_to_span_rows(
    request: ExportTraceServiceRequest,
) -> Iterator[SpanRow]:
    """Yield one :class:`SpanRow` per OTLP span in *request*.

    Walks the standard ``ResourceSpans -> ScopeSpans -> Span`` envelope.
    Resource and instrumentation-scope context is captured at the right
    level and applied to every span underneath: the ``Resource.attributes``
    feed both ``service_name`` (promoted) and ``resource_attributes``
    (the rest); the ``InstrumentationScope`` becomes the per-span
    ``scope`` column.
    """
    for rs in request.resource_spans:
        service_name = _resource_service_name(rs.resource)
        resource_attributes = _resource_attributes_minus_service_name(rs.resource)
        for ss in rs.scope_spans:
            scope = _instrumentation_scope_to_dict(ss.scope)
            for span in ss.spans:
                yield _span_to_row(
                    span,
                    service_name=service_name,
                    resource_attributes=resource_attributes,
                    scope=scope,
                )


def _span_to_row(
    span: trace_pb2.Span,
    *,
    service_name: str | None,
    resource_attributes: dict[str, Any] | None,
    scope: dict[str, Any] | None,
) -> SpanRow:
    return SpanRow(
        trace_id=span.trace_id.hex(),
        span_id=span.span_id.hex(),
        parent_id=span.parent_span_id.hex() if span.parent_span_id else None,
        name=span.name,
        kind=_SPAN_KIND_NAMES.get(span.kind),
        started_at=_ns_to_dt(span.start_time_unix_nano),
        ended_at=(
            _ns_to_dt(span.end_time_unix_nano)
            if span.end_time_unix_nano
            else None
        ),
        error=_status_code_to_error(span.status.code),
        status_message=span.status.message or None,
        service_name=service_name,
        attributes=_kvs_to_dict(span.attributes),
        events=_events_to_list(span.events),
        links=_links_to_list(span.links),
        scope=scope,
        resource_attributes=resource_attributes,
        otlp=_otlp_envelope(span),
    )


# --- promoted-column helpers -------------------------------------------------


def _status_code_to_error(code: int) -> bool | None:
    """Project OTLP ``Status.Code`` to the tri-state ``error`` column.

    Mapping per the issue body: ``ERROR→true``, ``OK→false``, ``UNSET→null``.
    UNSET must NOT collapse to ``False`` — a span with no explicit status
    is genuinely "unknown", and conflating that with "succeeded" loses the
    distinction the OTLP spec preserves.
    """
    if code == trace_pb2.Status.STATUS_CODE_ERROR:
        return True
    if code == trace_pb2.Status.STATUS_CODE_OK:
        return False
    return None


def _resource_service_name(resource: Any) -> str | None:
    """Pull the promoted ``service.name`` out of ``Resource.attributes``."""
    for kv in resource.attributes:
        if kv.key == _SERVICE_NAME_KEY:
            value = _any_value_to_python(kv.value)
            return value if isinstance(value, str) else None
    return None


def _resource_attributes_minus_service_name(
    resource: Any,
) -> dict[str, Any] | None:
    """Build the ``resource_attributes`` column minus the promoted key.

    Returns ``None`` when the post-removal mapping is empty — the issue
    body's "NULL when only service.name is present or no resource
    attributes at all" rule. We keep the empty-dict ``→`` ``None``
    discipline at this level so ``write_span`` itself stays a thin
    serialiser.
    """
    out: dict[str, Any] = {}
    for kv in resource.attributes:
        if kv.key == _SERVICE_NAME_KEY:
            continue
        out[kv.key] = _any_value_to_python(kv.value)
    return out or None


# --- jsonb-envelope helpers --------------------------------------------------


def _events_to_list(
    events: Iterable[trace_pb2.Span.Event],
) -> list[dict[str, Any]] | None:
    """Project ``Span.events`` to a list of dicts; ``None`` when empty.

    The empty-list-becomes-NULL rule is the issue body's invariant for
    ``events`` and ``links``. The receiver never writes ``[]``.
    """
    out: list[dict[str, Any]] = []
    for ev in events:
        out.append(
            {
                "name": ev.name,
                "time_unix_nano": ev.time_unix_nano,
                "attributes": _kvs_to_dict(ev.attributes),
            }
        )
    return out or None


def _links_to_list(
    links: Iterable[trace_pb2.Span.Link],
) -> list[dict[str, Any]] | None:
    """Project ``Span.links`` to a list of dicts; ``None`` when empty."""
    out: list[dict[str, Any]] = []
    for ln in links:
        out.append(
            {
                "trace_id": ln.trace_id.hex(),
                "span_id": ln.span_id.hex(),
                "attributes": _kvs_to_dict(ln.attributes),
            }
        )
    return out or None


def _instrumentation_scope_to_dict(
    scope: common_pb2.InstrumentationScope,
) -> dict[str, Any] | None:
    """Project ``InstrumentationScope`` to ``{name, version, attributes}``.

    Default-valued fields (empty ``name``, empty ``version``, empty
    ``attributes``) are omitted from the result. When *every* field is at
    its default — i.e. the scope is wholly absent on the wire — returns
    ``None`` so the column lands as SQL ``NULL`` per the issue body.
    """
    out: dict[str, Any] = {}
    if scope.name:
        out["name"] = scope.name
    if scope.version:
        out["version"] = scope.version
    attrs = _kvs_to_dict(scope.attributes)
    if attrs:
        out["attributes"] = attrs
    return out or None


def _otlp_envelope(span: trace_pb2.Span) -> dict[str, Any] | None:
    """Project non-promoted top-level OTLP fields to the ``otlp`` envelope.

    Fields included: ``trace_state``, ``flags``, ``dropped_attributes_count``,
    ``dropped_events_count``, ``dropped_links_count``. Each is included
    only when its value is non-default (non-empty string / non-zero); when
    every field is at default, returns ``None`` so the column lands as SQL
    ``NULL``.

    Span-level ``attributes`` are deliberately *not* included here — they
    live in their own ``attributes`` column. Mixing them into the envelope
    would make round-trip queries ambiguous.
    """
    out: dict[str, Any] = {}
    if span.trace_state:
        out["trace_state"] = span.trace_state
    if span.flags:
        out["flags"] = span.flags
    if span.dropped_attributes_count:
        out["dropped_attributes_count"] = span.dropped_attributes_count
    if span.dropped_events_count:
        out["dropped_events_count"] = span.dropped_events_count
    if span.dropped_links_count:
        out["dropped_links_count"] = span.dropped_links_count
    return out or None


# --- timestamp + attribute primitives ---------------------------------------


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
