"""Python oracle for the policy-level rule-combining semantics (issue #173).

The actual decision authority is OPA: :mod:`data_governance.risk.rules.rego`
compiles this same combining logic *into* the emitted Rego so a real OPA
evaluation, not this module, produces the ``policy_decision`` DAS persists.
:func:`combine` exists purely as an exhaustively-tested spec that
``tests/risk/rules/test_rego_opa.py`` checks the compiled Rego agrees with —
it is never called from the runtime request path.

Two modes, both drawn from ``schema/policy.schema.json``'s
``$defs/ruleCombiningModeValues``:

- ``"first_fires"`` — the decision of the first rule (in catalog order) that
  fires.
- ``"most_restrictive"`` — combines per field rather than picking one winning
  rule: ``risk_level`` comes from whichever firing rule ranks most severe on
  :data:`data_governance.risk.rules.catalog.RISK_LEVEL_ORDER`;
  ``enforcement_type``, ``allowed_actions``, and ``confidence`` all come
  together from whichever firing rule ranks most severe on ``ENFORCEMENT_ORDER``
  (so those three stay one rule's consistent judgment); ``explanation``
  concatenates the risk-level winner's and enforcement-type winner's
  explanations with ``"; "``, deduplicated to one when the same rule wins
  both axes. A rule that fires but wins neither axis contributes nothing to
  the combined decision beyond its id in ``triggered_rules``. Ties (same
  rank on an axis) break on rule id, lowest first — deterministic, not
  arbitrary.

Both modes report ``triggered_rules`` as every firing rule's id, regardless
of which one's fields end up in the combined decision.
"""

from __future__ import annotations

from typing import Any

from data_governance.risk.rules.catalog import ENFORCEMENT_ORDER, RISK_LEVEL_ORDER

__all__ = ["combine"]

_MODES = ("first_fires", "most_restrictive")


def combine(
    firing_decisions: list[dict[str, Any]],
    *,
    mode: str,
) -> dict[str, Any]:
    """Combine every firing rule's ``rule_decision`` into one policy-level
    decision, per *mode*.

    *firing_decisions* is a list of ``(rule_id, rule_decision)`` pairs — each
    a 2-tuple of the firing rule's id and its ``rule_decision`` dict — in
    catalog order (the order they appear in ``rules_source.json``). An empty
    list is a valid input (no rule fired): callers combining an empty list
    should use the policy's fallback rule decision instead, since this
    function has no fallback values of its own to fall back to.

    Returns a dict with the same field set as ``$defs/ruleDecision``
    (``risk_level``, ``enforcement_type``, ``allowed_actions``,
    ``explanation``, ``confidence``) plus ``triggered_rules`` (every firing
    rule's id, sorted) and ``rule_combining_mode`` (echoing *mode*).

    Raises ``ValueError`` for an empty *firing_decisions* (nothing to
    combine) or an unrecognized *mode* — both are caller bugs, not data the
    caller should silently paper over.
    """
    if mode not in _MODES:
        raise ValueError(f"mode must be one of {_MODES}, got {mode!r}")
    if not firing_decisions:
        raise ValueError("cannot combine an empty firing_decisions list")

    triggered_rules = sorted(rule_id for rule_id, _decision in firing_decisions)

    if mode == "first_fires":
        _rule_id, winner = firing_decisions[0]
        return {
            "risk_level": winner.get("risk_level"),
            "enforcement_type": winner.get("enforcement_type"),
            "allowed_actions": list(winner.get("allowed_actions") or []),
            "explanation": winner.get("explanation"),
            "confidence": winner.get("confidence"),
            "triggered_rules": triggered_rules,
            "rule_combining_mode": mode,
        }

    risk_winner, enf_winner, explanation = _most_restrictive(firing_decisions)
    return {
        "risk_level": risk_winner.get("risk_level"),
        "enforcement_type": enf_winner.get("enforcement_type"),
        "allowed_actions": list(enf_winner.get("allowed_actions") or []),
        "explanation": explanation,
        "confidence": enf_winner.get("confidence"),
        "triggered_rules": triggered_rules,
        "rule_combining_mode": mode,
    }


def _rank(order: tuple[str, ...], value: Any) -> int:
    ranks = {v: r for r, v in enumerate(order)}
    return ranks.get(value, len(order))


def _most_restrictive(
    firing_decisions: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """The risk-axis winner, the enforcement-axis winner, and their merged
    explanation.

    Each axis picks the firing decision ranking most severe on its own
    order (:data:`RISK_LEVEL_ORDER` / ``ENFORCEMENT_ORDER``) independently —
    the two winners need not be the same rule. A value absent from its order
    tuple (including ``None``) ranks least severe, via :func:`_rank`'s
    ``len(order)`` fallback. Ties break on rule id, lowest first, for a
    deterministic pick rather than an arbitrary one — matching the Rego
    compiler's ``sort([[rank, id], ...])[0]`` construct
    (:mod:`data_governance.risk.rules.rego`).

    The merged explanation is the risk winner's and enforcement winner's
    explanations joined with ``"; "``, deduplicated to one when the same
    rule wins both axes — rules that fired but won neither axis contribute
    nothing.
    """
    ranked_by_risk = sorted(
        firing_decisions,
        key=lambda item: (_rank(RISK_LEVEL_ORDER, item[1].get("risk_level")), item[0]),
    )
    ranked_by_enforcement = sorted(
        firing_decisions,
        key=lambda item: (_rank(ENFORCEMENT_ORDER, item[1].get("enforcement_type")), item[0]),
    )
    risk_winner_id, risk_winner = ranked_by_risk[0]
    enf_winner_id, enf_winner = ranked_by_enforcement[0]

    if risk_winner_id == enf_winner_id:
        explanation = risk_winner.get("explanation") or ""
    else:
        explanation = "; ".join(
            winner.get("explanation") or "" for winner in (risk_winner, enf_winner)
        )
    return risk_winner, enf_winner, explanation
