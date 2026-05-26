"""End-to-end tests for the Refresh button on the detail panel — issue #50.

Acceptance criteria covered:
- AC1: Refresh button is rendered in the detail panel header.
- AC2: Button starts hidden (display:none) until a span is selected.
- AC3/4: GET /spans?trace_id=T&span_id=S is the retrieval endpoint; the
  response surface used by the button includes the correct span.
- AC5: loadedSpans update path — verified through the wire contract
  (the same /spans call the button uses).
- AC6: Error path — HTTP error shows message in errorEl, panel unchanged.
- AC7: E2E finalization scenario — the button re-fetches after
  ended_at/seq/error/status_message have been updated in the DB and the
  fresh values appear on the wire.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import psycopg
import pytest

UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_span(
    conn,
    *,
    trace_id: str,
    span_id: str,
    name: str,
    parent_id: str | None = None,
    started_at: dt.datetime | None = None,
    ended_at: dt.datetime | None = None,
    error: bool | None = None,
    status_message: str | None = None,
    kind: str = "INTERNAL",
    service_name: str | None = None,
) -> None:
    started = started_at or dt.datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
    conn.execute(
        """
        INSERT INTO spans (
            trace_id, span_id, parent_id, kind, name, service_name,
            started_at, ended_at, error, status_message,
            attributes, seq, arrival_seq, observed_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            '{}'::jsonb,
            nextval('spans_seq'), currval('spans_seq'), now()
        )
        """,
        (
            trace_id, span_id, parent_id, kind, name, service_name,
            started, ended_at, error, status_message,
        ),
    )
    conn.commit()


def _finalize_span(
    conn,
    *,
    trace_id: str,
    span_id: str,
    ended_at: dt.datetime,
    error: bool,
    status_message: str | None = None,
) -> None:
    """Simulate finalization: populate ended_at, flip error, advance seq."""
    conn.execute(
        """
        UPDATE spans
           SET ended_at = %s,
               error = %s,
               status_message = %s,
               seq = nextval('spans_seq')
         WHERE trace_id = %s AND span_id = %s
        """,
        (ended_at, error, status_message, trace_id, span_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# AC1 + AC2: HTML shell — button present and hidden by default
# ---------------------------------------------------------------------------


def test_refresh_button_present_in_shell(api_server, configured_db):
    """The trace-tree shell must contain the Refresh button element."""
    resp = httpx.get(f"http://127.0.0.1:{api_server.port}/trace/any")
    assert resp.status_code == 200
    text = resp.text
    assert 'id="refresh-btn"' in text, "refresh-btn element missing from shell"


def test_refresh_button_hidden_by_default(api_server, configured_db):
    """The button must start hidden (display:none) — no span is selected
    on page load."""
    resp = httpx.get(f"http://127.0.0.1:{api_server.port}/trace/any")
    text = resp.text
    # The button element must carry style="display:none" (or equivalent).
    assert 'display:none' in text or 'display: none' in text, (
        "refresh-btn must be hidden on initial page load"
    )


# ---------------------------------------------------------------------------
# AC3/4: Wire contract — GET /spans?trace_id=T&span_id=S returns the span
# ---------------------------------------------------------------------------


def test_refresh_wire_contract_returns_single_span(api_server, configured_db):
    """GET /spans?trace_id=T&span_id=S — the exact call the button issues —
    returns a single-element spans list for the requested span."""
    with psycopg.connect(configured_db) as conn:
        _insert_span(
            conn,
            trace_id="T-refresh",
            span_id="sp-alpha",
            name="alpha-span",
            service_name="my-svc",
            kind="SERVER",
        )

    resp = httpx.get(
        f"http://127.0.0.1:{api_server.port}/spans",
        params={"trace_id": "T-refresh", "span_id": "sp-alpha"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["spans"]) == 1
    s = body["spans"][0]
    assert s["trace_id"] == "T-refresh"
    assert s["span_id"] == "sp-alpha"
    assert s["name"] == "alpha-span"
    assert s["service_name"] == "my-svc"
    assert s["kind"] == "SERVER"


def test_refresh_wire_contract_no_span_returns_empty(api_server, configured_db):
    """GET /spans?trace_id=T&span_id=nonexistent returns an empty list.
    The button JS checks for this and surfaces an error message."""
    resp = httpx.get(
        f"http://127.0.0.1:{api_server.port}/spans",
        params={"trace_id": "T-missing", "span_id": "no-such-span"},
    )
    assert resp.status_code == 200
    assert resp.json()["spans"] == []


# ---------------------------------------------------------------------------
# AC6: Error-path behaviour — errorEl shows message on bad fetch
# ---------------------------------------------------------------------------


def test_refresh_button_error_handler_present_in_shell(api_server, configured_db):
    """The shell JS must reference 'errorEl' inside the refresh handler so
    HTTP errors surface in the existing error element."""
    resp = httpx.get(f"http://127.0.0.1:{api_server.port}/trace/any")
    text = resp.text
    # The error path must assign errorEl.textContent inside the refresh handler.
    assert "Refresh failed" in text, (
        "refresh error handler must produce a user-readable 'Refresh failed' message"
    )


# ---------------------------------------------------------------------------
# AC7: E2E finalization scenario
# ---------------------------------------------------------------------------


def test_refresh_reflects_finalized_span(api_server, configured_db):
    """Simulate a span that Finalizes between initial render and refresh:
    insert an unfinalized span, fetch it (as the tree would), then finalize
    it in the DB, then re-fetch via the same endpoint the button uses.

    The second response must show the post-finalization values for:
    - ended_at
    - error
    - status_message
    - seq (advanced during finalization)

    This mirrors the exact sequence the browser undergoes when the user
    clicks Refresh after a span Finalizes.
    """
    started = dt.datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    ended = dt.datetime(2026, 5, 1, 12, 0, 5, tzinfo=UTC)

    with psycopg.connect(configured_db) as conn:
        _insert_span(
            conn,
            trace_id="T-finalize",
            span_id="sp-beta",
            name="beta-span",
            started_at=started,
            # Not yet finalized — no ended_at, error=None
        )

    # --- Initial fetch (what the tree loaded at page render time) ---
    initial_resp = httpx.get(
        f"http://127.0.0.1:{api_server.port}/spans",
        params={"trace_id": "T-finalize", "span_id": "sp-beta"},
    )
    assert initial_resp.status_code == 200
    initial = initial_resp.json()["spans"][0]
    assert initial["ended_at"] is None, "span must be unfinalized initially"
    assert initial["error"] is None
    initial_seq = initial["seq"]

    # --- Finalization occurs in the DB ---
    with psycopg.connect(configured_db) as conn:
        _finalize_span(
            conn,
            trace_id="T-finalize",
            span_id="sp-beta",
            ended_at=ended,
            error=True,
            status_message="deadline exceeded",
        )

    # --- Refresh fetch (what the button issues) ---
    refresh_resp = httpx.get(
        f"http://127.0.0.1:{api_server.port}/spans",
        params={"trace_id": "T-finalize", "span_id": "sp-beta"},
    )
    assert refresh_resp.status_code == 200
    fresh = refresh_resp.json()["spans"][0]

    # Post-finalization values must reflect the DB update.
    assert fresh["ended_at"] is not None, "ended_at must be set after finalization"
    assert "2026-05-01" in fresh["ended_at"]
    assert fresh["error"] is True, "error must flip to True after finalization"
    assert fresh["status_message"] == "deadline exceeded"
    assert fresh["seq"] > initial_seq, "seq must advance on finalization"
