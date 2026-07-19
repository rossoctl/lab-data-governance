"""Tests for the recent-traces listing / time-window slice — issue #12.

Covers the ``GET /api/traces`` surface that inherits the ``get_spans``
listing-root parameters (ADR-0018 retired the ``GET /spans?root_only=true``
shape these once exercised):

- ``time_from`` / ``time_to`` ISO-8601 parsing (Z and explicit offset),
  naive-datetime rejection.
- listing-root selection + per-trace **Trace counts** on the
  **TraceListingEntry**.
- ``in_time_window`` on the entry.

The parameter-compatibility raises (``root_only`` vs ``span_id`` /
``parent_id``, ``parent_id`` requires ``trace_id``) no longer have an HTTP
surface — those illegal combinations are unexpressible in the resource tree —
so they are covered at the library level under ``tests/retrieval/``.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import psycopg

from data_governance.api import SpansApiServer

UTC = dt.timezone.utc


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


# Reuse the api/conftest.py insert_span (NULL parent_id, started_at=now()).
# For tests that need explicit started_at / parent_id, drop down to raw SQL.
def _insert(
    conn,
    *,
    trace_id: str,
    span_id: str,
    name: str,
    parent_id: str | None = None,
    started_at: dt.datetime | None = None,
    error: bool | None = None,
) -> None:
    if started_at is None:
        conn.execute(
            """
            INSERT INTO spans (
                trace_id, span_id, parent_id, kind, name,
                started_at, error, attributes,
                seq, arrival_seq, observed_at
            ) VALUES (
                %s, %s, %s, 'INTERNAL', %s,
                now(), %s, '{}'::jsonb,
                nextval('spans_seq'), currval('spans_seq'), now()
            )
            """,
            (trace_id, span_id, parent_id, name, error),
        )
    else:
        conn.execute(
            """
            INSERT INTO spans (
                trace_id, span_id, parent_id, kind, name,
                started_at, error, attributes,
                seq, arrival_seq, observed_at
            ) VALUES (
                %s, %s, %s, 'INTERNAL', %s,
                %s, %s, '{}'::jsonb,
                nextval('spans_seq'), currval('spans_seq'), now()
            )
            """,
            (trace_id, span_id, parent_id, name, started_at, error),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# ISO-8601 datetime parsing
# ---------------------------------------------------------------------------


def test_naive_iso_time_from_returns_400(api_server, configured_db):
    # No "Z", no offset.
    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces",
        params={"time_from": "2026-05-01T12:00:00"},
    )
    assert resp.status_code == 400
    assert "naive" in resp.json()["error"].lower()


def test_naive_iso_time_to_returns_400(api_server, configured_db):
    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces",
        params={"time_to": "2026-05-01T12:00:00"},
    )
    assert resp.status_code == 400
    assert "naive" in resp.json()["error"].lower()


def test_iso_with_z_accepted(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="t", span_id="s",
            name="x", started_at=dt.datetime(2026, 5, 1, 12, tzinfo=UTC),
        )

    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces",
        params={"time_from": "2026-05-01T11:00:00Z"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["traces"]) == 1


def test_iso_with_explicit_offset_accepted(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="t", span_id="s",
            name="x", started_at=dt.datetime(2026, 5, 1, 12, tzinfo=UTC),
        )

    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces",
        params={"time_from": "2026-05-01T13:00:00+02:00"},  # = 11:00 UTC
    )
    assert resp.status_code == 200
    assert len(resp.json()["traces"]) == 1


def test_garbage_iso_returns_400(api_server, configured_db):
    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces",
        params={"time_from": "not-a-date"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Listing roots + per-trace counts on the TraceListingEntry
# ---------------------------------------------------------------------------


def test_traces_returns_listing_roots_with_counts(
    api_server, configured_db
):
    # Trace A: real root + child
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="A", span_id="rA", name="rA",
            parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 12, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="A", span_id="cA", name="cA",
            parent_id="rA",
            started_at=dt.datetime(2026, 5, 1, 12, 30, tzinfo=UTC),
            error=True,
        )

    resp = httpx.get(f"{_base_url(api_server)}/api/traces")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["traces"]) == 1
    entry = body["traces"][0]
    assert entry["listing_root"]["span_id"] == "rA"
    assert entry["counts"] == {"total": 2, "in_window": 2, "error_count": 1}


def test_in_time_window_in_response(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        # Real root well outside window
        _insert(
            conn, trace_id="T", span_id="r", name="r",
            parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 8, tzinfo=UTC),
        )
        # Child inside window
        _insert(
            conn, trace_id="T", span_id="c", name="c",
            parent_id="r",
            started_at=dt.datetime(2026, 5, 1, 12, tzinfo=UTC),
        )

    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces",
        params={
            "time_from": "2026-05-01T10:00:00Z",
            "time_to": "2026-05-01T14:00:00Z",
        },
    )
    assert resp.status_code == 200
    traces = resp.json()["traces"]
    assert len(traces) == 1
    assert traces[0]["listing_root"]["span_id"] == "r"
    assert traces[0]["in_time_window"] is False


# ---------------------------------------------------------------------------
# Sanity: response is valid JSON with the expected shape
# ---------------------------------------------------------------------------


def test_traces_response_shape(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="X", span_id="rX", name="rX",
            parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 12, tzinfo=UTC),
        )

    resp = httpx.get(f"{_base_url(api_server)}/api/traces")
    assert resp.status_code == 200
    # Re-parse to make sure JSON is well-formed and has the TraceListingEntry shape.
    body = json.loads(resp.text)
    assert set(body.keys()) == {"traces"}
    (entry,) = body["traces"]
    assert set(entry.keys()) == {
        "trace_id", "listing_root", "counts", "in_time_window"
    }
