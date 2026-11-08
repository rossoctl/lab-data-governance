"""Route registration for `/risk/alerts*` (issue #110), in
`test_risk_route_registration.py`'s style."""

from __future__ import annotations

import inspect

from data_governance.retrieval import alerts as alerts_retrieval
from data_governance.risk.api import alert_routes


def test_all_five_routes_are_registered():
    assert {r.path for r in alert_routes.routes()} == {
        "/risk/alerts/by-status/{value:str}",
        "/risk/alerts/by-risk-level/{value:str}",
        "/risk/alerts/by-enforcement-type/{value:str}",
        "/risk/alerts/{alert_id:str}",
        "/risk/alerts",
    }


def test_all_routes_are_get_only():
    for route in alert_routes.routes():
        assert route.methods == {"GET", "HEAD"}


def test_by_routes_precede_the_id_route():
    paths = [r.path for r in alert_routes.routes()]
    id_index = paths.index("/risk/alerts/{alert_id:str}")
    assert all(paths.index(p) < id_index for p in paths if "/by-" in p)


def test_build_app_serves_the_alert_routes(client, configured_db):
    assert client.get("/risk/alerts").status_code == 200
    assert client.get("/risk/alerts/by-status/new").status_code == 200
    assert client.get("/risk/alerts/by-risk-level/high").status_code == 200
    assert client.get("/risk/alerts/by-enforcement-type/block").status_code == 200
    # A JSON 404 (FR-DAS-081), not Starlette's bare one: the route matched.
    resp = client.get("/risk/alerts/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404 and resp.json()["error"] == "not found"


def test_alert_modules_do_not_import_the_ui_api_module():
    for mod in (alert_routes, alerts_retrieval):
        source = inspect.getsource(mod)
        assert "data_governance.api" not in source
        assert "data_governance import api" not in source
