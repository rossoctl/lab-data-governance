"""End-to-end tests for the trace-tree UI view — issue #14.

The trace-tree view replaces the recent-traces row's click-through
target. Reaching it: the user clicks a recent-traces row; the JS
caches the listing-root ``Span`` in sessionStorage and navigates to
``/ui/traces/<trace_id>`` (a real route, not a hash; ADR-0017). The shell
at that route reads the anchor from sessionStorage and lazy-expands subtrees
via ``GET /api/traces/T/spans/P/children?cursor=...`` (ADR-0018).

These tests exercise the AC matrix from issue #14:

- Multi-level trace with errors at varying depths
- Lazy-expansion descendant-badge appearance (data layer test ensures
  ancestors are flagged after each subtree-load)
- Wide-fanout pagination (parent with > limit children)
- Detail-panel content on click (attributes / events / links present
  in the JSON the UI consumes)
- v1 limitation: errors inside collapsed subtrees do NOT propagate
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import psycopg
import pytest

from data_governance.api import SpansApiServer

UTC = dt.timezone.utc

_LOGIC_JS = (
    Path(__file__).resolve().parents[2]
    / "data_governance" / "api" / "ui" / "trace_tree_logic.js"
)


def _have_node() -> bool:
    return shutil.which("node") is not None


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
    ended_at: dt.datetime | None = None,
    error: bool | None = None,
    status_message: str | None = None,
    kind: str = "INTERNAL",
    service_name: str | None = None,
    events: list | None = None,
    links: list | None = None,
    attributes: dict | None = None,
    otlp: dict | None = None,
    scope: dict | None = None,
    resource_attributes: dict | None = None,
) -> None:
    events_json = json.dumps(events) if events is not None else None
    links_json = json.dumps(links) if links is not None else None
    attrs_json = json.dumps(attributes) if attributes is not None else "{}"
    otlp_json = json.dumps(otlp) if otlp is not None else None
    scope_json = json.dumps(scope) if scope is not None else None
    res_json = json.dumps(resource_attributes) if resource_attributes is not None else None
    if started_at is None:
        ts_sql = "now()"
        params: tuple = (
            trace_id, span_id, parent_id, kind, name, service_name,
            ended_at, error, status_message, events_json, links_json,
            attrs_json, otlp_json, scope_json, res_json,
        )
    else:
        ts_sql = "%s"
        params = (
            trace_id, span_id, parent_id, kind, name, service_name,
            started_at, ended_at, error, status_message, events_json,
            links_json, attrs_json, otlp_json, scope_json, res_json,
        )
    conn.execute(
        f"""
        INSERT INTO spans (
            trace_id, span_id, parent_id, kind, name, service_name,
            started_at, ended_at, error, status_message, events, links,
            attributes, otlp, scope, resource_attributes,
            seq, arrival_seq, observed_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            {ts_sql}, %s, %s, %s, %s::jsonb, %s::jsonb,
            %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
            nextval('spans_seq'), currval('spans_seq'), now()
        )
        """,
        params,
    )
    conn.commit()


def _seed_multi_level_trace_with_errors(conn) -> None:
    """Multi-level trace ``T`` with errors at varying depths.

    Tree shape::

        root (no error)
        ├── http-call (CLIENT, no error directly)
        │   └── db-query (INTERNAL, ERROR — leaf failure deep in subtree)
        ├── server-handler (SERVER, ERROR — failure mid-tree)
        │   └── ok-leaf (INTERNAL, no error)
        └── batch (PRODUCER, no error)
            ├── batch-item-0 (INTERNAL, no error)
            ├── batch-item-1 (INTERNAL, ERROR)
            ├── ...                              # wide fanout
            └── batch-item-N

    Two error spans, at depths 3 (db-query) and 2 (server-handler), let
    us assert the descendant-error badge propagates up the loaded
    chain. The wide-fanout ``batch`` parent (with N > limit children)
    drives the lazy-expansion pagination test.
    """
    _insert(
        conn, trace_id="T", span_id="root", name="root",
        kind="INTERNAL", service_name="svc",
        parent_id=None,
        started_at=dt.datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        attributes={"request_id": "abc-123"},
    )
    _insert(
        conn, trace_id="T", span_id="http-call", name="http-call",
        kind="CLIENT", service_name="svc",
        parent_id="root",
        started_at=dt.datetime(2026, 5, 1, 10, 0, 1, tzinfo=UTC),
    )
    _insert(
        conn, trace_id="T", span_id="db-query", name="db-query",
        kind="INTERNAL", service_name="svc",
        parent_id="http-call",
        started_at=dt.datetime(2026, 5, 1, 10, 0, 2, tzinfo=UTC),
        error=True, status_message="connection refused",
        events=[
            {"name": "exception", "time_unix_nano": 1700000000000000000,
             "attributes": {"exception.type": "ConnectionError"}}
        ],
    )
    _insert(
        conn, trace_id="T", span_id="server-handler", name="server-handler",
        kind="SERVER", service_name="svc",
        parent_id="root",
        started_at=dt.datetime(2026, 5, 1, 10, 0, 3, tzinfo=UTC),
        error=True, status_message="500 Internal Server Error",
    )
    _insert(
        conn, trace_id="T", span_id="ok-leaf", name="ok-leaf",
        kind="INTERNAL", service_name="svc",
        parent_id="server-handler",
        started_at=dt.datetime(2026, 5, 1, 10, 0, 4, tzinfo=UTC),
    )
    _insert(
        conn, trace_id="T", span_id="batch", name="batch",
        kind="PRODUCER", service_name="svc",
        parent_id="root",
        started_at=dt.datetime(2026, 5, 1, 10, 0, 5, tzinfo=UTC),
        links=[{"trace_id": "T2", "span_id": "linked-x", "attributes": {}}],
    )
    # 12 children of "batch" — wider than the lazy-expansion page size
    # used in the test below (limit=5) so pagination is exercised.
    for i in range(12):
        _insert(
            conn, trace_id="T", span_id=f"batch-item-{i:02d}",
            name=f"batch-item-{i:02d}",
            kind="INTERNAL", service_name="svc",
            parent_id="batch",
            started_at=dt.datetime(2026, 5, 1, 10, 0, 6, tzinfo=UTC)
            + dt.timedelta(seconds=i),
            error=(i == 1),  # one in the middle errors
            status_message="batch-item-1 timed out" if i == 1 else None,
        )


# ---------------------------------------------------------------------------
# AC: multi-level trace, errors at varying depths
# ---------------------------------------------------------------------------


def test_listing_root_anchor_carries_render_columns(
    api_server, configured_db,
):
    """The trace-tree shell uses the listing-root Span as its root
    anchor with no extra fetch (issue #14). For that to work the
    listing endpoint must surface ``kind``/``error``/``status_message``
    on the listing-root Span — otherwise the row would render without
    its icon and badges until the user expanded something."""
    with psycopg.connect(configured_db) as conn:
        _seed_multi_level_trace_with_errors(conn)

    entry = httpx.get(f"{_base_url(api_server)}/api/traces/T").json()
    root = entry["listing_root"]
    assert root["span_id"] == "root"
    assert root["kind"] == "INTERNAL"
    # OTLP UNSET on the seeded root → null
    assert root["error"] is None


def test_lazy_expansion_walks_all_levels(api_server, configured_db):
    """Walk the tree top-down with
    ``GET /api/traces/T/spans/P/children`` calls and assert every level
    surfaces. This is the data-shape the UI's lazy-expansion drives."""
    with psycopg.connect(configured_db) as conn:
        _seed_multi_level_trace_with_errors(conn)

    def children(parent: str) -> list[dict]:
        return httpx.get(
            f"{_base_url(api_server)}/api/traces/T/spans/{parent}/children",
            params={"limit": 50},
        ).json()["spans"]

    level1 = children("root")
    level1_ids = {s["span_id"] for s in level1}
    assert level1_ids == {"http-call", "server-handler", "batch"}

    level2_http = children("http-call")
    assert {s["span_id"] for s in level2_http} == {"db-query"}

    level2_server = children("server-handler")
    assert {s["span_id"] for s in level2_server} == {"ok-leaf"}


def test_error_spans_at_varying_depths(api_server, configured_db):
    """Two error spans at different depths — db-query (depth 3) and
    server-handler (depth 2). Both surface the ``error: true`` flag and
    a ``status_message`` for the row's badge + below-row text."""
    with psycopg.connect(configured_db) as conn:
        _seed_multi_level_trace_with_errors(conn)

    spans = httpx.get(
        f"{_base_url(api_server)}/api/traces/T/spans",
        params={"limit": 500},
    ).json()["spans"]

    by_id = {s["span_id"]: s for s in spans}
    assert by_id["db-query"]["error"] is True
    assert by_id["db-query"]["status_message"] == "connection refused"
    assert by_id["server-handler"]["error"] is True
    assert by_id["server-handler"]["status_message"] == (
        "500 Internal Server Error"
    )
    # Not-error spans default to OTLP UNSET → null.
    assert by_id["root"]["error"] is None
    assert by_id["http-call"]["error"] is None


# ---------------------------------------------------------------------------
# AC: wide-fanout parent paginates correctly
# ---------------------------------------------------------------------------


def test_wide_fanout_parent_paginates_lazily(api_server, configured_db):
    """The ``batch`` parent has 12 children; lazy-expand at limit=5 and
    assert the user sees all 12 across the pages."""
    with psycopg.connect(configured_db) as conn:
        _seed_multi_level_trace_with_errors(conn)

    base = f"{_base_url(api_server)}/api/traces/T/spans/batch/children"
    seen: list[str] = []
    cursor: int | None = None
    for _ in range(10):
        params: dict[str, object] = {"limit": 5}
        if cursor is not None:
            params["cursor"] = cursor
        page = httpx.get(base, params=params).json()
        if not page["spans"]:
            break
        seen.extend(s["span_id"] for s in page["spans"])
        cursor = max(s["seq"] for s in page["spans"])

    assert seen == [f"batch-item-{i:02d}" for i in range(12)]


# ---------------------------------------------------------------------------
# AC: detail-panel JSON content
# ---------------------------------------------------------------------------


def test_detail_panel_payload_for_loaded_span(api_server, configured_db):
    """The detail panel renders ``attributes``, ``events``, ``links``
    as pretty-printed JSON. The data the panel consumes comes from the
    whole-trace spans response — assert all three are present on the wire."""
    with psycopg.connect(configured_db) as conn:
        _seed_multi_level_trace_with_errors(conn)

    spans = httpx.get(
        f"{_base_url(api_server)}/api/traces/T/spans",
        params={"limit": 500},
    ).json()["spans"]
    by_id = {s["span_id"]: s for s in spans}

    # root has user-supplied attributes
    assert by_id["root"]["attributes"] == {"request_id": "abc-123"}
    # db-query has events
    assert by_id["db-query"]["events"][0]["name"] == "exception"
    # batch has links
    assert by_id["batch"]["links"][0]["trace_id"] == "T2"
    # ok-leaf is the negative case — no events/links
    assert by_id["ok-leaf"]["events"] is None
    assert by_id["ok-leaf"]["links"] is None


# ---------------------------------------------------------------------------
# AC: lazy-expansion descendant-badge appearance
#
# This is the marquee end-to-end check from issue #14: as the user
# expands subtrees, ancestors of error spans receive the
# descendant-error badge. The check is data-layer (Node-driven) so it
# runs without a browser — the same pattern used for the recent-traces
# helpers.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _have_node(), reason="node is not installed; skipping UI-logic tests"
)
def test_descendant_badge_appears_progressively_as_subtrees_load(
    api_server, configured_db,
):
    """Walks the trace one level at a time using the real /api/traces API
    and feeds the loaded set into ``trace_tree_logic.js``. After each
    expansion the JS reports which (trace_id|span_id) keys carry the
    descendant-error badge. We assert the badge appears on the *first*
    expansion that surfaces an error span and that it spreads up the
    chain as deeper levels load.

    This exercises the v1 limitation: until the failing branch is
    expanded, no ancestor badge appears.
    """
    with psycopg.connect(configured_db) as conn:
        _seed_multi_level_trace_with_errors(conn)

    def fetch_root() -> dict:
        return httpx.get(
            f"{_base_url(api_server)}/api/traces/T"
        ).json()["listing_root"]

    def fetch_children(parent_id: str) -> list[dict]:
        return httpx.get(
            f"{_base_url(api_server)}/api/traces/T/spans/{parent_id}/children",
            params={"limit": 50},
        ).json()["spans"]

    def flagged(loaded: list[dict]) -> set[str]:
        script = (
            f"const M = require({json.dumps(str(_LOGIC_JS))});\n"
            f"const spans = {json.dumps(loaded)};\n"
            "const idx = M.buildParentIndex(spans);\n"
            "const out = Array.from("
            "M.descendantErrorAncestors(spans, idx)"
            ");\n"
            "process.stdout.write(JSON.stringify(out));\n"
        )
        result = subprocess.run(
            ["node", "-e", script],
            check=False, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        return set(json.loads(result.stdout))

    # Step 1: only the root is loaded. No errors visible -> no badges.
    loaded = [fetch_root()]
    assert flagged(loaded) == set()

    # Step 2: expand root. http-call, server-handler, batch arrive.
    # server-handler is itself an error span — but that's the *error*
    # badge, not the descendant-error badge. The walker excludes the
    # error span from its own ancestor set, so root should NOT yet be
    # flagged purely from server-handler... wait, actually root *is*
    # an ancestor of server-handler (an error span), so root *should*
    # be flagged on this expansion. v1 limitation only kicks in for
    # error spans whose parent chain isn't loaded.
    loaded.extend(fetch_children("root"))
    f = flagged(loaded)
    assert "T|root" in f, (
        "root is the loaded ancestor of server-handler (error=true); "
        "should be flagged once root's children land"
    )
    # http-call has not yet revealed its db-query failure, so http-call
    # itself is NOT yet flagged.
    assert "T|http-call" not in f

    # Step 3: expand http-call -> db-query (deep error) lands. Now
    # http-call must be flagged (parent chain to db-query goes through it).
    loaded.extend(fetch_children("http-call"))
    f = flagged(loaded)
    assert "T|http-call" in f, (
        "after expanding http-call's subtree, db-query (error=true) is "
        "loaded — http-call is its loaded ancestor and must carry the "
        "descendant-error badge"
    )
    assert "T|root" in f  # still flagged

    # The error span itself never carries the descendant-error badge —
    # that's the per-span error badge's job (the two badges are
    # visually distinct per docs/ui-design.md §2 / §3).
    assert "T|db-query" not in f
    assert "T|server-handler" not in f


@pytest.mark.skipif(
    not _have_node(), reason="node is not installed; skipping UI-logic tests"
)
def test_v1_limitation_collapsed_subtree_does_not_propagate_badge(
    api_server, configured_db,
):
    """v1 limitation, explicit. Load only the root and its direct
    children. db-query (deep error) is in a still-collapsed subtree
    under http-call — its ancestor chain therefore carries no badge
    yet. The user must drill in to surface it."""
    with psycopg.connect(configured_db) as conn:
        _insert(
            conn, trace_id="U", span_id="root", name="root",
            kind="INTERNAL", parent_id=None,
            started_at=dt.datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="U", span_id="middle", name="middle",
            kind="INTERNAL", parent_id="root",
            started_at=dt.datetime(2026, 5, 1, 10, 0, 1, tzinfo=UTC),
        )
        _insert(
            conn, trace_id="U", span_id="hidden-error", name="hidden-error",
            kind="INTERNAL", parent_id="middle",
            started_at=dt.datetime(2026, 5, 1, 10, 0, 2, tzinfo=UTC),
            error=True, status_message="boom",
        )

    root = httpx.get(
        f"{_base_url(api_server)}/api/traces/U"
    ).json()["listing_root"]
    direct_children = httpx.get(
        f"{_base_url(api_server)}/api/traces/U/spans/root/children",
        params={"limit": 50},
    ).json()["spans"]

    loaded = [root] + direct_children
    script = (
        f"const M = require({json.dumps(str(_LOGIC_JS))});\n"
        f"const spans = {json.dumps(loaded)};\n"
        "const idx = M.buildParentIndex(spans);\n"
        "const out = Array.from("
        "M.descendantErrorAncestors(spans, idx)"
        ");\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    result = subprocess.run(
        ["node", "-e", script],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    flagged = set(json.loads(result.stdout))

    # Neither root nor middle is flagged — hidden-error is in a
    # collapsed subtree (its ancestor middle's children were never
    # fetched), so the walker has no path from any error span to root.
    assert "U|root" not in flagged
    assert "U|middle" not in flagged


# ---------------------------------------------------------------------------
# UI shell HTML for the trace-tree route
# ---------------------------------------------------------------------------


def test_trace_tree_route_serves_shell_for_any_trace_id(
    api_server, configured_db,
):
    """``GET /ui/traces/<trace_id>`` returns the trace-tree shell HTML.
    The trace_id is consumed by in-page JS so the same HTML is served
    for any value."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/traces/abcdef")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Trace tree" in resp.text


def test_trace_tree_shell_loads_logic_js_asset(api_server, configured_db):
    """The shell pulls in ``trace_tree_logic.js`` for the descendant-
    badge walker."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/traces/anything")
    assert "/ui/trace_tree_logic.js" in resp.text


def test_trace_tree_logic_js_asset_is_served(api_server, configured_db):
    """The asset must be on the whitelist — ``/ui/<name>`` is whitelist-
    only by design (docs/ui-design.md / PROJECT.md §7)."""
    resp = httpx.get(
        f"{_base_url(api_server)}/ui/trace_tree_logic.js"
    )
    assert resp.status_code == 200
    assert "javascript" in resp.headers.get("content-type", "")
    assert "descendantErrorAncestors" in resp.text
    assert "buildParentIndex" in resp.text


def test_trace_tree_shell_calls_children_endpoint(api_server, configured_db):
    """The shell's lazy-expansion fetches the children sub-resource
    ``/api/traces/{tid}/spans/{sid}/children``. Smoke-check the endpoint
    string is present so a refactor doesn't silently break the contract."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/traces/x")
    assert "/children" in resp.text
    assert "/api/traces/" in resp.text


def test_trace_tree_shell_reads_listing_root_from_session_storage(
    api_server, configured_db,
):
    """The click-through handoff is via sessionStorage key
    ``dg.listingRoot.<trace_id>`` — see openTrace() in the recent-
    traces shell. The trace-tree shell must read the same key (so the
    anchor doesn't need re-fetching)."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/traces/x")
    assert "dg.listingRoot." in resp.text


def test_recent_traces_shell_links_to_real_trace_tree_route(
    api_server, configured_db,
):
    """Recent-traces row click-through must navigate to ``/ui/traces/<id>``
    (real route), not a hash. Hash navigation never reaches the server,
    so the previously-shipped ``#/traces/<id>`` href would never load
    the trace-tree shell. This test pins the regression."""
    resp = httpx.get(f"{_base_url(api_server)}/ui/")
    text = resp.text
    assert "/ui/traces/" in text
    # Defend against an accidental return to hash navigation.
    assert "'#/traces/'" not in text
    assert '"#/traces/"' not in text
