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
    "AnchorFacts",
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
class AnchorFacts:
    """The wire facts of one interaction's anchor (request) span, as the
    sidecar producer emitted them (docs/sidecar-wire-contract.md) — raw
    attribute values, no interpretation. Every field is optional: a fact the
    span does not carry stays ``None`` and is omitted downstream (honest
    absence). An interaction whose anchor is not a sidecar span (or that has
    no anchor at all) is represented by ``None`` in place of this object.

    ``peer_host`` names the destination host: outbound it is the service
    being called; inbound it is the address this workload was reached on.
    ``self_id`` is the sidecar's own workload identity — for an inbound
    exchange, the destination workload. ``principal_sub`` is the validated
    inbound caller identity, when a JWT was validated.
    """

    direction: str | None = None  # lineage.direction: inbound | outbound
    peer_host: str | None = None  # lineage.peer.host (may carry :port)
    self_id: str | None = None  # lineage.self.id
    url_scheme: str | None = None  # url.scheme (emitted only when observed)
    url_path: str | None = None  # url.path
    principal_sub: str | None = None  # lineage.principal.sub


def _facts_dict(anchor: AnchorFacts) -> dict[str, str]:
    """The anchor's non-None facts as a plain dict — the canonical form both
    the fingerprint and this module's own emptiness checks share."""
    return {
        key: value
        for key, value in dataclasses.asdict(anchor).items()
        if value is not None
    }


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


_CATEGORY_TO_TRUST_LEVEL: Final[dict[str, str]] = {
    "external": "UNTRUSTED_EXTERNAL",
    "public": "UNTRUSTED_PUBLIC",
}


def _trust_level_for_category(category: str) -> str:
    """The trust level implied by *category* when none was explicitly
    provided: ``"external"`` -> ``UNTRUSTED_EXTERNAL``, ``"public"`` ->
    ``UNTRUSTED_PUBLIC``, anything else -> ``UNKNOWN``."""
    return _CATEGORY_TO_TRUST_LEVEL.get(category, "UNKNOWN")


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


def _category(host: str, internal_patterns: list[str]) -> str:
    """Categorise a destination host as ``internal`` or ``external``.

    A host with no dot at all is a cluster-DNS short name (``records-tool``,
    ``opa``, ``ollama``) and is internal by construction — such a name cannot
    resolve outside the cluster's search domains, so this is a structural
    fact rather than a whitelist question, and no glob pattern can express
    "has no dot" anyway. Every other host is put to #178's wildcard hostname
    whitelist: internal iff it matches a pattern, external otherwise
    (including when the whitelist is empty — #178's safer default, which is
    why a real deployment must set ``RISK_INTERNAL_URL_WHITELIST_PATTERNS``;
    see ``deploy/k8s/85-leg-ready.yaml``).
    """
    hostname = _hostname(host)
    if hostname and "." not in hostname:
        return "internal"
    return (
        "internal"
        if matches_internal_whitelist(hostname, patterns=internal_patterns)
        else "external"
    )


@dataclasses.dataclass(frozen=True)
class _Endpoint:
    """One end of an exchange, as far as the anchor facts name it."""

    name: str
    category: str
    url: str | None = None


def _endpoints(
    anchor: AnchorFacts, internal_patterns: list[str]
) -> tuple[_Endpoint | None, _Endpoint | None]:
    """The exchange's ``(caller, callee)``, from the anchor facts. An end the
    facts do not name is ``None`` and is omitted downstream (honest absence).

    Outbound, the caller is the sidecar's own workload and the callee is the
    peer host it called, categorised by :func:`_category`. Inbound, the
    callee is the sidecar's own workload and the caller is not named: inbound
    ``peer.host`` is the address the workload was REACHED on, not where the
    call came from.

    The sidecar'd workload itself is in-cluster by construction — a
    structural fact, not a whitelist question. (The whitelist would misread
    it: the reached-on address is often a raw ClusterIP, which no hostname
    pattern can recognise — observed live as DG-001 false-positives on
    ordinary in-cluster a2a calls.) A full URL is composed only when the
    producer emitted a scheme (the contract's own no-guessing rule).
    """
    url = None
    if anchor.url_scheme and anchor.peer_host:
        url = f"{anchor.url_scheme}://{anchor.peer_host}{anchor.url_path or ''}"

    if anchor.direction == "outbound":
        caller = _Endpoint(anchor.self_id, "internal") if anchor.self_id else None
        callee = (
            _Endpoint(anchor.peer_host, _category(anchor.peer_host, internal_patterns), url)
            if anchor.peer_host
            else None
        )
        return caller, callee

    name = anchor.self_id or anchor.peer_host
    if name is None:
        return None, None
    category = (
        "internal"
        if anchor.direction == "inbound"
        else _category(anchor.peer_host or name, internal_patterns)
    )
    return None, _Endpoint(name, category, url)


def _data_source(endpoint: _Endpoint) -> dict[str, Any]:
    source: dict[str, Any] = {
        "data_source_name": endpoint.name,
        "data_source_categories": [endpoint.category],
    }
    if endpoint.url is not None:
        source["data_source_url"] = endpoint.url
    return source


def _data_destination(endpoint: _Endpoint) -> dict[str, Any]:
    """A flow's ``data_destinations`` entry. The trust level is derived from
    the category via :func:`_trust_level_for_category` (``external`` ->
    ``UNTRUSTED_EXTERNAL``, anything else — including ``internal``, which the
    MVP whitelist never distinguishes further — -> ``UNKNOWN``, which the
    policy's ``trust_level_category`` mapping groups with the untrusted
    levels rather than guessing a ``TRUSTED_*`` value)."""
    destination: dict[str, Any] = {
        "data_destination_name": endpoint.name,
        "data_destination_categories": [endpoint.category],
    }
    if endpoint.url is not None:
        destination["data_destination_url"] = endpoint.url
    destination["data_destination_trust_level"] = _trust_level_for_category(endpoint.category)
    return destination


def _event_type(source: _Endpoint | None, destination: _Endpoint | None) -> str | None:
    """What kind of event a flow is, from where its data goes and comes from.
    ``None`` (omitted) when the destination is not known."""
    if destination is None:
        return None
    if destination.category == "external":
        return "external_sharing"
    if source is not None and source.category == "external":
        return "data_import"
    return "internal_sharing"


def build_opa_input(
    *,
    legs: list[LegEvidence],
    span_ids: list[str],
    classifications: dict[str, Verdict | object],
    caller_entity_id: str | None,
    callee_entity_id: str | None,
    anchor: AnchorFacts | None = None,
    internal_patterns: list[str] | None = None,
) -> dict[str, Any]:
    """Build the OPA runtime evaluation input for one interaction, conforming
    to ``opa_input.schema.json`` (which shares ``policy.schema.json``'s
    ``$defs`` so the two stay in sync).

    The input is ``{"flows": {"request": {...}, "response": {...}}}``: one
    flow per leg, keyed by leg, because the two legs of an exchange carry data
    in opposite directions (#271). The request flow carries the request's
    data from the caller to the callee; the response flow carries the
    response's data from the callee back to the caller. Each
    flow states its own ``data_items``, ``data_sources``,
    ``data_destinations``, ``event_type`` and ``requested_actions``
    (``send`` / ``receive``), so a rule that reads "this data, to that
    destination" is matched against data that actually travelled there.
    A leg that is not evidenced has no flow. The key lets a policy address a
    leg by name (``input.flows.response``) when it needs to relate the two.

    *anchor* carries the interaction's request-span wire facts (issue #163);
    :func:`_endpoints` reads the exchange's caller and callee from it.
    ``event_type`` comes from :func:`_event_type`, and ``accessing_user`` is
    the validated inbound principal (``user_roles`` is ``[]`` because the
    schema requires the key and no roles fact exists — an empty list reads as
    "no roles known", which is the honest value). ``accessing_user`` and
    ``processing_agents`` describe the interaction, so every flow carries
    them.

    Category comes from #178's wildcard hostname whitelist
    (:func:`matches_internal_whitelist` over *internal_patterns*, defaulting
    to the configured ``RISK_INTERNAL_URL_WHITELIST_PATTERNS``); Rego never
    sees a URL, only the computed category. **MVP-only**, per #178: a single
    wildcard-hostname whitelist collapses every endpoint to one of two
    categories, and this will be treated more holistically (richer
    categories, trust levels, per-destination config) in a future version.

    Every field this module has no source data for (``data_lineage``,
    ``scope``, the five intent strings — and each of the above when its
    facts are absent) is omitted entirely — the schema requires nothing, and
    an omitted field reads honestly as "unknown" where a defaulted-null or
    empty value would read as a confident (but wrong) declaration to a policy
    author. ``span_ids`` has no corresponding field in the schema; it
    identifies the OTEL evidence behind this payload but carries no
    rule-relevant content of its own.
    """
    if internal_patterns is None:
        internal_patterns = INTERNAL_URL_WHITELIST_PATTERNS
    caller, callee = (
        _endpoints(anchor, internal_patterns) if anchor is not None else (None, None)
    )

    interaction: dict[str, Any] = {}
    processing_agents = [
        {"agent_name": entity_id}
        for entity_id in (caller_entity_id, callee_entity_id)
        if entity_id is not None
    ]
    if processing_agents:
        interaction["processing_agents"] = processing_agents
    if anchor is not None and anchor.principal_sub is not None:
        interaction["accessing_user"] = {
            "username": anchor.principal_sub,
            "user_roles": [],
        }

    present = {*legs_evidenced(legs), *classifications}
    flows: dict[str, Any] = {}
    for leg_type in _LEG_ORDER:
        if leg_type not in present:
            continue
        source, destination = (caller, callee) if leg_type == "request" else (callee, caller)
        verdict = classifications.get(leg_type)
        flow: dict[str, Any] = {
            "data_items": (
                [_verdict_to_data_item(verdict)] if isinstance(verdict, Verdict) else []
            ),
            "requested_actions": [_LEG_TYPE_TO_ACTION[leg_type]],
        }
        if source is not None:
            flow["data_sources"] = [_data_source(source)]
        if destination is not None:
            flow["data_destinations"] = [_data_destination(destination)]
        event_type = _event_type(source, destination)
        if event_type is not None:
            flow["event_type"] = event_type
        flows[leg_type] = {**flow, **interaction}
    return {"flows": flows}


def _normalize_for_fingerprint(
    legs: list[LegEvidence],
    classifications: dict[str, Verdict | object],
    anchor: AnchorFacts | None = None,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "legs_evidenced": legs_evidenced(legs),
        "classification_summary": classification_summary(classifications),
    }
    # The anchor's RAW facts join the fingerprint (issue #163): a changed
    # destination/principal must re-trigger OPA, or the cached decision would
    # keep answering for evidence it never saw. Raw facts, not the derived
    # categories — the whitelist is configuration, not evidence, and a config
    # change re-evaluating every cached decision is the same
    # policy-changed-with-unchanged-evidence gap PR #160 already records as
    # deferred (FR-DAS-012 condition 3). Keyed only when any fact exists, so
    # pre-existing fingerprints of anchor-less evidence stay valid.
    if anchor is not None:
        facts = _facts_dict(anchor)
        if facts:
            normalized["anchor_facts"] = facts
    return normalized


def fingerprint(
    legs: list[LegEvidence],
    classifications: dict[str, Verdict | object],
    anchor: AnchorFacts | None = None,
) -> str:
    """A canonical fingerprint string over the evidence that feeds one
    interaction risk computation — the legs, their classifications, and the
    anchor span's wire facts (issue #163).

    Used to detect whether evidence actually changed since the last OPA call
    (FR-DAS-014 idempotency): compare this against the fingerprint stored
    alongside the latest policy decision, not raw field-by-field comparison
    (DB round-trips return ``Decimal``/``list``/``dict`` types that mismatch
    freshly-computed Python values).
    """
    normalized = _normalize_for_fingerprint(legs, classifications, anchor)
    return json.dumps(normalized, sort_keys=True, default=str)
