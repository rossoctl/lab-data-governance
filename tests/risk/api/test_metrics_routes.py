"""Tests for the `/risk/metrics/*` HTTP routes (issue #111, PRD §4.6/§7.4).

Thin adapter over `data_governance.risk.metrics.aggregate` (issue #106) — see
that module's docstring for the query-layer contract these routes wrap. No
arithmetic happens here: every derived value (percentages, totals) comes from
#106's dataclass properties, already zero-division-safe.

`TestClient(build_app())` against a real (migrated, empty-by-default)
Postgres via `configured_db` — no DB mocking, matching the rest of this repo.
"""

from __future__ import annotations

import datetime as dt

import pytest

from data_governance.risk import config
from data_governance.risk.api import http
from data_governance.risk.metrics import aggregate

NOW = dt.datetime(2026, 8, 20, 12, 0, 0, tzinfo=dt.timezone.utc)


def _freeze(monkeypatch: pytest.MonkeyPatch, now: dt.datetime = NOW) -> None:
    monkeypatch.setattr(http, "_now", lambda: now)


# FR-DAS-084's completeness/partial-result concern, narrower than the shared
# conftest's FORBIDDEN_KEYS (which also bans "total" — legitimately required
# in these bodies by PRD §7.4). See plan D6.
_COMPLETENESS_KEYS = {"is_complete", "complete", "completeness"}


def _assert_no_completeness_keys(value) -> None:
    if isinstance(value, dict):
        assert not (set(value.keys()) & _COMPLETENESS_KEYS), value.keys()
        for v in value.values():
            _assert_no_completeness_keys(v)
    elif isinstance(value, list):
        for item in value:
            _assert_no_completeness_keys(item)


# ---------------------------------------------------------------------------
# GET /risk/metrics/summary
# ---------------------------------------------------------------------------


def test_summary_returns_200(client, configured_db):
    resp = client.get("/risk/metrics/summary")
    assert resp.status_code == 200


def test_summary_top_level_keys(client, configured_db, monkeypatch):
    _freeze(monkeypatch)
    body = client.get("/risk/metrics/summary").json()
    assert set(body.keys()) == {
        "window",
        "from",
        "to",
        "agents",
        "users",
        "workflows",
        "evaluated_interactions",
        "rules_fired",
        "computed_at",
    }


def test_summary_tile_shape(client, configured_db):
    body = client.get("/risk/metrics/summary").json()
    for tile_key in ("agents", "users", "workflows", "evaluated_interactions"):
        assert set(body[tile_key].keys()) == {"total", "risky", "risky_pct"}
    assert set(body["rules_fired"].keys()) == {"total", "critical"}


def test_summary_empty_db_tiles_are_zero_not_absent(client, configured_db):
    body = client.get("/risk/metrics/summary").json()
    for tile_key in ("agents", "users", "workflows", "evaluated_interactions"):
        assert body[tile_key]["total"] == 0
        assert body[tile_key]["risky"] == 0
        assert body[tile_key]["risky_pct"] == 0.0
    assert body["rules_fired"] == {"total": 0, "critical": 0}


def test_summary_echoes_window_from_and_to(client, configured_db, monkeypatch):
    _freeze(monkeypatch)
    body = client.get("/risk/metrics/summary?window=7d").json()
    assert body["window"] == "7d"
    assert body["to"] == NOW.isoformat()
    assert body["from"] == (NOW - dt.timedelta(days=7)).isoformat()


def test_summary_computed_at_present(client, configured_db, monkeypatch):
    _freeze(monkeypatch)
    body = client.get("/risk/metrics/summary").json()
    assert body["computed_at"] == NOW.isoformat()


def test_summary_counts_seeded_agents_and_users(
    client, configured_db, insert_entity, insert_interaction_risk, monkeypatch
):
    _freeze(monkeypatch)
    insert_entity(entity_id="agent-1", kind="agent")
    insert_entity(entity_id="agent-2", kind="agent")
    insert_entity(entity_id="user-1", kind="user")
    insert_interaction_risk(
        interaction_id="ix-1",
        caller_entity_id="agent-1",
        callee_entity_id="agent-2",
        risk_level="high",
        computed_at=NOW - dt.timedelta(hours=1),
    )
    insert_interaction_risk(
        interaction_id="ix-2",
        caller_entity_id="user-1",
        callee_entity_id="agent-1",
        risk_level="none",
        computed_at=NOW - dt.timedelta(hours=1),
    )

    body = client.get("/risk/metrics/summary?window=24h").json()
    assert body["agents"]["total"] == 2
    # Both agents are "risky": agent-1 as caller on the "high" ix-1, agent-2
    # as callee on that same record — get_summary's bool_or is per-entity
    # across every interaction it touched, not per-interaction.
    assert body["agents"]["risky"] == 2
    assert body["users"]["total"] == 1
    assert body["users"]["risky"] == 0
    assert body["evaluated_interactions"]["total"] == 2
    assert body["evaluated_interactions"]["risky"] == 1


def test_summary_workflows_tile_counts_traces(
    client, configured_db, insert_trace_risk, monkeypatch
):
    _freeze(monkeypatch)
    insert_trace_risk(
        trace_id="tr-1", trace_risk_level="critical", computed_at=NOW - dt.timedelta(hours=1)
    )
    insert_trace_risk(
        trace_id="tr-2", trace_risk_level="none", computed_at=NOW - dt.timedelta(hours=1)
    )
    body = client.get("/risk/metrics/summary?window=24h").json()
    assert body["workflows"]["total"] == 2
    assert body["workflows"]["risky"] == 1


def test_summary_rules_fired_counts_distinct_rule_ids(
    client, configured_db, insert_interaction_risk, varied_catalog, monkeypatch
):
    _freeze(monkeypatch)
    insert_interaction_risk(
        interaction_id="ix-1",
        triggered_rule_ids=["FX-CRIT-BLOCK", "FX-LOW-ALLOW"],
        computed_at=NOW - dt.timedelta(hours=1),
    )
    insert_interaction_risk(
        interaction_id="ix-2",
        triggered_rule_ids=["FX-CRIT-BLOCK"],
        computed_at=NOW - dt.timedelta(hours=1),
    )
    body = client.get("/risk/metrics/summary?window=24h").json()
    assert body["rules_fired"]["total"] == 2
    assert body["rules_fired"]["critical"] == 1


def test_summary_window_excludes_out_of_range_records(
    client, configured_db, insert_interaction_risk, monkeypatch
):
    _freeze(monkeypatch)
    insert_interaction_risk(
        interaction_id="ix-old", computed_at=NOW - dt.timedelta(days=2)
    )
    body = client.get("/risk/metrics/summary?window=24h").json()
    assert body["evaluated_interactions"]["total"] == 0


def test_summary_pre_migration_returns_zeros_not_500(
    client, configured_db, monkeypatch
):
    monkeypatch.setattr(aggregate, "_risk_tables_exist", lambda tx: False)
    resp = client.get("/risk/metrics/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agents"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /risk/metrics/risk-distribution
# ---------------------------------------------------------------------------


def test_risk_distribution_returns_200(client, configured_db):
    resp = client.get("/risk/metrics/risk-distribution")
    assert resp.status_code == 200


def test_risk_distribution_top_level_keys(client, configured_db):
    body = client.get("/risk/metrics/risk-distribution").json()
    assert set(body.keys()) == {"window", "distribution", "computed_at"}


def test_risk_distribution_has_no_from_to(client, configured_db):
    """D9: only /summary echoes from/to."""
    body = client.get("/risk/metrics/risk-distribution").json()
    assert "from" not in body
    assert "to" not in body


def test_risk_distribution_empty_db_all_levels_present_as_zero(
    client, configured_db
):
    body = client.get("/risk/metrics/risk-distribution").json()
    assert body["distribution"] == {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "none": 0,
        "total": 0,
    }


def test_risk_distribution_counts_seeded_records(
    client, configured_db, insert_interaction_risk, monkeypatch
):
    _freeze(monkeypatch)
    insert_interaction_risk(
        interaction_id="ix-1", risk_level="critical", computed_at=NOW - dt.timedelta(hours=1)
    )
    insert_interaction_risk(
        interaction_id="ix-2", risk_level="critical", computed_at=NOW - dt.timedelta(hours=1)
    )
    insert_interaction_risk(
        interaction_id="ix-3", risk_level="low", computed_at=NOW - dt.timedelta(hours=1)
    )
    body = client.get("/risk/metrics/risk-distribution?window=24h").json()
    assert body["distribution"]["critical"] == 2
    assert body["distribution"]["low"] == 1
    assert body["distribution"]["total"] == 3


def test_risk_distribution_window_boundary_time_from_inclusive(
    client, configured_db, insert_interaction_risk, monkeypatch
):
    """D5 pin: aggregate.py's predicate is closed at time_from."""
    _freeze(monkeypatch)
    time_from = NOW - dt.timedelta(hours=24)
    insert_interaction_risk(
        interaction_id="ix-at-boundary", risk_level="high", computed_at=time_from
    )
    body = client.get("/risk/metrics/risk-distribution?window=24h").json()
    assert body["distribution"]["total"] == 1


def test_risk_distribution_window_boundary_time_to_inclusive(
    client, configured_db, insert_interaction_risk, monkeypatch
):
    """D5 pin: aggregate.py's predicate is closed at time_to."""
    _freeze(monkeypatch)
    insert_interaction_risk(
        interaction_id="ix-at-now", risk_level="high", computed_at=NOW
    )
    body = client.get("/risk/metrics/risk-distribution?window=24h").json()
    assert body["distribution"]["total"] == 1


def test_risk_distribution_pre_migration_returns_zeros_not_500(
    client, configured_db, monkeypatch
):
    monkeypatch.setattr(aggregate, "_risk_tables_exist", lambda tx: False)
    resp = client.get("/risk/metrics/risk-distribution")
    assert resp.status_code == 200
    assert resp.json()["distribution"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /risk/metrics/enforcement-distribution
# ---------------------------------------------------------------------------


def test_enforcement_distribution_returns_200(client, configured_db):
    resp = client.get("/risk/metrics/enforcement-distribution")
    assert resp.status_code == 200


def test_enforcement_distribution_top_level_keys(client, configured_db):
    body = client.get("/risk/metrics/enforcement-distribution").json()
    assert set(body.keys()) == {"window", "distribution", "total", "computed_at"}


def test_enforcement_distribution_empty_db(client, configured_db):
    body = client.get("/risk/metrics/enforcement-distribution").json()
    assert body["distribution"] == {}
    assert body["total"] == 0


def test_enforcement_distribution_counts_and_pct(
    client, configured_db, insert_interaction_risk, monkeypatch
):
    _freeze(monkeypatch)
    insert_interaction_risk(
        interaction_id="ix-1", enforcement_type="block", computed_at=NOW - dt.timedelta(hours=1)
    )
    insert_interaction_risk(
        interaction_id="ix-2", enforcement_type="block", computed_at=NOW - dt.timedelta(hours=1)
    )
    insert_interaction_risk(
        interaction_id="ix-3", enforcement_type="allow", computed_at=NOW - dt.timedelta(hours=1)
    )
    body = client.get("/risk/metrics/enforcement-distribution?window=24h").json()
    assert body["total"] == 3
    assert body["distribution"]["block"] == {"count": 2, "pct": pytest.approx(66.67)}
    assert body["distribution"]["allow"] == {"count": 1, "pct": pytest.approx(33.33)}


def test_enforcement_distribution_excludes_null_enforcement_type(
    client, configured_db, insert_interaction_risk, monkeypatch
):
    _freeze(monkeypatch)
    insert_interaction_risk(
        interaction_id="ix-1", enforcement_type=None, computed_at=NOW - dt.timedelta(hours=1)
    )
    insert_interaction_risk(
        interaction_id="ix-2", enforcement_type="allow", computed_at=NOW - dt.timedelta(hours=1)
    )
    body = client.get("/risk/metrics/enforcement-distribution?window=24h").json()
    assert body["total"] == 1
    assert set(body["distribution"].keys()) == {"allow"}


def test_enforcement_distribution_absent_type_is_absent_key_not_zero(
    client, configured_db, insert_interaction_risk, monkeypatch
):
    _freeze(monkeypatch)
    insert_interaction_risk(
        interaction_id="ix-1", enforcement_type="allow", computed_at=NOW - dt.timedelta(hours=1)
    )
    body = client.get("/risk/metrics/enforcement-distribution?window=24h").json()
    assert "block" not in body["distribution"]


def test_enforcement_distribution_pre_migration_returns_zeros_not_500(
    client, configured_db, monkeypatch
):
    monkeypatch.setattr(aggregate, "_risk_tables_exist", lambda tx: False)
    resp = client.get("/risk/metrics/enforcement-distribution")
    assert resp.status_code == 200
    assert resp.json() == {
        "window": "24h",
        "distribution": {},
        "total": 0,
        "computed_at": resp.json()["computed_at"],
    }


# ---------------------------------------------------------------------------
# GET /risk/metrics/top-rules
# ---------------------------------------------------------------------------


def test_top_rules_returns_200(client, configured_db):
    resp = client.get("/risk/metrics/top-rules")
    assert resp.status_code == 200


def test_top_rules_top_level_keys(client, configured_db):
    body = client.get("/risk/metrics/top-rules").json()
    assert set(body.keys()) == {"window", "items"}


def test_top_rules_item_shape(
    client, configured_db, insert_interaction_risk, varied_catalog, monkeypatch
):
    _freeze(monkeypatch)
    insert_interaction_risk(
        interaction_id="ix-1",
        trace_id="tr-1",
        triggered_rule_ids=["FX-CRIT-BLOCK"],
        risk_level="critical",
        computed_at=NOW - dt.timedelta(hours=1),
    )
    body = client.get("/risk/metrics/top-rules?window=24h").json()
    assert len(body["items"]) == 1
    assert set(body["items"][0].keys()) == {
        "rule_id",
        "rule_name",
        "count",
        "trace_count",
        "risk_level",
        "risk_level_distribution",
    }


def test_top_rules_empty_db_is_empty_items(client, configured_db):
    body = client.get("/risk/metrics/top-rules").json()
    assert body["items"] == []


def test_top_rules_unknown_rule_id_has_null_name_and_level(
    client, configured_db, insert_interaction_risk, monkeypatch
):
    _freeze(monkeypatch)
    insert_interaction_risk(
        interaction_id="ix-1",
        triggered_rule_ids=["NOPE-NOT-IN-CATALOG"],
        computed_at=NOW - dt.timedelta(hours=1),
    )
    body = client.get("/risk/metrics/top-rules?window=24h").json()
    item = body["items"][0]
    assert item["rule_id"] == "NOPE-NOT-IN-CATALOG"
    assert item["rule_name"] is None
    assert item["risk_level"] is None


def test_top_rules_ranked_by_count_descending(
    client, configured_db, insert_interaction_risk, varied_catalog, monkeypatch
):
    _freeze(monkeypatch)
    for i in range(3):
        insert_interaction_risk(
            interaction_id=f"ix-crit-{i}",
            triggered_rule_ids=["FX-CRIT-BLOCK"],
            computed_at=NOW - dt.timedelta(hours=1),
        )
    insert_interaction_risk(
        interaction_id="ix-low",
        triggered_rule_ids=["FX-LOW-ALLOW"],
        computed_at=NOW - dt.timedelta(hours=1),
    )
    body = client.get("/risk/metrics/top-rules?window=24h").json()
    assert [item["rule_id"] for item in body["items"]] == [
        "FX-CRIT-BLOCK",
        "FX-LOW-ALLOW",
    ]


def test_top_rules_default_limit_is_config_driven(
    client, configured_db, insert_interaction_risk, monkeypatch
):
    _freeze(monkeypatch)
    monkeypatch.setattr(config, "API_METRICS_TOP_RULES_DEFAULT_LIMIT", 1)
    for i in range(3):
        insert_interaction_risk(
            interaction_id=f"ix-{i}",
            triggered_rule_ids=[f"RULE-{i}"],
            computed_at=NOW - dt.timedelta(hours=1),
        )
    body = client.get("/risk/metrics/top-rules?window=24h").json()
    assert len(body["items"]) == 1


def test_top_rules_default_limit_is_read_per_request(
    client, configured_db, insert_interaction_risk, monkeypatch
):
    _freeze(monkeypatch)
    for i in range(3):
        insert_interaction_risk(
            interaction_id=f"ix-{i}",
            triggered_rule_ids=[f"RULE-{i}"],
            computed_at=NOW - dt.timedelta(hours=1),
        )
    monkeypatch.setattr(config, "API_METRICS_TOP_RULES_DEFAULT_LIMIT", 1)
    first = client.get("/risk/metrics/top-rules?window=24h").json()
    assert len(first["items"]) == 1

    monkeypatch.setattr(config, "API_METRICS_TOP_RULES_DEFAULT_LIMIT", 2)
    second = client.get("/risk/metrics/top-rules?window=24h").json()
    assert len(second["items"]) == 2


def test_top_rules_limit_zero_is_400(client, configured_db):
    resp = client.get("/risk/metrics/top-rules?limit=0")
    assert resp.status_code == 400


def test_top_rules_limit_negative_is_400(client, configured_db):
    resp = client.get("/risk/metrics/top-rules?limit=-1")
    assert resp.status_code == 400


def test_top_rules_limit_non_integer_is_400(client, configured_db):
    resp = client.get("/risk/metrics/top-rules?limit=abc")
    assert resp.status_code == 400


def test_top_rules_limit_over_max_is_400(client, configured_db):
    resp = client.get("/risk/metrics/top-rules?limit=51")
    assert resp.status_code == 400


def test_top_rules_limit_equal_to_max_is_allowed(client, configured_db):
    resp = client.get("/risk/metrics/top-rules?limit=50")
    assert resp.status_code == 200


def test_top_rules_config_default_does_not_change_the_max(
    client, configured_db, monkeypatch
):
    monkeypatch.setattr(config, "API_METRICS_TOP_RULES_DEFAULT_LIMIT", 5)
    resp = client.get("/risk/metrics/top-rules?limit=51")
    assert resp.status_code == 400


def test_top_rules_has_no_next_cursor(client, configured_db):
    body = client.get("/risk/metrics/top-rules").json()
    assert "next_cursor" not in body


def test_top_rules_pre_migration_returns_empty_not_500(
    client, configured_db, monkeypatch
):
    monkeypatch.setattr(aggregate, "_risk_tables_exist", lambda tx: False)
    resp = client.get("/risk/metrics/top-rules")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# GET /risk/metrics/top-traces
# ---------------------------------------------------------------------------


def test_top_traces_returns_200(client, configured_db):
    resp = client.get("/risk/metrics/top-traces")
    assert resp.status_code == 200


def test_top_traces_top_level_keys(client, configured_db):
    body = client.get("/risk/metrics/top-traces").json()
    assert set(body.keys()) == {"window", "items"}


def test_top_traces_empty_db_is_empty_items(client, configured_db):
    body = client.get("/risk/metrics/top-traces").json()
    assert body["items"] == []


def test_top_traces_item_shape_matches_risk_traces_item_shape(
    client, configured_db, insert_trace_risk, monkeypatch
):
    """D1: same serializer as /risk/traces -> identical key set."""
    _freeze(monkeypatch)
    insert_trace_risk(trace_id="tr-1", computed_at=NOW - dt.timedelta(hours=1))

    metrics_body = client.get("/risk/metrics/top-traces?window=24h").json()
    traces_body = client.get("/risk/traces").json()

    assert len(metrics_body["items"]) == 1
    assert len(traces_body["items"]) == 1
    assert set(metrics_body["items"][0].keys()) == set(traces_body["items"][0].keys())
    assert metrics_body["items"][0] == traces_body["items"][0]


def test_top_traces_ranked_by_risk_level_severity(
    client, configured_db, insert_trace_risk, monkeypatch
):
    _freeze(monkeypatch)
    insert_trace_risk(
        trace_id="tr-low", trace_risk_level="low", computed_at=NOW - dt.timedelta(hours=1)
    )
    insert_trace_risk(
        trace_id="tr-critical",
        trace_risk_level="critical",
        computed_at=NOW - dt.timedelta(hours=1),
    )
    body = client.get("/risk/metrics/top-traces?window=24h").json()
    assert [item["trace_id"] for item in body["items"]] == ["tr-critical", "tr-low"]


def test_top_traces_limit_zero_is_400(client, configured_db):
    resp = client.get("/risk/metrics/top-traces?limit=0")
    assert resp.status_code == 400


def test_top_traces_limit_over_max_is_400(client, configured_db):
    resp = client.get("/risk/metrics/top-traces?limit=51")
    assert resp.status_code == 400


def test_top_traces_limit_equal_to_max_is_allowed(client, configured_db):
    resp = client.get("/risk/metrics/top-traces?limit=50")
    assert resp.status_code == 200


def test_top_traces_default_limit_is_config_driven(
    client, configured_db, insert_trace_risk, monkeypatch
):
    _freeze(monkeypatch)
    monkeypatch.setattr(config, "API_METRICS_TOP_TRACES_DEFAULT_LIMIT", 1)
    insert_trace_risk(trace_id="tr-1", computed_at=NOW - dt.timedelta(hours=1))
    insert_trace_risk(trace_id="tr-2", computed_at=NOW - dt.timedelta(hours=1))
    body = client.get("/risk/metrics/top-traces?window=24h").json()
    assert len(body["items"]) == 1


def test_top_traces_has_no_next_cursor(client, configured_db):
    body = client.get("/risk/metrics/top-traces").json()
    assert "next_cursor" not in body


def test_top_traces_pre_migration_returns_empty_not_500(
    client, configured_db, monkeypatch
):
    monkeypatch.setattr(aggregate, "_trace_risk_table_exists", lambda tx: False)
    resp = client.get("/risk/metrics/top-traces")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# GET /risk/metrics/risk-by-category
# ---------------------------------------------------------------------------


def test_risk_by_category_returns_200(client, configured_db):
    resp = client.get("/risk/metrics/risk-by-category")
    assert resp.status_code == 200


def test_risk_by_category_top_level_keys(client, configured_db):
    body = client.get("/risk/metrics/risk-by-category").json()
    assert set(body.keys()) == {"window", "items"}


def test_risk_by_category_empty_db_is_empty_items(client, configured_db):
    body = client.get("/risk/metrics/risk-by-category").json()
    assert body["items"] == []


def test_risk_by_category_item_shape(
    client, configured_db, insert_interaction_risk, varied_catalog, monkeypatch
):
    _freeze(monkeypatch)
    insert_interaction_risk(
        interaction_id="ix-1",
        triggered_rule_ids=["FX-CRIT-BLOCK"],
        computed_at=NOW - dt.timedelta(hours=1),
    )
    body = client.get("/risk/metrics/risk-by-category?window=24h").json()
    assert len(body["items"]) > 0
    for item in body["items"]:
        assert set(item.keys()) == {"category", "count"}


def test_risk_by_category_counts_and_sorted_by_category(
    client, configured_db, insert_interaction_risk, varied_catalog, monkeypatch
):
    _freeze(monkeypatch)
    # FX-CRIT-BLOCK -> [pii_exposure, data_leakage]; FX-LOW-ALLOW -> [data_leakage]
    insert_interaction_risk(
        interaction_id="ix-1",
        triggered_rule_ids=["FX-CRIT-BLOCK"],
        computed_at=NOW - dt.timedelta(hours=1),
    )
    insert_interaction_risk(
        interaction_id="ix-2",
        triggered_rule_ids=["FX-LOW-ALLOW"],
        computed_at=NOW - dt.timedelta(hours=1),
    )
    body = client.get("/risk/metrics/risk-by-category?window=24h").json()
    got = {item["category"]: item["count"] for item in body["items"]}
    assert got == {"pii_exposure": 1, "data_leakage": 2}
    assert [item["category"] for item in body["items"]] == sorted(got.keys())


def test_risk_by_category_unknown_rule_id_skipped(
    client, configured_db, insert_interaction_risk, monkeypatch
):
    _freeze(monkeypatch)
    insert_interaction_risk(
        interaction_id="ix-1",
        triggered_rule_ids=["NOPE-NOT-IN-CATALOG"],
        computed_at=NOW - dt.timedelta(hours=1),
    )
    body = client.get("/risk/metrics/risk-by-category?window=24h").json()
    assert body["items"] == []


def test_risk_by_category_pre_migration_returns_empty_not_500(
    client, configured_db, monkeypatch
):
    monkeypatch.setattr(aggregate, "_risk_tables_exist", lambda tx: False)
    resp = client.get("/risk/metrics/risk-by-category")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# Cross-endpoint parametrized sweep (D4/D9)
# ---------------------------------------------------------------------------

_ALL_METRICS_PATHS = (
    "/risk/metrics/summary",
    "/risk/metrics/risk-distribution",
    "/risk/metrics/enforcement-distribution",
    "/risk/metrics/top-rules",
    "/risk/metrics/top-traces",
    "/risk/metrics/risk-by-category",
)


@pytest.mark.parametrize("path", _ALL_METRICS_PATHS)
def test_default_window_is_24h(client, configured_db, path):
    body = client.get(path).json()
    assert body["window"] == "24h"


@pytest.mark.parametrize("path", _ALL_METRICS_PATHS)
def test_window_is_echoed(client, configured_db, path):
    body = client.get(f"{path}?window=7d").json()
    assert body["window"] == "7d"


@pytest.mark.parametrize("path", _ALL_METRICS_PATHS)
def test_unknown_window_is_400_with_fr_das_081_shape(client, configured_db, path):
    resp = client.get(f"{path}?window=bogus")
    assert resp.status_code == 400
    assert set(resp.json().keys()) == {"error", "detail", "timestamp"}


@pytest.mark.parametrize("path", _ALL_METRICS_PATHS)
def test_custom_window_without_from_and_to_is_400(client, configured_db, path):
    resp = client.get(f"{path}?window=custom")
    assert resp.status_code == 400


@pytest.mark.parametrize("path", _ALL_METRICS_PATHS)
def test_custom_window_from_after_to_is_400(client, configured_db, path):
    resp = client.get(
        f"{path}?window=custom&from=2026-01-02T00:00:00Z&to=2026-01-01T00:00:00Z"
    )
    assert resp.status_code == 400


@pytest.mark.parametrize("path", _ALL_METRICS_PATHS)
def test_custom_window_naive_from_is_400(client, configured_db, path):
    resp = client.get(
        f"{path}?window=custom&from=2026-01-01T00:00:00&to=2026-01-02T00:00:00Z"
    )
    assert resp.status_code == 400


@pytest.mark.parametrize("path", _ALL_METRICS_PATHS)
def test_custom_window_valid_from_and_to_succeeds(client, configured_db, path):
    resp = client.get(
        f"{path}?window=custom&from=2026-01-01T00:00:00Z&to=2026-01-02T00:00:00Z"
    )
    assert resp.status_code == 200
    assert resp.json()["window"] == "custom"


@pytest.mark.parametrize("path", _ALL_METRICS_PATHS)
def test_no_next_cursor_key_anywhere(client, configured_db, path):
    body = client.get(path).json()
    assert "next_cursor" not in body


@pytest.mark.parametrize("path", _ALL_METRICS_PATHS)
def test_no_completeness_keys_anywhere(client, configured_db, path):
    body = client.get(path).json()
    _assert_no_completeness_keys(body)


@pytest.mark.parametrize(
    "path", [p for p in _ALL_METRICS_PATHS if p != "/risk/metrics/summary"]
)
def test_only_summary_echoes_from_and_to(client, configured_db, path):
    """D9: every other endpoint omits from/to, echoing window alone."""
    body = client.get(path).json()
    assert "from" not in body
    assert "to" not in body
