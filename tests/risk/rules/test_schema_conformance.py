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


def test_schema_requires_all_enum_definition_blocks():
    """The schema's ``required`` list grew from 5 to 24 fields when the enum
    vocabularies moved from ``recommended_enum_values.md`` (now deleted) into
    the schema itself. Pinned so a partial re-vendor — e.g. copying the
    ``$defs`` but not the top-level ``required`` list — fails loudly here
    instead of surfacing as 18 confusing "is a required property" errors on
    the shipped catalog."""
    required = set(_load_schema()["required"])
    assert required == {
        "policy_id", "policy_type", "status", "version", "runtime_enforcement_mode",
        "rules", "event_type", "enforcement_type", "action_type", "location_type",
        "processing_entity_type", "entity_type", "regulatory_tags", "data_type",
        "classification_level", "domain", "category", "risk_level", "trust_level",
        "transformation type", "data_source_type", "data_destination_type",
        "data_source_category", "data_destination_category",
    }


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
    """``data_destination_categories`` is a closed enum of coarse categories
    (local/internal/external/...). A trust-level name is not a category, so
    putting one here must fail — the two axes are separate fields."""
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
    [
        "catalog_minimal.json",
        "catalog_malformed.json",
        "catalog_empty.json",
        "catalog_varied.json",
    ],
)
def test_fixtures_are_schema_valid(fixture_name: str):
    """The fixtures exercise loader edge cases, not schema violations. A
    "malformed" fixture is malformed for :mod:`catalog`'s purposes — a
    ``rule_decision`` missing its ranked fields, an empty (not absent)
    ``rule_categories``, a duplicated category — all of which the schema
    permits: ``ruleDecision.required`` is only ``["explanation",
    "confidence"]``, and an empty list satisfies ``rule_categories``' type.
    Keeping every fixture valid stops a fixture from teaching a shape the
    schema forbids.
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


def test_untrusted_external_destinations_carry_the_named_trust_level():
    """Trust is a category, not a magnitude: ``data_destination_trust_level``
    is the named string ``UNTRUSTED_EXTERNAL``, alongside the coarser
    ``external`` category."""
    for raw_rule in catalog.load_rules_source()["rules"]:
        destinations = raw_rule["data_destinations"]
        assert destinations, raw_rule["rule_id"]
        for destination in destinations:
            assert destination["data_destination_categories"] == ["external"]
            assert destination["data_destination_trust_level"] == "UNTRUSTED_EXTERNAL"


def test_trust_levels_are_strings_not_numbers():
    """Guards the schema fix: a numeric trust level must be rejected, since
    no numeric scale is defined for trust anywhere in the vocabulary."""
    bad = json.loads(json.dumps(catalog.load_rules_source()))
    bad["rules"][0]["data_destinations"][0]["data_destination_trust_level"] = 0
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_load_schema())


def test_trust_level_enum_is_closed_to_the_documented_names():
    bad = json.loads(json.dumps(catalog.load_rules_source()))
    bad["rules"][0]["data_destinations"][0]["data_destination_trust_level"] = (
        "SOMEWHAT_TRUSTED"
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_load_schema())


def test_schema_defines_trust_level_as_a_string_enum():
    """Both trust-level fields in the schema — destination and agent — resolve
    to the shared string enum rather than a number.

    This is a deliberate divergence from the vendored source: the upstream
    schema defines ``$defs/trustLevelValues`` as a closed string enum of the
    8 trust names, but (inconsistently) still typed
    ``data_destination_trust_level``/``agent_trust_level`` as
    ``{"type": "number"}`` and its own reference instance wrote ``0`` for
    both. Both fields are repointed at the schema's own ``trustLevelValues``
    here rather than left as the inconsistency, per the standing instruction
    that trust levels are enum strings, not numbers.
    """
    schema = _load_schema()
    trust_level = schema["$defs"]["trustLevelValues"]
    assert trust_level["type"] == "string"
    assert "UNTRUSTED_EXTERNAL" in trust_level["enum"]

    for definition, field in (
        ("dataDestination", "data_destination_trust_level"),
        ("processingAgent", "agent_trust_level"),
    ):
        prop = schema["$defs"][definition]["properties"][field]
        assert prop == {"$ref": "#/$defs/trustLevelValues"}, (definition, field)


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


# --- catalog.py's severity orderings stay in sync with the schema's own ----
# --- vocabularies, rather than silently drifting from a hardcoded list -----


def test_risk_order_matches_the_schema_vocabulary():
    """:data:`catalog.RISK_LEVEL_ORDER` must contain exactly the schema's
    ``riskLevelValues`` names, minus the empty-string placeholder. Checking
    against the vendored schema (rather than a second hardcoded list here)
    means a future re-vendor that renames or adds a risk level fails this
    test instead of silently sorting the new value last forever.
    """
    schema_values = set(_load_schema()["$defs"]["riskLevelValues"]["enum"]) - {""}
    assert set(catalog.RISK_LEVEL_ORDER) == schema_values


def test_rule_categories_are_drawn_from_the_closed_vocabulary():
    """Every category on every shipped rule is a member of the schema's
    ``ruleCategoriesValues`` enum. A hand-added category that isn't in the
    vocabulary would otherwise only surface as one of many errors in
    ``test_shipped_rules_source_has_no_validation_errors_at_all`` — this
    isolates the failure to the actual offending category.
    """
    allowed = set(_load_schema()["$defs"]["ruleCategoriesValues"]["enum"])
    for raw_rule in catalog.load_rules_source()["rules"]:
        for category in raw_rule.get("rule_categories", []):
            assert category in allowed, (raw_rule["rule_id"], category)
