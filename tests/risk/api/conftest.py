"""Shared fixtures for `/risk/*` route tests (issues #109/#113).

`/risk/rules*` (issue #113) reads only the in-memory rule catalog, no
Postgres needed. `/risk/interactions*`/`/risk/traces*` (issue #109) read the
DAS risk tables and the P-interactions forest, so this module also carries
its **own** local copy of `configured_db` (the repo's documented convention:
each test subtree keeps its own copy rather than importing
`tests/api/conftest.py`'s) for tests that need a migrated Postgres.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from starlette.testclient import TestClient

from data_governance import db
from data_governance.api import build_app
from data_governance.risk.rules import catalog

_FIXTURES = Path(__file__).parent / "fixtures"
_RULES_FIXTURES = Path(__file__).parent.parent / "rules" / "fixtures"


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    catalog.reload()
    yield
    catalog.reload()


@pytest.fixture
def client():
    return TestClient(build_app())


def _use_fixture(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(catalog, "_RULES_SOURCE", path)
    catalog.reload()


@pytest.fixture
def varied_catalog(monkeypatch: pytest.MonkeyPatch):
    """5 rules with distinct risk/enforcement values (shared with #107's tests)."""
    _use_fixture(monkeypatch, _RULES_FIXTURES / "catalog_varied.json")


@pytest.fixture
def paging_catalog(monkeypatch: pytest.MonkeyPatch):
    """7 rules incl. a severity tie, for gapless pagination walks."""
    _use_fixture(monkeypatch, _FIXTURES / "catalog_paging.json")


@pytest.fixture
def categories_rule_catalog(monkeypatch: pytest.MonkeyPatch):
    """A rule whose id is literally 'categories', to pin route-order shadowing."""
    _use_fixture(monkeypatch, _FIXTURES / "catalog_categories_rule.json")


@pytest.fixture
def empty_catalog(monkeypatch: pytest.MonkeyPatch):
    """Zero rules, for empty-catalog edge cases (shared with #107's tests)."""
    _use_fixture(monkeypatch, _RULES_FIXTURES / "catalog_empty.json")


@pytest.fixture()
def configured_db(migrated_dsn: str) -> Iterator[str]:
    """Local copy of `tests/api/conftest.py`'s fixture of the same name — the
    repo's documented per-subtree convention, not an oversight."""
    db.close_pool()
    db.configure(migrated_dsn)
    try:
        yield migrated_dsn
    finally:
        db.close_pool()


# FR-DAS-084: no `total`/`is_complete`/`complete`/`completeness` field
# anywhere in a `/risk/*` response, at any nesting depth. Shared by
# test_risk_routes.py and test_risk_trace_detail.py rather than duplicated —
# the trace-detail file needs the recursion to reach through its
# `interactions` list.
FORBIDDEN_KEYS = {"total", "is_complete", "complete", "completeness"}


def assert_no_forbidden_keys(value) -> None:
    if isinstance(value, dict):
        assert not (set(value.keys()) & FORBIDDEN_KEYS), value.keys()
        for v in value.values():
            assert_no_forbidden_keys(v)
    elif isinstance(value, list):
        for item in value:
            assert_no_forbidden_keys(item)


# ---------------------------------------------------------------------------
# Seeding fixtures for /risk/metrics/* tests (issue #111) — local copies of
# tests/risk/metrics/conftest.py's fixtures of the same name/signature, per
# this repo's documented per-subtree convention (module docstring above).
# ---------------------------------------------------------------------------


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
