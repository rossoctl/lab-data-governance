"""End-to-end tests for the cold-open / deep-link to /traces/T — issue #15.

A user pastes a ``/traces/T`` URL (or otherwise lands on the trace tree
without coming from the recent-traces listing). The UI must:

- Fetch ``GET /spans?root_only=true&trace_id=T`` without any window params.
- Render the returned span as the tree root anchor via the same code path
  as the click-through flow.
- Drive missing-parent / real-root badging from the root span's ``parent_id``.
- Drive the error-count badge from ``counts[T]`` returned by the same call.
- Render an empty state when the trace has no spans at all.
- Preserve browser back/forward behaviour (real routes, not hashes).
- Support lazy subtree expansion identically to the click-through path.

These tests exercise the acceptance criteria matrix from issue #15.  The
``/traces/{trace_id}`` server route, the cold-open fetch, and the empty-state
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
# AC: /traces/T URL is a real, shareable, bookmarkable route
# ---------------------------------------------------------------------------


def test_trace_route_returns_html_shell(api_server, configured_db):
    """/traces/<trace_id> returns 200 HTML for any trace_id value.

    The route must be a real server-side route (not a hash fragment) so a
    pasted URL reaches the server and is served the same HTML shell
    regardless of whether the trace exists.
    """
    resp = httpx.get(f"{_base_url(api_server)}/traces/some-trace-id-123")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


def test_trace_route_is_not_hash_navigation(api_server, configured_db):
    """Recent-traces click-through must use real /traces/<id> routes, not hashes.

    A ``#/traces/<id>`` href never reaches the server on a paste/reload;
    only a real path does.  This pins the regression from the early
    hash-based prototype.
    """
    resp = httpx.get(f"{_base_url(api_server)}/")
    assert "'#/traces/'" not in resp.text
    assert '"#/traces/"' not in resp.text


def test_multiple_distinct_trace_ids_each_served(api_server, configured_db):
    """Different trace IDs each get the same HTML shell — the trace_id is
    consumed by in-page JS from window.location.pathname, not from the HTML."""
    for tid in ("trace-aaa", "trace-bbb", "trace-ccc"):
        resp = httpx.get(f"{_base_url(api_server)}/traces/{tid}")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# AC: cold-open fetches root_only=true&trace_id=T, no window parameters
# ---------------------------------------------------------------------------


def test_cold_open_fetch_uses_root_only_and_trace_id(api_server, configured_db):
    """The trace-tree shell must include ``root_only=true`` and ``trace_id``
    in the cold-open fetch, and must NOT include time-window parameters."""
    resp = httpx.get(f"{_base_url(api_server)}/traces/T")
    html = resp.text

    # The cold-open fetch must use root_only=true (not root_only=false or absent).
    assert "root_only=true" in html
    assert "trace_id" in html

    # Time-window parameters must not appear in the trace-tree shell at all.
    # The cold-open fetch ignores the window per PROJECT.md §6; if time_from
    # or time_to crept into the cold-open URL, this AC would be violated.
    assert "time_from" not in html
    assert "time_to" not in html


def test_cold_open_endpoint_returns_root_span_and_counts(api_server, configured_db):
    """The API call the cold-open UI makes — GET /spans?root_only=true&trace_id=T —
    returns exactly the listing root span plus counts[T]."""
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

    body = httpx.get(
        f"{_base_url(api_server)}/spans",
        params={"root_only": "true", "trace_id": "CO"},
    ).json()

    # Exactly one listing root returned.
    assert len(body["spans"]) == 1
    root = body["spans"][0]
    assert root["span_id"] == "root"
    assert root["trace_id"] == "CO"

    # counts[T] present and accurate.
    assert "CO" in body["counts"]
    c = body["counts"]["CO"]
    assert c["total"] == 3
    assert c["error_count"] == 1


def test_cold_open_ignores_time_window(api_server, configured_db):
    """root_only=true with trace_id ignores any time window — the span is
    returned even if its started_at is outside [time_from, time_to]."""
    old_time = dt.datetime(2020, 1, 1, tzinfo=UTC)
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="OLD", span_id="root", name="old-root",
            parent_id=None,
            started_at=old_time,
        )

    # Narrow window that excludes the span's started_at.
    window_from = dt.datetime(2026, 5, 1, tzinfo=UTC).isoformat().replace(
        "+00:00", "Z"
    )
    window_to = dt.datetime(2026, 5, 2, tzinfo=UTC).isoformat().replace(
        "+00:00", "Z"
    )
    body = httpx.get(
        f"{_base_url(api_server)}/spans",
        params={
            "root_only": "true",
            "trace_id": "OLD",
            "time_from": window_from,
            "time_to": window_to,
        },
    ).json()

    # The span is returned despite the window because trace_id is specified.
    assert len(body["spans"]) == 1
    assert body["spans"][0]["span_id"] == "root"


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

    body = httpx.get(
        f"{_base_url(api_server)}/spans",
        params={"root_only": "true", "trace_id": "RR"},
    ).json()
    [root] = body["spans"]
    assert root["parent_id"] is None


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

    body = httpx.get(
        f"{_base_url(api_server)}/spans",
        params={"root_only": "true", "trace_id": "MP"},
    ).json()
    [root] = body["spans"]
    assert root["parent_id"] == "missing-parent-id"


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

    body = httpx.get(
        f"{_base_url(api_server)}/spans",
        params={"root_only": "true", "trace_id": "EC"},
    ).json()
    assert body["counts"]["EC"]["error_count"] == 3


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

    body = httpx.get(
        f"{_base_url(api_server)}/spans",
        params={"root_only": "true", "trace_id": "OK"},
    ).json()
    assert body["counts"]["OK"]["error_count"] == 0


# ---------------------------------------------------------------------------
# AC: empty response (trace has no spans) renders an empty state
# ---------------------------------------------------------------------------


def test_cold_open_empty_response_for_unknown_trace(api_server, configured_db):
    """GET /spans?root_only=true&trace_id=T returns an empty spans list when
    the trace does not exist.  The UI renders 'Trace not found.' rather than
    crashing or hanging."""
    body = httpx.get(
        f"{_base_url(api_server)}/spans",
        params={"root_only": "true", "trace_id": "no-such-trace"},
    ).json()
    assert body["spans"] == []


def test_cold_open_shell_contains_empty_state_message(api_server, configured_db):
    """The trace-tree HTML shell must include the 'trace not found' empty-state
    text so the in-page JS can surface it without a second round-trip."""
    resp = httpx.get(f"{_base_url(api_server)}/traces/anything")
    html = resp.text
    # The shell's init() renders this when data.spans is empty.
    assert "not found" in html.lower() or "Trace not found" in html


# ---------------------------------------------------------------------------
# AC: lazy subtree expansion works identically to the click-through path
# ---------------------------------------------------------------------------


def test_cold_open_subtree_expansion_fetches_children(api_server, configured_db):
    """After cold-opening a trace, subtree expansion uses the same
    GET /spans?trace_id=T&parent_id=P endpoint as the click-through path."""
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

    # Step 1: cold-open fetch gets the root.
    cold = httpx.get(
        f"{_base_url(api_server)}/spans",
        params={"root_only": "true", "trace_id": "EXP"},
    ).json()
    [root_span] = cold["spans"]
    assert root_span["span_id"] == "root"

    # Step 2: subtree expansion — same endpoint, same contract.
    children = httpx.get(
        f"{_base_url(api_server)}/spans",
        params={"trace_id": "EXP", "parent_id": "root"},
    ).json()
    child_ids = {s["span_id"] for s in children["spans"]}
    assert child_ids == {"child-a", "child-b"}


def test_cold_open_subtree_expansion_paginates_wide_fanout(api_server, configured_db):
    """Wide-fanout roots paginate correctly via cursor on the subtree endpoint
    after a cold open — same contract as the click-through path."""
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

    seen: list[str] = []
    cursor: int | None = None
    for _ in range(10):
        params: dict[str, object] = {
            "trace_id": "WF", "parent_id": "root", "limit": 3,
        }
        if cursor is not None:
            params["cursor"] = cursor
        page = httpx.get(
            f"{_base_url(api_server)}/spans", params=params
        ).json()
        if not page["spans"]:
            break
        seen.extend(s["span_id"] for s in page["spans"])
        cursor = max(s["seq"] for s in page["spans"])

    assert len(seen) == 8
    assert set(seen) == {f"child-{i:02d}" for i in range(8)}


# ---------------------------------------------------------------------------
# AC: browser back/forward — real routes, not hash navigation
# ---------------------------------------------------------------------------


def test_trace_route_and_root_route_are_separate_real_routes(
    api_server, configured_db,
):
    """Both / and /traces/<id> must be distinct server-side routes so
    browser history entries are real URLs that survive a reload."""
    root_resp = httpx.get(f"{_base_url(api_server)}/")
    trace_resp = httpx.get(f"{_base_url(api_server)}/traces/abc")

    assert root_resp.status_code == 200
    assert trace_resp.status_code == 200
    # Different HTML shells.
    assert "Recent traces" in root_resp.text
    assert "Trace tree" in trace_resp.text


# ---------------------------------------------------------------------------
# AC: end-to-end — paste URL, walk tree, expand subtree
# ---------------------------------------------------------------------------


def test_end_to_end_cold_open_walk_and_expand(api_server, configured_db):
    """Simulates the full cold-open user journey:

    1. User pastes /traces/E2E — server returns HTML shell (200).
    2. JS fetches GET /spans?root_only=true&trace_id=E2E — returns listing root.
    3. JS expands the root's children.
    4. JS expands a child's grandchildren.
    5. Error spans at leaf level surface ``error=true``.
    6. counts[E2E].error_count reflects the error leaf.
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
    shell = httpx.get(f"{_base_url(api_server)}/traces/E2E")
    assert shell.status_code == 200
    assert "text/html" in shell.headers.get("content-type", "")

    # Step 2: cold-open fetch returns the listing root + counts.
    cold = httpx.get(
        f"{_base_url(api_server)}/spans",
        params={"root_only": "true", "trace_id": "E2E"},
    ).json()
    assert len(cold["spans"]) == 1
    root = cold["spans"][0]
    assert root["span_id"] == "root"
    assert root["parent_id"] is None  # real root → 'Real root' badge
    assert cold["counts"]["E2E"]["error_count"] == 1  # one error leaf

    # Step 3: expand root's children.
    level1 = httpx.get(
        f"{_base_url(api_server)}/spans",
        params={"trace_id": "E2E", "parent_id": "root"},
    ).json()
    l1_ids = {s["span_id"] for s in level1["spans"]}
    assert l1_ids == {"db-call", "cache-miss"}

    # Step 4: expand db-call's children.
    level2 = httpx.get(
        f"{_base_url(api_server)}/spans",
        params={"trace_id": "E2E", "parent_id": "db-call"},
    ).json()
    assert len(level2["spans"]) == 1
    leaf = level2["spans"][0]
    assert leaf["span_id"] == "db-query"

    # Step 5: error leaf surfaces error=true and status_message.
    assert leaf["error"] is True
    assert leaf["status_message"] == "timeout after 30s"
