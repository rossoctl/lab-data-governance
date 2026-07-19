"""Wire-contract tests for the full-row Span the detail panel renders — issue #49.

The nine-section detail-panel layout now lives in the React SPA (ADR-0019); its
rendered structure is covered by the SPA's own tests. What remains here is the
**API boundary** that feeds the panel — unchanged by the UI migration:

- ``Span`` carries the new full-row fields over the wire.
- The ``GET /api/traces/{tid}/spans`` JSON response includes every new field
  (ADR-0018; was ``GET /spans?trace_id``), and nullable fields surface as null.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import psycopg

UTC = dt.timezone.utc


def _insert_finalized_error_span(conn) -> None:
    """Insert a fully-populated, finalized error span for section tests."""
    otlp = {
        "trace_state": "vendor=abc",
        "flags": 1,
        "dropped_attributes_count": 0,
        "dropped_events_count": 0,
        "dropped_links_count": 0,
    }
    scope = {"name": "my.instrumentor", "version": "1.2.3"}
    resource_attrs = {"host.name": "worker-1", "k8s.pod.name": "pod-abc"}
    events = [
        {
            "name": "exception",
            "time_unix_nano": 1700000000000000000,
            "attributes": {"exception.type": "ValueError"},
        }
    ]
    links = [{"trace_id": "linked-trace", "span_id": "linked-span", "attributes": {}}]

    conn.execute(
        """
        INSERT INTO spans (
            trace_id, span_id, parent_id, kind, name, service_name,
            started_at, ended_at,
            error, status_message,
            events, links, attributes,
            otlp, scope, resource_attributes,
            seq, arrival_seq, observed_at
        ) VALUES (
            'trace-full', 'span-full', NULL, 'SERVER', 'full-span', 'my-svc',
            %s, %s,
            TRUE, 'something went wrong',
            %s::jsonb, %s::jsonb, '{"req_id": "xyz"}'::jsonb,
            %s::jsonb, %s::jsonb, %s::jsonb,
            nextval('spans_seq'), currval('spans_seq'), now()
        )
        """,
        (
            dt.datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC),
            dt.datetime(2026, 5, 1, 10, 0, 1, tzinfo=UTC),
            json.dumps(events),
            json.dumps(links),
            json.dumps(otlp),
            json.dumps(scope),
            json.dumps(resource_attrs),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Wire-level: new fields present in the whole-trace spans response
# ---------------------------------------------------------------------------


def test_new_fields_present_on_wire(api_server, configured_db):
    """Every new ADR-0006 field surfaces on the whole-trace spans JSON."""
    with psycopg.connect(configured_db) as conn:
        _insert_finalized_error_span(conn)

    spans = httpx.get(
        f"http://127.0.0.1:{api_server.port}/api/traces/trace-full/spans",
    ).json()["spans"]
    assert len(spans) == 1
    s = spans[0]

    # ended_at: populated for a finalized span
    assert s["ended_at"] is not None
    assert "2026-05-01" in s["ended_at"]

    # observed_at: set by receiver/inserter
    assert s["observed_at"] is not None

    # arrival_seq: stable per-row integer
    assert isinstance(s["arrival_seq"], int)

    # otlp envelope
    assert s["otlp"]["trace_state"] == "vendor=abc"
    assert s["otlp"]["flags"] == 1

    # scope
    assert s["scope"]["name"] == "my.instrumentor"
    assert s["scope"]["version"] == "1.2.3"

    # resource_attributes
    assert s["resource_attributes"]["host.name"] == "worker-1"


def test_nullable_fields_surface_as_null_for_minimal_span(
    api_server, configured_db
):
    """A span with none of the optional new fields has null on the wire."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            """
            INSERT INTO spans (
                trace_id, span_id, parent_id, kind, name,
                started_at, attributes, seq, arrival_seq, observed_at
            ) VALUES (
                'trace-min', 'span-min', NULL, 'INTERNAL', 'minimal',
                now(), '{}'::jsonb,
                nextval('spans_seq'), currval('spans_seq'), now()
            )
            """
        )
        conn.commit()

    spans = httpx.get(
        f"http://127.0.0.1:{api_server.port}/api/traces/trace-min/spans",
    ).json()["spans"]
    assert len(spans) == 1
    s = spans[0]

    assert s["ended_at"] is None
    assert s["otlp"] is None
    assert s["scope"] is None
    assert s["resource_attributes"] is None
    # observed_at and arrival_seq are NOT NULL on the table
    assert s["observed_at"] is not None
    assert isinstance(s["arrival_seq"], int)
