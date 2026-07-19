"""Tests for the subtree-expansion resource — issue #14.

The trace-tree UI's lazy expansion calls
``GET /api/traces/{tid}/spans/{sid}/children?cursor=...&limit=...``
(ADR-0018 replaced the ``GET /spans?trace_id&parent_id`` query shape). This
file asserts that surface end-to-end through the Starlette app: direct-children
scoping, sort order, cursor pagination, and the trace-tree render columns
(``kind``, ``error``, ``status_message``, ``events``, ``links``) present on
JSON-serialized rows (fetched via the whole-trace collection
``GET /api/traces/{tid}/spans``).
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


def _insert(
    conn,
    *,
    trace_id: str,
    span_id: str,
    name: str,
    parent_id: str | None = None,
    started_at: dt.datetime | None = None,
    error: bool | None = None,
    status_message: str | None = None,
    kind: str = "INTERNAL",
    events: list | None = None,
    links: list | None = None,
) -> None:
    events_json = json.dumps(events) if events is not None else None
    links_json = json.dumps(links) if links is not None else None
    if started_at is None:
        started_at_sql = "now()"
        params: tuple = (
            trace_id, span_id, parent_id, kind, name,
            error, status_message, events_json, links_json,
        )
    else:
        started_at_sql = "%s"
        params = (
            trace_id, span_id, parent_id, kind, name,
            started_at, error, status_message, events_json, links_json,
        )

    conn.execute(
        f"""
        INSERT INTO spans (
            trace_id, span_id, parent_id, kind, name,
            started_at, error, status_message, events, links,
            attributes, seq, arrival_seq, observed_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            {started_at_sql}, %s, %s, %s::jsonb, %s::jsonb,
            '{{}}'::jsonb, nextval('spans_seq'), currval('spans_seq'), now()
        )
        """,
        params,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Direct children over the wire
# ---------------------------------------------------------------------------


def _children_url(server: SpansApiServer, trace_id: str, parent_id: str) -> str:
    return f"{_base_url(server)}/api/traces/{trace_id}/spans/{parent_id}/children"


def test_children_returns_direct_children(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="T", span_id="root", name="root",
            parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="T", span_id="c1", name="c1",
            parent_id="root",
            started_at=dt.datetime(2026, 5, 1, 11, 0, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="T", span_id="gc", name="grandchild",
            parent_id="c1",  # NOT a direct child of root
            started_at=dt.datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        )

    resp = httpx.get(_children_url(api_server, "T", "root"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"] is None
    assert [s["span_id"] for s in body["spans"]] == ["c1"]


def test_children_sort_seq_asc_over_wire(api_server, configured_db):
    """PROJECT.md §6 Path 3 over the wire: parent_id set → seq asc.

    Insert children with ``started_at`` deliberately uncorrelated
    with insertion order (c-arrived-first has the *latest*
    ``started_at``). Under the new contract the response is in seq
    order (= insertion order on this single-receiver test), not
    ``started_at`` order — keeping the sort axis aligned with the
    cursor axis on the lazy-expansion path.
    """
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="T", span_id="root", name="root",
            parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="T", span_id="c-arrived-first", name="c1",
            parent_id="root",
            started_at=dt.datetime(2026, 5, 1, 14, 0, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="T", span_id="c-arrived-second", name="c2",
            parent_id="root",
            started_at=dt.datetime(2026, 5, 1, 11, 0, tzinfo=UTC),
        )

    resp = httpx.get(_children_url(api_server, "T", "root"))
    assert [s["span_id"] for s in resp.json()["spans"]] == [
        "c-arrived-first", "c-arrived-second",
    ]


def test_children_paginate_wide_fanout_over_wire(
    api_server, configured_db,
):
    """A parent with > limit children paginates across multiple calls,
    matching the lazy-expansion idiom the trace-tree UI uses."""
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="T", span_id="p", name="p",
            parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 8, 0, tzinfo=UTC),
        )
        for i in range(7):
            _insert(
                conn, trace_id="T", span_id=f"c{i:02d}", name=f"c{i:02d}",
                parent_id="p",
                started_at=dt.datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
                + dt.timedelta(seconds=i),
            )

    seen: list[str] = []
    cursor: int | None = None
    for _ in range(10):
        params: dict[str, object] = {"limit": 3}
        if cursor is not None:
            params["cursor"] = cursor
        page = httpx.get(
            _children_url(api_server, "T", "p"), params=params
        ).json()
        if not page["spans"]:
            break
        seen.extend(s["span_id"] for s in page["spans"])
        cursor = max(s["seq"] for s in page["spans"])

    assert seen == [f"c{i:02d}" for i in range(7)]


# ---------------------------------------------------------------------------
# JSON shape — render columns the UI relies on
# ---------------------------------------------------------------------------


def test_render_columns_serialized_for_trace_tree(api_server, configured_db):
    """The UI row needs ``kind`` for the icon and ``error`` /
    ``status_message`` for the error badge. The detail panel needs
    ``events`` and ``links``."""
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="T", span_id="s", name="server-call",
            parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
            kind="SERVER",
            error=True,
            status_message="upstream timeout",
            events=[
                {"name": "exception", "time_unix_nano": 1, "attributes": {}}
            ],
            links=[
                {"trace_id": "T2", "span_id": "x", "attributes": {}}
            ],
        )

    body = httpx.get(
        f"{_base_url(api_server)}/api/traces/T/spans",
    ).json()
    [row] = body["spans"]
    assert row["kind"] == "SERVER"
    assert row["error"] is True
    assert row["status_message"] == "upstream timeout"
    assert row["events"][0]["name"] == "exception"
    assert row["links"][0]["trace_id"] == "T2"


def test_render_columns_default_to_none(api_server, configured_db):
    """A span with OTLP UNSET status / no events / no links should
    serialize them as ``null`` (not ``[]``) so the UI can distinguish
    "no events arrived" from an empty array."""
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="T", span_id="s", name="quiet",
            parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        )

    body = httpx.get(
        f"{_base_url(api_server)}/api/traces/T/spans",
    ).json()
    [row] = body["spans"]
    assert row["error"] is None
    assert row["status_message"] is None
    assert row["events"] is None
    assert row["links"] is None
