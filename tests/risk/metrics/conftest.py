"""Fixtures for DAS dashboard metrics aggregation tests (issue #106).

Own local copy of `configured_db`, per this repo's documented
per-test-subtree convention (see `tests/risk/conftest.py`).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from data_governance import db


@pytest.fixture()
def configured_db(migrated_dsn: str) -> Iterator[str]:
    db.close_pool()
    db.configure(migrated_dsn)
    try:
        yield migrated_dsn
    finally:
        db.close_pool()


def _uuid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def insert_entity(configured_db: str):
    def _insert(
        *,
        entity_id: str,
        kind: str = "agent",
        display_name: str | None = None,
        original_seq: int = 1,
    ) -> None:
        with psycopg.connect(configured_db) as conn:
            conn.execute(
                "INSERT INTO entities ("
                "id, kind, natural_key, display_name, detected_from, original_seq"
                ") VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (id) DO NOTHING",
                (
                    entity_id,
                    kind,
                    entity_id,
                    display_name or entity_id,
                    "test-fixture",
                    original_seq,
                ),
            )
            conn.commit()

    return _insert


@pytest.fixture()
def insert_interaction_risk(configured_db: str):
    def _insert(
        *,
        interaction_id: str,
        trace_id: str = "tr-1",
        version: int = 1,
        risk_level: str = "low",
        enforcement_type: str | None = None,
        caller_entity_id: str = "ent-caller",
        callee_entity_id: str = "ent-callee",
        triggered_rule_ids: list[str] | None = None,
        computed_at: dt.datetime | None = None,
    ) -> str:
        interaction_risk_id = _uuid()
        with psycopg.connect(configured_db) as conn:
            conn.execute(
                "INSERT INTO interaction_risk_records ("
                "interaction_risk_id, interaction_id, trace_id, "
                "caller_entity_id, callee_entity_id, version, computed_at, "
                "risk_level, enforcement_type, policy_event_count, "
                "triggered_rule_ids"
                ") VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now()), %s, "
                "%s, %s, %s)",
                (
                    interaction_risk_id,
                    interaction_id,
                    trace_id,
                    caller_entity_id,
                    callee_entity_id,
                    version,
                    computed_at,
                    risk_level,
                    enforcement_type,
                    1,
                    triggered_rule_ids or [],
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
        trace_enforcement_type: str | None = None,
        interaction_count: int = 1,
        policy_event_count: int = 1,
        all_entity_ids: list[str] | None = None,
        triggered_rule_ids: list[str] | None = None,
        computed_at: dt.datetime | None = None,
    ) -> str:
        trace_risk_id = _uuid()
        with psycopg.connect(configured_db) as conn:
            conn.execute(
                "INSERT INTO trace_risk_records ("
                "trace_risk_id, trace_id, version, computed_at, "
                "trace_risk_level, trace_enforcement_type, interaction_count, "
                "policy_event_count, all_entity_ids, triggered_rule_ids"
                ") VALUES (%s, %s, %s, COALESCE(%s, now()), %s, %s, %s, %s, %s, %s)",
                (
                    trace_risk_id,
                    trace_id,
                    version,
                    computed_at,
                    trace_risk_level,
                    trace_enforcement_type,
                    interaction_count,
                    policy_event_count,
                    all_entity_ids or [],
                    triggered_rule_ids or [],
                ),
            )
            conn.commit()
        return trace_risk_id

    return _insert
