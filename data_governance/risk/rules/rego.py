"""JSON policy -> Rego compiler (issue #173).

Compiles a policy JSON document (``schema/policy.schema.json``-conformant —
the shape ``rules_source.json`` and every fixture under
``tests/risk/rules/fixtures/`` already have) into a Rego module OPA can
evaluate at ``POST /v1/data/data_governance/policy_decision``
(``data_governance/risk/config.py:OPA_DECISION_PATH``), the endpoint
:class:`data_governance.risk.engine.opa.OpaClient` already calls.

Each rule becomes a ``triggered_rules contains "<rule_id>" if { ... }``
block whose body is the rule's structural predicate — a rule is a *sparse*
instance of the same shape as the OPA runtime input
(``schema/opa_input.schema.json``, sharing ``$defs`` with
``policy.schema.json``), so "does this rule match this input" is "is the
rule's sparse subset of fields satisfied by the input's concrete values,"
field by field. A rule field that is absent contributes no clause — it does
not mean "match anything," it means "this rule does not constrain that
axis."

``policy_decision`` is then built from whichever rules fired, per the
policy's own top-level ``rule_combining_mode`` (a required field on
``schema/policy.schema.json`` — the combining mode is a property of the
authored policy, not a deployment-level config parameter), falling back to
``runtime_enforcement_mode.unknown_behavior_enforcement_type`` when nothing
fires. The shape of the returned ``policy_decision`` value is
``schema/opa_output.schema.json`` — the fallback and combined decisions both
emit exactly that key set (no ``rule_id``/``rule_name``). The combining
semantics mirror :func:`data_governance.risk.rules.combining.combine` — that
module is the Python oracle this Rego is tested against, not a second
implementation callers should pick between.

Under ``most_restrictive``, the combined decision is built **per field**
rather than by picking one winning rule: ``risk_level`` comes from whichever
firing rule ranks most severe on :data:`RISK_LEVEL_ORDER`; ``enforcement_type``,
``allowed_actions``, and ``confidence`` all come together from whichever
firing rule ranks most severe on :data:`ENFORCEMENT_ORDER` (so those three
stay one rule's consistent judgment); ``explanation`` concatenates the two
winners' explanations with ``"; "``, deduplicated to one when the same rule
wins both axes. A rule that fires but wins neither axis contributes nothing
beyond its id in ``triggered_rules``.

Both the fallback and the combined decision also carry ``policy_version``
(``"<policy_id>:<version>"`` from the policy envelope, omitted when the
envelope names neither) so the persisted decision records which compiled
policy produced it (FR-DAS-033 audit trail: ``interaction_policy_decisions
.policy_version`` / ``interaction_risk_records.opa_policy_versions_used``)
without the engine guessing from whatever catalog *it* happens to ship —
the bundle OPA loaded is the only authority on its own version.

Every string emitted into the generated Rego source goes through
``json.dumps`` (which doubles as a safe Rego string literal — both languages
use the same double-quoted, backslash-escaped syntax), so a quote or
backslash in a policy value cannot break out into injected Rego.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_governance.risk.rules.catalog import ENFORCEMENT_ORDER, RISK_LEVEL_ORDER

__all__ = ["FALLBACK_RULE_ID", "compile_policy", "policy_version"]

_PACKAGE = "data_governance"

# The id the fallback decision reports in ``triggered_rules`` when no
# catalog rule fired. Not a catalog rule: consumers that count or name
# fired rules (risk records, metrics, alerts) must treat it as "nothing
# fired" — see ``data_governance.risk.engine.compute``.
FALLBACK_RULE_ID = "0000"

# Vendored alongside the catalog module, same convention as
# ``catalog.py``'s own ``_POLICY_DATA_DIR``. Self-contained — no cross-file
# ``$ref`` into ``opa_input.schema.json`` — so a plain ``jsonschema.validate``
# against the loaded file is enough; no ``referencing.Registry`` needed here.
_SCHEMA_PATH = Path(__file__).parent / "schema" / "policy.schema.json"

_VALID_MODES = ("first_fires", "most_restrictive")

# Rule fields this compiler does not know how to translate into a Rego
# predicate. Both are deeply nested structures with no rule in the shipped
# catalog using them and no field in opa_input.schema.json supplying them
# today — compiling a rule that carries either would silently emit a rule
# that can never fire, which is worse than refusing outright.
_UNSUPPORTED_RULE_FIELDS = ("data_lineage", "scope")


def _lit(value: Any) -> str:
    """A value rendered as a Rego literal via ``json.dumps`` — safe for
    strings, numbers, bools, and lists/dicts of the same."""
    return json.dumps(value)


def _rule_var(rule_id: str, suffix: str, index: int) -> str:
    """A Rego-safe local variable name derived from a rule id, a fixed
    per-field-type suffix, and *index* — a rule's position within that
    field's list on this rule (e.g. the second ``data_items[]`` entry).

    Rego requires every use of the same ``some``-bound variable within one
    rule body to resolve to the same value, so two entries of the same field
    type on one rule must get distinct variable names: without *index*, a
    rule with two ``data_items[]`` entries would emit ``some _R_1_item in
    input.data_items`` twice under the identical name, which Rego reads as
    "one single item satisfies both entries simultaneously" rather than the
    intended "some item satisfies entry 1 and some (possibly different) item
    satisfies entry 2."
    """
    safe = "".join(c if c.isalnum() else "_" for c in rule_id)
    return f"_{safe}_{suffix}_{index}"


def _scalar_clause(field: str, value: Any) -> str:
    return f"input.{field} == {_lit(value)}"


def _set_subset_clause(input_field: str, required: list[Any]) -> str:
    """``required`` (a rule-side list) must be a subset of ``input.<field>``
    (an input-side list) — every rule-listed value must appear in the
    input's list, but the input may carry additional values the rule does
    not care about."""
    var = f"_{input_field}_set"
    return (
        f"{var} := {{v | some v in input.{input_field}}}\n"
        f"    every v in {_lit(required)} {{ v in {var} }}"
    )


def _data_item_clause(rule_id: str, item: dict[str, Any], index: int) -> str:
    """One ``data_items[]`` entry: matched if *some* input data item
    satisfies every field the rule specifies on this entry."""
    var = _rule_var(rule_id, "item", index)
    lines = [f"some {var} in input.data_items"]
    if "classification_level" in item:
        lines.append(f"{var}.classification_level == {_lit(item['classification_level'])}")
    if "primary_domain" in item:
        lines.append(f"{var}.primary_domain == {_lit(item['primary_domain'])}")
    if "regulatory_tags" in item:
        tags = _lit(item["regulatory_tags"])
        lines.append(f"every t in {tags} {{ t in {var}.regulatory_tags }}")
    if "list_of_entities" in item:
        entities = _lit(item["list_of_entities"])
        lines.append(f"every e in {entities} {{ e in {var}.list_of_entities }}")
    return "\n    ".join(lines)


def _data_destination_clause(
    rule_id: str, destination: dict[str, Any], index: int
) -> str:
    """One ``data_destinations[]`` entry: matched if *some* input
    destination satisfies every field the rule specifies on this entry."""
    var = _rule_var(rule_id, "dest", index)
    lines = [f"some {var} in input.data_destinations"]
    if "data_destination_categories" in destination:
        cats = _lit(destination["data_destination_categories"])
        lines.append(f"every c in {cats} {{ c in {var}.data_destination_categories }}")
    if "data_destination_trust_level" in destination:
        lines.append(
            f"{var}.data_destination_trust_level "
            f"== {_lit(destination['data_destination_trust_level'])}"
        )
    return "\n    ".join(lines)


def _data_source_clause(rule_id: str, source: dict[str, Any], index: int) -> str:
    """Mirror of :func:`_data_destination_clause` for ``data_sources[]``."""
    var = _rule_var(rule_id, "src", index)
    lines = [f"some {var} in input.data_sources"]
    if "data_source_categories" in source:
        cats = _lit(source["data_source_categories"])
        lines.append(f"every c in {cats} {{ c in {var}.data_source_categories }}")
    return "\n    ".join(lines)


def _processing_agent_clause(rule_id: str, agent: dict[str, Any], index: int) -> str:
    var = _rule_var(rule_id, "agent", index)
    lines = [f"some {var} in input.processing_agents"]
    if "agent_name" in agent:
        lines.append(f"{var}.agent_name == {_lit(agent['agent_name'])}")
    if "agent_trust_level" in agent:
        lines.append(f"{var}.agent_trust_level == {_lit(agent['agent_trust_level'])}")
    return "\n    ".join(lines)


def _accessing_user_clause(user: dict[str, Any]) -> list[str]:
    clauses = []
    if "username" in user:
        clauses.append(_scalar_clause("accessing_user.username", user["username"]))
    if "user_roles" in user:
        roles = _lit(user["user_roles"])
        clauses.append(f"every r in {roles} {{ r in input.accessing_user.user_roles }}")
    return clauses


def _rule_predicate_clauses(rule: dict[str, Any]) -> list[str]:
    """Every top-level predicate clause for one rule, per the field ->
    predicate mapping in the module docstring. A field absent from *rule*
    contributes no clause."""
    for field in _UNSUPPORTED_RULE_FIELDS:
        if field in rule:
            raise NotImplementedError(
                f"rule {rule.get('rule_id')!r} carries {field!r}, which this "
                f"compiler does not yet translate to a Rego predicate"
            )

    clauses: list[str] = []

    if "event_type" in rule:
        clauses.append(_scalar_clause("event_type", rule["event_type"]))

    if "data_count" in rule:
        clauses.append(f"input.data_count >= {_lit(rule['data_count'])}")

    if "requested_actions" in rule:
        clauses.append(_set_subset_clause("requested_actions", rule["requested_actions"]))

    if "accessing_user" in rule:
        clauses.extend(_accessing_user_clause(rule["accessing_user"]))

    rule_id = rule["rule_id"]

    for index, item in enumerate(rule.get("data_items", [])):
        clauses.append(_data_item_clause(rule_id, item, index))

    for index, destination in enumerate(rule.get("data_destinations", [])):
        clauses.append(_data_destination_clause(rule_id, destination, index))

    for index, source in enumerate(rule.get("data_sources", [])):
        clauses.append(_data_source_clause(rule_id, source, index))

    for index, agent in enumerate(rule.get("processing_agents", [])):
        clauses.append(_processing_agent_clause(rule_id, agent, index))

    return clauses


def _rule_block(rule: dict[str, Any]) -> str:
    """One ``triggered_rules contains "<id>" if { ... }`` block. A rule with
    no predicate clauses at all (every match field absent) always fires —
    that is what the schema's all-optional match fields mean structurally,
    though no shipped rule is actually shapeless like this."""
    clauses = _rule_predicate_clauses(rule)
    rule_id_literal = _lit(rule["rule_id"])
    if not clauses:
        return f"triggered_rules contains {rule_id_literal} if {{\n    true\n}}"
    body = "\n    ".join(clauses)
    return f"triggered_rules contains {rule_id_literal} if {{\n    {body}\n}}"


def _rank_object(order: tuple[str, ...]) -> str:
    """*order* (most-severe-first) rendered as a Rego object literal mapping
    each value to its rank (0 = most severe) — the single source of truth
    for severity is :data:`RISK_LEVEL_ORDER`/``ENFORCEMENT_ORDER``; this
    just gives the generated Rego the same ranks Python already agrees on,
    with no second hand-written ordering to drift from it."""
    pairs = ", ".join(f"{_lit(value)}: {rank}" for rank, value in enumerate(order))
    return f"{{{pairs}}}"


def policy_version(policy: dict[str, Any]) -> str | None:
    """``"<policy_id>:<version>"`` for a policy envelope naming both, else
    ``None`` — the value the compiled decisions report as
    ``policy_version``."""
    policy_id = policy.get("policy_id")
    version = policy.get("version")
    if policy_id is None or version is None:
        return None
    return f"{policy_id}:{version}"


def _fallback_decision(policy: dict[str, Any], *, mode: str) -> dict[str, Any]:
    unknown_enforcement = policy["runtime_enforcement_mode"][
        "unknown_behavior_enforcement_type"
    ]
    decision = {
        "risk_level": "none",
        "enforcement_type": unknown_enforcement,
        "allowed_actions": [],
        "explanation": "No rules fired, falling back to default rule",
        "confidence": 1.0,
        "triggered_rules": [FALLBACK_RULE_ID],
        "rule_combining_mode": mode,
    }
    version = policy_version(policy)
    if version is not None:
        decision["policy_version"] = version
    return decision


def _version_line(policy: dict[str, Any]) -> str:
    """The ``"policy_version": ...`` entry for a combined-decision object
    literal — empty when the envelope names no version, so the key is
    omitted rather than emitted as ``null``."""
    version = policy_version(policy)
    if version is None:
        return ""
    return f'        "policy_version": {_lit(version)},\n'


def _combining_block(policy: dict[str, Any]) -> str:
    """The ``default policy_decision`` fallback plus the per-rule-id lookup
    tables and the ``policy_decision`` rule that combines whichever rules
    fired, per the policy's own ``rule_combining_mode``."""
    mode = policy["rule_combining_mode"]
    if mode not in _VALID_MODES:
        raise ValueError(f"rule_combining_mode must be one of {_VALID_MODES}, got {mode!r}")

    fallback = _fallback_decision(policy, mode=mode)
    version_line = _version_line(policy)
    rules = policy["rules"]

    # rule_id -> rule_decision, so the combining rule can look up each
    # firing rule's decision by id without re-deriving it from the rule
    # list at runtime.
    decisions_by_id = ", ".join(
        f"{_lit(rule['rule_id'])}: {_lit(rule['rule_decision'])}" for rule in rules
    )
    # rule_id -> its position in catalog order, for first_fires' "earliest
    # in catalog order" tie-break.
    order_by_id = ", ".join(
        f"{_lit(rule['rule_id'])}: {index}" for index, rule in enumerate(rules)
    )

    lines = [
        f"_rule_decisions := {{{decisions_by_id}}}",
        f"_rule_order := {{{order_by_id}}}",
        f"_risk_level_rank := {_rank_object(RISK_LEVEL_ORDER)}",
        f"_enforcement_rank := {_rank_object(ENFORCEMENT_ORDER)}",
        "",
        f"default policy_decision := {_lit(fallback)}",
        "",
    ]

    if mode == "first_fires":
        lines.append(
            "policy_decision := decision if {\n"
            "    count(triggered_rules) > 0\n"
            "    ordered_ids := sort([[_rule_order[id], id] |\n"
            "        some id in triggered_rules\n"
            "    ])\n"
            "    first_id := ordered_ids[0][1]\n"
            "    decision := object.union(_rule_decisions[first_id], {\n"
            '        "triggered_rules": sort([id | some id in triggered_rules]),\n'
            '        "rule_combining_mode": "first_fires",\n'
            f"{version_line}"
            "    })\n"
            "}\n"
        )
    else:
        lines.append(
            "_UNRANKED := 9999\n"
            "\n"
            "_risk_rank(id) := _risk_level_rank[_rule_decisions[id].risk_level]\n"
            "_risk_rank(id) := _UNRANKED if { not _risk_level_rank[_rule_decisions[id].risk_level] }\n"
            "\n"
            "_enf_rank(id) := _enforcement_rank[_rule_decisions[id].enforcement_type]\n"
            "_enf_rank(id) := _UNRANKED if { not _enforcement_rank[_rule_decisions[id].enforcement_type] }\n"
            "\n"
            "policy_decision := decision if {\n"
            "    count(triggered_rules) > 0\n"
            "    ids := sort([id | some id in triggered_rules])\n"
            "\n"
            "    risk_winner := sort([[_risk_rank(id), id] | some id in ids])[0][1]\n"
            "    enf_winner := sort([[_enf_rank(id), id] | some id in ids])[0][1]\n"
            "\n"
            "    same := [risk_winner | risk_winner == enf_winner]\n"
            "    chosen := array.concat(same, [id | some id in [risk_winner, enf_winner]; risk_winner != enf_winner])\n"
            '    explanation := concat("; ", [_rule_decisions[id].explanation | some id in chosen])\n'
            "\n"
            "    decision := {\n"
            '        "risk_level": object.get(_rule_decisions[risk_winner], "risk_level", null),\n'
            '        "enforcement_type": object.get(_rule_decisions[enf_winner], "enforcement_type", null),\n'
            '        "allowed_actions": object.get(_rule_decisions[enf_winner], "allowed_actions", []),\n'
            '        "explanation": explanation,\n'
            '        "confidence": object.get(_rule_decisions[enf_winner], "confidence", null),\n'
            '        "triggered_rules": ids,\n'
            '        "rule_combining_mode": "most_restrictive",\n'
            f"{version_line}"
            "    }\n"
            "}\n"
        )

    return "\n".join(lines)


def compile_policy(
    policy: dict[str, Any],
    *,
    validate: bool = True,
) -> str:
    """Compile *policy* (a ``schema/policy.schema.json``-conformant dict)
    into a Rego module.

    *validate*, when true (the default), validates *policy* against the
    vendored ``schema/policy.schema.json`` before compiling and raises
    ``jsonschema.ValidationError`` on the first violation. Callers that have
    already validated the same policy object (e.g. immediately after
    loading it, when the schema check just ran) may pass ``validate=False``
    to skip re-checking it.

    The combining mode compiled in is read from *policy*'s own top-level
    ``rule_combining_mode`` — a required field on the schema, mirroring
    ``runtime_enforcement_mode``. There is no config-module fallback and no
    implicit default: a policy missing the field raises ``KeyError`` here
    (and, under ``validate=True``, is already rejected earlier by the schema
    check).

    Raises ``NotImplementedError`` if any rule carries ``data_lineage`` or
    ``scope`` (see the module docstring), and ``ValueError`` for an
    unrecognized combining mode.
    """
    if validate:
        # Dev-only dependency, imported where it is used: the engine imports
        # this module at runtime for FALLBACK_RULE_ID/policy_version, and the
        # processor image ships no jsonschema (observed live: leg-ready
        # crash-looped on import).
        import jsonschema

        with _SCHEMA_PATH.open(encoding="utf-8") as f:
            schema = json.load(f)
        jsonschema.validate(instance=policy, schema=schema)

    rule_blocks = "\n\n".join(_rule_block(rule) for rule in policy["rules"])
    combining = _combining_block(policy)

    parts = [f"package {_PACKAGE}", ""]
    if rule_blocks:
        parts.append(rule_blocks)
        parts.append("")
    parts.append(combining)
    return "\n".join(parts).rstrip() + "\n"
