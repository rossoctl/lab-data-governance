"""Pure interaction-risk aggregation functions (issue #101, PRD-SENTRY-001 v8 §6.2).

Everything here is a plain function over plain dataclasses — no Postgres, no
HTTP. ``evidence.py``/``opa.py``/``compute.py`` are the I/O shells that gather
:class:`LegEvidence`/:class:`PolicyDecision` and hand them to these functions;
keeping the semantics pure makes the corner-case matrix (severity max,
classification summary shapes, confidence quantization, fingerprint
stability) testable with no DB and no network.

Severity ranking reuses :data:`data_governance.risk.rules.catalog
.RISK_LEVEL_ORDER`/``ENFORCEMENT_ORDER`` rather than re-deriving an ordering —
same most-severe-first tuples, same "unranked values sort last, never raise"
convention (:func:`severity_max` mirrors the catalog's ``ranks.get(value,
len(order))`` fallback).
"""

from __future__ import annotations

import dataclasses
import json
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from data_governance.processors.classification.verdict import Verdict
from data_governance.risk.rules.catalog import ENFORCEMENT_ORDER, RISK_LEVEL_ORDER

__all__ = [
    "RISK_LEVEL_ORDER",
    "ENFORCEMENT_ORDER",
    "LegEvidence",
    "PolicyDecision",
    "PENDING",
    "NO_PAYLOAD",
    "severity_max",
    "legs_evidenced",
    "classification_summary",
    "quantize_confidence",
    "fingerprint",
]

# Sentinels for the two non-classified states a leg's classification slot can
# be in. Distinct from an absent dict key (leg doesn't exist at all) and from
# a real Verdict (leg is classified) — see classification_summary.
PENDING: Final = object()
NO_PAYLOAD: Final = object()

_LEG_ORDER: Final[tuple[str, ...]] = ("request", "response")


@dataclasses.dataclass(frozen=True)
class LegEvidence:
    """The minimal identity of one interaction leg needed by aggregation:
    which leg it is and whether it has a payload to classify."""

    leg_type: str
    payload_hash: str | None = None


@dataclasses.dataclass(frozen=True)
class PolicyDecision:
    """One OPA policy decision for an interaction (mirrors
    ``interaction_policy_decisions``' OPA-sourced columns)."""

    risk_level: str | None
    enforcement_type: str | None
    allowed_actions: list[str]
    explanation: str | None
    triggered_rules: list[str]
    confidence: float | None
    policy_version: str | None


def severity_max(a: Any, b: Any, *, order: tuple[str, ...]) -> Any:
    """The more severe of two values per *order* (most-severe-first).

    A value absent from *order* (including ``None``) ranks after every
    value present in *order* — mirroring
    :func:`data_governance.risk.rules.catalog._sort_key`'s
    ``ranks.get(value, len(order))`` fallback, so a novel or missing
    OPA-sourced value never raises here either. When both values are equally
    (un)ranked, returns *a*.
    """
    ranks = {value: rank for rank, value in enumerate(order)}
    rank_a = ranks.get(a, len(order))
    rank_b = ranks.get(b, len(order))
    return a if rank_a <= rank_b else b


def legs_evidenced(legs: list[LegEvidence]) -> list[str]:
    """Which leg types are present, in request-then-response order,
    regardless of the input order. No completeness inference — a leg that
    doesn't exist is simply absent from the result."""
    present = {leg.leg_type for leg in legs}
    return [leg_type for leg_type in _LEG_ORDER if leg_type in present]


def _verdict_summary(verdict: Verdict) -> dict[str, Any]:
    return {
        "sensitivity_level": verdict.sensitivity_level,
        "regulatory_tags": verdict.regulatory_tags,
        "contains_identity_bundle": verdict.contains_identity_bundle,
        "is_personalized": verdict.is_personalized,
        "primary_domain": verdict.primary_domain,
        "finding_count": len(verdict.findings),
        "model_version": verdict.model_version,
    }


def classification_summary(
    classifications: dict[str, Verdict | object],
) -> dict[str, dict[str, Any]]:
    """The ``classification_summary`` JSONB shape, per leg.

    *classifications* maps leg type -> one of:
      - a :class:`Verdict` (leg has a classified payload) -> full verdict fields.
      - :data:`PENDING` (leg has a payload, no classification row yet) ->
        ``{"classification_pending": True}``, no verdict fields.
      - :data:`NO_PAYLOAD` (leg exists but has no payload) -> ``{"payload": None}``.

    A leg absent from *classifications* is absent from the result entirely —
    never defaulted to "none" (explicit issue requirement).
    """
    summary: dict[str, dict[str, Any]] = {}
    for leg_type, value in classifications.items():
        if value is PENDING:
            summary[leg_type] = {"classification_pending": True}
        elif value is NO_PAYLOAD:
            summary[leg_type] = {"payload": None}
        else:
            summary[leg_type] = _verdict_summary(value)
    return summary


def quantize_confidence(value: float | None) -> Decimal | None:
    """Quantize a raw ``[0, 1]`` confidence to exactly 3 decimal places
    (``NUMERIC(4,3)``), rounding half up. ``None`` passes through unchanged
    (OPA omitted a confidence). Raises ``ValueError`` outside ``[0, 1]`` so
    the DB's ``NUMERIC(4,3)`` range check never rejects mid-transaction.
    """
    if value is None:
        return None
    if not 0 <= value <= 1:
        raise ValueError(f"confidence must be within [0, 1], got {value!r}")
    return Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def _normalize_for_fingerprint(
    legs: list[LegEvidence],
    classifications: dict[str, Verdict | object],
) -> dict[str, Any]:
    return {
        "legs_evidenced": legs_evidenced(legs),
        "classification_summary": classification_summary(classifications),
    }


def fingerprint(
    legs: list[LegEvidence],
    classifications: dict[str, Verdict | object],
) -> str:
    """A canonical fingerprint string over the evidence that feeds one
    interaction risk computation.

    Used to detect whether evidence actually changed since the last OPA call
    (FR-DAS-014 idempotency): compare this against the fingerprint stored
    alongside the latest policy decision, not raw field-by-field comparison
    (DB round-trips return ``Decimal``/``list``/``dict`` types that mismatch
    freshly-computed Python values).
    """
    normalized = _normalize_for_fingerprint(legs, classifications)
    return json.dumps(normalized, sort_keys=True, default=str)
