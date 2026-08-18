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
- ``"most_restrictive"`` — the most severe ``risk_level``/``enforcement_type``
  across every firing rule, ranked by
  :data:`data_governance.risk.rules.catalog.RISK_LEVEL_ORDER`/
  ``ENFORCEMENT_ORDER``.

Both modes report ``triggered_rules`` as every firing rule's id, regardless
of which one's fields end up in the combined decision.
"""

from __future__ import annotations

from typing import Any

from data_governance.risk.engine.utils import severity_max
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
    else:
        winner = _most_restrictive(firing_decisions)

    return {
        "risk_level": winner.get("risk_level"),
        "enforcement_type": winner.get("enforcement_type"),
        "allowed_actions": list(winner.get("allowed_actions") or []),
        "explanation": winner.get("explanation"),
        "confidence": winner.get("confidence"),
        "triggered_rules": triggered_rules,
        "rule_combining_mode": mode,
    }


def _most_restrictive(firing_decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """The single firing decision whose ``risk_level``/``enforcement_type``
    is the most severe, per :data:`RISK_LEVEL_ORDER`/``ENFORCEMENT_ORDER``.

    ``risk_level`` is the primary axis; ``enforcement_type`` breaks a tie on
    ``risk_level`` (two rules at the same risk level can still specify
    different enforcement severity, e.g. ``block`` vs ``escalate``). A value
    absent from its order tuple (including ``None``) ranks least severe,
    mirroring :func:`severity_max`'s own unranked-last convention — so a
    decision missing both fields never wins a tie against one that has them.
    """
    winner = firing_decisions[0][1]
    for _rule_id, decision in firing_decisions[1:]:
        most_severe_risk = severity_max(
            winner.get("risk_level"), decision.get("risk_level"), order=RISK_LEVEL_ORDER
        )
        if most_severe_risk != winner.get("risk_level"):
            winner = decision
            continue
        if most_severe_risk != decision.get("risk_level"):
            continue
        # Tied on risk_level: break the tie on enforcement_type.
        most_severe_enforcement = severity_max(
            winner.get("enforcement_type"),
            decision.get("enforcement_type"),
            order=ENFORCEMENT_ORDER,
        )
        if most_severe_enforcement != winner.get("enforcement_type"):
            winner = decision
    return winner
