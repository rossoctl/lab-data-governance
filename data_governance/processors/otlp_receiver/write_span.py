"""Layer 2 ``write_span`` — the receiver's per-span insert.

This is the **tracer-bullet stage** of ``write_span`` introduced in issue #3.
It writes only the columns needed to round-trip a span through the
``spans`` table:

    trace_id, span_id, parent_id, name, started_at, attributes,
    seq, arrival_seq, observed_at

with ``seq == arrival_seq`` on initial INSERT, ``attributes`` always a JSON
object (never SQL NULL), ``observed_at`` set by the receiver, and
``ON CONFLICT (trace_id, span_id) DO NOTHING``. Every call opens its own
Layer 1 transaction (ADR-0005), so a poison span aborts only its own write
and never blocks a neighbour.

**Out of scope here, by design:**

- ADR-0004 conditional finalization. ON CONFLICT stays DO NOTHING for now;
  the conditional UPDATE on ``ended_at`` arrives in issue #6, along with
  population of every other span column (``kind``, ``ended_at``, ``error``,
  ``status_message``, ``service_name``, ``events``, ``links``, ``otlp``,
  ``scope``, ``resource_attributes``).
- The §3.1 blocklist. That's issue #9.
- The ADR-0003 SQLSTATE→OTLP error mapping. That's issue #8.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

from data_governance import db


__all__ = ["SpanRow", "write_span"]


@dataclass(frozen=True)
class SpanRow:
    """Minimal span shape consumed by the tracer-bullet ``write_span``.

    Only the columns issue #3 populates are present. Future slices that
    populate further columns (issue #6 et seq.) extend this dataclass
    additively or replace it with the full domain ``Span`` object.

    Attributes:
        trace_id: Hex-encoded 16-byte OTLP trace id.
        span_id:  Hex-encoded 8-byte OTLP span id.
        parent_id: Hex-encoded 8-byte OTLP parent span id, or ``None`` when
            the span is a real root.
        name: OTLP ``Span.name``.
        started_at: ``Span.start_time_unix_nano`` truncated to microsecond
            precision and tagged with UTC. Naive datetimes are accepted but
            interpreted as UTC.
        attributes: ``Span.attributes`` as a JSON-serialisable mapping. Must
            be a (possibly empty) ``dict``; the receiver never writes SQL
            NULL into this column.
    """

    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    started_at: dt.datetime
    attributes: dict[str, Any]


_INSERT_SQL = """
    INSERT INTO spans (
        trace_id, span_id, parent_id, name, started_at, attributes,
        seq, arrival_seq, observed_at
    )
    VALUES (
        %s, %s, %s, %s, %s, %s::jsonb,
        nextval('spans_seq'), currval('spans_seq'), %s
    )
    ON CONFLICT (trace_id, span_id) DO NOTHING
"""


def write_span(span: SpanRow) -> None:
    """Write *span* to the ``spans`` table in its own Layer 1 transaction.

    Allocates a single value from ``spans_seq`` and uses it for both ``seq``
    and ``arrival_seq`` (they are equal at initial INSERT; ADR-0004's
    finalization that advances ``seq`` is deferred to issue #6).

    On a primary-key conflict the row is silently dropped per the
    tracer-bullet ON CONFLICT DO NOTHING policy. The conditional finalization
    rule from ADR-0004 lands in issue #6.

    The function intentionally does no error classification — any psycopg
    error escapes to the caller, where the OTLP server's default behaviour
    crashes the batch. The ADR-0003 SQLSTATE→OTLP mapping lands in issue #8.
    """
    attributes = span.attributes if span.attributes is not None else {}
    attributes_json = json.dumps(attributes)

    started_at = _ensure_aware_utc(span.started_at)
    observed_at = dt.datetime.now(tz=dt.timezone.utc)

    with db.transaction() as tx:
        tx.execute(
            _INSERT_SQL,
            (
                span.trace_id,
                span.span_id,
                span.parent_id,
                span.name,
                started_at,
                attributes_json,
                observed_at,
            ),
        )


def _ensure_aware_utc(ts: dt.datetime) -> dt.datetime:
    """Tag a naive datetime as UTC; pass aware datetimes through unchanged.

    The OTLP wire carries Unix nanoseconds, which the receiver decodes into
    aware UTC datetimes truncated to microseconds. Tests sometimes construct
    naive datetimes for brevity; treating them as UTC keeps a sensible
    default without silently shifting offsets.
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=dt.timezone.utc)
    return ts
