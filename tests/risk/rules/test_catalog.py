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


# --- list_rules(): optional filtering ---------------------------------------


@pytest.fixture
def _varied(monkeypatch):
    """Point the catalog at a fixture with varied risk/enforcement values —
    all three shipped DG-* rules are critical/block, so they cannot
    discriminate between filter or sort orders."""
    monkeypatch.setattr(catalog, "_RULES_SOURCE", _FIXTURES / "catalog_varied.json")
    catalog.reload()
    yield
    catalog.reload()


def _ids(rules):
    return [r["rule_id"] for r in rules]


def test_list_rules_unfiltered_returns_file_order(_varied):
    assert _ids(catalog.list_rules()) == [
        "FX-MED-ESC",
        "FX-CRIT-BLOCK",
        "FX-LOW-ALLOW",
        "FX-HIGH-BLOCK",
        "FX-NO-DECISION",
    ]


def test_filter_by_single_risk_level(_varied):
    assert _ids(catalog.list_rules(risk_level="critical")) == ["FX-CRIT-BLOCK"]


def test_filter_by_multiple_risk_levels(_varied):
    """A collection matches any of the given levels, preserving file order."""
    result = catalog.list_rules(risk_level=["critical", "low"])
    assert _ids(result) == ["FX-CRIT-BLOCK", "FX-LOW-ALLOW"]


def test_filter_by_single_enforcement(_varied):
    assert _ids(catalog.list_rules(enforcement="block")) == [
        "FX-CRIT-BLOCK",
        "FX-HIGH-BLOCK",
    ]


def test_filter_by_multiple_enforcements(_varied):
    result = catalog.list_rules(enforcement=["allow", "escalate"])
    assert _ids(result) == ["FX-MED-ESC", "FX-LOW-ALLOW"]


def test_filters_combine_conjunctively(_varied):
    """Both criteria must hold — high/block matches, high/allow does not."""
    assert _ids(catalog.list_rules(risk_level="high", enforcement="block")) == [
        "FX-HIGH-BLOCK"
    ]
    assert catalog.list_rules(risk_level="high", enforcement="allow") == []


def test_filter_by_category(_varied):
    assert _ids(catalog.list_rules(category="category_b")) == [
        "FX-CRIT-BLOCK",
        "FX-LOW-ALLOW",
    ]


def test_filter_no_match_returns_empty_list(_varied):
    assert catalog.list_rules(risk_level="nonexistent") == []


def test_filter_is_case_sensitive(_varied):
    """Catalog values are opaque strings from OPA, not normalized user input."""
    assert catalog.list_rules(risk_level="CRITICAL") == []


def test_filter_empty_collection_matches_nothing(_varied):
    """An empty collection is an explicit "no accepted values", distinct from
    None meaning "no filter" — otherwise a caller passing a computed-empty
    list would silently get everything."""
    assert catalog.list_rules(risk_level=[]) == []
    assert _ids(catalog.list_rules(risk_level=None)) == _ids(catalog.list_rules())


def test_filter_excludes_rules_with_no_policy_decision(_varied):
    """A rule with no policy_decision has risk_level None, so it cannot match
    any concrete filter value."""
    assert "FX-NO-DECISION" not in _ids(catalog.list_rules(risk_level="critical"))
    assert "FX-NO-DECISION" in _ids(catalog.list_rules())


def test_filter_on_shipped_catalog(_reset_cache):
    """The real file: all three rules are critical/block."""
    assert len(catalog.list_rules(risk_level="critical")) == 3
    assert len(catalog.list_rules(enforcement="block")) == 3
    assert catalog.list_rules(risk_level="low") == []


# --- list_rules(): optional sorting -----------------------------------------


def test_sort_by_risk_level_is_severity_ordered_not_alphabetical(_varied):
    """critical > high > medium > low — alphabetical order would put critical
    after... nothing, but 'high' before 'low' before 'medium', which is
    meaningless for a severity axis."""
    result = _ids(catalog.list_rules(sort_by="risk_level"))
    assert result[:4] == [
        "FX-CRIT-BLOCK",
        "FX-HIGH-BLOCK",
        "FX-MED-ESC",
        "FX-LOW-ALLOW",
    ]


def test_sort_by_risk_level_descending_reverses_severity(_varied):
    result = _ids(catalog.list_rules(sort_by="risk_level", descending=True))
    assert result[-4:] == [
        "FX-LOW-ALLOW",
        "FX-MED-ESC",
        "FX-HIGH-BLOCK",
        "FX-CRIT-BLOCK",
    ]


def test_sort_by_risk_level_places_unranked_last(_varied):
    """A rule with no policy_decision sorts after every ranked rule rather
    than crashing on a None comparison or sorting first."""
    assert _ids(catalog.list_rules(sort_by="risk_level"))[-1] == "FX-NO-DECISION"


def test_sort_by_enforcement_is_alphabetical(_varied):
    """enforcement_type has no documented severity order, so alphabetical is
    the honest choice rather than an invented ranking."""
    result = _ids(catalog.list_rules(sort_by="enforcement"))
    assert result[:4] == [
        "FX-LOW-ALLOW",
        "FX-CRIT-BLOCK",
        "FX-HIGH-BLOCK",
        "FX-MED-ESC",
    ]


def test_sort_by_enforcement_is_stable_within_a_tie(_varied):
    """The two block rules keep their relative file order."""
    result = _ids(catalog.list_rules(sort_by="enforcement"))
    assert result.index("FX-CRIT-BLOCK") < result.index("FX-HIGH-BLOCK")


def test_sort_by_rule_id_and_rule_name(_varied):
    assert _ids(catalog.list_rules(sort_by="rule_id"))[0] == "FX-CRIT-BLOCK"
    assert _ids(catalog.list_rules(sort_by="rule_name"))[0] == "FX-CRIT-BLOCK"


def test_sort_rejects_an_unknown_key(_varied):
    with pytest.raises(ValueError, match="sort_by"):
        catalog.list_rules(sort_by="confidence")


def test_sort_and_filter_compose(_varied):
    result = _ids(catalog.list_rules(enforcement="block", sort_by="risk_level"))
    assert result == ["FX-CRIT-BLOCK", "FX-HIGH-BLOCK"]


def test_sorting_does_not_mutate_the_memoized_source(_varied):
    catalog.list_rules(sort_by="risk_level")
    assert _ids(catalog.list_rules()) == [
        "FX-MED-ESC",
        "FX-CRIT-BLOCK",
        "FX-LOW-ALLOW",
        "FX-HIGH-BLOCK",
        "FX-NO-DECISION",
    ]


def test_risk_order_covers_every_documented_level():
    """Guards against a vocabulary value silently sorting as unranked."""
    assert set(catalog.RISK_LEVEL_ORDER) == {
        "critical",
        "high",
        "medium",
        "low",
        "none",
        "unknown",
    }


def test_descending_sort_puts_unranked_first(_varied):
    """``descending`` reverses the whole ordering, so the rule with no
    ``policy_decision`` leads rather than staying pinned last. Documented
    because "unranked last" and "reverse everything" pull in opposite
    directions and a caller paging descending needs to know which wins.
    """
    for field in ("risk_level", "enforcement"):
        result = _ids(catalog.list_rules(sort_by=field, descending=True))
        assert result[0] == "FX-NO-DECISION", field


def test_ties_keep_file_order_even_when_descending(_varied):
    """Stability applies to the tie, not the reversal: the two block rules
    stay in file order rather than flipping."""
    result = _ids(catalog.list_rules(sort_by="enforcement", descending=True))
    assert result.index("FX-CRIT-BLOCK") < result.index("FX-HIGH-BLOCK")


def test_filter_by_event_type(_varied):
    assert _ids(catalog.list_rules(event_type="data_export")) == ["FX-HIGH-BLOCK"]


def test_all_sort_keys_are_accepted(_varied):
    """Every advertised key actually sorts, so SORT_KEYS cannot drift from
    what _sort_key handles."""
    for field in catalog.SORT_KEYS:
        assert len(catalog.list_rules(sort_by=field)) == 5, field


def test_get_rule_and_category_counts_unaffected_by_new_parameters(_varied):
    """The other public helpers call list_rules() with no arguments, so the
    unfiltered default must stay their behaviour."""
    assert catalog.get_rule("FX-LOW-ALLOW")["risk_level"] == "low"
    assert catalog.category_counts() == {"category_a": 3, "category_b": 2}
