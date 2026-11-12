"""Route registration + ordering tests for `/risk/rules*` (issue #113).

No Postgres here either — see `tests/risk/api/conftest.py`'s module docstring.
`/healthz` is invoked (not just resolved) below: without a configured DB its
handler's broad `except Exception` degrades to a 503, so exercising it proves
route resolution without needing a live Postgres.
"""

from __future__ import annotations

from data_governance.risk.api import rules_routes


def test_rules_list_route_resolves(client):
    resp = client.get("/risk/rules")
    assert resp.status_code == 200


def test_rule_detail_route_resolves(client):
    resp = client.get("/risk/rules/DG-001")
    assert resp.status_code == 200


def test_categories_route_resolves(client):
    resp = client.get("/risk/rules/categories")
    assert resp.status_code == 200


def test_categories_is_not_shadowed_by_the_rule_id_route(client):
    """Plan finding #3: with {rule_id} registered first, GET
    /risk/rules/categories would 200 as a single-rule lookup for the literal
    id "categories" instead of hitting the categories handler."""
    body = client.get("/risk/rules/categories").json()
    assert set(body.keys()) == {"items"}
    assert "rule_id" not in body


def test_a_rule_literally_named_categories_is_shadowed_intentionally(
    client, categories_rule_catalog
):
    """D6: the shadowing is a known, accepted trade-off, pinned here so a
    future change to registration order is caught by this test rather than
    silently un-shadowing (or re-shadowing) the fixture rule."""
    body = client.get("/risk/rules/categories").json()
    assert set(body.keys()) == {"items"}
    assert body != {"rule_id": "categories"}


def test_risk_api_does_not_import_the_ui_api_module():
    """D3: import direction is strictly data_governance.api ->
    data_governance.risk.api, never the reverse. No module of `risk.api`
    references `data_governance.api` anywhere in its source."""
    from pathlib import Path

    modules = sorted(Path(rules_routes.__file__).parent.glob("*.py"))
    assert len(modules) > 2
    for path in modules:
        source = path.read_text(encoding="utf-8")
        assert "data_governance.api" not in source, path.name
        assert "data_governance import api" not in source, path.name


def test_existing_healthz_route_still_resolves(client):
    resp = client.get("/healthz")
    assert resp.status_code in (200, 503)


def test_existing_root_redirect_route_still_resolves(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302


def test_rules_routes_returns_categories_before_rule_id_route():
    """Pins D6's ordering requirement directly against routes(), independent
    of the build_app() wiring, so a future reordering fails fast here."""
    paths = [r.path for r in rules_routes.routes()]
    assert paths.index("/risk/rules/categories") < paths.index(
        "/risk/rules/{rule_id:str}"
    )
