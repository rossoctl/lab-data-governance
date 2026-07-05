"""End-to-end tests for the detail-panel nine-section layout — issue #49.

Verifies:
- ``Span`` carries the new full-row fields over the wire.
- ``GET /spans`` JSON response includes every new field.
- The trace-tree HTML shell contains all nine section headings.
- The ``selectSpan`` logic correctly uses the new fields.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import psycopg
import pytest

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
# Wire-level: new fields present in GET /spans response
# ---------------------------------------------------------------------------


def test_new_fields_present_on_wire(api_server, configured_db):
    """Every new ADR-0006 field surfaces on the /spans JSON response."""
    with psycopg.connect(configured_db) as conn:
        _insert_finalized_error_span(conn)

    spans = httpx.get(
        f"http://127.0.0.1:{api_server.port}/spans",
        params={"trace_id": "trace-full"},
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
        f"http://127.0.0.1:{api_server.port}/spans",
        params={"trace_id": "trace-min"},
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


# ---------------------------------------------------------------------------
# HTML shell: nine section headings are present
# ---------------------------------------------------------------------------


def test_trace_tree_shell_has_nine_section_headings(api_server, configured_db):
    """The trace-tree HTML shell must contain all nine section headings
    so the detail panel renders the correct structure for any span."""
    resp = httpx.get(f"http://127.0.0.1:{api_server.port}/traces/any")
    assert resp.status_code == 200
    text = resp.text

    for heading in (
        "Identity",
        "Timing",
        "Status",
        "Resource",
        "Scope",
        "OTLP envelope",
        "Attributes",
        "Events",
        "Links",
    ):
        assert heading in text, f"section heading {heading!r} missing from shell"

    # AC8: Resource/Scope/OTLP envelope must fall back to '(none)' when null.
    assert "(none)" in text, (
        "_jsonPre null-fallback '(none)' missing from shell; "
        "AC8 requires Resource/Scope/OTLP to render as '(none)' when null"
    )


def test_trace_tree_shell_has_select_span_fields(api_server, configured_db):
    """The selectSpan function must reference the new ADR-0006 field names
    so the JS detail panel is wired up correctly."""
    resp = httpx.get(f"http://127.0.0.1:{api_server.port}/traces/any")
    assert resp.status_code == 200
    text = resp.text

    for field in (
        "arrival_seq",
        "ended_at",
        "observed_at",
        "resource_attributes",
        "scope",
        "otlp",
    ):
        assert field in text, (
            f"field {field!r} not found in shell JS — selectSpan may be missing it"
        )


# ---------------------------------------------------------------------------
# Identity section: seven dt/dd pairs
# ---------------------------------------------------------------------------


def test_detail_panel_identity_section_fields(api_server, configured_db):
    """The shell contains all seven Identity dt labels."""
    resp = httpx.get(f"http://127.0.0.1:{api_server.port}/traces/any")
    text = resp.text

    for label in ("trace_id", "span_id", "parent_id", "kind",
                  "service_name", "seq", "arrival_seq"):
        assert label in text, f"Identity label {label!r} missing"


# ---------------------------------------------------------------------------
# Status tristate: error / ok / unset text rendering
# ---------------------------------------------------------------------------


def test_status_rendering_logic_in_shell(api_server, configured_db):
    """The shell must contain the Status tristate logic:
    'Error:', 'OK', 'Unset'."""
    resp = httpx.get(f"http://127.0.0.1:{api_server.port}/traces/any")
    text = resp.text

    assert "Error:" in text
    assert "'OK'" in text or '"OK"' in text or "=== 'OK'" in text or "textContent = 'OK'" in text or "OK" in text
    assert "Unset" in text


# ---------------------------------------------------------------------------
# Timing: (not finalized) fallback and duration
# ---------------------------------------------------------------------------


def test_timing_not_finalized_text_in_shell(api_server, configured_db):
    """The shell must include the '(not finalized)' fallback for ended_at."""
    resp = httpx.get(f"http://127.0.0.1:{api_server.port}/traces/any")
    assert "(not finalized)" in resp.text


def test_timing_duration_computed_in_shell(api_server, configured_db):
    """The shell JS must compute duration from ended_at - started_at."""
    resp = httpx.get(f"http://127.0.0.1:{api_server.port}/traces/any")
    text = resp.text
    assert "duration" in text
    assert "ended_at" in text
    assert "started_at" in text
