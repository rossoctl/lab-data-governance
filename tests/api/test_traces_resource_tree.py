"""Tests for the /api/traces resource tree — ADR-0018 (retire GET /spans).

``GET /spans`` is gone; the five query shapes it multiplexed are now
addressable resources under ``/api/``:

- ``GET /api/traces``                              → ``{"traces": [TraceListingEntry]}``
- ``GET /api/traces/{tid}``                        → one ``TraceListingEntry``
- ``GET /api/traces/{tid}/spans``                  → whole trace, flat, paginated
- ``GET /api/traces/{tid}/spans/{sid}``            → one ``Span``
- ``GET /api/traces/{tid}/spans/{sid}/children``   → direct children, keyset-paginated

``TraceListingEntry`` shape (collection element AND singular body, identical):
``{trace_id, listing_root, counts:{total, in_window, error_count}, in_time_window}``.
"""

from __future__ import annotations

import datetime as dt

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
) -> None:
    """Insert one span. Mirrors test_spans_endpoint_listing_roots._insert."""
    conn.execute(
        """
        INSERT INTO spans (
            trace_id, span_id, parent_id, kind, name,
            started_at, error, attributes,
            seq, arrival_seq, observed_at
        ) VALUES (
            %s, %s, %s, 'INTERNAL', %s,
            COALESCE(%s, now()), %s, '{}'::jsonb,
            nextval('spans_seq'), currval('spans_seq'), now()
        )
        """,
        (trace_id, span_id, parent_id, name, started_at, error),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# GET /api/traces — recent-traces feed
# ---------------------------------------------------------------------------


def test_traces_returns_trace_listing_entries(api_server, configured_db):
    """The feed is trace-shaped: {traces:[{trace_id, listing_root, counts,
    in_time_window}]} — the listing root nested, not a span list + sidecar."""
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
    assert set(body.keys()) == {"traces"}
    assert len(body["traces"]) == 1

    entry = body["traces"][0]
    assert set(entry.keys()) == {
        "trace_id", "listing_root", "counts", "in_time_window"
    }
    assert entry["trace_id"] == "A"
    assert entry["listing_root"]["span_id"] == "rA"
    assert entry["counts"] == {"total": 2, "in_window": 2, "error_count": 1}
    assert entry["in_time_window"] is True


# ---------------------------------------------------------------------------
# GET /api/traces/{tid} — cold-open seed (one TraceListingEntry)
# ---------------------------------------------------------------------------


def test_trace_singular_returns_one_listing_entry(api_server, configured_db):
    """The cold-open seed. Same TraceListingEntry shape as a collection
    element — not wrapped in {"traces": [...]} (ADR-0018: collection and
    singular are symmetric)."""
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="A", span_id="rA", name="rA", parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 12, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="A", span_id="cA", name="cA", parent_id="rA",
            started_at=dt.datetime(2026, 5, 1, 12, 30, tzinfo=UTC),
        )
        # A second trace that must NOT leak into the singular response.
        _insert(
            conn, trace_id="B", span_id="rB", name="rB", parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 13, tzinfo=UTC),
        )

    resp = httpx.get(f"{_base_url(api_server)}/api/traces/A")
    assert resp.status_code == 200
    entry = resp.json()
    assert set(entry.keys()) == {
        "trace_id", "listing_root", "counts", "in_time_window"
    }
    assert entry["trace_id"] == "A"
    assert entry["listing_root"]["span_id"] == "rA"
    assert entry["counts"] == {"total": 2, "in_window": 2, "error_count": 0}


def test_trace_singular_unknown_returns_404(api_server, configured_db):
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/traces/{tid}/spans/{sid} — one Span
# ---------------------------------------------------------------------------


def test_single_span_returns_full_row(api_server, configured_db):
    """Was /spans?trace_id&span_id. Returns the full-row Span object —
    ADR-0006's one-shape-one-contract, same object as a collection element."""
    with psycopg.connect(configured_db) as conn:
        _insert(conn, trace_id="T", span_id="s1", name="first")
        _insert(conn, trace_id="T", span_id="s2", name="second")

    resp = httpx.get(f"{_base_url(api_server)}/api/traces/T/spans/s1")
    assert resp.status_code == 200
    span = resp.json()
    assert span["trace_id"] == "T"
    assert span["span_id"] == "s1"
    assert span["name"] == "first"
    # Full-row Span, not wrapped in {"spans": [...]} and no sidecar counts.
    assert "seq" in span and "attributes" in span and "arrival_seq" in span


def test_single_span_unknown_returns_404(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _insert(conn, trace_id="T", span_id="s1", name="first")

    resp = httpx.get(f"{_base_url(api_server)}/api/traces/T/spans/nope")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/traces/{tid}/spans/{sid}/children — direct children, keyset-paginated
# ---------------------------------------------------------------------------


def test_children_returns_direct_children_only(api_server, configured_db):
    """Was /spans?trace_id&parent_id. Direct children of {sid} only — not
    grandchildren. Shape is {"spans": [...], "counts": null}."""
    with psycopg.connect(configured_db) as conn:
        _insert(conn, trace_id="T", span_id="root", name="root", parent_id=None)
        _insert(conn, trace_id="T", span_id="c1", name="c1", parent_id="root")
        _insert(conn, trace_id="T", span_id="c2", name="c2", parent_id="root")
        _insert(conn, trace_id="T", span_id="gc", name="gc", parent_id="c1")

    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces/T/spans/root/children"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"] is None
    ids = {s["span_id"] for s in body["spans"]}
    assert ids == {"c1", "c2"}


def test_children_keyset_paginates(api_server, configured_db):
    """cursor + limit walk the direct children by seq asc, no skips/dups."""
    with psycopg.connect(configured_db) as conn:
        _insert(conn, trace_id="T", span_id="p", name="p", parent_id=None)
        for i in range(5):
            _insert(conn, trace_id="T", span_id=f"k{i}", name=f"k{i}", parent_id="p")

    base = f"{_base_url(api_server)}/api/traces/T/spans/p/children"
    page1 = httpx.get(base, params={"limit": 3}).json()["spans"]
    assert len(page1) == 3
    last_seq = page1[-1]["seq"]

    page2 = httpx.get(base, params={"limit": 3, "cursor": last_seq}).json()["spans"]
    assert len(page2) == 2
    assert all(s["seq"] > last_seq for s in page2)


# ---------------------------------------------------------------------------
# GET /api/traces/{tid}/spans — whole trace, flat, paginated
# ---------------------------------------------------------------------------


def test_whole_trace_returns_all_spans_flat(api_server, configured_db):
    """Was /spans?trace_id. Every span in the trace, flat (no current UI
    caller — shipped anyway per ADR-0018). Other traces excluded."""
    with psycopg.connect(configured_db) as conn:
        _insert(conn, trace_id="T", span_id="root", name="root", parent_id=None)
        _insert(conn, trace_id="T", span_id="c1", name="c1", parent_id="root")
        _insert(conn, trace_id="T", span_id="gc", name="gc", parent_id="c1")
        _insert(conn, trace_id="OTHER", span_id="x", name="x", parent_id=None)

    resp = httpx.get(f"{_base_url(api_server)}/api/traces/T/spans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"] is None
    ids = {s["span_id"] for s in body["spans"]}
    assert ids == {"root", "c1", "gc"}


def test_whole_trace_paginates(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        for i in range(5):
            _insert(conn, trace_id="T", span_id=f"s{i}", name=f"s{i}", parent_id=None)

    base = f"{_base_url(api_server)}/api/traces/T/spans"
    page1 = httpx.get(base, params={"limit": 3}).json()["spans"]
    assert len(page1) == 3
    last_seq = page1[-1]["seq"]
    page2 = httpx.get(base, params={"limit": 3, "cursor": last_seq}).json()["spans"]
    assert len(page2) == 2
    assert all(s["seq"] > last_seq for s in page2)


# ---------------------------------------------------------------------------
# GET /spans is retired
# ---------------------------------------------------------------------------


def test_old_spans_route_is_gone(api_server, configured_db):
    """ADR-0018: the /spans pass-through is removed entirely."""
    resp = httpx.get(f"{_base_url(api_server)}/spans")
    assert resp.status_code == 404


def test_traces_feed_limit_above_500_returns_400(api_server, configured_db):
    """The 500-row hard cap (PROJECT.md §6) still fires on the recent-traces
    feed — the one route a browser actually paginates."""
    resp = httpx.get(f"{_base_url(api_server)}/api/traces", params={"limit": 501})
    assert resp.status_code == 400
    assert "500" in resp.json()["error"]


# ---------------------------------------------------------------------------
# Namespacing: pages under /ui/, root redirects (ADR-0017)
# ---------------------------------------------------------------------------


def test_root_redirects_to_ui(api_server, configured_db):
    resp = httpx.get(f"{_base_url(api_server)}/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/ui/"


def test_ui_index_serves_shell(api_server, configured_db):
    resp = httpx.get(f"{_base_url(api_server)}/ui/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


def test_ui_trace_tree_page_serves_shell(api_server, configured_db):
    resp = httpx.get(f"{_base_url(api_server)}/ui/traces/abcdef")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Trace tree" in resp.text


def test_healthz_stays_at_root(api_server, configured_db):
    """The infra probe is un-prefixed (ADR-0017)."""
    resp = httpx.get(f"{_base_url(api_server)}/healthz")
    assert resp.status_code in (200, 503)
