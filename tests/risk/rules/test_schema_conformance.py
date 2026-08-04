"""The shipped rule catalog is a valid instance of the canonical policy schema.

``rules_source.json`` is meant to be exactly what the policy component emits, so
"it conforms to ``policy.schema.json``" needs to be machine-checked rather than
asserted by whoever last edited the file. The schema is vendored alongside the
catalog (``data_governance/risk/rules/schema/``) so this check has no network or
OneDrive dependency.

The demo-era file this replaced was invalid in three specific ways, each of which
gets its own regression guard below: it carried a ``conditions`` array (the
schema's ``$defs/rule`` sets ``additionalProperties: false`` and defines no such
property), spelled the rule-source field ``article_clause`` instead of
``article/clause``, and modelled ``event_type`` as a list-valued condition rather
than a singular string.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from jsonschema import Draft202012Validator

from data_governance.risk.rules import catalog

_SCHEMA_DIR = Path(catalog.__file__).parent / "schema"
_POLICY_SCHEMA = _SCHEMA_DIR / "policy.schema.json"


def _load_schema() -> dict[str, Any]:
    with _POLICY_SCHEMA.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def _fresh_catalog():
    catalog.reload()
    yield
    catalog.reload()


# --- the schema itself -------------------------------------------------------


def test_policy_schema_is_vendored_alongside_the_catalog():
    assert _POLICY_SCHEMA.is_file()


def test_policy_schema_is_itself_a_valid_2020_12_schema():
    Draft202012Validator.check_schema(_load_schema())


def test_recommended_enum_values_doc_is_vendored():
    assert (_SCHEMA_DIR / "recommended_enum_values.md").is_file()


# --- the shipped catalog validates ------------------------------------------


def test_shipped_rules_source_conforms_to_policy_schema():
    jsonschema.validate(instance=catalog.load_rules_source(), schema=_load_schema())


def test_shipped_rules_source_has_no_validation_errors_at_all():
    """``validate`` raises on the first error; collect every one so a failure
    report names all problems rather than just the earliest."""
    validator = Draft202012Validator(_load_schema())
    errors = sorted(
        validator.iter_errors(catalog.load_rules_source()),
        key=lambda e: list(e.absolute_path),
    )
    assert errors == [], [
        f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errors
    ]


# --- negative control --------------------------------------------------------
#
# Without this, a validator that silently accepted anything would make every
# test above pass. Proves additionalProperties:false is actually enforced.


def test_validator_rejects_a_rule_with_a_stray_conditions_key():
    bad = json.loads(json.dumps(catalog.load_rules_source()))
    bad["rules"][0]["conditions"] = [{"type": "event_type_in", "values": ["x"]}]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_load_schema())


def test_validator_rejects_the_old_article_clause_spelling():
    bad = json.loads(json.dumps(catalog.load_rules_source()))
    source = bad["rules"][0]["rule_sources"][0]
    source["article_clause"] = source.pop("article/clause")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_load_schema())


def test_validator_rejects_a_list_valued_event_type():
    bad = json.loads(json.dumps(catalog.load_rules_source()))
    bad["rules"][0]["event_type"] = ["external_sharing"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_load_schema())


def test_validator_rejects_an_out_of_enum_destination_category():
    """``data_destination_categories`` is one of the few genuinely closed
    string enums in the schema — the field this catalog uses to express
    untrusted-external, so its enforcement matters."""
    bad = json.loads(json.dumps(catalog.load_rules_source()))
    bad["rules"][0]["data_destinations"][0]["data_destination_categories"] = [
        "UNTRUSTED_EXTERNAL"
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_load_schema())


def test_unknown_behavior_enforcement_type_is_allow():
    """Behaviour not matched by any DG-* rule is permitted rather than blocked.

    Pinned deliberately: nothing in :mod:`catalog` reads
    ``runtime_enforcement_mode``, so without this assertion the fail-open
    default could be flipped by an unrelated edit and no test would notice.
    """
    mode = catalog.load_rules_source()["runtime_enforcement_mode"]
    assert mode["unknown_behavior_enforcement_type"] == "allow"


# --- the test fixtures are schema-valid too ---------------------------------


@pytest.mark.parametrize(
    "fixture_name",
    ["catalog_minimal.json", "catalog_malformed.json", "catalog_empty.json"],
)
def test_fixtures_are_schema_valid(fixture_name: str):
    """The fixtures exercise loader edge cases, not schema violations — a
    "malformed" fixture is malformed for :mod:`catalog`'s purposes (missing
    ``policy_decision``, absent ``rule_categories``, a duplicated category),
    all of which the schema permits since only ``rule_name`` is required.
    Keeping them valid stops a fixture from teaching a shape the schema forbids.
    """
    with (Path(__file__).parent / "fixtures" / fixture_name).open(
        encoding="utf-8"
    ) as f:
        jsonschema.validate(instance=json.load(f), schema=_load_schema())


# --- the three specific violations that motivated the reshape ---------------


def test_no_rule_carries_a_conditions_key():
    for raw_rule in catalog.load_rules_source()["rules"]:
        assert "conditions" not in raw_rule


def test_every_rule_source_uses_the_slashed_article_clause_key():
    for raw_rule in catalog.load_rules_source()["rules"]:
        for source in raw_rule.get("rule_sources", []):
            assert "article_clause" not in source
            assert "article/clause" in source


def test_event_type_is_a_singular_string_on_every_rule():
    for raw_rule in catalog.load_rules_source()["rules"]:
        assert isinstance(raw_rule["event_type"], str)


# --- the structural predicates survived the translation ---------------------


def test_untrusted_external_is_expressed_as_an_external_destination_category():
    """The schema types ``data_destination_trust_level`` as a number while the
    enum doc defines trust as a string enum with no numeric scale, so the
    catalog deliberately uses the closed category enum instead and leaves the
    numeric field unset."""
    for raw_rule in catalog.load_rules_source()["rules"]:
        destinations = raw_rule["data_destinations"]
        assert destinations, raw_rule["rule_id"]
        for destination in destinations:
            assert destination["data_destination_categories"] == ["external"]
            assert "data_destination_trust_level" not in destination


def test_regulatory_tag_predicates_survived_translation():
    tags = {
        rule_id: [
            tag
            for item in catalog.get_rule(rule_id)["data_items"]
            for tag in item.get("regulatory_tags", [])
        ]
        for rule_id in ("DG-001", "DG-002")
    }
    assert tags == {"DG-001": ["PII"], "DG-002": ["PHI"]}


def test_every_rule_still_targets_external_sharing():
    for rule in catalog.list_rules():
        assert rule["event_type"] == "external_sharing"
