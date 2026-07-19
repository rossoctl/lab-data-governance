"""Wire-contract tests for the detail-panel Refresh flow — issue #50.

The rendered Refresh button now lives in the React SPA (ADR-0019); its
DOM-level behaviour (present, hidden-until-selected, error message) is covered
by the SPA's own tests. What remains here is the **API boundary** the Refresh
action exercises — unchanged by the UI migration:

- AC3/4: GET /api/traces/{tid}/spans/{sid} is the retrieval endpoint (ADR-0018,
  replacing GET /spans?trace_id&span_id); the response is the full-row Span.
- AC5: loadedSpans update path — verified through the wire contract
  (the same single-span call the button uses).
- AC7: E2E finalization scenario — the endpoint re-fetches after
  ended_at/seq/error/status_message have been updated in the DB and the
  fresh values appear on the wire.
"""

from __future__ import annotations

import datetime as dt

import httpx
import psycopg

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
# AC3/4: Wire contract — GET /api/traces/{tid}/spans/{sid} returns the span
# ---------------------------------------------------------------------------


def test_refresh_wire_contract_returns_single_span(api_server, configured_db):
    """GET /api/traces/{tid}/spans/{sid} — the exact call the button issues —
    returns the full-row Span object directly (ADR-0018)."""
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
        f"http://127.0.0.1:{api_server.port}/api/traces/T-refresh/spans/sp-alpha",
    )
    assert resp.status_code == 200
    s = resp.json()
    assert s["trace_id"] == "T-refresh"
    assert s["span_id"] == "sp-alpha"
    assert s["name"] == "alpha-span"
    assert s["service_name"] == "my-svc"
    assert s["kind"] == "SERVER"


def test_refresh_wire_contract_no_span_returns_404(api_server, configured_db):
    """GET /api/traces/{tid}/spans/{sid} returns 404 for an unknown span.
    The button JS checks for this and surfaces an error message."""
    resp = httpx.get(
        f"http://127.0.0.1:{api_server.port}/api/traces/T-missing/spans/no-such-span",
    )
    assert resp.status_code == 404


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
        f"http://127.0.0.1:{api_server.port}/api/traces/T-finalize/spans/sp-beta",
    )
    assert initial_resp.status_code == 200
    initial = initial_resp.json()
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
        f"http://127.0.0.1:{api_server.port}/api/traces/T-finalize/spans/sp-beta",
    )
    assert refresh_resp.status_code == 200
    fresh = refresh_resp.json()

    # Post-finalization values must reflect the DB update.
    assert fresh["ended_at"] is not None, "ended_at must be set after finalization"
    assert "2026-05-01" in fresh["ended_at"]
    assert fresh["error"] is True, "error must flip to True after finalization"
    assert fresh["status_message"] == "deadline exceeded"
    assert fresh["seq"] > initial_seq, "seq must advance on finalization"
