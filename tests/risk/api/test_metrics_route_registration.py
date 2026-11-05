"""Route registration tests for `/risk/metrics/*` (issue #111).

Mirrors `test_risk_route_registration.py`'s style. Unlike `risk_routes`'
`/history`-before-`{id}` pairs and `rules_routes`' `categories`-before-
`{rule_id}` catch-all, none of the six metrics paths has a path parameter,
so there is no ordering hazard to test for here — see `metrics_routes.py`'s
own `routes()` docstring.
"""

from __future__ import annotations

import inspect

from data_governance.risk.api import http as http_mod
from data_governance.risk.api import metrics_routes
from data_governance.risk.api import risk_routes

_EXPECTED_PATHS = {
    "/risk/metrics/summary",
    "/risk/metrics/risk-distribution",
    "/risk/metrics/enforcement-distribution",
    "/risk/metrics/top-rules",
    "/risk/metrics/top-traces",
    "/risk/metrics/risk-by-category",
}


def test_all_six_metrics_routes_are_registered():
    paths = {r.path for r in metrics_routes.routes()}
    assert paths == _EXPECTED_PATHS


def test_all_metrics_routes_are_get_only():
    for route in metrics_routes.routes():
        assert route.methods == {"GET", "HEAD"}


def test_no_metrics_route_has_a_path_parameter():
    for route in metrics_routes.routes():
        assert "{" not in route.path


def test_build_app_dispatches_every_metrics_route(client, configured_db):
    for path in _EXPECTED_PATHS:
        resp = client.get(path)
        assert resp.status_code == 200, path


def test_existing_rules_route_still_registered_after_metrics_added(client):
    resp = client.get("/risk/rules")
    assert resp.status_code == 200


def test_existing_risk_traces_route_still_registered_after_metrics_added(
    client, configured_db
):
    resp = client.get("/risk/traces")
    assert resp.status_code == 200


def test_metrics_routes_does_not_import_the_ui_api_module():
    """Same import-direction guard as test_risk_route_registration.py's
    D3 check, extended to the new module."""
    for mod in (metrics_routes, http_mod):
        source = inspect.getsource(mod)
        assert "data_governance.api" not in source
        assert "data_governance import api" not in source


def test_metrics_routes_reuses_risk_routes_trace_serializer():
    """D1: the top-traces leaderboard must serialize `TraceRiskView` with the
    exact same function `/risk/traces` uses — not a re-implementation that
    could drift from it."""
    assert metrics_routes.trace_risk_to_json is risk_routes.trace_risk_to_json
