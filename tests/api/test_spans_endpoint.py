"""Tests for GET /spans — issue #4 acceptance criteria."""

from __future__ import annotations

import psycopg
import pytest
import httpx

from data_governance.api import SpansApiServer


def _insert_span(conn, *, trace_id: str, span_id: str, name: str):
    conn.execute(
        """
        INSERT INTO spans (
            trace_id, span_id, parent_id, kind, name,
            started_at, attributes, seq, arrival_seq, observed_at
        ) VALUES (
            %s, %s, NULL, 'INTERNAL', %s,
            now(), '{}'::jsonb, nextval('spans_seq'), currval('spans_seq'), now()
        )
        """,
        (trace_id, span_id, name),
    )
    conn.commit()


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


# ---------------------------------------------------------------------------
# GET /spans tests
# ---------------------------------------------------------------------------


def test_get_spans_returns_json(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _insert_span(conn, trace_id="t1", span_id="s1", name="hello")

    resp = httpx.get(f"{_base_url(api_server)}/spans")
    assert resp.status_code == 200
    data = resp.json()
    assert "spans" in data
    assert "counts" in data
    assert data["counts"] is None


def test_get_spans_returns_inserted_span(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _insert_span(conn, trace_id="trace-X", span_id="span-X", name="my-span")

    resp = httpx.get(f"{_base_url(api_server)}/spans")
    assert resp.status_code == 200
    spans = resp.json()["spans"]
    assert len(spans) == 1
    assert spans[0]["name"] == "my-span"
    assert spans[0]["trace_id"] == "trace-X"
    assert spans[0]["span_id"] == "span-X"


def test_get_spans_trace_id_filter(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _insert_span(conn, trace_id="trace-A", span_id="s1", name="A1")
        _insert_span(conn, trace_id="trace-B", span_id="s2", name="B1")

    resp = httpx.get(f"{_base_url(api_server)}/spans", params={"trace_id": "trace-A"})
    assert resp.status_code == 200
    spans = resp.json()["spans"]
    assert len(spans) == 1
    assert spans[0]["trace_id"] == "trace-A"


def test_get_spans_trace_id_and_span_id(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _insert_span(conn, trace_id="trace-A", span_id="s1", name="A1")
        _insert_span(conn, trace_id="trace-A", span_id="s2", name="A2")

    resp = httpx.get(
        f"{_base_url(api_server)}/spans",
        params={"trace_id": "trace-A", "span_id": "s1"},
    )
    assert resp.status_code == 200
    spans = resp.json()["spans"]
    assert len(spans) == 1
    assert spans[0]["span_id"] == "s1"


def test_get_spans_order_desc(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _insert_span(conn, trace_id="t", span_id="s1", name="first")
        _insert_span(conn, trace_id="t", span_id="s2", name="second")

    resp = httpx.get(f"{_base_url(api_server)}/spans", params={"order": "desc"})
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()["spans"]]
    assert names == ["second", "first"]


def test_get_spans_limit_above_500_returns_400(api_server, configured_db):
    resp = httpx.get(f"{_base_url(api_server)}/spans", params={"limit": 501})
    assert resp.status_code == 400
    assert "500" in resp.json()["error"]


def test_get_spans_cursor_pagination(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        for i in range(5):
            _insert_span(conn, trace_id="t", span_id=f"s{i}", name=f"span-{i}")

    page1 = httpx.get(f"{_base_url(api_server)}/spans", params={"limit": 3}).json()
    assert len(page1["spans"]) == 3
    last_seq = page1["spans"][-1]["seq"]

    page2 = httpx.get(
        f"{_base_url(api_server)}/spans", params={"limit": 3, "cursor": last_seq}
    ).json()
    assert len(page2["spans"]) == 2
    assert all(s["seq"] > last_seq for s in page2["spans"])
