"""End-to-end tests for the recent-traces UI view — issue #13.

The view is the default landing surface, rendered against ``GET /api/traces``
(ADR-0018 retired the former ``GET /spans?root_only=true``). These tests cover
the full acceptance-criterion matrix from issue #13:

- real-root-only traces
- orphan-only traces (listing-root fallback per ADR-0001)
- traces with both a real root and an orphan
- in-window vs out-of-window listing roots
- traces with errors (``error_count`` in counts)

The view itself is plain HTML/JS rendered by the existing
:mod:`data_governance.api` Starlette app. We exercise the API the view
calls (``GET /api/traces``, returning ``{traces:[TraceListingEntry]}``) and the
UI shell (``GET /ui/``) so the round-trip a browser would take is covered
without booting a browser-engine. Each **TraceListingEntry** nests its listing
root under ``listing_root`` and its counts under ``counts``. The
dedupe-by-trace_id and grey-out-on-out-of-window behaviours live in the shipped
JS; their *inputs* are asserted here at the API boundary.
"""

from __future__ import annotations

import datetime as dt

import httpx
import psycopg

from data_governance.api import SpansApiServer

UTC = dt.timezone.utc


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


# ---------------------------------------------------------------------------
# Direct-insert helper — pinned started_at, parent_id, error, service_name
# ---------------------------------------------------------------------------


def _insert(
    conn,
    *,
    trace_id: str,
    span_id: str,
    name: str,
    parent_id: str | None = None,
    started_at: dt.datetime | None = None,
    error: bool | None = None,
    service_name: str | None = None,
) -> None:
    if started_at is None:
        started_at_sql = "now()"
        params: tuple = (
            trace_id, span_id, parent_id, name, service_name, error,
        )
    else:
        started_at_sql = "%s"
        params = (
            trace_id, span_id, parent_id, name, service_name, started_at,
            error,
        )

    conn.execute(
        f"""
        INSERT INTO spans (
            trace_id, span_id, parent_id, kind, name, service_name,
            started_at, error, attributes,
            seq, arrival_seq, observed_at
        ) VALUES (
            %s, %s, %s, 'INTERNAL', %s, %s,
            {started_at_sql}, %s, '{{}}'::jsonb,
            nextval('spans_seq'), currval('spans_seq'), now()
        )
        """,
        params,
    )
    conn.commit()


def _seed_acceptance_matrix(conn) -> None:
    """Seed the AC matrix: real-root only, orphan only, both,
    in/out window, with errors.

    Window for these tests: ``[10:00, 14:00]`` UTC on 2026-05-01.
    """
    # Trace A: REAL-ROOT ONLY, in window, no errors.
    _insert(
        conn, trace_id="A", span_id="rA", name="A.real-root",
        parent_id=None,
        started_at=dt.datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        service_name="svc-A",
    )
    _insert(
        conn, trace_id="A", span_id="cA", name="A.child",
        parent_id="rA",
        started_at=dt.datetime(2026, 5, 1, 12, 30, tzinfo=UTC),
        service_name="svc-A",
    )

    # Trace B: ORPHAN ONLY (real root never arrived), in window, with error.
    _insert(
        conn, trace_id="B", span_id="oB", name="B.orphan",
        parent_id="missingB",  # parent never inserted -> orphan
        started_at=dt.datetime(2026, 5, 1, 13, 0, tzinfo=UTC),
        service_name="svc-B",
        error=True,
    )

    # Trace C: BOTH real root AND extra orphan; in window. Listing root
    # must prefer the real root per ADR-0001.
    _insert(
        conn, trace_id="C", span_id="rC", name="C.real-root",
        parent_id=None,
        started_at=dt.datetime(2026, 5, 1, 12, 15, tzinfo=UTC),
        service_name="svc-C",
    )
    _insert(
        conn, trace_id="C", span_id="oC", name="C.orphan",
        parent_id="missingC",
        started_at=dt.datetime(2026, 5, 1, 12, 20, tzinfo=UTC),
        service_name="svc-C",
    )

    # Trace D: real root OUT OF WINDOW (08:00) but child IN WINDOW (12:00).
    _insert(
        conn, trace_id="D", span_id="rD", name="D.real-root-early",
        parent_id=None,
        started_at=dt.datetime(2026, 5, 1, 8, 0, tzinfo=UTC),
        service_name="svc-D",
    )
    _insert(
        conn, trace_id="D", span_id="cD", name="D.child-in-window",
        parent_id="rD",
        started_at=dt.datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        service_name="svc-D",
    )

    # Trace E: ENTIRELY OUT OF WINDOW; should not appear.
    _insert(
        conn, trace_id="E", span_id="rE", name="E.real-root-very-early",
        parent_id=None,
        started_at=dt.datetime(2026, 5, 1, 5, 0, tzinfo=UTC),
        service_name="svc-E",
    )

    # Trace F: real root WITH error, in window.
    _insert(
        conn, trace_id="F", span_id="rF", name="F.real-root",
        parent_id=None,
        started_at=dt.datetime(2026, 5, 1, 12, 45, tzinfo=UTC),
        service_name="svc-F",
        error=True,
    )


_WINDOW_FROM = "2026-05-01T10:00:00Z"
_WINDOW_TO = "2026-05-01T14:00:00Z"


# ---------------------------------------------------------------------------
# Acceptance matrix — listing roots
# ---------------------------------------------------------------------------


def _entries_to_listing(body: dict) -> dict:
    """Flatten a ``GET /api/traces`` body into the legacy listing shape.

    ``GET /api/traces`` returns ``{traces:[TraceListingEntry]}`` where each
    entry nests the listing-root span under ``listing_root`` and the counts
    under ``counts`` (ADR-0018). These tests assert on the listing-root span
    fields and per-trace counts; rebuild the pre-ADR-0018 ``{spans, counts}``
    shape (listing-root span with ``in_time_window`` merged in, plus a
    ``{trace_id: counts}`` map) so the assertions read against the same
    inputs the JS view flattens to."""
    spans = []
    counts = {}
    for entry in body["traces"]:
        span = dict(entry["listing_root"])
        span["in_time_window"] = entry["in_time_window"]
        spans.append(span)
        counts[entry["trace_id"]] = entry["counts"]
    return {"spans": spans, "counts": counts}


def _fetch_listing(server: SpansApiServer) -> dict:
    resp = httpx.get(
        f"{_base_url(server)}/api/traces",
        params={
            "time_from": _WINDOW_FROM,
            "time_to": _WINDOW_TO,
            "limit": 20,
        },
    )
    assert resp.status_code == 200, resp.text
    return _entries_to_listing(resp.json())


def test_real_root_only_trace_uses_real_root_as_listing_root(
    api_server, configured_db
):
    with psycopg.connect(configured_db) as conn:
        _seed_acceptance_matrix(conn)

    body = _fetch_listing(api_server)
    by_trace = {s["trace_id"]: s for s in body["spans"]}
    assert by_trace["A"]["span_id"] == "rA"
    assert by_trace["A"]["parent_id"] is None  # "real root" badge case


def test_orphan_only_trace_uses_orphan_as_listing_root(
    api_server, configured_db
):
    with psycopg.connect(configured_db) as conn:
        _seed_acceptance_matrix(conn)

    body = _fetch_listing(api_server)
    by_trace = {s["trace_id"]: s for s in body["spans"]}
    assert by_trace["B"]["span_id"] == "oB"
    assert by_trace["B"]["parent_id"] == "missingB"  # "missing parent" badge


def test_trace_with_both_prefers_real_root(api_server, configured_db):
    with psycopg.connect(configured_db) as conn:
        _seed_acceptance_matrix(conn)

    body = _fetch_listing(api_server)
    by_trace = {s["trace_id"]: s for s in body["spans"]}
    assert by_trace["C"]["span_id"] == "rC"
    assert by_trace["C"]["parent_id"] is None


def test_in_window_trace_with_out_of_window_root_marked_not_in_window(
    api_server, configured_db
):
    """Trace D's real root is out-of-window but its child is in-window;
    the row appears in the listing with ``in_time_window=false`` so the
    UI greys it out."""
    with psycopg.connect(configured_db) as conn:
        _seed_acceptance_matrix(conn)

    body = _fetch_listing(api_server)
    by_trace = {s["trace_id"]: s for s in body["spans"]}
    assert by_trace["D"]["span_id"] == "rD"
    assert by_trace["D"]["in_time_window"] is False


def test_entirely_out_of_window_trace_excluded(api_server, configured_db):
    """Trace E has *no* in-window spans and must not appear at all."""
    with psycopg.connect(configured_db) as conn:
        _seed_acceptance_matrix(conn)

    body = _fetch_listing(api_server)
    trace_ids = {s["trace_id"] for s in body["spans"]}
    assert "E" not in trace_ids


def test_error_count_surfaced_per_trace(api_server, configured_db):
    """Trace B and Trace F each have one error span -> error_count=1.

    Traces A and C have no errors -> error_count=0. The UI renders the
    listing-row error count badge (#11) only when this is > 0."""
    with psycopg.connect(configured_db) as conn:
        _seed_acceptance_matrix(conn)

    body = _fetch_listing(api_server)
    counts = body["counts"]
    assert counts["A"]["error_count"] == 0
    assert counts["B"]["error_count"] == 1
    assert counts["C"]["error_count"] == 0
    assert counts["F"]["error_count"] == 1


def test_in_window_total_counts_drive_listing_display(
    api_server, configured_db
):
    """The "in_window / total" the UI shows comes straight from
    ``counts[trace_id]``."""
    with psycopg.connect(configured_db) as conn:
        _seed_acceptance_matrix(conn)

    body = _fetch_listing(api_server)
    counts = body["counts"]

    # Trace A: 2 spans, both in window.
    assert counts["A"] == {"total": 2, "in_window": 2, "error_count": 0}
    # Trace D: 2 spans, only the child is in window (the listing root is
    # out-of-window and per PROJECT.md §6 is excluded from in_window).
    assert counts["D"] == {"total": 2, "in_window": 1, "error_count": 0}


def test_service_name_returned_for_listing_row(api_server, configured_db):
    """The recent-traces row displays ``service_name``; assert the API
    surfaces it on each listing root."""
    with psycopg.connect(configured_db) as conn:
        _seed_acceptance_matrix(conn)

    body = _fetch_listing(api_server)
    by_trace = {s["trace_id"]: s for s in body["spans"]}
    assert by_trace["A"]["service_name"] == "svc-A"
    assert by_trace["B"]["service_name"] == "svc-B"


def test_default_landing_query_is_traces_limit_20(
    api_server, configured_db
):
    """The view's default landing query: GET /api/traces with limit=20.
    Smoke-test the API supports it (limit=20 is a query parameter, so this
    is really a sanity check that nothing bombs)."""
    with psycopg.connect(configured_db) as conn:
        _seed_acceptance_matrix(conn)

    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces",
        params={"limit": 20},
    )
    assert resp.status_code == 200
    body = resp.json()
    # No window supplied -> all in-window=true; no E exclusion -> 6 traces.
    assert len(body["traces"]) == 6


# ---------------------------------------------------------------------------
# Pagination by cursor — "Load more" semantics
# ---------------------------------------------------------------------------


def test_pagination_by_cursor_advances(api_server, configured_db):
    """Walk pages of size 2 over the AC matrix using the max-seq cursor
    strategy, until the API returns an empty page.

    The key invariant (issue #30 AC): every listing root that satisfies the
    filter must appear in the union of pages — "skips impossible". This holds
    because ``_listing_roots_paginated`` now uses a composite ``(started_at,
    span_id)`` keyset cursor aligned with the ``started_at DESC, span_id ASC``
    sort. Each page advances strictly forward in the sort order, so no row can
    be pushed past the cursor and silently excluded.

    The AC matrix has six traces (A–F) seeded with deliberately uncorrelated
    ``seq`` and ``started_at`` values. With the old seq-only cursor, traces D
    and E (whose listing roots have old ``started_at`` but high ``seq``) were
    silently skipped once the cursor advanced past their seq. With the
    composite cursor all six must appear.
    """
    with psycopg.connect(configured_db) as conn:
        _seed_acceptance_matrix(conn)

    seen_trace_ids: set[str] = set()
    cursor: int | None = None
    # Hard cap: six listing roots, page size 2 → at most 3 pages, plus headroom.
    # No time window: all six traces are in scope, including trace E whose
    # started_at is entirely outside the [10:00, 14:00] window used elsewhere.
    # This is exactly the repro from issue #30 — with a seq-only cursor, D and
    # E were silently skipped because their old started_at kept pushing them
    # off each page until the cursor advanced past their (relatively high) seq.
    for _ in range(20):
        params: dict[str, object] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        page = httpx.get(
            f"{_base_url(api_server)}/api/traces", params=params
        ).json()
        entries = page["traces"]
        if not entries:
            break
        seen_trace_ids.update(e["trace_id"] for e in entries)
        # Use the last entry in sort order (entries[-1]) as the cursor anchor,
        # not max(seq). The composite keyset cursor on the server resolves seq
        # to (started_at, span_id) and uses the page boundary position in the
        # started_at DESC, span_id ASC sort — the last element on the page. The
        # cursor is the listing root's seq, nested under the entry.
        cursor = entries[-1]["listing_root"]["seq"]
    else:  # pragma: no cover - hard-cap safety net
        raise AssertionError("pagination did not terminate")

    # All six listing roots must be reachable regardless of how seq and
    # started_at correlate — the "skips impossible" property from PROJECT.md §6
    # and ADR-0001, now enforced by the composite keyset cursor (issue #30).
    assert seen_trace_ids == {"A", "B", "C", "D", "E", "F"}, (
        f"some listing roots were skipped; seen: {seen_trace_ids!r}"
    )


# ---------------------------------------------------------------------------
# UI shell HTML — the implementing slice ships a recent-traces shell
# ---------------------------------------------------------------------------


def test_ui_shell_served_at_ui(api_server, configured_db):
    """``GET /ui/`` returns the recent-traces UI shell HTML (ADR-0017)."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


def test_ui_shell_calls_traces_listing_endpoint(
    api_server, configured_db
):
    """The shell is wired to call ``/api/traces`` for its initial render —
    the canonical endpoint for the recent-traces view (ADR-0018)."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/")
    assert "/api/traces" in resp.text


def test_ui_shell_default_page_size_is_20(api_server, configured_db):
    """Default page size is 20 (issue #13 spec)."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/")
    # The constant should appear in the JS literal that drives the request.
    assert "20" in resp.text


def test_ui_shell_has_design_classes_from_ui_design_doc(
    api_server, configured_db
):
    """Shell carries the design-token CSS classes from
    ``docs/ui-design.md`` §6 / §7 / §9 (greyed-out + tabular-num count)."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/")
    text = resp.text
    assert "dg-row--out-of-window" in text  # §6 greyed-out
    assert "dg-window-count" in text  # §7 in_window/total format


def test_ui_shell_has_missing_parent_filter_toggle(
    api_server, configured_db
):
    """Shell exposes the filter toggle that hides missing-parent
    listing roots (issue #13 acceptance criterion)."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/")
    # Use a stable id we can hook tests onto.
    assert 'id="hide-missing-parent"' in resp.text


def test_ui_shell_has_time_window_picker(api_server, configured_db):
    """Shell exposes a time-window selection surface (issue #13:
    "User-visible time-window selection ... at whatever granularity
    #11 settled on")."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/")
    assert 'id="time-window"' in resp.text


def test_ui_logic_js_asset_is_served(api_server, configured_db):
    """The dedupe / filter helpers ship as a sibling JS asset the
    shell loads via ``/ui/recent_traces_logic.js``."""
    resp = httpx.get(
        f"{_base_url(api_server)}/ui/recent_traces_logic.js"
    )
    assert resp.status_code == 200
    assert "javascript" in resp.headers.get("content-type", "")
    assert "dedupeByTraceId" in resp.text


def test_ui_asset_route_rejects_unknown_files(api_server, configured_db):
    """The asset route is whitelist-only and never functions as a
    generic file server (e.g. cannot exfiltrate ``index.html`` or
    arbitrary files)."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/index.html")
    assert resp.status_code == 404
    resp = httpx.get(f"{_base_url(api_server)}/ui/../api/__init__.py")
    # Starlette path matcher rejects with 404 anyway, but assert
    # explicitly so a regression in routing doesn't slip through.
    assert resp.status_code == 404
