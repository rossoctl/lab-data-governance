"""The ``/forest/{trace_id}`` route serves the execution-forest UI shell (ADR-0009).

Mirrors ``test_cold_open_deep_link.py``'s ``/trace`` serve checks, but DB-free:
``_forest_handler`` returns a static shell (the trace_id is consumed by in-page
JS from ``window.location.pathname``), so it needs no Postgres. Exercised via
Starlette's ``TestClient`` over ``build_app()`` — no ``api_server`` /
``configured_db`` fixtures required.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from data_governance.api import build_app


def _client() -> TestClient:
    return TestClient(build_app())


def test_forest_route_returns_html_shell():
    """/forest/<trace_id> returns 200 HTML referencing the logic module."""
    resp = _client().get("/forest/some-trace-id-123")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Execution forest" in resp.text
    assert "/ui/forest_logic.js" in resp.text


def test_forest_route_same_shell_for_any_trace_id():
    """The trace_id lives in the URL, not the HTML — every id gets the shell."""
    client = _client()
    for tid in ("trace-aaa", "trace-bbb", "trace-ccc"):
        resp = client.get(f"/forest/{tid}")
        assert resp.status_code == 200


def test_forest_logic_asset_is_whitelisted_and_served():
    """/ui/forest_logic.js serves the JS (the asset whitelist includes it)."""
    resp = _client().get("/ui/forest_logic.js")
    assert resp.status_code == 200
    assert "application/javascript" in resp.headers.get("content-type", "")
    assert "buildForest" in resp.text
