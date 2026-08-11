"""Shared fixtures for `/risk/rules*` route tests (issue #113).

No Postgres anywhere here — these routes read only the in-memory rule
catalog, so this deliberately does NOT reuse `tests/api/conftest.py`'s
`api_server`/`configured_db` fixtures (those spin a Postgres testcontainer).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

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
