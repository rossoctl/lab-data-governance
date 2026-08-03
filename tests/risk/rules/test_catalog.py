"""Tests for ``data_governance.risk.rules.catalog`` (issue #107, PRD §6.5/§8.4).

Covers the read-only rule-catalog serving layer over the baked-in
``rules_source.json``: the memoized raw loader, the §6.5 flattened
``list_rules``/``get_rule`` views, FR-DAS-061 category counts, and the
manual ``reload()`` path. No Postgres involved — this module reads only a
bundled JSON file, so none of these tests take a DB fixture.

Fixture-backed tests point the module's private ``_RULES_SOURCE`` path
constant at a committed file under ``tests/risk/rules/fixtures/`` via
``monkeypatch.setattr`` — the same substitution idiom ``tests/api/conftest.py``
uses for ``_UI_DIR`` (see PRD implementation-notes-v3 §6.1) — rather than a
new env var or a caller-supplied path parameter (§8.4: read-only, no
caller-supplied path).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data_governance.risk.rules import catalog

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _reset_cache():
    """Every test starts and ends with a clean memo, regardless of outcome."""
    catalog.reload()
    yield
    catalog.reload()


# --- loading the real shipped file ------------------------------------------


def test_load_rules_source_reads_shipped_file():
    source = catalog.load_rules_source()
    assert source["policy_id"] == "data-governance-v1"
    assert isinstance(source["rules"], list)
    assert len(source["rules"]) > 0


def test_bundle_version_matches_shipped_file():
    assert catalog.bundle_version() == "1.0.0"
