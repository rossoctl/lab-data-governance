"""`GET /risk/traces/{trace_id}` HTTP adapter tests (issue #109).

The forest read itself (ordering, span counts, risk joins) is already
covered end-to-end at the retrieval layer in
`tests/retrieval/test_trace_risk_detail.py`. This file only pins the HTTP
adapter's own responsibilities: response shape, 404 mapping, and
FR-DAS-084's forbidden-keys check nested through the forest's `interactions`
list.
"""

from __future__ import annotations

import datetime as dt
import uuid

import psycopg
import pytest


def _uuid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def seed(configured_db: str):
    class _Seed:
        def __init__(self, dsn: str):
            self._dsn = dsn

        def interaction(
            self,
            *,
            interaction_id: str,
            trace_id: str = "tr-detail-1",
            parent_interaction_id: str | None = None,
            caller_entity_id: str = "ent-caller",
            callee_entity_id: str = "ent-callee",
            summary: str = "did a thing",
        ) -> None:
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    "INSERT INTO interactions (id, trace_id, "
                    "parent_interaction_id, caller_entity_id, "
                    "callee_entity_id, summary) VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        interaction_id,
                        trace_id,
                        parent_interaction_id,
                        caller_entity_id,
                        callee_entity_id,
                        summary,
                    ),
                )
                conn.commit()

        def leg(
            self,
            *,
            interaction_id: str,
            leg_type: str,
            occurred_at: dt.datetime | None = None,
        ) -> None:
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    "INSERT INTO interaction_legs (interaction_id, leg_type, "
                    "occurred_at) VALUES (%s, %s, %s)",
                    (interaction_id, leg_type, occurred_at),
                )
                conn.commit()

        def trace_risk(
            self,
            *,
            trace_id: str = "tr-detail-1",
            version: int = 1,
            trace_risk_level: str = "low",
        ) -> None:
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    "INSERT INTO trace_risk_records (trace_risk_id, trace_id, "
                    "version, computed_at, trace_risk_level, interaction_count, "
                    "policy_event_count) VALUES (%s, %s, %s, now(), %s, %s, %s)",
                    (_uuid(), trace_id, version, trace_risk_level, 1, 1),
                )
                conn.commit()

    return _Seed(configured_db)


def test_trace_detail_returns_trace_risk_and_interactions(client, seed):
    seed.trace_risk(trace_id="tr-detail-1", trace_risk_level="high")
    seed.interaction(interaction_id="ix-1", trace_id="tr-detail-1")

    resp = client.get("/risk/traces/tr-detail-1")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"trace_risk", "interactions"}
    assert body["trace_risk"]["trace_risk_level"] == "high"
    assert [ix["interaction_id"] for ix in body["interactions"]] == ["ix-1"]


def test_trace_detail_interaction_shape_includes_legs_and_span_counts(client, seed):
    seed.trace_risk(trace_id="tr-detail-1")
    seed.interaction(interaction_id="ix-1", trace_id="tr-detail-1")
    seed.leg(interaction_id="ix-1", leg_type="request")

    body = client.get("/risk/traces/tr-detail-1").json()

    ix = body["interactions"][0]
    assert set(ix.keys()) == {
        "interaction_id",
        "trace_id",
        "parent_interaction_id",
        "caller_entity_id",
        "callee_entity_id",
        "summary",
        "legs",
        "risk",
        "span_count",
        "anchor_count",
    }
    assert ix["legs"][0]["leg_type"] == "request"
    assert ix["risk"] is None
    assert ix["span_count"] == 0
    assert ix["anchor_count"] == 0


def test_trace_detail_unknown_trace_id_is_404(client, configured_db):
    resp = client.get("/risk/traces/nope")

    assert resp.status_code == 404
    assert set(resp.json().keys()) == {"error", "detail", "timestamp"}


def test_trace_detail_interactions_exist_but_no_trace_risk_record_is_404(
    client, seed
):
    seed.interaction(interaction_id="ix-1", trace_id="tr-detail-1")

    resp = client.get("/risk/traces/tr-detail-1")

    assert resp.status_code == 404


_FORBIDDEN_KEYS = {"total", "is_complete", "complete", "completeness"}


def _assert_no_forbidden_keys(value) -> None:
    if isinstance(value, dict):
        assert not (set(value.keys()) & _FORBIDDEN_KEYS), value.keys()
        for v in value.values():
            _assert_no_forbidden_keys(v)
    elif isinstance(value, list):
        for item in value:
            _assert_no_forbidden_keys(item)


def test_trace_detail_has_no_forbidden_keys_nested_through_interactions(
    client, seed
):
    seed.trace_risk(trace_id="tr-detail-1")
    seed.interaction(interaction_id="ix-1", trace_id="tr-detail-1")

    body = client.get("/risk/traces/tr-detail-1").json()

    _assert_no_forbidden_keys(body)
