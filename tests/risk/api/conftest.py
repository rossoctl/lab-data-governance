"""Shared fixtures for `/risk/*` route tests (issues #109/#113).

`/risk/rules*` (issue #113) reads only the in-memory rule catalog, no
Postgres needed. `/risk/interactions*`/`/risk/traces*` (issue #109) read the
DAS risk tables and the P-interactions forest, so this module also carries
its **own** local copy of `configured_db` (the repo's documented convention:
each test subtree keeps its own copy rather than importing
`tests/api/conftest.py`'s) for tests that need a migrated Postgres.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

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
