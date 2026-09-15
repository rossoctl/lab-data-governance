"""Orchestration + write path for the interaction risk computation engine
(issues #101/#158).

The engine is split at the transaction boundary (issue #158):
``prepare_interaction_risk`` is the compute half — gather evidence, reuse or
refresh the OPA policy decision (``evidence_fingerprint`` comparison), with
NO transaction held across the OPA HTTP round-trip — returning the write
half as a closure that runs inside the CALLER's transaction. The leg-ready
consumer's observer drives that pair per ready leg, so the record write
commits atomically with the stream's cursor advance.
``compute_interaction_risk`` remains the self-contained entry point
(prepare + write in an own transaction):

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
from collections.abc import Callable
from decimal import Decimal

import psycopg

from data_governance import db
from data_governance.risk.engine import utils
from data_governance.risk.engine.evidence import gather_evidence
from data_governance.risk.engine.opa import OpaClient
from data_governance.risk.rules.rego import FALLBACK_RULE_ID

__all__ = ["RecordWrite", "compute_interaction_risk", "prepare_interaction_risk"]

# The write half prepare_interaction_risk returns: runs the idempotency-checked
# risk-record insert inside the transaction the CALLER owns (the leg-ready
# consumer's per-leg delivery transaction — issue #158).
RecordWrite = Callable[[db.Transaction], None]

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
    decision: utils.PolicyDecision
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
        decision=utils.PolicyDecision(
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
    *, interaction_id: str, decision: utils.PolicyDecision, evidence_fingerprint: str
) -> dict:
    return {
        "interaction_id": interaction_id,
        "risk_level": decision.risk_level,
        "enforcement_type": decision.enforcement_type,
        "allowed_actions": decision.allowed_actions,
        "explanation": decision.explanation,
        "triggered_rules": decision.triggered_rules,
        "confidence": (
            str(utils.quantize_confidence(decision.confidence))
            if decision.confidence is not None
            else None
        ),
        "policy_version": decision.policy_version,
        "evidence_fingerprint": evidence_fingerprint,
    }


def _get_or_refresh_decision(
    *,
    interaction_id: str,
    opa_client: OpaClient,
    evidence,
) -> utils.PolicyDecision:
    """Reuse the cached decision when the evidence fingerprint is unchanged;
    otherwise call OPA and persist a new decision version.

    No transaction is held across the OPA HTTP round-trip (issue #158): the
    cache lookup and the decision insert each run in their own short
    transaction, with the network call in between holding nothing. The
    decision insert commits independently of any later risk-record write —
    deliberately: the decision is a cache keyed by evidence fingerprint, so
    if the record write later rolls back (leg re-delivered, ADR-0007), the
    committed decision is simply reused on the retry with no second OPA
    call.
    """
    fingerprint = utils.fingerprint(
        evidence.legs, evidence.classifications, evidence.anchor
    )

    with db.transaction() as tx:
        row = tx.fetch_one(_LATEST_DECISION_SQL, (interaction_id,))
    if row is not None:
        stored = _row_to_stored_decision(row)
        if stored.evidence_fingerprint == fingerprint:
            return stored.decision

    opa_input = utils.build_opa_input(
        legs=evidence.legs,
        span_ids=evidence.span_ids,
        classifications=evidence.classifications,
        caller_entity_id=evidence.caller_entity_id,
        callee_entity_id=evidence.callee_entity_id,
        anchor=evidence.anchor,
    )
    opa_decision = opa_client.evaluate(
        interaction_id=interaction_id,
        opa_input=opa_input,
    )
    decision = utils.PolicyDecision(
        risk_level=opa_decision.risk_level,
        enforcement_type=opa_decision.enforcement_type,
        allowed_actions=opa_decision.allowed_actions,
        explanation=opa_decision.explanation,
        triggered_rules=opa_decision.triggered_rules,
        confidence=opa_decision.confidence,
        policy_version=opa_decision.policy_version,
    )
    with db.transaction() as tx:
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
    legs: list[utils.LegEvidence],
    classifications: dict,
    decision: utils.PolicyDecision,
) -> tuple[dict, dict]:
    """Build the params dict for both the insert and the idempotency
    comparison, returned together so callers can't drift them apart.

    ``triggered_rule_ids`` carries catalog rules only: the compiled bundle's
    fallback decision reports :data:`FALLBACK_RULE_ID` in ``triggered_rules``
    to say "nothing fired", and storing that sentinel would make every clean
    interaction count as a rule firing downstream (the metrics rules-fired /
    top-rules queries unnest this column; alerts name rules from it). The
    decision cache (``interaction_policy_decisions``) keeps OPA's answer
    verbatim — this is the one place the sentinel is interpreted.
    """
    legs_evidenced = utils.legs_evidenced(legs)
    classification_summary = utils.classification_summary(classifications)
    confidence = utils.quantize_confidence(decision.confidence)
    fired_rule_ids = [r for r in decision.triggered_rules if r != FALLBACK_RULE_ID]
    normalized = {
        "risk_level": decision.risk_level,
        "enforcement_type": decision.enforcement_type,
        "policy_event_count": 1,
        "triggered_rule_ids": sorted(fired_rule_ids),
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
        "triggered_rule_ids": fired_rule_ids,
        "legs_evidenced": legs_evidenced,
        "classification_summary": json.dumps(classification_summary, default=str),
        "opa_policy_versions_used": (
            [decision.policy_version] if decision.policy_version else []
        ),
        "overall_confidence": str(confidence) if confidence is not None else None,
    }
    return params, normalized


def prepare_interaction_risk(
    interaction_id: str, *, opa_client: OpaClient
) -> RecordWrite:
    """The compute half of the engine, run with NO transaction held across
    the OPA round-trip (issue #158): gather evidence, reuse or refresh the
    policy decision (fingerprint comparison; the refreshed decision commits
    in its own short transaction — see :func:`_get_or_refresh_decision`),
    and return the *write* half as a closure.

    The returned closure runs inside the CALLER's transaction — the
    leg-ready consumer passes its per-leg delivery transaction, so the risk
    record insert commits atomically with the stream's cursor advance
    (ADR-0007: a write failure rolls the cursor back and the leg is
    re-delivered, never silently skipped). The closure re-checks idempotency
    at write time (FR-DAS-014): identical to the latest stored version ⇒ no
    insert, no NOTIFY.

    Raises whatever gathering or deciding raises —
    :class:`~.evidence.InteractionNotFoundError`, the typed
    :class:`~.opa.OpaError` family — the caller owns failure semantics
    (the leg-ready observer maps these onto hold/skip; see
    ``data_governance/risk/engine/observer.py``).
    """
    evidence = gather_evidence(interaction_id)

    decision = _get_or_refresh_decision(
        interaction_id=interaction_id,
        opa_client=opa_client,
        evidence=evidence,
    )

    params, normalized = _record_params(
        interaction_id=evidence.interaction_id,
        trace_id=evidence.trace_id,
        parent_interaction_id=evidence.parent_interaction_id,
        caller_entity_id=evidence.caller_entity_id,
        callee_entity_id=evidence.callee_entity_id,
        legs=evidence.legs,
        classifications=evidence.classifications,
        decision=decision,
    )

    def write(tx: db.Transaction) -> None:
        latest_row = tx.fetch_one(_LATEST_RISK_RECORD_SQL, (interaction_id,))
        if _normalized_latest_record(latest_row) == normalized:
            return
        tx.execute(_INSERT_RISK_RECORD_SQL, params)

    return write


def compute_interaction_risk(interaction_id: str, *, opa_client: OpaClient) -> None:
    """Compute and persist the current risk record for *interaction_id* —
    the self-contained entry point (prepare + write in an own transaction).

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
            write = prepare_interaction_risk(interaction_id, opa_client=opa_client)
            with db.transaction() as tx:
                write(tx)
            return
        except psycopg.errors.UniqueViolation as exc:
            last_error = exc
            continue
    raise last_error
