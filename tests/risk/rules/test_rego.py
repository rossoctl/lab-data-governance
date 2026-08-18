"""Tests for ``data_governance.risk.rules.rego`` (issue #173).

Structural tests: package declaration, one ``triggered_rules`` block per
rule, the predicate clause emitted per match field, the fallback rule's
values, the combining block per mode, and the ``NotImplementedError`` guard
on ``data_lineage``/``scope``. These check the *shape* of the emitted Rego
source (string containment on the compiled output) — whether that Rego
actually evaluates correctly in OPA is ``test_rego_opa.py``'s job
(``@pytest.mark.opa``, a real OPA container).

Test policies here are minimal inline dicts built with :func:`_policy`,
compiled with ``validate=False`` — these tests exercise clause generation
for one field at a time, not full schema conformance (that's
``test_schema_conformance.py`` and the golden-file test in
``test_rego_golden.py``), so they skip the full top-level enum-vocabulary
envelope the schema's ``required`` list otherwise demands.
"""

from __future__ import annotations

from typing import Any

import pytest

from data_governance.risk.rules.rego import compile_policy

_DECISION = {
    "risk_level": "high",
    "enforcement_type": "block",
    "allowed_actions": [],
    "explanation": "test",
    "confidence": 0.9,
}


def _policy(rules: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """A minimal policy envelope: just the fields ``compile_policy`` itself
    reads (``runtime_enforcement_mode``, ``rules``), plus whatever the
    caller overrides (e.g. a top-level ``policy_decision`` block)."""
    policy: dict[str, Any] = {
        "runtime_enforcement_mode": {"unknown_behavior_enforcement_type": "allow"},
        "rules": rules,
    }
    policy.update(kwargs)
    return policy


def _rule(rule_id: str, /, **fields: Any) -> dict[str, Any]:
    rule = {"rule_id": rule_id, "rule_decision": dict(_DECISION)}
    rule.update(fields)
    return rule


def _compile(policy: dict[str, Any], **kwargs: Any) -> str:
    return compile_policy(policy, validate=False, **kwargs)


# --- package + overall shape -------------------------------------------------


def test_emits_the_data_governance_package_declaration():
    rego = _compile(_policy([_rule("R-1")]))
    assert rego.startswith("package data_governance\n")


def test_one_triggered_rules_block_per_rule():
    rego = _compile(_policy([_rule("R-1"), _rule("R-2"), _rule("R-3")]))
    assert rego.count("triggered_rules contains") == 3
    for rule_id in ("R-1", "R-2", "R-3"):
        assert f'triggered_rules contains "{rule_id}"' in rego


def test_a_rule_with_no_match_fields_always_fires():
    """Every match field on the schema's ``$defs/rule`` is optional; a rule
    that specifies none of them structurally matches every input."""
    rego = _compile(_policy([_rule("R-1")]))
    assert 'triggered_rules contains "R-1" if {\n    true\n}' in rego


# --- predicate clauses, one field at a time ----------------------------------


def test_event_type_emits_an_equality_clause():
    rego = _compile(_policy([_rule("R-1", event_type="external_sharing")]))
    assert 'input.event_type == "external_sharing"' in rego


def test_data_count_emits_a_threshold_clause():
    rego = _compile(_policy([_rule("R-1", data_count=5)]))
    assert "input.data_count >= 5" in rego


def test_requested_actions_emits_a_subset_clause():
    rego = _compile(_policy([_rule("R-1", requested_actions=["send", "write"])]))
    assert "input.requested_actions" in rego
    assert '["send", "write"]' in rego
    assert "every v in" in rego


def test_accessing_user_username_emits_an_equality_clause():
    rego = _compile(
        _policy([_rule("R-1", accessing_user={"username": "alice"})])
    )
    assert 'input.accessing_user.username == "alice"' in rego


def test_accessing_user_roles_emits_a_subset_clause():
    rego = _compile(
        _policy([_rule("R-1", accessing_user={"user_roles": ["admin"]})])
    )
    assert "input.accessing_user.user_roles" in rego
    assert '["admin"]' in rego


def test_data_items_classification_level_emits_an_equality_clause():
    rego = _compile(
        _policy([_rule("R-1", data_items=[{"classification_level": "RESTRICTED"}])])
    )
    assert "some" in rego and "input.data_items" in rego
    assert '.classification_level == "RESTRICTED"' in rego


def test_data_items_primary_domain_emits_an_equality_clause():
    rego = _compile(_policy([_rule("R-1", data_items=[{"primary_domain": "person"}])]))
    assert '.primary_domain == "person"' in rego


def test_data_items_regulatory_tags_emits_a_subset_clause():
    rego = _compile(
        _policy([_rule("R-1", data_items=[{"regulatory_tags": ["PII"]}])])
    )
    assert "every t in" in rego
    assert '["PII"]' in rego
    assert ".regulatory_tags" in rego


def test_data_items_list_of_entities_emits_a_subset_clause():
    rego = _compile(
        _policy([_rule("R-1", data_items=[{"list_of_entities": ["EMAIL"]}])])
    )
    assert "every e in" in rego
    assert ".list_of_entities" in rego


def test_multiple_data_items_each_get_their_own_existential_clause():
    rego = _compile(
        _policy(
            [
                _rule(
                    "R-1",
                    data_items=[
                        {"classification_level": "RESTRICTED"},
                        {"primary_domain": "finance"},
                    ],
                )
            ]
        )
    )
    assert rego.count("in input.data_items") == 2


def test_data_destinations_categories_emits_a_subset_clause():
    rego = _compile(
        _policy(
            [_rule("R-1", data_destinations=[{"data_destination_categories": ["external"]}])]
        )
    )
    assert "input.data_destinations" in rego
    assert ".data_destination_categories" in rego


def test_data_destinations_trust_level_emits_an_equality_clause():
    rego = _compile(
        _policy(
            [
                _rule(
                    "R-1",
                    data_destinations=[
                        {"data_destination_trust_level": "UNTRUSTED_EXTERNAL"}
                    ],
                )
            ]
        )
    )
    assert '.data_destination_trust_level == "UNTRUSTED_EXTERNAL"' in rego


def test_data_sources_categories_emits_a_subset_clause():
    rego = _compile(
        _policy([_rule("R-1", data_sources=[{"data_source_categories": ["internal"]}])])
    )
    assert "input.data_sources" in rego
    assert ".data_source_categories" in rego


def test_processing_agents_name_emits_an_equality_clause():
    rego = _compile(
        _policy([_rule("R-1", processing_agents=[{"agent_name": "tool-x"}])])
    )
    assert "input.processing_agents" in rego
    assert '.agent_name == "tool-x"' in rego


def test_processing_agents_trust_level_emits_an_equality_clause():
    rego = _compile(
        _policy([_rule("R-1", processing_agents=[{"agent_trust_level": "TRUSTED"}])])
    )
    assert '.agent_trust_level == "TRUSTED"' in rego


# --- negative control: absent fields emit no clause --------------------------


def test_absent_match_fields_emit_no_clause_for_them():
    """A rule specifying only event_type must not emit clauses referencing
    data_items, data_destinations, requested_actions, or accessing_user —
    proving the compiler reads presence, not some implicit always-there
    template."""
    rego = _compile(_policy([_rule("R-1", event_type="external_sharing")]))
    assert "input.data_items" not in rego
    assert "input.data_destinations" not in rego
    assert "input.data_sources" not in rego
    assert "input.requested_actions" not in rego
    assert "input.accessing_user" not in rego
    assert "input.processing_agents" not in rego


# --- unsupported fields --------------------------------------------------


@pytest.mark.parametrize("field", ["data_lineage", "scope"])
def test_data_lineage_and_scope_raise_not_implemented(field: str):
    policy = _policy([_rule("R-1", **{field: {} if field == "data_lineage" else []})])
    with pytest.raises(NotImplementedError, match=field):
        _compile(policy)


# --- fallback rule -------------------------------------------------------


def test_fallback_rule_id_and_risk_level():
    rego = _compile(_policy([_rule("R-1")]))
    assert '"rule_id": "0000"' in rego
    assert '"risk_level": "none"' in rego


def test_fallback_enforcement_type_reads_unknown_behavior_enforcement_type():
    policy = _policy(
        [_rule("R-1")],
        runtime_enforcement_mode={"unknown_behavior_enforcement_type": "escalate"},
    )
    rego = _compile(policy)
    assert 'default policy_decision := {"rule_id": "0000"' in rego
    assert '"enforcement_type": "escalate"' in rego


def test_fallback_explanation_and_confidence():
    rego = _compile(_policy([_rule("R-1")]))
    assert "No rules fired, falling back to default rule" in rego
    assert '"confidence": 1.0' in rego


def test_fallback_triggered_rules_is_empty():
    rego = _compile(_policy([_rule("R-1")]))
    assert 'default policy_decision := {' in rego
    # The default block's own triggered_rules is empty; the *combined*
    # non-default rule below fills it in dynamically. Assert the literal
    # empty list appears in the default block specifically.
    default_line = next(
        line for line in rego.splitlines() if line.startswith("default policy_decision")
    )
    assert '"triggered_rules": []' in default_line


# --- combining block, per mode -----------------------------------------------


def test_most_restrictive_mode_emits_the_rank_lookup_and_sort_by_severity():
    rego = _compile(_policy([_rule("R-1")]), default_mode="most_restrictive")
    assert "_risk_level_rank" in rego
    assert "_enforcement_rank" in rego
    assert '"rule_combining_mode": "most_restrictive"' in rego
    assert "sort(ranked)" in rego


def test_first_fires_mode_emits_the_catalog_order_lookup():
    rego = _compile(_policy([_rule("R-1")]), default_mode="first_fires")
    assert "_rule_order" in rego
    assert '"rule_combining_mode": "first_fires"' in rego
    assert "ordered_ids" in rego


def test_policy_level_rule_combining_mode_overrides_the_default_mode():
    """The policy JSON's own top-level policy_decision.rule_combining_mode
    wins over whatever default_mode the caller passes — the compiler-level
    default is a fallback for policies that declare none, not an override."""
    policy = _policy(
        [_rule("R-1")], policy_decision={"rule_combining_mode": "first_fires"}
    )
    rego = _compile(policy, default_mode="most_restrictive")
    assert '"rule_combining_mode": "first_fires"' in rego
    assert '"rule_combining_mode": "most_restrictive"' not in rego


def test_default_mode_none_falls_back_to_most_restrictive():
    rego = _compile(_policy([_rule("R-1")]), default_mode=None)
    assert '"rule_combining_mode": "most_restrictive"' in rego


def test_unrecognized_combining_mode_raises_value_error():
    policy = _policy([_rule("R-1")], policy_decision={"rule_combining_mode": "bogus"})
    with pytest.raises(ValueError, match="rule_combining_mode"):
        _compile(policy)


def test_rule_decisions_lookup_table_keys_every_rule_by_id():
    rego = _compile(_policy([_rule("R-1"), _rule("R-2")]))
    assert '"R-1":' in rego
    assert '"R-2":' in rego


# --- injection safety ---------------------------------------------------


def test_string_values_are_emitted_via_json_dumps_and_cannot_break_out():
    """A value containing a quote and a backslash must round-trip as an
    escaped Rego string literal, not break out of the surrounding quotes
    and inject additional Rego."""
    rego = _compile(_policy([_rule("R-1", event_type='weird"value\\here')]))
    assert '"weird\\"value\\\\here"' in rego
    # No unescaped quote broke the clause into two statements.
    assert 'input.event_type == "weird\\"value\\\\here"' in rego


# --- schema validation ----------------------------------------------------


def test_validate_true_rejects_a_policy_missing_required_top_level_fields():
    """The default validate=True path actually runs jsonschema against the
    vendored schema — a policy missing the schema's required top-level
    fields (e.g. policy_id, version, the enum-vocabulary blocks) must be
    rejected rather than silently compiled."""
    import jsonschema

    bare_policy = _policy([_rule("R-1")])
    with pytest.raises(jsonschema.ValidationError):
        compile_policy(bare_policy)


def test_validate_false_skips_schema_validation():
    """The same bare policy that validate=True rejects compiles fine with
    validate=False — proving the flag actually gates the check rather than
    validation being silently skipped either way."""
    bare_policy = _policy([_rule("R-1")])
    rego = compile_policy(bare_policy, validate=False)
    assert "package data_governance" in rego
