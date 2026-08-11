"""Tests for the `/risk/rules*` HTTP routes (issue #113).

`TestClient(build_app())` — sync, no Postgres, no asyncio marker needed.
The serving layer underneath (`data_governance.risk.rules.catalog`) is #107's
read-only reference; these tests only exercise the HTTP adapter over it.
"""

from __future__ import annotations

from data_governance.risk import config
from data_governance.risk.api import http
from data_governance.risk.rules import catalog


# ---------------------------------------------------------------------------
# Step 2 — GET /risk/rules/categories
# ---------------------------------------------------------------------------


def test_categories_returns_200(client):
    resp = client.get("/risk/rules/categories")
    assert resp.status_code == 200


def test_categories_shape_is_items_list_of_category_and_rule_count(client):
    body = client.get("/risk/rules/categories").json()
    assert set(body.keys()) == {"items"}
    for entry in body["items"]:
        assert set(entry.keys()) == {"category", "rule_count"}


def test_categories_matches_catalog_category_counts(client):
    body = client.get("/risk/rules/categories").json()
    got = {entry["category"]: entry["rule_count"] for entry in body["items"]}
    assert got == catalog.category_counts()


def test_categories_sorted_by_category_name(client, varied_catalog):
    body = client.get("/risk/rules/categories").json()
    names = [entry["category"] for entry in body["items"]]
    assert names == sorted(names)


def test_categories_empty_catalog_returns_empty_items(client, empty_catalog):
    body = client.get("/risk/rules/categories").json()
    assert body == {"items": []}


def test_categories_has_no_total_key(client):
    body = client.get("/risk/rules/categories").json()
    assert "total" not in body


# ---------------------------------------------------------------------------
# Step 3 — GET /risk/rules/{rule_id}
# ---------------------------------------------------------------------------

_SIX_FIVE_KEYS = {
    "rule_id",
    "rule_name",
    "categories",
    "risk_level",
    "enforcement",
    "explanation",
    "event_type",
    "data_items",
    "data_destinations",
    "allowed_actions",
    "rule_sources",
}


def test_rule_detail_hit_returns_bare_object_not_wrapped(client):
    body = client.get("/risk/rules/DG-001").json()
    assert set(body.keys()) == _SIX_FIVE_KEYS


def test_rule_detail_matches_catalog_get_rule_for_every_shipped_rule(client):
    for rule in catalog.list_rules():
        body = client.get(f"/risk/rules/{rule['rule_id']}").json()
        assert body == catalog.get_rule(rule["rule_id"])


def test_rule_detail_miss_is_404(client):
    resp = client.get("/risk/rules/NOPE")
    assert resp.status_code == 404


def test_rule_detail_miss_body_is_fr_das_081_shape(client):
    body = client.get("/risk/rules/NOPE").json()
    assert set(body.keys()) == {"error", "detail", "timestamp"}


def test_rule_detail_lookup_is_case_sensitive(client):
    resp = client.get("/risk/rules/dg-001")
    assert resp.status_code == 404


def test_rule_detail_extra_path_segment_is_router_404_not_500(client):
    resp = client.get("/risk/rules/DG-001/extra")
    assert resp.status_code == 404


def test_rule_detail_url_encoded_id_round_trips(client, varied_catalog):
    """DG-001-style ids need no encoding; assert the router decodes
    percent-escapes before dispatch, using a synthetic slash-free id."""
    resp = client.get("/risk/rules/FX%2DCRIT%2DBLOCK")
    assert resp.status_code == 200
    assert resp.json()["rule_id"] == "FX-CRIT-BLOCK"


# ---------------------------------------------------------------------------
# Step 4 — GET /risk/rules: list, sort, filters
# ---------------------------------------------------------------------------


def _ids(body: dict) -> list[str]:
    return [item["rule_id"] for item in body["items"]]


def test_rules_list_returns_200(client):
    resp = client.get("/risk/rules")
    assert resp.status_code == 200


def test_rules_list_unfiltered_shape_has_items_and_next_cursor(client):
    body = client.get("/risk/rules").json()
    assert set(body.keys()) == {"items", "next_cursor"}


def test_rules_list_every_item_is_six_five_shaped(client):
    body = client.get("/risk/rules").json()
    for item in body["items"]:
        assert set(item.keys()) == _SIX_FIVE_KEYS


def test_rules_list_unfiltered_returns_every_shipped_rule(client):
    body = client.get("/risk/rules").json()
    assert set(_ids(body)) == {r["rule_id"] for r in catalog.list_rules()}


def test_rules_list_has_no_total_key(client):
    body = client.get("/risk/rules").json()
    assert "total" not in body


def test_rules_list_has_no_completeness_field(client):
    """FR-DAS-084: no is_complete/completeness status anywhere in the body."""
    body = client.get("/risk/rules").json()
    assert "is_complete" not in body
    for item in body["items"]:
        assert "is_complete" not in item


def test_default_sort_is_rule_id_asc_not_file_order(client, varied_catalog):
    """On catalog_varied.json, file order and rule_id order differ — the
    default must sort explicitly rather than fall through to file order
    (plan finding #2)."""
    body = client.get("/risk/rules").json()
    assert _ids(body) == sorted(_ids(body))
    assert _ids(body) == [
        "FX-CRIT-BLOCK",
        "FX-HIGH-BLOCK",
        "FX-LOW-ALLOW",
        "FX-MED-ESC",
        "FX-NO-DECISION",
    ]


def test_sort_rule_id_asc_is_explicit_default_equivalent(client, varied_catalog):
    default_body = client.get("/risk/rules").json()
    explicit_body = client.get("/risk/rules?sort=rule_id_asc").json()
    assert _ids(default_body) == _ids(explicit_body)


def test_sort_risk_level_desc_is_most_severe_first(client, varied_catalog):
    """Plan finding #1 — the inversion guard. RISK_LEVEL_ORDER is already
    most-severe-first, so risk_level_desc must map to descending=False in
    the route handler; mapping it to descending=True would invert this."""
    body = client.get("/risk/rules?sort=risk_level_desc").json()
    assert _ids(body) == [
        "FX-CRIT-BLOCK",
        "FX-HIGH-BLOCK",
        "FX-MED-ESC",
        "FX-LOW-ALLOW",
        "FX-NO-DECISION",
    ]


def test_sort_enforcement_desc_is_most_severe_first(client, varied_catalog):
    """Same inversion guard as risk_level_desc (plan finding #1) —
    ENFORCEMENT_ORDER is already most-severe-first, so enforcement_desc must
    map to descending=False in the route handler."""
    body = client.get("/risk/rules?sort=enforcement_desc").json()
    assert _ids(body) == [
        "FX-CRIT-BLOCK",
        "FX-HIGH-BLOCK",
        "FX-MED-ESC",
        "FX-LOW-ALLOW",
        "FX-NO-DECISION",
    ]


def test_sort_unknown_value_is_400(client):
    resp = client.get("/risk/rules?sort=bogus")
    assert resp.status_code == 400


def test_sort_unknown_value_body_is_fr_das_081_shape(client):
    body = client.get("/risk/rules?sort=bogus").json()
    assert set(body.keys()) == {"error", "detail", "timestamp"}


def test_sort_catalog_only_key_is_rejected_not_passed_through(client):
    """rule_name is a valid catalog.SORT_KEYS entry but not one of the two
    HTTP-exposed values (D5) — must be 400, not silently accepted."""
    resp = client.get("/risk/rules?sort=rule_name")
    assert resp.status_code == 400


def test_filter_by_single_category(client, varied_catalog):
    body = client.get("/risk/rules?category=data_leakage").json()
    assert set(_ids(body)) == {"FX-CRIT-BLOCK", "FX-LOW-ALLOW"}


def test_filter_by_multiple_categories_is_union(client, varied_catalog):
    body = client.get("/risk/rules?category=data_leakage,pii_exposure").json()
    assert set(_ids(body)) == {
        "FX-CRIT-BLOCK",
        "FX-LOW-ALLOW",
        "FX-MED-ESC",
        "FX-HIGH-BLOCK",
    }


def test_filter_by_category_no_match_is_empty_items(client, varied_catalog):
    body = client.get("/risk/rules?category=nonexistent").json()
    assert body["items"] == []


def test_filter_by_category_is_case_sensitive(client, varied_catalog):
    body = client.get("/risk/rules?category=PII_EXPOSURE").json()
    assert body["items"] == []


def test_filter_by_category_empty_string_matches_nothing(client, varied_catalog):
    """D7: `?category=` (empty) means "accept nothing", not "no filter"."""
    body = client.get("/risk/rules?category=").json()
    assert body["items"] == []


def test_filter_by_single_risk_level(client, varied_catalog):
    body = client.get("/risk/rules?risk_level=critical").json()
    assert _ids(body) == ["FX-CRIT-BLOCK"]


def test_filter_by_multiple_risk_levels_is_union(client, varied_catalog):
    body = client.get("/risk/rules?risk_level=critical,low").json()
    assert set(_ids(body)) == {"FX-CRIT-BLOCK", "FX-LOW-ALLOW"}


def test_filter_by_risk_level_no_match_is_empty_items(client, varied_catalog):
    body = client.get("/risk/rules?risk_level=nonexistent").json()
    assert body["items"] == []


def test_filter_by_risk_level_empty_string_matches_nothing(client, varied_catalog):
    body = client.get("/risk/rules?risk_level=").json()
    assert body["items"] == []


def test_filters_combine_conjunctively(client, varied_catalog):
    body = client.get("/risk/rules?risk_level=high&category=pii_exposure").json()
    assert _ids(body) == ["FX-HIGH-BLOCK"]

    body = client.get("/risk/rules?risk_level=high&category=data_leakage").json()
    assert body["items"] == []


def test_unknown_query_param_is_ignored_not_400(client):
    """D8: only category/risk_level are exposed; an unrecognized param (e.g.
    a future enforcement=) must not break existing clients."""
    resp = client.get("/risk/rules?enforcement=block")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Step 5 — pagination over the list
# ---------------------------------------------------------------------------


def _walk_all(client, *, limit: int, sort: str | None = None) -> list[str]:
    """Follow next_cursor to the end, returning every rule_id seen in order."""
    ids: list[str] = []
    cursor = None
    query = f"limit={limit}"
    if sort is not None:
        query += f"&sort={sort}"
    while True:
        url = f"/risk/rules?{query}"
        if cursor is not None:
            url += f"&cursor={cursor}"
        body = client.get(url).json()
        ids.extend(_ids(body))
        cursor = body["next_cursor"]
        if cursor is None:
            break
    return ids


def test_first_page_honors_limit(client, paging_catalog):
    body = client.get("/risk/rules?limit=3").json()
    assert len(body["items"]) == 3
    assert body["next_cursor"] is not None


def test_walk_via_next_cursor_is_gapless_and_duplicate_free(client, paging_catalog):
    walked = _walk_all(client, limit=3)
    assert walked == [r["rule_id"] for r in catalog.list_rules(sort_by="rule_id")]
    assert len(walked) == len(set(walked))


def test_paging_under_risk_level_desc_is_also_gapless(client, paging_catalog):
    """Walk crosses the PG-002/PG-005 severity tie without gap or dup."""
    walked = _walk_all(client, limit=2, sort="risk_level_desc")
    expected = [
        r["rule_id"]
        for r in catalog.list_rules(sort_by="risk_level", descending=False)
    ]
    assert walked == expected
    assert len(walked) == len(set(walked))


def test_paging_under_enforcement_desc_is_also_gapless(client, paging_catalog):
    """Walk crosses the PG-002/PG-004/PG-005 three-way block tie without gap
    or dup."""
    walked = _walk_all(client, limit=2, sort="enforcement_desc")
    expected = [
        r["rule_id"]
        for r in catalog.list_rules(sort_by="enforcement", descending=False)
    ]
    assert walked == expected
    assert len(walked) == len(set(walked))


def test_last_page_has_null_next_cursor(client, paging_catalog):
    body = client.get("/risk/rules?limit=100").json()
    assert len(body["items"]) == 7
    assert body["next_cursor"] is None


def test_cursor_past_end_returns_empty_items_not_error(client, paging_catalog):
    """AC-DAS-034."""
    body = client.get("/risk/rules?limit=100").json()
    assert body["next_cursor"] is None

    last_id = body["items"][-1]["rule_id"]
    token = http.encode_cursor(len(body["items"]), "rule_id_asc")
    resp = client.get(f"/risk/rules?limit=100&cursor={token}")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "next_cursor": None}
    assert last_id  # sanity: fixture is non-empty


def test_limit_over_max_is_400(client):
    resp = client.get("/risk/rules?limit=201")
    assert resp.status_code == 400


def test_limit_equal_to_max_is_allowed(client):
    resp = client.get("/risk/rules?limit=200")
    assert resp.status_code == 200


def test_cursor_minted_under_one_sort_replayed_under_another_is_400(
    client, paging_catalog
):
    body = client.get("/risk/rules?limit=2&sort=rule_id_asc").json()
    cursor = body["next_cursor"]
    resp = client.get(f"/risk/rules?limit=2&sort=risk_level_desc&cursor={cursor}")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Step 6 — config-driven default limit (AC-DAS-038)
# ---------------------------------------------------------------------------


def test_unqualified_request_returns_default_limit_items(
    client, paging_catalog, monkeypatch
):
    monkeypatch.setattr(config, "API_RISK_RULES_DEFAULT_LIMIT", 3)
    body = client.get("/risk/rules").json()
    assert len(body["items"]) == 3


def test_config_default_is_read_per_request_not_at_import(
    client, paging_catalog, monkeypatch
):
    """Guards the `from config import CONST` mistake that would freeze the
    default at import time, making AC-DAS-038 unverifiable by monkeypatch."""
    monkeypatch.setattr(config, "API_RISK_RULES_DEFAULT_LIMIT", 2)
    first = client.get("/risk/rules").json()
    assert len(first["items"]) == 2

    monkeypatch.setattr(config, "API_RISK_RULES_DEFAULT_LIMIT", 5)
    second = client.get("/risk/rules").json()
    assert len(second["items"]) == 5


def test_config_default_does_not_change_the_hard_maximum(
    client, paging_catalog, monkeypatch
):
    """Default and cap are independent knobs — raising the default above the
    unchanged max must still be rejected the same way an explicit limit is."""
    monkeypatch.setattr(config, "API_RISK_RULES_DEFAULT_LIMIT", 5)
    resp = client.get("/risk/rules?limit=201")
    assert resp.status_code == 400

    resp = client.get("/risk/rules?limit=200")
    assert resp.status_code == 200


def test_changing_default_does_not_affect_the_explicit_limit_path(
    client, paging_catalog, monkeypatch
):
    monkeypatch.setattr(config, "API_RISK_RULES_DEFAULT_LIMIT", 3)
    body = client.get("/risk/rules?limit=7").json()
    assert len(body["items"]) == 7
