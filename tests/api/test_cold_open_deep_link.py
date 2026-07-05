"""End-to-end tests for the cold-open / deep-link to /ui/traces/T — issue #15.

A user pastes a ``/ui/traces/T`` URL (or otherwise lands on the trace tree
without coming from the recent-traces listing). The UI must (routes shown
post-ADR-0017 / ADR-0018):

- Fetch the **TraceListingEntry** singular ``GET /api/traces/T`` (seed anchor).
- Render its ``listing_root`` as the tree root anchor via the same code path
  as the click-through flow.
- Drive missing-parent / real-root badging from the ``listing_root.parent_id``.
- Drive the error-count badge from the entry's ``counts`` (no second fetch).
- Render an empty state (404) when the trace has no spans at all.
- Preserve browser back/forward behaviour (real routes, not hashes).
- Support lazy subtree expansion via ``GET /api/traces/T/spans/P/children``.

These tests exercise the acceptance criteria matrix from issue #15.  The
``/ui/traces/{tid}`` server route, the cold-open fetch, and the empty-state
path all land in this slice; the subtree-expansion contract was already
covered by issue #14 (test_trace_tree_view.py) and is smoke-checked here in
the context of a cold-opened root.
"""

from __future__ import annotations

import datetime as dt

import httpx
import psycopg
import pytest

from data_governance.api import SpansApiServer

UTC = dt.timezone.utc


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


# ---------------------------------------------------------------------------
# Direct-insert helper
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
    status_message: str | None = None,
    kind: str = "INTERNAL",
    service_name: str | None = None,
) -> None:
    if started_at is None:
        ts_sql = "now()"
        params: tuple = (
            trace_id, span_id, parent_id, kind, name, service_name,
            error, status_message,
        )
    else:
        ts_sql = "%s"
        params = (
            trace_id, span_id, parent_id, kind, name, service_name,
            started_at, error, status_message,
        )
    conn.execute(
        f"""
        INSERT INTO spans (
            trace_id, span_id, parent_id, kind, name, service_name,
            started_at, error, status_message, attributes,
            seq, arrival_seq, observed_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            {ts_sql}, %s, %s, '{{}}'::jsonb,
            nextval('spans_seq'), currval('spans_seq'), now()
        )
        """,
        params,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# AC: /ui/traces/T URL is a real, shareable, bookmarkable route
# ---------------------------------------------------------------------------


def test_trace_route_returns_html_shell(api_server, configured_db):
    """/ui/traces/<trace_id> returns 200 HTML for any trace_id value.

    The route must be a real server-side route (not a hash fragment) so a
    pasted URL reaches the server and is served the same HTML shell
    regardless of whether the trace exists.
    """
    resp = httpx.get(f"{_base_url(api_server)}/ui/traces/some-trace-id-123")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


def test_trace_route_is_not_hash_navigation(api_server, configured_db):
    """Recent-traces click-through must use real /ui/traces/<id> routes, not hashes.

    A ``#/traces/<id>`` href never reaches the server on a paste/reload;
    only a real path does.  This pins the regression from the early
    hash-based prototype.
    """
    resp = httpx.get(f"{_base_url(api_server)}/ui/")
    assert "'#/traces/'" not in resp.text
    assert '"#/traces/"' not in resp.text


def test_multiple_distinct_trace_ids_each_served(api_server, configured_db):
    """Different trace IDs each get the same HTML shell — the trace_id is
    consumed by in-page JS from window.location.pathname, not from the HTML."""
    for tid in ("trace-aaa", "trace-bbb", "trace-ccc"):
        resp = httpx.get(f"{_base_url(api_server)}/ui/traces/{tid}")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# AC: cold-open fetches the TraceListingEntry singular, no window parameters
# ---------------------------------------------------------------------------


def test_cold_open_fetch_uses_traces_singular(api_server, configured_db):
    """The trace-tree shell must fetch the ``/api/traces/`` singular in the
    cold-open path, and must NOT include time-window parameters (the singular
    ignores the window; ADR-0018)."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/traces/T")
    html = resp.text

    # The cold-open fetch targets the /api/traces/ resource tree.
    assert "/api/traces/" in html
    # The retired multi-shape query surface must not reappear.
    assert "root_only" not in html

    # Time-window parameters must not appear in the trace-tree shell at all.
    assert "time_from" not in html
    assert "time_to" not in html


def test_cold_open_endpoint_returns_listing_root_and_counts(api_server, configured_db):
    """The API call the cold-open UI makes — GET /api/traces/T — returns the
    TraceListingEntry with the listing root nested and per-trace counts."""
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="CO", span_id="root", name="cold-open-root",
            parent_id=None, kind="SERVER", service_name="svc-co",
            started_at=dt.datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="CO", span_id="child-1", name="child-1",
            parent_id="root", kind="INTERNAL", service_name="svc-co",
            started_at=dt.datetime(2026, 5, 1, 12, 0, 1, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="CO", span_id="child-2", name="child-2",
            parent_id="root", kind="INTERNAL", service_name="svc-co",
            started_at=dt.datetime(2026, 5, 1, 12, 0, 2, tzinfo=UTC),
            error=True, status_message="something went wrong",
        )

    entry = httpx.get(f"{_base_url(api_server)}/api/traces/CO").json()

    # Listing root nested on the entry.
    assert entry["trace_id"] == "CO"
    assert entry["listing_root"]["span_id"] == "root"

    # Counts present and accurate.
    assert entry["counts"]["total"] == 3
    assert entry["counts"]["error_count"] == 1


def test_cold_open_ignores_time_window(api_server, configured_db):
    """The TraceListingEntry singular ignores any time window — the listing
    root is returned even when its started_at is old (ADR-0018)."""
    old_time = dt.datetime(2020, 1, 1, tzinfo=UTC)
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="OLD", span_id="root", name="old-root",
            parent_id=None,
            started_at=old_time,
        )

    entry = httpx.get(f"{_base_url(api_server)}/api/traces/OLD").json()
    # The listing root is returned; the singular takes no window.
    assert entry["listing_root"]["span_id"] == "root"


# ---------------------------------------------------------------------------
# AC: parent_id drives real-root vs missing-parent badging
# ---------------------------------------------------------------------------


def test_cold_open_real_root_has_null_parent_id(api_server, configured_db):
    """A cold-opened trace whose listing root is a real root (parent_id IS
    NULL) surfaces null parent_id — drives the 'Real root' badge in the UI."""
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="RR", span_id="root", name="real-root",
            parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        )

    entry = httpx.get(f"{_base_url(api_server)}/api/traces/RR").json()
    assert entry["listing_root"]["parent_id"] is None


def test_cold_open_orphan_has_non_null_parent_id(api_server, configured_db):
    """A cold-opened trace whose listing root is an orphan (no real root
    arrived) surfaces a non-null parent_id — drives the 'Missing parent'
    badge in the UI."""
    with psycopg.connect(configured_db) as conn:
        # Only an orphan span — its parent never arrived.
        _insert(
            conn, trace_id="MP", span_id="orphan", name="orphan-root",
            parent_id="missing-parent-id",
            started_at=dt.datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        )

    entry = httpx.get(f"{_base_url(api_server)}/api/traces/MP").json()
    assert entry["listing_root"]["parent_id"] == "missing-parent-id"


# ---------------------------------------------------------------------------
# AC: counts[T] drives error-count badging
# ---------------------------------------------------------------------------


def test_cold_open_counts_include_error_count(api_server, configured_db):
    """counts[T].error_count lets the UI render the error badge without a
    second fetch — same data shape as the click-through path from the
    recent-traces listing."""
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="EC", span_id="root", name="root",
            parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        )
        for i in range(3):
            _insert(
                conn, trace_id="EC", span_id=f"err-{i}", name=f"err-{i}",
                parent_id="root",
                started_at=dt.datetime(2026, 5, 1, 12, 0, 1, tzinfo=UTC),
                error=True, status_message=f"error {i}",
            )

    entry = httpx.get(f"{_base_url(api_server)}/api/traces/EC").json()
    assert entry["counts"]["error_count"] == 3


def test_cold_open_counts_zero_errors_when_no_errors(api_server, configured_db):
    """error_count is 0 for a fully healthy trace — no false positive badge."""
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="OK", span_id="root", name="root",
            parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="OK", span_id="child", name="child",
            parent_id="root",
            started_at=dt.datetime(2026, 5, 1, 12, 0, 1, tzinfo=UTC),
            error=False,
        )

    entry = httpx.get(f"{_base_url(api_server)}/api/traces/OK").json()
    assert entry["counts"]["error_count"] == 0


# ---------------------------------------------------------------------------
# AC: empty response (trace has no spans) renders an empty state
# ---------------------------------------------------------------------------


def test_cold_open_404_for_unknown_trace(api_server, configured_db):
    """GET /api/traces/T returns 404 when the trace does not exist. The UI
    renders 'Trace not found.' rather than crashing or hanging."""
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/no-such-trace")
    assert resp.status_code == 404


def test_cold_open_shell_contains_empty_state_message(api_server, configured_db):
    """The trace-tree HTML shell must include the 'trace not found' empty-state
    text so the in-page JS can surface it without a second round-trip."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/traces/anything")
    html = resp.text
    # The shell's init() renders this when the singular 404s / has no root.
    assert "not found" in html.lower() or "Trace not found" in html


# ---------------------------------------------------------------------------
# AC: lazy subtree expansion works identically to the click-through path
# ---------------------------------------------------------------------------


def test_cold_open_subtree_expansion_fetches_children(api_server, configured_db):
    """After cold-opening a trace, subtree expansion uses the same
    GET /api/traces/T/spans/P/children endpoint as the click-through path."""
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="EXP", span_id="root", name="root",
            parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="EXP", span_id="child-a", name="child-a",
            parent_id="root",
            started_at=dt.datetime(2026, 5, 1, 12, 0, 1, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="EXP", span_id="child-b", name="child-b",
            parent_id="root",
            started_at=dt.datetime(2026, 5, 1, 12, 0, 2, tzinfo=UTC),
        )

    # Step 1: cold-open fetch gets the listing root.
    entry = httpx.get(f"{_base_url(api_server)}/api/traces/EXP").json()
    assert entry["listing_root"]["span_id"] == "root"

    # Step 2: subtree expansion — the children sub-resource.
    children = httpx.get(
        f"{_base_url(api_server)}/api/traces/EXP/spans/root/children"
    ).json()
    child_ids = {s["span_id"] for s in children["spans"]}
    assert child_ids == {"child-a", "child-b"}


def test_cold_open_subtree_expansion_paginates_wide_fanout(api_server, configured_db):
    """Wide-fanout roots paginate correctly via cursor on the children
    sub-resource after a cold open — same contract as the click-through path."""
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="WF", span_id="root", name="root",
            parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        )
        for i in range(8):
            _insert(
                conn, trace_id="WF", span_id=f"child-{i:02d}",
                name=f"child-{i:02d}", parent_id="root",
                started_at=dt.datetime(2026, 5, 1, 12, 0, 1 + i, tzinfo=UTC),
            )

    base = f"{_base_url(api_server)}/api/traces/WF/spans/root/children"
    seen: list[str] = []
    cursor: int | None = None
    for _ in range(10):
        params: dict[str, object] = {"limit": 3}
        if cursor is not None:
            params["cursor"] = cursor
        page = httpx.get(base, params=params).json()
        if not page["spans"]:
            break
        seen.extend(s["span_id"] for s in page["spans"])
        cursor = max(s["seq"] for s in page["spans"])

    assert len(seen) == 8
    assert set(seen) == {f"child-{i:02d}" for i in range(8)}


# ---------------------------------------------------------------------------
# AC: browser back/forward — real routes, not hash navigation
# ---------------------------------------------------------------------------


def test_trace_route_and_index_route_are_separate_real_routes(
    api_server, configured_db,
):
    """Both /ui/ and /ui/traces/<id> must be distinct server-side routes so
    browser history entries are real URLs that survive a reload."""
    index_resp = httpx.get(f"{_base_url(api_server)}/ui/")
    trace_resp = httpx.get(f"{_base_url(api_server)}/ui/traces/abc")

    assert index_resp.status_code == 200
    assert trace_resp.status_code == 200
    # Different HTML shells.
    assert "Recent traces" in index_resp.text
    assert "Trace tree" in trace_resp.text


# ---------------------------------------------------------------------------
# AC: end-to-end — paste URL, walk tree, expand subtree
# ---------------------------------------------------------------------------


def test_end_to_end_cold_open_walk_and_expand(api_server, configured_db):
    """Simulates the full cold-open user journey:

    1. User pastes /ui/traces/E2E — server returns HTML shell (200).
    2. JS fetches GET /api/traces/E2E — returns the TraceListingEntry.
    3. JS expands the root's children via the children sub-resource.
    4. JS expands a child's grandchildren.
    5. Error spans at leaf level surface ``error=true``.
    6. The entry's counts.error_count reflects the error leaf.
    """
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="E2E", span_id="root", name="api-handler",
            parent_id=None, kind="SERVER", service_name="svc",
            started_at=dt.datetime(2026, 5, 1, 9, 0, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="E2E", span_id="db-call", name="db-call",
            parent_id="root", kind="CLIENT", service_name="svc",
            started_at=dt.datetime(2026, 5, 1, 9, 0, 1, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="E2E", span_id="cache-miss", name="cache-miss",
            parent_id="root", kind="INTERNAL", service_name="svc",
            started_at=dt.datetime(2026, 5, 1, 9, 0, 2, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="E2E", span_id="db-query", name="db-query",
            parent_id="db-call", kind="INTERNAL", service_name="svc",
            started_at=dt.datetime(2026, 5, 1, 9, 0, 3, tzinfo=UTC),
            error=True, status_message="timeout after 30s",
        )

    # Step 1: server serves the HTML shell for the pasted URL.
    shell = httpx.get(f"{_base_url(api_server)}/ui/traces/E2E")
    assert shell.status_code == 200
    assert "text/html" in shell.headers.get("content-type", "")

    # Step 2: cold-open fetch returns the TraceListingEntry (root + counts).
    entry = httpx.get(f"{_base_url(api_server)}/api/traces/E2E").json()
    root = entry["listing_root"]
    assert root["span_id"] == "root"
    assert root["parent_id"] is None  # real root → 'Real root' badge
    assert entry["counts"]["error_count"] == 1  # one error leaf

    # Step 3: expand root's children.
    level1 = httpx.get(
        f"{_base_url(api_server)}/api/traces/E2E/spans/root/children"
    ).json()
    l1_ids = {s["span_id"] for s in level1["spans"]}
    assert l1_ids == {"db-call", "cache-miss"}

    # Step 4: expand db-call's children.
    level2 = httpx.get(
        f"{_base_url(api_server)}/api/traces/E2E/spans/db-call/children"
    ).json()
    assert len(level2["spans"]) == 1
    leaf = level2["spans"][0]
    assert leaf["span_id"] == "db-query"

    # Step 5: error leaf surfaces error=true and status_message.
    assert leaf["error"] is True
    assert leaf["status_message"] == "timeout after 30s"
