"""`/risk/alerts*` HTTP tests (issue #110, read half) against a migrated
Postgres (`conftest.py`'s local `configured_db`).

Acceptance criteria covered, by test name:

- AC-DAS-025 — each `by-*` endpoint returns the same result set as the
  equivalent single-filter `GET /risk/alerts` query
  (`test_by_endpoint_matches_the_single_filter_list`).
- `include_superseded` — false by default excludes superseded alerts, true
  includes them.
- AC-DAS-031..034 — cursor/limit semantics: a gapless, duplicate-free walk
  under both sorts with timestamp ties, cursor/sort mismatch and bad limits
  rejected, no `total`.
- AC-DAS-038 — `api.risk_alerts.default_limit` changes only this endpoint's
  unqualified-request `limit`.

Also: rows written by the real generator (#104) are served as written.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import psycopg
import pytest

from data_governance import db
from data_governance.risk import config
from data_governance.risk.alerts.generate import generate_for_trace_risk_record
from data_governance.risk.api import alert_routes, http
from tests.risk.api.conftest import assert_no_forbidden_keys

_T0 = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc)


def _at(minutes: int) -> dt.datetime:
    return _T0 + dt.timedelta(minutes=minutes)


@pytest.fixture()
def insert_alert(configured_db: str):
    def _insert(
        *,
        timestamp: dt.datetime,
        trace_id: str = "tr-1",
        risk_level: str = "high",
        enforcement_type: str | None = None,
        status: str = "new",
        alert_type: str | None = "standard",
        assigned_to: str | None = None,
        entity_ids: list[str] | None = None,
        triggered_rule_ids: list[str] | None = None,
        superseded_by: str | None = None,
        duplicate_count: int = 0,
        alert_id: str | None = None,
    ) -> str:
        alert_id = alert_id or str(uuid.uuid4())
        entities = [
            {"entity_id": e, "entity_kind": "agent", "display_name": e}
            for e in (entity_ids or [])
        ]
        with psycopg.connect(configured_db) as conn:
            conn.execute(
                'INSERT INTO alerts (alert_id, "timestamp", trace_id, risk_level, '
                "enforcement_type, title, description, involved_entities, "
                "triggered_rule_ids, status, alert_type, assigned_to, "
                "duplicate_count, superseded_by) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)",
                (
                    alert_id,
                    timestamp,
                    trace_id,
                    risk_level,
                    enforcement_type,
                    f"title {alert_id[:8]}",
                    "description",
                    json.dumps(entities),
                    triggered_rule_ids or [],
                    status,
                    alert_type,
                    assigned_to,
                    duplicate_count,
                    superseded_by,
                ),
            )
            conn.commit()
        return alert_id

    return _insert


def _ids(body: dict) -> list[str]:
    return [item["alert_id"] for item in body["items"]]


def ids_of(client, **params) -> list[str]:
    return _ids(client.get("/risk/alerts", params=params).json())


def _walk(client, path: str, params: dict) -> list[str]:
    """Every alert id across all pages, following `next_cursor`."""
    seen: list[str] = []
    cursor = None
    for _ in range(50):
        query = dict(params, **({"cursor": cursor} if cursor else {}))
        resp = client.get(path, params=query)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        seen.extend(_ids(body))
        cursor = body["next_cursor"]
        if cursor is None:
            return seen
    raise AssertionError("pagination did not terminate")


# ---------------------------------------------------------------------------
# GET /risk/alerts — shape, order, superseded
# ---------------------------------------------------------------------------


def test_list_shape_and_newest_first(client, insert_alert):
    old = insert_alert(timestamp=_at(0))
    new = insert_alert(timestamp=_at(5))

    resp = client.get("/risk/alerts")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "next_cursor"}
    assert _ids(body) == [new, old]
    assert body["next_cursor"] is None
    assert set(body["items"][0]) == {
        "alert_id", "timestamp", "trace_id", "trace_risk_record_id", "risk_level",
        "enforcement_type", "title", "description", "involved_entities",
        "triggered_rule_ids", "status", "alert_type", "assigned_to", "dedup_key",
        "duplicate_count", "superseded_by",
    }
    assert_no_forbidden_keys(body)


def test_empty_table_is_an_empty_page(client, configured_db):
    assert client.get("/risk/alerts").json() == {"items": [], "next_cursor": None}


def test_superseded_alerts_are_excluded_by_default(client, insert_alert):
    head = insert_alert(timestamp=_at(5), risk_level="critical")
    old = insert_alert(timestamp=_at(0), risk_level="high", superseded_by=head)

    assert _ids(client.get("/risk/alerts").json()) == [head]
    assert _ids(
        client.get("/risk/alerts", params={"include_superseded": "false"}).json()
    ) == [head]
    assert _ids(
        client.get("/risk/alerts", params={"include_superseded": "true"}).json()
    ) == [head, old]


def test_include_superseded_rejects_other_values(client, configured_db):
    resp = client.get("/risk/alerts", params={"include_superseded": "yes"})
    assert resp.status_code == 400
    assert set(resp.json()) == {"error", "detail", "timestamp"}


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_csv_filters_take_any_listed_value(client, insert_alert):
    crit = insert_alert(timestamp=_at(3), risk_level="critical", status="new",
                        enforcement_type="block")
    high = insert_alert(timestamp=_at(2), risk_level="high", status="acknowledged",
                        enforcement_type="warn")
    low = insert_alert(timestamp=_at(1), risk_level="low", status="resolved",
                       enforcement_type=None)

    def ids(**params):
        return _ids(client.get("/risk/alerts", params=params).json())

    assert ids(risk_level="critical,low") == [crit, low]
    assert ids(status="acknowledged,resolved") == [high, low]
    assert ids(enforcement_type="block,warn") == [crit, high]
    assert ids(risk_level="critical", status="acknowledged") == []


def test_empty_csv_filter_matches_nothing(client, insert_alert):
    insert_alert(timestamp=_at(0))
    assert ids_of(client, status="") == []


def test_scalar_filters(client, insert_alert):
    a = insert_alert(timestamp=_at(3), trace_id="tr-a", alert_type="standard",
                     assigned_to="ops-1", entity_ids=["e-1", "e-2"],
                     triggered_rule_ids=["DG-001"])
    b = insert_alert(timestamp=_at(2), trace_id="tr-b", alert_type="new_entity",
                     assigned_to=None, entity_ids=["e-2"],
                     triggered_rule_ids=["DG-002", "DG-004"])

    assert ids_of(client, trace_id="tr-a") == [a]
    assert ids_of(client, alert_type="new_entity") == [b]
    assert ids_of(client, assigned_to="ops-1") == [a]
    assert ids_of(client, entity_id="e-1") == [a]
    assert ids_of(client, entity_id="e-2") == [a, b]
    assert ids_of(client, entity_id="e-9") == []
    assert ids_of(client, rule_id="DG-004") == [b]


def test_time_window_is_inclusive_on_timestamp(client, insert_alert):
    insert_alert(timestamp=_at(0))
    mid = insert_alert(timestamp=_at(5))
    edge = insert_alert(timestamp=_at(10))
    insert_alert(timestamp=_at(15))

    params = {"from": _at(5).isoformat(), "to": _at(10).isoformat()}
    assert ids_of(client, **params) == [edge, mid]


def test_naive_datetime_is_rejected(client, configured_db):
    resp = client.get("/risk/alerts", params={"from": "2026-10-01T12:00:00"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Sort and pagination (AC-DAS-031..034)
# ---------------------------------------------------------------------------


def test_risk_level_sort_most_severe_first_then_newest(client, insert_alert):
    low = insert_alert(timestamp=_at(9), risk_level="low")
    crit_old = insert_alert(timestamp=_at(1), risk_level="critical")
    crit_new = insert_alert(timestamp=_at(2), risk_level="critical")
    medium = insert_alert(timestamp=_at(5), risk_level="medium")

    assert ids_of(client, sort="risk_level_desc") == [crit_new, crit_old, medium, low]


def test_unknown_sort_is_rejected(client, configured_db):
    resp = client.get("/risk/alerts", params={"sort": "computed_at_desc"})
    assert resp.status_code == 400


@pytest.mark.parametrize("sort", ["timestamp_desc", "risk_level_desc"])
def test_pagination_walk_is_gapless_with_timestamp_ties(client, insert_alert, sort):
    """Rows share a level AND a sub-second timestamp, so a page boundary falls
    inside a tie that only alert_id breaks, under both sorts."""
    levels = ["critical", "high"]
    created = [
        insert_alert(
            timestamp=_at(i // 6) + dt.timedelta(microseconds=123457),
            risk_level=levels[i % 2],
        )
        for i in range(14)
    ]
    full = ids_of(client, sort=sort, limit="200")

    walked = _walk(client, "/risk/alerts", {"sort": sort, "limit": "2"})

    assert walked == full
    assert sorted(walked) == sorted(created)
    # The tie-break is alert_id ascending within (level, timestamp).
    rows = client.get("/risk/alerts", params={"sort": sort, "limit": "200"}).json()["items"]
    key = (lambda r: (levels.index(r["risk_level"]), r["timestamp"])) if sort == "risk_level_desc" \
        else (lambda r: r["timestamp"])
    ties = [(a, b) for a, b in zip(rows, rows[1:]) if key(a) == key(b)]
    assert ties, "the fixture must put rows in a tie"
    assert all(a["alert_id"] < b["alert_id"] for a, b in ties)


def test_cursor_from_another_sort_is_rejected(client, insert_alert):
    for i in range(3):
        insert_alert(timestamp=_at(i))
    cursor = client.get("/risk/alerts", params={"limit": "1"}).json()["next_cursor"]

    resp = client.get(
        "/risk/alerts", params={"cursor": cursor, "sort": "risk_level_desc"}
    )
    assert resp.status_code == 400
    assert "does not match" in resp.json()["detail"]


@pytest.mark.parametrize("cursor", ["not-base64!", "e30="])
def test_malformed_cursor_is_rejected(client, configured_db, cursor):
    assert client.get("/risk/alerts", params={"cursor": cursor}).status_code == 400


@pytest.mark.parametrize(
    "key",
    [
        {"timestamp": _T0.isoformat(), "alert_id": f"urn:uuid:{uuid.UUID(int=1)}"},
        {"timestamp": "2026-W40-4T12:00:00+00:00", "alert_id": str(uuid.UUID(int=1))},
        {"timestamp": "2026-10-01T12:00:00,5+00:00", "alert_id": str(uuid.UUID(int=1))},
    ],
)
def test_cursor_in_a_form_only_python_reads_is_not_a_500(client, configured_db, key):
    """Accepted forms are passed on parsed, so Postgres never sees them raw."""
    cursor = http.encode_keyset_cursor(key, "timestamp_desc")
    assert client.get("/risk/alerts", params={"cursor": cursor}).status_code == 200


@pytest.mark.parametrize(
    "sort,key",
    [
        ("timestamp_desc", {"timestamp": _T0.isoformat(), "alert_id": "nope"}),
        ("timestamp_desc", {"timestamp": "yesterday", "alert_id": str(uuid.UUID(int=1))}),
        ("timestamp_desc", {"timestamp": "2026-10-01T12:00:00", "alert_id": str(uuid.UUID(int=1))}),
        ("timestamp_desc", {"timestamp": 5, "alert_id": str(uuid.UUID(int=1))}),
        ("risk_level_desc", {"timestamp": _T0.isoformat(), "alert_id": str(uuid.UUID(int=1)),
                             "risk_rank": "1"}),
    ],
)
def test_cursor_with_bad_values_is_a_400(client, configured_db, sort, key):
    """Right keys, wrong values: the standard 400, never a database error."""
    cursor = http.encode_keyset_cursor(key, sort)
    resp = client.get("/risk/alerts", params={"cursor": cursor, "sort": sort})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cursor is malformed"


@pytest.mark.parametrize("limit", ["0", "201", "x"])
def test_limit_out_of_range_is_rejected(client, configured_db, limit):
    assert client.get("/risk/alerts", params={"limit": limit}).status_code == 400


def test_limit_max_is_accepted(client, configured_db):
    assert client.get("/risk/alerts", params={"limit": "200"}).status_code == 200


def test_max_limit_is_the_alerts_setting(client, configured_db, monkeypatch):
    monkeypatch.setattr(config, "API_RISK_ALERTS_MAX_LIMIT", 3)
    assert client.get("/risk/alerts", params={"limit": "3"}).status_code == 200
    assert client.get("/risk/alerts", params={"limit": "4"}).status_code == 400
    assert client.get("/risk/traces", params={"limit": "4"}).status_code == 200


def test_default_limit_is_config_driven_and_alerts_only(
    client, insert_alert, insert_trace_risk, monkeypatch
):
    """AC-DAS-038: the alerts default moves the alerts page, not /risk/traces'."""
    for i in range(3):
        insert_alert(timestamp=_at(i))
        insert_trace_risk(trace_id=f"tr-{i}")
    monkeypatch.setattr(config, "API_RISK_ALERTS_DEFAULT_LIMIT", 2)

    alerts = client.get("/risk/alerts").json()
    traces = client.get("/risk/traces").json()

    assert len(alerts["items"]) == 2 and alerts["next_cursor"] is not None
    assert len(traces["items"]) == 3


# ---------------------------------------------------------------------------
# GET /risk/alerts/by-* (AC-DAS-025)
# ---------------------------------------------------------------------------

_BY = [
    ("by-status", "status", "acknowledged"),
    ("by-risk-level", "risk_level", "critical"),
    ("by-enforcement-type", "enforcement_type", "block"),
]


@pytest.fixture()
def mixed_alerts(insert_alert):
    head = insert_alert(timestamp=_at(20), risk_level="critical",
                        status="acknowledged", enforcement_type="block")
    rows = [head]
    for i, (level, status, enforcement) in enumerate([
        ("critical", "new", "block"),
        ("high", "acknowledged", "warn"),
        ("critical", "acknowledged", None),
        ("low", "resolved", "block"),
        ("high", "new", None),
    ]):
        rows.append(insert_alert(timestamp=_at(i), risk_level=level, status=status,
                                 enforcement_type=enforcement))
    # One superseded alert that matches every filter: hidden from both sides
    # by default, present on both with include_superseded=true.
    rows.append(insert_alert(timestamp=_at(10), risk_level="critical",
                             status="acknowledged", enforcement_type="block",
                             superseded_by=head))
    return rows


@pytest.mark.parametrize("segment,name,value", _BY)
@pytest.mark.parametrize("extra", [{}, {"include_superseded": "true"},
                                   {"sort": "risk_level_desc"}])
def test_by_endpoint_matches_the_single_filter_list(
    client, mixed_alerts, segment, name, value, extra
):
    by = client.get(f"/risk/alerts/{segment}/{value}", params=extra)
    single = client.get("/risk/alerts", params={name: value, **extra})

    assert by.status_code == single.status_code == 200
    assert by.json() == single.json()
    assert by.json()["items"], "the fixture must make the comparison non-trivial"


@pytest.mark.parametrize("segment,name,value", _BY)
def test_by_endpoint_pages_like_the_list(client, mixed_alerts, segment, name, value):
    assert _walk(client, f"/risk/alerts/{segment}/{value}", {"limit": "1"}) == _walk(
        client, "/risk/alerts", {name: value, "limit": "1"}
    )


@pytest.mark.parametrize("segment,name,value", _BY)
def test_by_endpoint_path_value_wins_over_query(client, mixed_alerts, segment, name, value):
    by = client.get(f"/risk/alerts/{segment}/{value}", params={name: "nonsense"})
    assert by.json() == client.get("/risk/alerts", params={name: value}).json()


@pytest.mark.parametrize("segment", ["by-status", "by-risk-level", "by-enforcement-type"])
def test_by_endpoint_rejects_an_unrecognized_value(client, configured_db, segment):
    resp = client.get(f"/risk/alerts/{segment}/bogus")
    assert resp.status_code == 400
    body = resp.json()
    assert set(body) == {"error", "detail", "timestamp"}
    assert "bogus" in body["detail"]


def test_status_vocabulary_is_the_prd_one():
    assert alert_routes.ALERT_STATUSES == (
        "new", "acknowledged", "investigating", "resolved", "false_positive",
    )


# ---------------------------------------------------------------------------
# GET /risk/alerts/{alert_id}
# ---------------------------------------------------------------------------


def test_get_one_alert(client, insert_alert):
    head = insert_alert(timestamp=_at(1), entity_ids=["e-1"])
    old = insert_alert(timestamp=_at(0), superseded_by=head)

    resp = client.get(f"/risk/alerts/{old}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["alert_id"] == old and body["superseded_by"] == head
    assert client.get(f"/risk/alerts/{head}").json()["involved_entities"] == [
        {"entity_id": "e-1", "entity_kind": "agent", "display_name": "e-1"}
    ]


@pytest.mark.parametrize("alert_id", [str(uuid.uuid4()), "not-a-uuid"])
def test_unknown_alert_is_404(client, configured_db, alert_id):
    resp = client.get(f"/risk/alerts/{alert_id}")
    assert resp.status_code == 404
    assert set(resp.json()) == {"error", "detail", "timestamp"}


# ---------------------------------------------------------------------------
# Rows written by the generator (#104)
# ---------------------------------------------------------------------------


def _seed_generated_trace(dsn: str, trace_id: str) -> tuple[str, str]:
    """A trace whose level rises high -> critical: two alerts, the first
    superseded by the second, both written by the real generator."""
    with psycopg.connect(dsn) as conn:
        for entity_id, name in (("e-agent", "agent-a"), ("e-ext", "partner.example")):
            conn.execute(
                "INSERT INTO entities (id, kind, natural_key, display_name, "
                "detected_from, original_seq) VALUES (%s, 'agent', %s, %s, 'test', 0)",
                (entity_id, entity_id, name),
            )
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) VALUES ('ix-1', %s, 'e-agent', 'e-ext', 's')",
            (trace_id,),
        )
        records = []
        for version, level in ((1, "high"), (2, "critical")):
            (record_id,) = conn.execute(
                "INSERT INTO trace_risk_records (trace_id, version, computed_at, "
                "trace_risk_level, interaction_count, policy_event_count, "
                "triggered_rule_ids) VALUES (%s, %s, now(), %s, 1, 1, '{DG-001}') "
                "RETURNING trace_risk_id",
                (trace_id, version, level),
            ).fetchone()
            records.append(str(record_id))
        conn.commit()
    alert_ids = []
    for record_id in records:
        with db.transaction() as tx:
            alert_ids.append(generate_for_trace_risk_record(tx, record_id).alert_id)
    return alert_ids[0], alert_ids[1]


def test_generated_alerts_are_served(client, configured_db, monkeypatch):
    monkeypatch.setattr(config, "ALERT_MIN_RISK_LEVEL", "low")
    first, head = _seed_generated_trace(configured_db, "tr-gen")

    current = client.get("/risk/alerts", params={"trace_id": "tr-gen"}).json()
    every = client.get(
        "/risk/alerts", params={"trace_id": "tr-gen", "include_superseded": "true"}
    ).json()

    assert _ids(current) == [head]
    assert current["items"][0]["status"] == "new"
    assert current["items"][0]["risk_level"] == "critical"
    assert set(_ids(every)) == {first, head}
    assert client.get(f"/risk/alerts/{first}").json()["superseded_by"] == head
    assert ids_of(client, entity_id="e-ext") == [head]
    assert ids_of(client, rule_id="DG-001", status="new") == [head]
