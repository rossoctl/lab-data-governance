"""Orchestration + write path for the interaction risk computation engine
(issue #101).

``compute_interaction_risk`` is the engine's one directly-callable entry
point (leg-ready-consumer wiring is deferred to issue #158):

    gather evidence -> reuse or refresh the OPA policy decision
    (``evidence_fingerprint`` comparison) -> aggregate -> write a new
    ``interaction_risk_records`` version, but only when the computed record
    actually differs from the latest stored one (FR-DAS-014 idempotency).
    A prior version is never mutated (FR-DAS-013/032 immutability).

Both writes (the policy decision and the risk record) allocate their next
``version`` in-statement (``SELECT COALESCE(MAX(version), 0) + 1 ...``)
rather than read-then-write, so two concurrent recomputes racing on the same
interaction collide on the table's ``UNIQUE (interaction_id, version)``
index rather than silently double-writing the same version. On that
collision this module retries once — re-gathering evidence and re-checking
idempotency, since the winner's write may make the retry's write a no-op.
"""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal

import psycopg

from data_governance import db
from data_governance.risk.engine import aggregate
from data_governance.risk.engine.evidence import gather_evidence
from data_governance.risk.engine.opa import OpaClient

__all__ = ["compute_interaction_risk"]

_MAX_ATTEMPTS = 2

_LATEST_DECISION_SQL = (
    "SELECT version, risk_level, enforcement_type, allowed_actions, "
    "explanation, triggered_rules, confidence, policy_version, "
    "evidence_fingerprint "
    "FROM interaction_policy_decisions WHERE interaction_id = %s "
    "ORDER BY version DESC LIMIT 1"
)

_INSERT_DECISION_SQL = """
INSERT INTO interaction_policy_decisions (
    interaction_id, version, evaluated_at, risk_level, enforcement_type,
    allowed_actions, explanation, triggered_rules, confidence,
    policy_version, evidence_fingerprint
)
SELECT %(interaction_id)s,
       COALESCE(MAX(version), 0) + 1,
       now(), %(risk_level)s, %(enforcement_type)s, %(allowed_actions)s,
       %(explanation)s, %(triggered_rules)s, %(confidence)s,
       %(policy_version)s, %(evidence_fingerprint)s
  FROM interaction_policy_decisions WHERE interaction_id = %(interaction_id)s
"""

_LATEST_RISK_RECORD_SQL = (
    "SELECT version, risk_level, enforcement_type, policy_event_count, "
    "triggered_rule_ids, legs_evidenced, classification_summary, "
    "opa_policy_versions_used, overall_confidence "
    "FROM interaction_risk_records WHERE interaction_id = %s "
    "ORDER BY version DESC LIMIT 1"
)

_INSERT_RISK_RECORD_SQL = """
INSERT INTO interaction_risk_records (
    interaction_id, trace_id, parent_interaction_id, caller_entity_id,
    callee_entity_id, version, computed_at, risk_level, enforcement_type,
    policy_event_count, triggered_rule_ids, legs_evidenced,
    classification_summary, opa_policy_versions_used, overall_confidence
)
SELECT %(interaction_id)s, %(trace_id)s, %(parent_interaction_id)s,
       %(caller_entity_id)s, %(callee_entity_id)s,
       COALESCE(MAX(version), 0) + 1,
       now(), %(risk_level)s, %(enforcement_type)s, %(policy_event_count)s,
       %(triggered_rule_ids)s, %(legs_evidenced)s,
       %(classification_summary)s, %(opa_policy_versions_used)s,
       %(overall_confidence)s
  FROM interaction_risk_records WHERE interaction_id = %(interaction_id)s
"""


@dataclasses.dataclass(frozen=True)
class _StoredDecision:
    version: int
    decision: aggregate.PolicyDecision
    evidence_fingerprint: str


def _row_to_stored_decision(row: tuple) -> _StoredDecision:
    (
        version,
        risk_level,
        enforcement_type,
        allowed_actions,
        explanation,
        triggered_rules,
        confidence,
        policy_version,
        evidence_fingerprint,
    ) = row
    return _StoredDecision(
        version=version,
        decision=aggregate.PolicyDecision(
            risk_level=risk_level,
            enforcement_type=enforcement_type,
            allowed_actions=list(allowed_actions or []),
            explanation=explanation,
            triggered_rules=list(triggered_rules or []),
            confidence=float(confidence) if confidence is not None else None,
            policy_version=policy_version,
        ),
        evidence_fingerprint=evidence_fingerprint,
    )


def _decision_params(
    *, interaction_id: str, decision: aggregate.PolicyDecision, evidence_fingerprint: str
) -> dict:
    return {
        "interaction_id": interaction_id,
        "risk_level": decision.risk_level,
        "enforcement_type": decision.enforcement_type,
        "allowed_actions": decision.allowed_actions,
        "explanation": decision.explanation,
        "triggered_rules": decision.triggered_rules,
        "confidence": (
            str(aggregate.quantize_confidence(decision.confidence))
            if decision.confidence is not None
            else None
        ),
        "policy_version": decision.policy_version,
        "evidence_fingerprint": evidence_fingerprint,
    }


def _get_or_refresh_decision(
    tx: db.Transaction,
    *,
    interaction_id: str,
    opa_client: OpaClient,
    legs: list[aggregate.LegEvidence],
    span_ids: list[str],
    classifications: dict,
    caller_entity_id: str | None,
    callee_entity_id: str | None,
) -> aggregate.PolicyDecision:
    """Reuse the cached decision when the evidence fingerprint is unchanged;
    otherwise call OPA and persist a new decision version."""
    fingerprint = aggregate.fingerprint(legs, classifications)

    row = tx.fetch_one(_LATEST_DECISION_SQL, (interaction_id,))
    if row is not None:
        stored = _row_to_stored_decision(row)
        if stored.evidence_fingerprint == fingerprint:
            return stored.decision

    opa_decision = opa_client.evaluate(
        interaction_id=interaction_id,
        span_ids=span_ids,
        caller_entity_id=caller_entity_id or "",
        callee_entity_id=callee_entity_id or "",
    )
    decision = aggregate.PolicyDecision(
        risk_level=opa_decision.risk_level,
        enforcement_type=opa_decision.enforcement_type,
        allowed_actions=opa_decision.allowed_actions,
        explanation=opa_decision.explanation,
        triggered_rules=opa_decision.triggered_rules,
        confidence=opa_decision.confidence,
        policy_version=opa_decision.policy_version,
    )
    tx.execute(
        _INSERT_DECISION_SQL,
        _decision_params(
            interaction_id=interaction_id,
            decision=decision,
            evidence_fingerprint=fingerprint,
        ),
    )
    return decision


def _normalized_latest_record(row: tuple | None) -> dict | None:
    if row is None:
        return None
    (
        _version,
        risk_level,
        enforcement_type,
        policy_event_count,
        triggered_rule_ids,
        legs_evidenced,
        classification_summary,
        opa_policy_versions_used,
        overall_confidence,
    ) = row
    return {
        "risk_level": risk_level,
        "enforcement_type": enforcement_type,
        "policy_event_count": policy_event_count,
        "triggered_rule_ids": sorted(triggered_rule_ids or []),
        "legs_evidenced": list(legs_evidenced or []),
        "classification_summary": classification_summary,
        "opa_policy_versions_used": sorted(opa_policy_versions_used or []),
        "overall_confidence": (
            str(overall_confidence) if overall_confidence is not None else None
        ),
    }


def _record_params(
    *,
    interaction_id: str,
    trace_id: str,
    parent_interaction_id: str | None,
    caller_entity_id: str | None,
    callee_entity_id: str | None,
    legs: list[aggregate.LegEvidence],
    classifications: dict,
    decision: aggregate.PolicyDecision,
) -> tuple[dict, dict]:
    """Build the params dict for both the insert and the idempotency
    comparison, returned together so callers can't drift them apart."""
    legs_evidenced = aggregate.legs_evidenced(legs)
    classification_summary = aggregate.classification_summary(classifications)
    confidence = aggregate.quantize_confidence(decision.confidence)
    normalized = {
        "risk_level": decision.risk_level,
        "enforcement_type": decision.enforcement_type,
        "policy_event_count": 1,
        "triggered_rule_ids": sorted(decision.triggered_rules),
        "legs_evidenced": legs_evidenced,
        "classification_summary": (
            json.loads(json.dumps(classification_summary, sort_keys=True, default=str))
        ),
        "opa_policy_versions_used": (
            sorted([decision.policy_version]) if decision.policy_version else []
        ),
        "overall_confidence": str(confidence) if confidence is not None else None,
    }
    params = {
        "interaction_id": interaction_id,
        "trace_id": trace_id,
        "parent_interaction_id": parent_interaction_id,
        "caller_entity_id": caller_entity_id,
        "callee_entity_id": callee_entity_id,
        "risk_level": decision.risk_level,
        "enforcement_type": decision.enforcement_type,
        "policy_event_count": 1,
        "triggered_rule_ids": decision.triggered_rules,
        "legs_evidenced": legs_evidenced,
        "classification_summary": json.dumps(classification_summary, default=str),
        "opa_policy_versions_used": (
            [decision.policy_version] if decision.policy_version else []
        ),
        "overall_confidence": str(confidence) if confidence is not None else None,
    }
    return params, normalized


def _attempt(interaction_id: str, opa_client: OpaClient) -> None:
    evidence = gather_evidence(interaction_id)

    with db.transaction() as tx:
        decision = _get_or_refresh_decision(
            tx,
            interaction_id=interaction_id,
            opa_client=opa_client,
            legs=evidence.legs,
            span_ids=evidence.span_ids,
            classifications=evidence.classifications,
            caller_entity_id=evidence.caller_entity_id,
            callee_entity_id=evidence.callee_entity_id,
        )

        params, normalized = _record_params(
            interaction_id=evidence.interaction_id,
            trace_id=evidence.trace_id,
            parent_interaction_id=None,
            caller_entity_id=evidence.caller_entity_id,
            callee_entity_id=evidence.callee_entity_id,
            legs=evidence.legs,
            classifications=evidence.classifications,
            decision=decision,
        )

        latest_row = tx.fetch_one(_LATEST_RISK_RECORD_SQL, (interaction_id,))
        if _normalized_latest_record(latest_row) == normalized:
            return

        tx.execute(_INSERT_RISK_RECORD_SQL, params)


def compute_interaction_risk(interaction_id: str, *, opa_client: OpaClient) -> None:
    """Compute and persist the current risk record for *interaction_id*.

    Idempotent: if the freshly-computed record is identical to the latest
    stored version, nothing is written and no NOTIFY fires. Retries once on
    a ``UNIQUE (interaction_id, version)`` collision from a racing concurrent
    recompute — the retry re-gathers evidence and re-checks idempotency, so a
    retry that lost the race to a winner whose write already matches simply
    becomes a no-op.
    """
    last_error: psycopg.errors.UniqueViolation | None = None
    for _attempt_number in range(_MAX_ATTEMPTS):
        try:
            _attempt(interaction_id, opa_client)
            return
        except psycopg.errors.UniqueViolation as exc:
            last_error = exc
            continue
    raise last_error
