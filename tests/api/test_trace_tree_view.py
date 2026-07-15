"""Wire-contract tests for the trace-tree view's data layer — issue #14.

The trace-tree view is now a React SPA route (ADR-0019) reached from a
recent-traces row: the SPA caches the listing-root ``Span`` in sessionStorage
and navigates to ``/ui/traces/<trace_id>`` (React Router, ``basename="/ui"``),
then lazy-expands subtrees via ``GET /api/traces/T/spans/P/children?cursor=...``
(ADR-0018).

These tests exercise the **API boundary** the view drives — unchanged by the UI
migration:

- Multi-level trace with errors at varying depths
- Wide-fanout pagination (parent with > limit children)
- Detail-panel content (attributes / events / links present in the JSON the UI
  consumes)

The descendant-error-badge walker (``descendantErrorAncestors``) moved from the
Node-driven ``trace_tree_logic.js`` to the SPA's ``src/lib/traceTree.ts`` and is
covered by Vitest, including the v1 collapsed-subtree limitation. The former
shell-HTML-string assertions (``/ui/traces`` shell content, the retired
``trace_tree_logic.js`` asset whitelist, the sessionStorage handoff) went with
the vanilla shell; the SPA-serving wire contract lives in ``test_spa_serving.py``.
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
