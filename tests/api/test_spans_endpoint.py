"""Tests for the trace/span resource tree — issue #4 acceptance criteria.

Originally the issue #4 tracer bullet for ``GET /spans``. ADR-0018 retired the
multi-shape ``/spans`` route; the span reads now live under
``/api/traces/{tid}/spans`` (whole trace, ``{spans, counts}``) and
``/api/traces/{tid}/spans/{sid}`` (one ``Span``). The library ``get_spans``
these call is unchanged, so the #4 acceptance criteria (trace/span filters,
order, limit cap, cursor pagination) are pinned here against the new routes.
"""

from __future__ import annotations

import psycopg
import httpx

from data_governance.api import SpansApiServer


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


# ---------------------------------------------------------------------------
# GET /api/traces/{tid}/spans — whole trace, flat
# ---------------------------------------------------------------------------


def test_trace_spans_returns_json(api_server, configured_db, insert_span):
    with psycopg.connect(configured_db) as conn:
        insert_span(conn, trace_id="t1", span_id="s1", name="hello")

    resp = httpx.get(f"{_base_url(api_server)}/api/traces/t1/spans")
    assert resp.status_code == 200
    data = resp.json()
    assert "spans" in data
    assert "counts" in data
    assert data["counts"] is None


def test_trace_spans_returns_inserted_span(api_server, configured_db, insert_span):
    with psycopg.connect(configured_db) as conn:
        insert_span(conn, trace_id="trace-X", span_id="span-X", name="my-span")

    resp = httpx.get(f"{_base_url(api_server)}/api/traces/trace-X/spans")
    assert resp.status_code == 200
    spans = resp.json()["spans"]
    assert len(spans) == 1
    assert spans[0]["name"] == "my-span"
    assert spans[0]["trace_id"] == "trace-X"
    assert spans[0]["span_id"] == "span-X"


def test_trace_spans_scopes_to_the_trace(api_server, configured_db, insert_span):
    with psycopg.connect(configured_db) as conn:
        insert_span(conn, trace_id="trace-A", span_id="s1", name="A1")
        insert_span(conn, trace_id="trace-B", span_id="s2", name="B1")

    resp = httpx.get(f"{_base_url(api_server)}/api/traces/trace-A/spans")
    assert resp.status_code == 200
    spans = resp.json()["spans"]
    assert len(spans) == 1
    assert spans[0]["trace_id"] == "trace-A"


def test_single_span_by_trace_and_span_id(api_server, configured_db, insert_span):
    with psycopg.connect(configured_db) as conn:
        insert_span(conn, trace_id="trace-A", span_id="s1", name="A1")
        insert_span(conn, trace_id="trace-A", span_id="s2", name="A2")

    resp = httpx.get(f"{_base_url(api_server)}/api/traces/trace-A/spans/s1")
    assert resp.status_code == 200
    # Single-span route returns the Span object directly (ADR-0018).
    span = resp.json()
    assert span["span_id"] == "s1"


def test_trace_spans_order_desc(api_server, configured_db, insert_span):
    with psycopg.connect(configured_db) as conn:
        insert_span(conn, trace_id="t", span_id="s1", name="first")
        insert_span(conn, trace_id="t", span_id="s2", name="second")

    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces/t/spans", params={"order": "desc"}
    )
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()["spans"]]
    assert names == ["second", "first"]


def test_trace_spans_limit_above_500_returns_400(api_server, configured_db):
    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces/t/spans", params={"limit": 501}
    )
    assert resp.status_code == 400
    assert "500" in resp.json()["error"]


def test_trace_spans_cursor_pagination(api_server, configured_db, insert_span):
    with psycopg.connect(configured_db) as conn:
        for i in range(5):
            insert_span(conn, trace_id="t", span_id=f"s{i}", name=f"span-{i}")

    base = f"{_base_url(api_server)}/api/traces/t/spans"
    page1 = httpx.get(base, params={"limit": 3}).json()
    assert len(page1["spans"]) == 3
    last_seq = page1["spans"][-1]["seq"]

    page2 = httpx.get(base, params={"limit": 3, "cursor": last_seq}).json()
    assert len(page2["spans"]) == 2
    assert all(s["seq"] > last_seq for s in page2["spans"])
