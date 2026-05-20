"""Layer 2 ``write_span`` — the receiver's per-span insert.

Issue #3 introduced this with a tracer-bullet column set
(``trace_id, span_id, parent_id, name, started_at, attributes`` plus
``seq``/``arrival_seq``/``observed_at``). Issue #6 widened the write to
populate the rest of the v1 ``spans`` schema. Issue #7 implements
ADR-0004's conditional finalization on top:

- Promoted columns: ``kind``, ``ended_at``, ``error``, ``status_message``,
  ``service_name``.
- JSONB envelope columns: ``events``, ``links``, ``scope``,
  ``resource_attributes``, ``otlp``.

The two NULL-vs-empty rules from the issue body are enforced here:

1. ``events`` and ``links`` are SQL ``NULL`` when the span has none —
   never the empty array. ``scope``, ``resource_attributes``, and
   ``otlp`` follow the same NULL-vs-``{}`` discipline.
2. ``attributes`` is the exception: it is always a (possibly empty) JSON
   object, never SQL ``NULL``. The receiver never writes NULL into that
   column.

Every call still opens its own Layer 1 transaction (ADR-0005), so a poison
span aborts only its own write and never blocks a neighbour.

**ADR-0004 finalization (issue #7).** ``ON CONFLICT (trace_id, span_id)``
takes the conditional UPDATE branch iff the *incoming* ``ended_at`` is not
null and the *existing* ``ended_at`` is null — i.e. exactly the
"partial then completion" case. In every other case (completion-then-partial,
partial-then-partial, end-version retry of an already-finalized row) the
``WHERE`` predicate makes the UPDATE a no-op and the row is left untouched.

The UPDATE branch:

- Overwrites every mutable column (``kind``, ``name``, ``attributes``,
  ``events``, ``links``, ``otlp``, ``scope``, ``resource_attributes``,
  ``error``, ``status_message``, ``ended_at``) from ``EXCLUDED.*``.
- Assigns a fresh ``seq`` from ``nextval('spans_seq')`` so stream consumers
  see the finalization as a watermark advance.
- **Preserves** ``arrival_seq``, ``started_at``, ``parent_id``,
  ``service_name``, ``observed_at`` by *omitting them from the SET clause*
  (they keep their existing row values automatically). Defensive
  ``col = spans.col`` writes are deliberately avoided.

**Still out of scope here, by design:**

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
    """Per-span shape consumed by ``write_span``.

    Mirrors the v1 ``spans`` schema columns the receiver writes. The
    NULL-vs-empty rules in the module docstring are encoded by *type*:
    columns that may be SQL ``NULL`` carry ``| None`` here, and the
    canonical "absent" value passed by callers is ``None`` rather than
    ``[]``/``{}``.

    Attributes:
        trace_id: Hex-encoded 16-byte OTLP trace id.
        span_id:  Hex-encoded 8-byte OTLP span id.
        parent_id: Hex-encoded 8-byte OTLP parent span id, or ``None`` when
            the span is a real root.
        name: OTLP ``Span.name``.
        kind: OTLP ``Span.SpanKind`` mapped to its enum name
            (``INTERNAL``/``SERVER``/``CLIENT``/``PRODUCER``/``CONSUMER``),
            or ``None`` when the OTLP enum was ``SPAN_KIND_UNSPECIFIED``.
        started_at: ``Span.start_time_unix_nano`` truncated to microsecond
            precision and tagged with UTC. Naive datetimes are accepted but
            interpreted as UTC.
        ended_at: ``Span.end_time_unix_nano`` truncated to microseconds, or
            ``None`` when the span has not ended (``end_time_unix_nano == 0``
            on the wire).
        error: Tri-state projection of OTLP ``Status.Code``: ``ERROR→True``,
            ``OK→False``, ``UNSET→None``.
        status_message: OTLP ``Status.Message`` carried verbatim, or ``None``
            when the message is empty.
        service_name: ``Resource.attributes["service.name"]`` if present,
            else ``None``.
        attributes: ``Span.attributes`` as a JSON-serialisable mapping. Must
            be a (possibly empty) ``dict``; the receiver never writes SQL
            NULL into this column.
        events: ``Span.events`` as
            ``[{name, time_unix_nano, attributes}, ...]``. ``None`` (not an
            empty list) when the span has no events.
        links: ``Span.links`` as
            ``[{trace_id, span_id, attributes}, ...]``. ``None`` (not an
            empty list) when the span has no links.
        scope: ``InstrumentationScope`` projected to ``{name, version,
            attributes}`` with default-valued fields omitted; ``None`` when
            every field is at its default.
        resource_attributes: ``Resource.attributes`` minus the promoted
            ``service.name`` key; ``None`` when the post-removal mapping is
            empty.
        otlp: Envelope of non-promoted top-level OTLP fields
            (``trace_state``, ``flags``, ``dropped_attributes_count``,
            ``dropped_events_count``, ``dropped_links_count``) with
            default-valued fields omitted; ``None`` when every field is at
            its default.
    """

    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    kind: str | None
    started_at: dt.datetime
    ended_at: dt.datetime | None
    error: bool | None
    status_message: str | None
    service_name: str | None
    attributes: dict[str, Any]
    events: list[dict[str, Any]] | None
    links: list[dict[str, Any]] | None
    scope: dict[str, Any] | None
    resource_attributes: dict[str, Any] | None
    otlp: dict[str, Any] | None


_INSERT_SQL = """
    INSERT INTO spans (
        trace_id, span_id, parent_id, name, kind,
        started_at, ended_at, error, status_message, service_name,
        attributes, events, links, scope, resource_attributes, otlp,
        seq, arrival_seq, observed_at
    )
    VALUES (
        %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s,
        %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
        nextval('spans_seq'), currval('spans_seq'), %s
    )
    ON CONFLICT (trace_id, span_id) DO UPDATE SET
        kind                = EXCLUDED.kind,
        name                = EXCLUDED.name,
        attributes          = EXCLUDED.attributes,
        events              = EXCLUDED.events,
        links               = EXCLUDED.links,
        otlp                = EXCLUDED.otlp,
        scope               = EXCLUDED.scope,
        resource_attributes = EXCLUDED.resource_attributes,
        error               = EXCLUDED.error,
        status_message      = EXCLUDED.status_message,
        ended_at            = EXCLUDED.ended_at,
        seq                 = nextval('spans_seq')
    WHERE
        EXCLUDED.ended_at IS NOT NULL
        AND spans.ended_at IS NULL
"""


def write_span(span: SpanRow) -> None:
    """Write *span* to the ``spans`` table in its own Layer 1 transaction.

    Allocates a single value from ``spans_seq`` and uses it for both ``seq``
    and ``arrival_seq`` on initial INSERT (they are equal at first sight).
    On the conditional UPDATE branch (ADR-0004 finalization), a fresh
    ``seq`` is drawn from ``nextval('spans_seq')`` atomically with the
    UPDATE; ``arrival_seq`` is left at its original value. ``arrival_seq``
    is never updated by application code and is enforced ``NOT NULL`` by
    the schema.

    On a primary-key conflict the conditional UPSERT either UPDATEs (when
    the incoming version finalizes a previously-partial row) or DOes
    NOTHING (every other case: completion-then-partial, partial-then-partial,
    end-version retry of an already-finalized row).

    The function intentionally does no error classification — any psycopg
    error escapes to the caller, where the OTLP server's default behaviour
    crashes the batch. The ADR-0003 SQLSTATE→OTLP mapping lands in issue #8.
    """
    started_at = _ensure_aware_utc(span.started_at)
    ended_at = (
        _ensure_aware_utc(span.ended_at) if span.ended_at is not None else None
    )
    observed_at = dt.datetime.now(tz=dt.timezone.utc)

    with db.transaction() as tx:
        tx.execute(
            _INSERT_SQL,
            (
                span.trace_id,
                span.span_id,
                span.parent_id,
                span.name,
                span.kind,
                started_at,
                ended_at,
                span.error,
                span.status_message,
                span.service_name,
                json.dumps(span.attributes),
                _json_or_none(span.events),
                _json_or_none(span.links),
                _json_or_none(span.scope),
                _json_or_none(span.resource_attributes),
                _json_or_none(span.otlp),
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


def _json_or_none(value: Any) -> str | None:
    """``json.dumps(value)`` for non-``None`` values, ``None`` otherwise.

    The NULLable JSONB columns (``events``, ``links``, ``scope``,
    ``resource_attributes``, ``otlp``) follow the rule "absent → SQL NULL,
    not empty container". Callers pass ``None`` to mean "absent"; this
    helper translates that into a literal SQL ``NULL`` (psycopg sends
    ``None`` parameters as ``NULL``) rather than ``'null'::jsonb`` or
    ``'[]'::jsonb``.
    """
    if value is None:
        return None
    return json.dumps(value)
