"""JSON policy -> Rego compiler (issue #173).

Compiles a policy JSON document (``schema/policy.schema.json``-conformant —
the shape ``rules_source.json`` and every fixture under
``tests/risk/rules/fixtures/`` already have) into a Rego module OPA can
evaluate at ``POST /v1/data/data_governance/policy_decision``
(``data_governance/risk/config.py:OPA_DECISION_PATH``), the endpoint
:class:`data_governance.risk.engine.opa.OpaClient` already calls.

Each rule becomes a ``triggered_rules contains "<rule_id>" if { ... }``
block whose body is the rule's structural predicate — a rule is a *sparse*
instance of the same shape as one flow of the OPA runtime input
(``schema/opa_input.schema.json``, sharing ``$defs`` with
``policy.schema.json``). The input holds one flow per leg of the exchange
(#271), so "does this rule match this input" is "does some one flow's
concrete values satisfy the rule's sparse subset of fields," field by
field. A rule field that is absent contributes no clause — it does
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

Every string emitted into the generated Rego source goes through
``json.dumps`` (which doubles as a safe Rego string literal — both languages
use the same double-quoted, backslash-escaped syntax), so a quote or
backslash in a policy value cannot break out into injected Rego.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from data_governance.risk.rules.catalog import ENFORCEMENT_ORDER, RISK_LEVEL_ORDER

__all__ = ["FALLBACK_RULE_ID", "compile_policy"]

# The rule id the compiled bundle's ``default policy_decision`` reports in
# ``triggered_rules`` to say "no catalog rule fired". A sentinel, not a rule:
# consumers that count fired rules (the engine's stored records) must not
# count it.
FALLBACK_RULE_ID = "0000"

_PACKAGE = "data_governance"

# Vendored alongside the catalog module, same convention as
# ``catalog.py``'s own ``_POLICY_DATA_DIR``. Self-contained — no cross-file
# ``$ref`` into ``opa_input.schema.json`` — so a plain ``jsonschema.validate``
# against the loaded file is enough; no ``referencing.Registry`` needed here.
_SCHEMA_PATH = Path(__file__).parent / "schema" / "policy.schema.json"

_VALID_MODES = ("first_fires", "most_restrictive")

# Compile only predicates the current engine can supply. The schema also
# describes future input capabilities; schema-valid fields alone do not make
# an authored guard usable by build_opa_input().
_SCALAR_RULE_FIELDS = ("event_type",)

_RULE_METADATA_FIELDS = (
    "rule_id", "rule_name", "rule_categories", "rule_sources", "rule_decision",
)


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
    flow.data_items`` twice under the identical name, which Rego reads as
    "one single item satisfies both entries simultaneously" rather than the
    intended "some item satisfies entry 1 and some (possibly different) item
    satisfies entry 2."
    """
    safe = "".join(c if c.isalnum() else "_" for c in rule_id)
    return f"_{safe}_{suffix}_{index}"


def _scalar_clause(field: str, value: Any) -> str:
    return f"flow.{field} == {_lit(value)}"


def _set_subset_clause(input_field: str, required: list[Any]) -> str:
    """``required`` (a rule-side list) must be a subset of ``flow.<field>``
    (an input-side list) — every rule-listed value must appear in the
    input's list, but the input may carry additional values the rule does
    not care about."""
    var = f"_{input_field}_set"
    return (
        f"{var} := {{v | some v in flow.{input_field}}}\n"
        f"    every v in {_lit(required)} {{ v in {var} }}"
    )


def _check_supported_fields(
    rule_id: str, constraints: dict[str, Any], supported: set[str],
    *, path: str = "",
) -> None:
    """Reject guards absent from the runtime, even with validate=False."""
    for field in constraints:
        if field not in supported:
            field_path = f"{path}.{field}" if path else field
            raise NotImplementedError(
                f"rule {rule_id!r} carries {field_path!r}, which the risk "
                "engine does not supply in its OPA runtime input"
            )


def _object_field_clauses(
    rule_id: str,
    var: str,
    constraints: dict[str, Any],
    *,
    path: str,
    scalar_fields: tuple[str, ...],
    subset_fields: dict[str, str],
) -> list[str]:
    """All sparse scalar/list constraints on one bound input object.

    Scalars compare exactly; primitive lists require every authored value.
    Every field must have a runtime source and a translation.
    """
    _check_supported_fields(
        rule_id, constraints, set(scalar_fields) | set(subset_fields), path=path,
    )
    lines = [
        f"{var}.{field} == {_lit(constraints[field])}"
        for field in scalar_fields if field in constraints
    ]
    for field, loop_var in subset_fields.items():
        if field in constraints:
            lines.append(
                f"every {loop_var} in {_lit(constraints[field])} "
                f"{{ {loop_var} in {var}.{field} }}"
            )
    return lines


def _data_item_clause(rule_id: str, item: dict[str, Any], index: int) -> str:
    """One ``data_items[]`` entry: matched if *some* input data item
    satisfies every field the rule specifies on this entry."""
    var = _rule_var(rule_id, "item", index)
    lines = [f"some {var} in flow.data_items"]
    lines.extend(_object_field_clauses(
        rule_id, var, {field: value for field, value in item.items() if field != "entities"},
        path="data_items[]", scalar_fields=("classification_level", "primary_domain"),
        subset_fields={
            "regulatory_tags": "t", "list_of_entities": "e",
            "list_of_domains": "d", "identity_bundles": "b",
        },
    ))
    for entity_index, entity in enumerate(item.get("entities", [])):
        # Include the parent item index: entities in distinct item predicates
        # must not accidentally share one existential variable.
        entity_var = _rule_var(rule_id, f"item_{index}_entity", entity_index)
        lines.append(f"some {entity_var} in {var}.entities")
        lines.extend(_object_field_clauses(
            rule_id, entity_var, entity,
            path="data_items[].entities[]",
            scalar_fields=(
                "entity_type", "start", "end", "text", "domain", "category",
                "data_type", "classification_level",
            ),
            subset_fields={"regulatory_tags": "t"},
        ))
    return "\n    ".join(lines)


def _data_destination_clause(
    rule_id: str, destination: dict[str, Any], index: int
) -> str:
    """One ``data_destinations[]`` entry: matched if *some* input
    destination satisfies every field the rule specifies on this entry."""
    var = _rule_var(rule_id, "dest", index)
    lines = [f"some {var} in flow.data_destinations"]
    lines.extend(_object_field_clauses(
        rule_id, var, destination,
        path="data_destinations[]",
        scalar_fields=(
            "data_destination_name", "data_destination_url",
            "data_destination_trust_level",
        ),
        subset_fields={"data_destination_categories": "c"},
    ))
    return "\n    ".join(lines)


def _data_source_clause(rule_id: str, source: dict[str, Any], index: int) -> str:
    """Mirror of :func:`_data_destination_clause` for ``data_sources[]``."""
    var = _rule_var(rule_id, "src", index)
    lines = [f"some {var} in flow.data_sources"]
    lines.extend(_object_field_clauses(
        rule_id, var, source,
        path="data_sources[]",
        scalar_fields=("data_source_name", "data_source_url"),
        subset_fields={"data_source_categories": "c"},
    ))
    return "\n    ".join(lines)


def _processing_agent_clause(rule_id: str, agent: dict[str, Any], index: int) -> str:
    var = _rule_var(rule_id, "agent", index)
    lines = [f"some {var} in flow.processing_agents"]
    lines.extend(_object_field_clauses(
        rule_id, var, agent,
        path="processing_agents[]", scalar_fields=("agent_name",),
        subset_fields={},
    ))
    return "\n    ".join(lines)


def _accessing_user_clause(rule_id: str, user: dict[str, Any]) -> list[str]:
    _check_supported_fields(rule_id, user, {"username", "user_roles"}, path="accessing_user")
    # The adapter's required user_roles key is always []: no role facts exist.
    # An empty constraint is vacuous, but a required role can never match.
    if user.get("user_roles"):
        _check_supported_fields(rule_id, {"user_roles": user["user_roles"]}, set(),
                                path="accessing_user")
    clauses = []
    if "username" in user:
        clauses.append(_scalar_clause("accessing_user.username", user["username"]))
    if "user_roles" in user:
        roles = _lit(user["user_roles"])
        clauses.append(f"every r in {roles} {{ r in flow.accessing_user.user_roles }}")
    return clauses


def _rule_predicate_clauses(rule: dict[str, Any]) -> list[str]:
    """Every top-level predicate clause for one rule, per the field ->
    predicate mapping in the module docstring. A field absent from *rule*
    contributes no clause."""
    _check_supported_fields(
        rule["rule_id"], rule,
        set(_RULE_METADATA_FIELDS) | set(_SCALAR_RULE_FIELDS) | {
            "requested_actions", "accessing_user", "data_items",
            "data_destinations", "data_sources", "processing_agents",
        },
    )

    clauses: list[str] = []

    for field in _SCALAR_RULE_FIELDS:
        if field in rule:
            clauses.append(_scalar_clause(field, rule[field]))

    if "requested_actions" in rule:
        clauses.append(_set_subset_clause("requested_actions", rule["requested_actions"]))

    if "accessing_user" in rule:
        clauses.extend(_accessing_user_clause(rule["rule_id"], rule["accessing_user"]))

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
    """One ``triggered_rules contains "<id>" if { ... }`` block. Every clause
    reads the same ``flow``, bound once by ``some flow in input.flows`` (which
    walks the flows of both legs, whatever their keys): the rule fires when
    one flow satisfies all of it, never when one flow
    supplies the data and another the destination. A rule with no predicate
    clauses at all (every match field absent) always fires — that is what the
    schema's all-optional match fields mean structurally, though no shipped
    rule is actually shapeless like this."""
    clauses = _rule_predicate_clauses(rule)
    rule_id_literal = _lit(rule["rule_id"])
    if not clauses:
        return f"triggered_rules contains {rule_id_literal} if {{\n    true\n}}"
    body = "\n    ".join(["some flow in input.flows", *clauses])
    return f"triggered_rules contains {rule_id_literal} if {{\n    {body}\n}}"


def _rank_object(order: tuple[str, ...]) -> str:
    """*order* (most-severe-first) rendered as a Rego object literal mapping
    each value to its rank (0 = most severe) — the single source of truth
    for severity is :data:`RISK_LEVEL_ORDER`/``ENFORCEMENT_ORDER``; this
    just gives the generated Rego the same ranks Python already agrees on,
    with no second hand-written ordering to drift from it."""
    pairs = ", ".join(f"{_lit(value)}: {rank}" for rank, value in enumerate(order))
    return f"{{{pairs}}}"


def _fallback_decision(policy: dict[str, Any], *, mode: str) -> dict[str, Any]:
    unknown_enforcement = policy["runtime_enforcement_mode"][
        "unknown_behavior_enforcement_type"
    ]
    return {
        "risk_level": "none",
        "enforcement_type": unknown_enforcement,
        "allowed_actions": [],
        "explanation": "No rules fired, falling back to default rule",
        "confidence": 1.0,
        "triggered_rules": [FALLBACK_RULE_ID],
        "rule_combining_mode": mode,
    }


def _combining_block(policy: dict[str, Any]) -> str:
    """The ``default policy_decision`` fallback plus the per-rule-id lookup
    tables and the ``policy_decision`` rule that combines whichever rules
    fired, per the policy's own ``rule_combining_mode``."""
    mode = policy["rule_combining_mode"]
    if mode not in _VALID_MODES:
        raise ValueError(f"rule_combining_mode must be one of {_VALID_MODES}, got {mode!r}")

    fallback = _fallback_decision(policy, mode=mode)
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

    Compile scalar and list predicates backed by the current runtime input,
    including nested entities. Rule names, categories, sources and decisions
    are metadata. Raises ``NotImplementedError`` for unavailable predicates
    (including schema-valid future fields) at any depth even when validation
    is skipped, and ``ValueError`` for an unrecognized combining mode.
    """
    if validate:
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
