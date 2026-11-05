"""Route registration + ordering tests for `/risk/interactions*`/`/risk/traces*`
(issue #109).

Mirrors `test_route_registration.py`'s style for the `risk_routes` module.
Path-resolution assertions here are DB-free — even a 404 body proves the
route was matched (Starlette's own 404 has no JSON body); the import-safety
and ordering checks need no DB at all.
"""

from __future__ import annotations

import inspect

from data_governance.risk.api import http as http_mod
from data_governance.risk.api import risk_routes


def test_interactions_list_route_resolves(client, configured_db):
    resp = client.get("/risk/interactions")
    assert resp.status_code == 200


def test_interaction_detail_route_resolves(client, configured_db):
    resp = client.get("/risk/interactions/nope")
    assert resp.status_code == 404


def test_interaction_history_route_resolves(client, configured_db):
    resp = client.get("/risk/interactions/nope/history")
    assert resp.status_code == 200


def test_traces_list_route_resolves(client, configured_db):
    resp = client.get("/risk/traces")
    assert resp.status_code == 200


def test_trace_detail_route_resolves(client, configured_db):
    resp = client.get("/risk/traces/nope")
    assert resp.status_code == 404


def test_trace_history_route_resolves(client, configured_db):
    resp = client.get("/risk/traces/nope/history")
    assert resp.status_code == 200


def test_history_precedes_id_route_for_interactions():
    paths = [r.path for r in risk_routes.routes()]
    assert paths.index(
        "/risk/interactions/{interaction_id:str}/history"
    ) < paths.index("/risk/interactions/{interaction_id:str}")


def test_history_precedes_id_route_for_traces():
    paths = [r.path for r in risk_routes.routes()]
    assert paths.index("/risk/traces/{trace_id:str}/history") < paths.index(
        "/risk/traces/{trace_id:str}"
    )


def test_all_six_routes_are_registered():
    paths = {r.path for r in risk_routes.routes()}
    assert paths == {
        "/risk/interactions/{interaction_id:str}/history",
        "/risk/interactions/{interaction_id:str}",
        "/risk/interactions",
        "/risk/traces/{trace_id:str}/history",
        "/risk/traces/{trace_id:str}",
        "/risk/traces",
    }


def test_all_routes_are_get_only():
    for route in risk_routes.routes():
        assert route.methods == {"GET", "HEAD"}


def test_build_app_includes_all_six_risk_routes(client, configured_db):
    for path in (
        "/risk/interactions",
        "/risk/interactions/nope",
        "/risk/interactions/nope/history",
        "/risk/traces",
        "/risk/traces/nope",
        "/risk/traces/nope/history",
    ):
        resp = client.get(path)
        assert resp.status_code != 404 or path in (
            "/risk/interactions/nope",
            "/risk/traces/nope",
        )


def test_existing_rules_routes_still_registered(client):
    resp = client.get("/risk/rules")
    assert resp.status_code == 200


def test_risk_routes_does_not_import_the_ui_api_module():
    """D3: import direction is strictly data_governance.api ->
    data_governance.risk.api, never the reverse."""
    for mod in (risk_routes, http_mod):
        source = inspect.getsource(mod)
        assert "data_governance.api" not in source
        assert "data_governance import api" not in source
