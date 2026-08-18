"""Tests for ``data_governance.risk.rules.combining`` (issue #173).

:func:`combining.combine` is the exhaustively-tested Python oracle for the
policy-level rule-combining semantics; ``tests/risk/rules/test_rego_opa.py``
checks that the Rego :mod:`data_governance.risk.rules.rego` compiles agrees
with it on the same inputs. This file covers the oracle itself in isolation
— modes crossed with firing sets (empty, single, ties, unranked values,
``first_fires`` ordering) — so a disagreement surfaced by the OPA tests can
be traced to either side rather than debugged from scratch there.
"""

from __future__ import annotations

import pytest

from data_governance.risk.rules.combining import combine

# --- empty input -------------------------------------------------------------


@pytest.mark.parametrize("mode", ["first_fires", "most_restrictive"])
def test_empty_firing_decisions_raises(mode: str):
    """No rule fired: the caller must fall back to the policy's own default
    rule decision rather than get a fabricated result from this function,
    which has no fallback values of its own."""
    with pytest.raises(ValueError, match="empty"):
        combine([], mode=mode)


def test_unrecognized_mode_raises():
    decision = {"risk_level": "low", "enforcement_type": "warn"}
    with pytest.raises(ValueError, match="mode must be one of"):
        combine([("R-1", decision)], mode="bogus")


# --- single firing rule: both modes agree ------------------------------------


@pytest.mark.parametrize("mode", ["first_fires", "most_restrictive"])
def test_single_firing_rule_is_returned_verbatim_plus_metadata(mode: str):
    decision = {
        "risk_level": "high",
        "enforcement_type": "block",
        "allowed_actions": ["redact"],
        "explanation": "because",
        "confidence": 0.9,
    }
    result = combine([("R-1", decision)], mode=mode)
    assert result["risk_level"] == "high"
    assert result["enforcement_type"] == "block"
    assert result["allowed_actions"] == ["redact"]
    assert result["explanation"] == "because"
    assert result["confidence"] == 0.9
    assert result["triggered_rules"] == ["R-1"]
    assert result["rule_combining_mode"] == mode


def test_missing_allowed_actions_defaults_to_empty_list():
    """A rule_decision with no allowed_actions key (schema does not require
    it) combines to ``[]``, not ``None`` or a KeyError."""
    decision = {"risk_level": "low", "enforcement_type": "warn"}
    result = combine([("R-1", decision)], mode="first_fires")
    assert result["allowed_actions"] == []


# --- first_fires: catalog order wins, regardless of severity ----------------


def test_first_fires_picks_the_first_rule_even_when_less_severe():
    low = {"risk_level": "low", "enforcement_type": "warn", "explanation": "low"}
    high = {"risk_level": "critical", "enforcement_type": "block", "explanation": "high"}
    result = combine([("LOW-1", low), ("HIGH-1", high)], mode="first_fires")
    assert result["explanation"] == "low"
    assert result["risk_level"] == "low"


def test_first_fires_respects_caller_supplied_order_not_id_sort():
    """``combine`` trusts the list order it is given (catalog order) — it
    does not re-sort by rule id. Rule id ``Z-1`` listed first still wins
    over ``A-1`` listed second."""
    first = {"risk_level": "low", "enforcement_type": "warn", "explanation": "first"}
    second = {"risk_level": "low", "enforcement_type": "warn", "explanation": "second"}
    result = combine([("Z-1", first), ("A-1", second)], mode="first_fires")
    assert result["explanation"] == "first"


def test_first_fires_reports_every_triggered_rule_sorted():
    """``triggered_rules`` is sorted regardless of which rule's decision
    fields won — sorting is about deterministic reporting, not selection."""
    a = {"risk_level": "low", "enforcement_type": "warn"}
    b = {"risk_level": "critical", "enforcement_type": "block"}
    result = combine([("Z-1", a), ("A-1", b)], mode="first_fires")
    assert result["triggered_rules"] == ["A-1", "Z-1"]


# --- most_restrictive: severity wins, regardless of order --------------------


def test_most_restrictive_picks_the_more_severe_risk_level_regardless_of_order():
    low = {"risk_level": "low", "enforcement_type": "warn", "explanation": "low"}
    critical = {
        "risk_level": "critical",
        "enforcement_type": "block",
        "explanation": "critical",
    }
    result = combine([("LOW-1", low), ("CRIT-1", critical)], mode="most_restrictive")
    assert result["explanation"] == "critical"
    assert result["risk_level"] == "critical"

    # Order reversed: same winner.
    result_reversed = combine(
        [("CRIT-1", critical), ("LOW-1", low)], mode="most_restrictive"
    )
    assert result_reversed["explanation"] == "critical"


def test_most_restrictive_breaks_a_risk_level_tie_on_enforcement_type():
    """Two rules at the same risk_level but different enforcement severity:
    the more severe enforcement_type (per ENFORCEMENT_ORDER) wins the tie."""
    block = {
        "risk_level": "high",
        "enforcement_type": "block",
        "explanation": "block",
    }
    warn = {"risk_level": "high", "enforcement_type": "warn", "explanation": "warn"}
    result = combine([("WARN-1", warn), ("BLOCK-1", block)], mode="most_restrictive")
    assert result["explanation"] == "block"
    assert result["enforcement_type"] == "block"


def test_most_restrictive_fully_tied_decision_keeps_the_first_in_the_list():
    """Two rules tied on both risk_level and enforcement_type: the earlier
    one in *firing_decisions* order wins (severity_max returns ``a`` on a
    tie), a deterministic but otherwise arbitrary tie-break."""
    first = {"risk_level": "high", "enforcement_type": "block", "explanation": "first"}
    second = {"risk_level": "high", "enforcement_type": "block", "explanation": "second"}
    result = combine([("A-1", first), ("B-1", second)], mode="most_restrictive")
    assert result["explanation"] == "first"


def test_most_restrictive_reports_every_triggered_rule_sorted():
    a = {"risk_level": "low", "enforcement_type": "warn"}
    b = {"risk_level": "critical", "enforcement_type": "block"}
    result = combine([("Z-1", a), ("A-1", b)], mode="most_restrictive")
    assert result["triggered_rules"] == ["A-1", "Z-1"]


def test_most_restrictive_across_three_rules_picks_the_single_most_severe():
    low = {"risk_level": "low", "enforcement_type": "warn", "explanation": "low"}
    medium = {
        "risk_level": "medium",
        "enforcement_type": "notify",
        "explanation": "medium",
    }
    critical = {
        "risk_level": "critical",
        "enforcement_type": "block",
        "explanation": "critical",
    }
    result = combine(
        [("LOW-1", low), ("CRIT-1", critical), ("MED-1", medium)],
        mode="most_restrictive",
    )
    assert result["explanation"] == "critical"


# --- unranked values never crash the comparison ------------------------------


def test_most_restrictive_treats_an_unranked_risk_level_as_least_severe():
    """A risk_level not present in RISK_LEVEL_ORDER (e.g. a stray value from
    a data bug) ranks after every recognized value, mirroring
    ``severity_max``'s own unranked-last convention — it does not raise and
    does not win against a recognized value."""
    unranked = {
        "risk_level": "not_a_real_level",
        "enforcement_type": "warn",
        "explanation": "unranked",
    }
    recognized = {
        "risk_level": "low",
        "enforcement_type": "warn",
        "explanation": "recognized",
    }
    result = combine(
        [("UNRANKED-1", unranked), ("LOW-1", recognized)], mode="most_restrictive"
    )
    assert result["explanation"] == "recognized"


def test_most_restrictive_treats_a_none_risk_level_as_least_severe():
    """A rule_decision missing risk_level entirely (``.get`` returns
    ``None``) ranks least severe too — same unranked-last convention,
    exercised via the ``None`` case specifically since it's the shape a
    genuinely incomplete rule_decision produces."""
    missing = {"enforcement_type": "warn", "explanation": "missing"}
    recognized = {
        "risk_level": "low",
        "enforcement_type": "warn",
        "explanation": "recognized",
    }
    result = combine(
        [("MISSING-1", missing), ("LOW-1", recognized)], mode="most_restrictive"
    )
    assert result["explanation"] == "recognized"


def test_most_restrictive_both_axes_unranked_keeps_the_first():
    """Two decisions missing both risk_level and enforcement_type: neither
    axis can break the tie, so the first in the list wins — same
    deterministic first-wins tie-break as the fully-tied-ranked case."""
    first = {"explanation": "first"}
    second = {"explanation": "second"}
    result = combine([("A-1", first), ("B-1", second)], mode="most_restrictive")
    assert result["explanation"] == "first"


def test_most_restrictive_risk_level_tied_unranked_breaks_tie_on_enforcement():
    """Two decisions both missing risk_level (tied, unranked) but with
    different enforcement_type: the tie-break still runs on the
    enforcement axis, per ENFORCEMENT_ORDER — ``notify`` outranks ``warn``."""
    warn = {"enforcement_type": "warn", "explanation": "warn"}
    notify = {"enforcement_type": "notify", "explanation": "notify"}
    result = combine([("A-1", warn), ("B-1", notify)], mode="most_restrictive")
    assert result["explanation"] == "notify"
