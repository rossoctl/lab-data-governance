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


# --- list_rules(): flattening to the §6.5 serving shape ---------------------

_SIX_FIVE_KEYS = {
    "rule_id",
    "rule_name",
    "categories",
    "risk_level",
    "enforcement",
    "explanation",
    # Match criteria are structural fields (schema/policy.schema.json permits
    # no `conditions` array); a field's presence is the predicate.
    "event_type",
    "data_items",
    "data_destinations",
    "allowed_actions",
    "rule_sources",
}


def test_list_rules_returns_all_shipped_rules():
    rules = catalog.list_rules()
    assert [r["rule_id"] for r in rules] == ["DG-001", "DG-002", "DG-004"]


def test_list_rules_every_entry_has_six_five_shape():
    for rule in catalog.list_rules():
        assert set(rule.keys()) == _SIX_FIVE_KEYS


def test_list_rules_flattens_policy_decision_fields():
    dg001 = next(r for r in catalog.list_rules() if r["rule_id"] == "DG-001")
    assert dg001["risk_level"] == "critical"
    assert dg001["enforcement"] == "block"
    assert dg001["allowed_actions"] == ["redact_pii", "require_approval"]
    assert "PII" in dg001["explanation"]


def test_list_rules_flattens_rule_categories():
    dg001 = next(r for r in catalog.list_rules() if r["rule_id"] == "DG-001")
    assert dg001["categories"] == ["data_exfiltration", "pii_protection"]


def test_list_rules_preserves_file_order():
    rules = catalog.list_rules()
    assert [r["rule_id"] for r in rules] == sorted(r["rule_id"] for r in rules)


# --- get_rule(): single lookup -----------------------------------------------


def test_get_rule_hit_returns_flattened_rule():
    rule = catalog.get_rule("DG-002")
    assert rule is not None
    assert rule["rule_name"] == "phi_to_untrusted_external"
    assert rule["risk_level"] == "critical"


def test_get_rule_miss_returns_none():
    assert catalog.get_rule("DG-999") is None


def test_get_rule_lookup_is_case_sensitive():
    assert catalog.get_rule("dg-001") is None


# --- category_counts(): FR-DAS-061 -------------------------------------------


def test_category_counts_exact_mapping():
    assert catalog.category_counts() == {
        "access_control": 1,
        "data_exfiltration": 3,
        "hipaa": 1,
        "phi_protection": 1,
        "pii_protection": 1,
    }


def test_category_counts_sum_matches_total_category_memberships():
    rules = catalog.list_rules()
    total_memberships = sum(len(set(r["categories"])) for r in rules)
    assert sum(catalog.category_counts().values()) == total_memberships


def test_category_counts_sorted_by_key():
    keys = list(catalog.category_counts().keys())
    assert keys == sorted(keys)


# --- memoization + reload() --------------------------------------------------


def test_load_is_memoized_across_calls():
    first = catalog.load_rules_source()
    second = catalog.load_rules_source()
    assert first is second


def test_reload_clears_the_memo(monkeypatch: pytest.MonkeyPatch):
    first = catalog.load_rules_source()
    catalog.reload()
    second = catalog.load_rules_source()
    assert first is not second
    assert first == second  # same file on disk -> equal content, new object


def test_reload_picks_up_a_changed_file(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(catalog, "_RULES_SOURCE", _FIXTURES / "catalog_minimal.json")
    catalog.reload()
    minimal_rules = catalog.list_rules()
    assert [r["rule_id"] for r in minimal_rules] == ["FX-001", "FX-002"]


# --- corner cases -------------------------------------------------------------


def test_missing_policy_decision_yields_none_fields(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(catalog, "_RULES_SOURCE", _FIXTURES / "catalog_malformed.json")
    catalog.reload()
    no_decision = catalog.get_rule("FX-NO-DECISION")
    assert no_decision is not None
    assert no_decision["risk_level"] is None
    assert no_decision["enforcement"] is None
    assert no_decision["explanation"] is None
    assert no_decision["allowed_actions"] == []


def test_missing_rule_categories_yields_empty_list(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(catalog, "_RULES_SOURCE", _FIXTURES / "catalog_malformed.json")
    catalog.reload()
    no_categories = catalog.get_rule("FX-NO-CATEGORIES")
    assert no_categories is not None
    assert no_categories["categories"] == []


def test_empty_rules_list_yields_empty_catalog(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(catalog, "_RULES_SOURCE", _FIXTURES / "catalog_empty.json")
    catalog.reload()
    assert catalog.list_rules() == []
    assert catalog.category_counts() == {}
    assert catalog.get_rule("DG-001") is None


def test_duplicate_category_within_one_rule_counts_once(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(catalog, "_RULES_SOURCE", _FIXTURES / "catalog_malformed.json")
    catalog.reload()
    counts = catalog.category_counts()
    # FX-DUP-CATEGORY lists "data_exfiltration" twice in rule_categories.
    assert counts["data_exfiltration"] == 1


def test_mutating_a_returned_rule_does_not_affect_next_call():
    rule = catalog.get_rule("DG-001")
    rule["categories"].append("mutated")
    rule["risk_level"] = "mutated"

    fresh = catalog.get_rule("DG-001")
    assert fresh["categories"] == ["data_exfiltration", "pii_protection"]
    assert fresh["risk_level"] == "critical"


# --- content assertions on the real shipped file -----------------------------


def test_shipped_file_has_exactly_the_expected_rule_ids():
    assert {r["rule_id"] for r in catalog.list_rules()} == {
        "DG-001",
        "DG-002",
        "DG-004",
    }


def test_dg004_gates_on_restricted_classification_not_a_regulated_source():
    """DG-004 deliberately drops the demo rule's regulated-source predicate:
    a RESTRICTED classification level is sufficient on its own.

    The source-side check is now guarded structurally — the rule carries no
    ``data_sources`` at all — rather than by the absence of a named condition
    type, since the schema has no ``conditions`` array to inspect.
    """
    dg004 = catalog.get_rule("DG-004")
    assert dg004 is not None

    levels = [item.get("classification_level") for item in dg004["data_items"]]
    assert levels == ["RESTRICTED"]

    # No regulated-source gate: no data_sources on the rule, and no
    # regulatory_tags predicate standing in for one.
    raw_dg004 = next(
        r for r in catalog.load_rules_source()["rules"] if r["rule_id"] == "DG-004"
    )
    assert "data_sources" not in raw_dg004
    assert all("regulatory_tags" not in item for item in dg004["data_items"])
