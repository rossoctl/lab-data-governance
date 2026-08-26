"""`/risk/interactions*` and `/risk/traces*` HTTP adapter tests (issue #109).

Thin adapters over `data_governance.retrieval.risk`'s
list_interaction_risk/get_interaction_risk/get_interaction_risk_history and
their trace-grain counterparts. Unlike `/risk/rules*` these need a real,
migrated Postgres — see `conftest.py`'s local `configured_db` copy.

The forest endpoint (`/risk/traces/{trace_id}`) has its own file,
`test_risk_trace_detail.py`.
"""

from __future__ import annotations

import datetime as dt
import uuid

import psycopg
import pytest

from tests.risk.api.conftest import assert_no_forbidden_keys


def _uuid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def insert_interaction_risk(configured_db: str):
    def _insert(
        *,
        interaction_id: str,
        trace_id: str = "tr-1",
        version: int = 1,
        risk_level: str = "low",
        enforcement_type: str | None = None,
        triggered_rule_ids: list[str] | None = None,
        classification_summary: dict | None = None,
        computed_at: dt.datetime | None = None,
    ) -> str:
        interaction_risk_id = _uuid()
        with psycopg.connect(configured_db) as conn:
            conn.execute(
                "INSERT INTO interaction_risk_records ("
                "interaction_risk_id, interaction_id, trace_id, "
                "caller_entity_id, callee_entity_id, version, computed_at, "
                "risk_level, enforcement_type, policy_event_count, "
                "triggered_rule_ids, classification_summary"
                ") VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now()), %s, "
                "%s, %s, %s, %s)",
                (
                    interaction_risk_id,
                    interaction_id,
                    trace_id,
                    "ent-caller",
                    "ent-callee",
                    version,
                    computed_at,
                    risk_level,
                    enforcement_type,
                    1,
                    triggered_rule_ids or [],
                    (
                        psycopg.types.json.Jsonb(classification_summary)
                        if classification_summary is not None
                        else None
                    ),
                ),
            )
            conn.commit()
        return interaction_risk_id

    return _insert


@pytest.fixture()
def insert_trace_risk(configured_db: str):
    def _insert(
        *,
        trace_id: str,
        version: int = 1,
        trace_risk_level: str = "low",
        computed_at: dt.datetime | None = None,
    ) -> str:
        trace_risk_id = _uuid()
        with psycopg.connect(configured_db) as conn:
            conn.execute(
                "INSERT INTO trace_risk_records ("
                "trace_risk_id, trace_id, version, computed_at, "
                "trace_risk_level, interaction_count, policy_event_count"
                ") VALUES (%s, %s, %s, COALESCE(%s, now()), %s, %s, %s)",
                (trace_risk_id, trace_id, version, computed_at, trace_risk_level, 1, 1),
            )
            conn.commit()
        return trace_risk_id

    return _insert


# ---------------------------------------------------------------------------
# GET /risk/interactions — list
# ---------------------------------------------------------------------------


def test_list_interactions_returns_latest_version_only(client, insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", version=1, risk_level="low")
    insert_interaction_risk(interaction_id="ix-1", version=2, risk_level="high")

    resp = client.get("/risk/interactions")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["risk_level"] == "high"


def test_list_interactions_default_shape_has_items_and_next_cursor(
    client, insert_interaction_risk
):
    insert_interaction_risk(interaction_id="ix-1")

    body = client.get("/risk/interactions").json()

    assert set(body.keys()) == {"items", "next_cursor"}


def test_list_interactions_filter_by_trace_id(client, insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", trace_id="tr-a")
    insert_interaction_risk(interaction_id="ix-2", trace_id="tr-b")

    body = client.get("/risk/interactions", params={"trace_id": "tr-a"}).json()

    assert [item["interaction_id"] for item in body["items"]] == ["ix-1"]


def test_list_interactions_filter_by_risk_level_csv(client, insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", risk_level="critical")
    insert_interaction_risk(interaction_id="ix-2", risk_level="high")
    insert_interaction_risk(interaction_id="ix-3", risk_level="low")

    body = client.get(
        "/risk/interactions", params={"risk_level": "critical,high"}
    ).json()

    assert {item["interaction_id"] for item in body["items"]} == {"ix-1", "ix-2"}


def test_list_interactions_empty_risk_level_matches_nothing(
    client, insert_interaction_risk
):
    insert_interaction_risk(interaction_id="ix-1", risk_level="critical")

    body = client.get("/risk/interactions", params={"risk_level": ""}).json()

    assert body["items"] == []


def test_list_interactions_unknown_query_param_is_ignored(
    client, insert_interaction_risk
):
    insert_interaction_risk(interaction_id="ix-1")

    resp = client.get("/risk/interactions", params={"bogus": "x"})

    assert resp.status_code == 200


def test_list_interactions_sort_risk_level_desc(client, insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-low", risk_level="low")
    insert_interaction_risk(interaction_id="ix-critical", risk_level="critical")

    body = client.get(
        "/risk/interactions", params={"sort": "risk_level_desc"}
    ).json()

    assert [item["interaction_id"] for item in body["items"]] == [
        "ix-critical",
        "ix-low",
    ]


def test_list_interactions_unknown_sort_is_400(client, insert_interaction_risk):
    resp = client.get("/risk/interactions", params={"sort": "bogus"})

    assert resp.status_code == 400
    assert set(resp.json().keys()) == {"error", "detail", "timestamp"}


def test_list_interactions_limit_over_max_is_400(client, insert_interaction_risk):
    resp = client.get("/risk/interactions", params={"limit": "501"})

    assert resp.status_code == 400


def test_list_interactions_limit_at_max_is_200(client, insert_interaction_risk):
    resp = client.get("/risk/interactions", params={"limit": "500"})

    assert resp.status_code == 200


def test_list_interactions_limit_zero_is_400(client, insert_interaction_risk):
    resp = client.get("/risk/interactions", params={"limit": "0"})

    assert resp.status_code == 400


def test_list_interactions_limit_non_integer_is_400(client, insert_interaction_risk):
    resp = client.get("/risk/interactions", params={"limit": "abc"})

    assert resp.status_code == 400


def test_list_interactions_malformed_cursor_is_400(client, insert_interaction_risk):
    resp = client.get("/risk/interactions", params={"cursor": "not-base64!!"})

    assert resp.status_code == 400


def test_list_interactions_from_naive_datetime_is_400(client, insert_interaction_risk):
    resp = client.get("/risk/interactions", params={"from": "2026-01-01T00:00:00"})

    assert resp.status_code == 400


def test_list_interactions_from_to_inclusive(client, insert_interaction_risk):
    early = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    late = dt.datetime(2026, 1, 10, tzinfo=dt.timezone.utc)
    insert_interaction_risk(interaction_id="ix-early", computed_at=early)
    insert_interaction_risk(interaction_id="ix-late", computed_at=late)

    body = client.get(
        "/risk/interactions",
        params={"from": "2026-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
    ).json()

    assert [item["interaction_id"] for item in body["items"]] == ["ix-early"]


def test_list_interactions_from_after_to_is_empty_not_error(
    client, insert_interaction_risk
):
    insert_interaction_risk(interaction_id="ix-1")

    resp = client.get(
        "/risk/interactions",
        params={"from": "2026-06-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
    )

    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_list_interactions_cursor_walk_is_gapless_and_duplicate_free(
    client, insert_interaction_risk
):
    for i in range(6):
        insert_interaction_risk(
            interaction_id=f"ix-{i}",
            computed_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
            + dt.timedelta(minutes=i),
        )

    seen: list[str] = []
    cursor = None
    for _ in range(20):
        params = {"limit": "2"}
        if cursor is not None:
            params["cursor"] = cursor
        body = client.get("/risk/interactions", params=params).json()
        seen.extend(item["interaction_id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert sorted(seen) == sorted(f"ix-{i}" for i in range(6))
    assert len(seen) == len(set(seen))


def test_list_interactions_cursor_minted_under_one_sort_rejected_under_another(
    client, insert_interaction_risk
):
    insert_interaction_risk(interaction_id="ix-1")
    insert_interaction_risk(interaction_id="ix-2")

    body = client.get(
        "/risk/interactions", params={"limit": "1", "sort": "computed_at_desc"}
    ).json()
    cursor = body["next_cursor"]
    assert cursor is not None

    resp = client.get(
        "/risk/interactions",
        params={"cursor": cursor, "sort": "risk_level_desc"},
    )

    assert resp.status_code == 400


def test_list_interactions_empty_result_has_no_total_or_completeness_field(
    client, configured_db
):
    body = client.get("/risk/interactions").json()

    assert_no_forbidden_keys(body)


def test_list_interactions_regulatory_tag_matches_a_leg(
    client, insert_interaction_risk
):
    insert_interaction_risk(
        interaction_id="ix-1",
        classification_summary={
            "request": {"regulatory_tags": ["gdpr"]},
        },
    )
    insert_interaction_risk(interaction_id="ix-2")

    body = client.get(
        "/risk/interactions", params={"regulatory_tag": "gdpr"}
    ).json()

    assert [item["interaction_id"] for item in body["items"]] == ["ix-1"]


def test_list_interactions_regulatory_tag_does_not_match_null_summary(
    client, insert_interaction_risk
):
    insert_interaction_risk(interaction_id="ix-1", classification_summary=None)

    body = client.get(
        "/risk/interactions", params={"regulatory_tag": "gdpr"}
    ).json()

    assert body["items"] == []


# ---------------------------------------------------------------------------
# GET /risk/interactions/{interaction_id}
# ---------------------------------------------------------------------------


def test_get_interaction_returns_latest_version(client, insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", version=1, risk_level="low")
    insert_interaction_risk(interaction_id="ix-1", version=2, risk_level="high")

    body = client.get("/risk/interactions/ix-1").json()

    assert body["risk_level"] == "high"
    assert body["version"] == 2


def test_get_interaction_unknown_id_is_404(client, insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1")

    resp = client.get("/risk/interactions/nope")

    assert resp.status_code == 404
    assert set(resp.json().keys()) == {"error", "detail", "timestamp"}


# ---------------------------------------------------------------------------
# GET /risk/interactions/{interaction_id}/history
# ---------------------------------------------------------------------------


def test_get_interaction_history_returns_all_versions_ascending(
    client, insert_interaction_risk
):
    insert_interaction_risk(interaction_id="ix-1", version=1)
    insert_interaction_risk(interaction_id="ix-1", version=2)
    insert_interaction_risk(interaction_id="ix-1", version=3)

    body = client.get("/risk/interactions/ix-1/history").json()

    assert [item["version"] for item in body["items"]] == [1, 2, 3]


def test_get_interaction_history_unknown_id_is_empty_not_404(
    client, insert_interaction_risk
):
    insert_interaction_risk(interaction_id="ix-1")

    resp = client.get("/risk/interactions/nope/history")

    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_get_interaction_history_ignores_unrecognized_sort(
    client, insert_interaction_risk
):
    insert_interaction_risk(interaction_id="ix-1", version=1)
    insert_interaction_risk(interaction_id="ix-1", version=2)

    resp = client.get(
        "/risk/interactions/ix-1/history", params={"sort": "bogus"}
    )

    assert resp.status_code == 200
    assert [item["version"] for item in resp.json()["items"]] == [1, 2]


def test_history_route_precedes_id_route_for_the_literal_segment(
    client, insert_interaction_risk
):
    """'/history' must not be swallowed by {interaction_id:str} as a literal
    id — registration order (history before {id}) is what prevents that."""
    insert_interaction_risk(interaction_id="ix-1", version=1)

    body = client.get("/risk/interactions/ix-1/history").json()

    assert "items" in body
    assert "version" not in body


# ---------------------------------------------------------------------------
# GET /risk/traces — list
# ---------------------------------------------------------------------------


def test_list_traces_returns_latest_version_only(client, insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", version=1, trace_risk_level="low")
    insert_trace_risk(trace_id="tr-1", version=2, trace_risk_level="high")

    body = client.get("/risk/traces").json()

    assert len(body["items"]) == 1
    assert body["items"][0]["trace_risk_level"] == "high"


def test_list_traces_ignores_regulatory_tag_param(client, insert_trace_risk):
    insert_trace_risk(trace_id="tr-1")

    resp = client.get("/risk/traces", params={"regulatory_tag": "gdpr"})

    assert resp.status_code == 200


def test_list_traces_limit_over_max_is_400(client, insert_trace_risk):
    resp = client.get("/risk/traces", params={"limit": "201"})

    assert resp.status_code == 400


def test_list_traces_limit_at_max_is_200(client, insert_trace_risk):
    resp = client.get("/risk/traces", params={"limit": "200"})

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /risk/traces/{trace_id}/history
# ---------------------------------------------------------------------------


def test_get_trace_history_returns_all_versions_ascending(client, insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", version=1)
    insert_trace_risk(trace_id="tr-1", version=2)

    body = client.get("/risk/traces/tr-1/history").json()

    assert [item["version"] for item in body["items"]] == [1, 2]


def test_get_trace_history_unknown_id_is_empty_not_404(client, insert_trace_risk):
    resp = client.get("/risk/traces/nope/history")

    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# FR-DAS-084: no total / completeness field, recursively
# ---------------------------------------------------------------------------


def test_list_interactions_response_has_no_forbidden_keys(
    client, insert_interaction_risk
):
    insert_interaction_risk(interaction_id="ix-1")

    body = client.get("/risk/interactions").json()

    assert_no_forbidden_keys(body)


def test_list_traces_response_has_no_forbidden_keys(client, insert_trace_risk):
    insert_trace_risk(trace_id="tr-1")

    body = client.get("/risk/traces").json()

    assert_no_forbidden_keys(body)
