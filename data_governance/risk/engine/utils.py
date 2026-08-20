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
import fnmatch
import json
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final
from urllib.parse import urlsplit

from data_governance.processors.classification.verdict import Verdict
from data_governance.risk.config import INTERNAL_URL_WHITELIST_PATTERNS
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
    "matches_internal_whitelist",
    "fingerprint",
    "build_opa_input",
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


# leg_type -> the closest opa_input.schema.json actionTypeValues member. A
# leg's temporal half is directed (a request is sent, a response is
# received), so this maps one-to-one rather than needing a lookup table keyed
# on anything richer.
_LEG_TYPE_TO_ACTION: Final[dict[str, str]] = {"request": "send", "response": "receive"}

# Finding.identifier_type -> opa_input.schema.json's dataTypeValues. DAS's
# classifier (logic.py's _base_classification) emits a third value, "NON_ID",
# for a tag the model detected but the entity-metadata CSV doesn't recognize —
# the schema's dataTypeValues enum has no member for that (only
# PID/OPID/DATA), so it is deliberately left unmapped: omitting data_type is
# honest here, sending an invalid enum value is not.
_IDENTIFIER_TYPE_TO_DATA_TYPE: Final[dict[str, str]] = {
    "PID": "PID",
    "OPID": "OPID",
    "DATA": "DATA",
}


def _hostname(url: str) -> str:
    """Best-effort hostname extraction, tolerating a URL with no scheme
    (``urlsplit`` parses a bare ``host/path`` string's leading segment as
    ``path``, not ``netloc``, unless a scheme-like prefix is present)."""
    parsed = urlsplit(url if "//" in url else f"//{url}")
    return (parsed.hostname or "").lower()


def matches_internal_whitelist(url: str, *, patterns: list[str]) -> bool:
    """Whether *url*'s hostname matches any wildcard hostname *pattern*
    (e.g. ``"*.corp.internal"``), case-insensitively.

    MVP-only: plain ``fnmatch`` glob matching against the hostname, no
    awareness of scheme/port/path, IP literals, or normalization beyond
    lowercasing. An empty *patterns* list never matches anything — the
    caller's default-external behaviour, not a special case here. This will
    be replaced by more holistic destination classification in a future
    version (see ``build_opa_input``'s docstring).
    """
    hostname = _hostname(url)
    return any(fnmatch.fnmatch(hostname, pattern.lower()) for pattern in patterns)


def _finding_to_entity(finding: dict[str, Any]) -> dict[str, Any]:
    entity = {
        "entity_type": finding.get("entity_type"),
        "start": finding.get("start"),
        "end": finding.get("end"),
        "text": finding.get("text"),
        "domain": finding.get("domain"),
        "category": finding.get("category"),
        "regulatory_tags": finding.get("regulatory_tags", []),
        "data_type": _IDENTIFIER_TYPE_TO_DATA_TYPE.get(finding.get("identifier_type")),
        "classification_level": finding.get("sensitivity_level"),
    }
    return {key: value for key, value in entity.items() if value is not None}


def _verdict_to_data_item(verdict: Verdict) -> dict[str, Any]:
    item: dict[str, Any] = {
        "entities": [_finding_to_entity(f) for f in verdict.findings],
        "list_of_entities": [f.get("entity_type") for f in verdict.findings],
        "classification_level": verdict.sensitivity_level,
        "regulatory_tags": verdict.regulatory_tags,
    }
    if verdict.primary_domain is not None:
        item["primary_domain"] = verdict.primary_domain
        item["list_of_domains"] = [verdict.primary_domain]
    if verdict.contains_identity_bundle:
        item["identity_bundles"] = ["identity_bundle"]
    return item


def build_opa_input(
    *,
    legs: list[LegEvidence],
    span_ids: list[str],
    classifications: dict[str, Verdict | object],
    caller_entity_id: str | None,
    callee_entity_id: str | None,
    destination_url: str | None = None,
) -> dict[str, Any]:
    """Build the OPA runtime evaluation input for one interaction, conforming
    to ``opa_input.schema.json`` (which shares ``policy.schema.json``'s
    ``$defs`` so the two stay in sync).

    Every field this module has no DAS source data for yet (``event_type``,
    ``data_sources``, ``data_lineage``, ``scope``, the five intent strings,
    ``accessing_user``) is omitted entirely — the schema requires nothing,
    and an omitted field reads honestly as "unknown" where a defaulted-null
    or empty value would read as a confident (but wrong) declaration to a
    policy author. ``span_ids`` has no corresponding top-level field in the
    schema; it identifies the OTEL evidence behind this payload but carries
    no rule-relevant content of its own.

    ``destination_url``, once issue #163 wires it in from the interaction
    record, is classified against :data:`data_governance.risk.config
    .INTERNAL_URL_WHITELIST_PATTERNS` (wildcard hostname patterns, e.g.
    ``"*.corp.internal"``) via :func:`matches_internal_whitelist` and emitted
    as ``data_destinations[0].data_destination_categories`` — ``["internal"]``
    on a match, ``["external"]`` otherwise (including when the whitelist is
    empty, the default). Rego never sees a URL, only this already-computed
    category. **MVP-only**: a single wildcard-hostname whitelist collapses
    every destination to one of two categories; this will be treated more
    holistically (richer categories, trust levels, per-destination config)
    in a future version. ``destination_url`` omitted (``None``, the default
    until #163 lands) means no ``data_destinations`` key at all, matching
    this function's existing "absent means unknown" convention.
    """
    data_items = [
        _verdict_to_data_item(verdict)
        for verdict in classifications.values()
        if isinstance(verdict, Verdict)
    ]
    payload: dict[str, Any] = {
        "data_items": data_items,
        "requested_actions": [
            _LEG_TYPE_TO_ACTION[leg_type] for leg_type in legs_evidenced(legs)
        ],
    }
    processing_agents = [
        {"agent_name": entity_id}
        for entity_id in (caller_entity_id, callee_entity_id)
        if entity_id is not None
    ]
    if processing_agents:
        payload["processing_agents"] = processing_agents
    if destination_url is not None:
        category = (
            "internal"
            if matches_internal_whitelist(
                destination_url, patterns=INTERNAL_URL_WHITELIST_PATTERNS
            )
            else "external"
        )
        payload["data_destinations"] = [{"data_destination_categories": [category]}]
    return payload


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
