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

**Tiers.** The policy is asked per tier of evidence on one snapshot
(:func:`labels.tiers`: classification; with inherited labels on, the labels known
inside the conversation; with the cross switch on too, the crossed labels), and the
record takes :func:`utils.strictest` of them: a later tier never relaxes an earlier
one, and a new snapshot asks every tier again, so a correction of the evidence may.
A lower tier the policy cannot answer never discards a known block (:func:`_decide`).
Each version gets one ``interaction_risk_tiers`` row in the same write: the tier that
decided it, and ``late`` when it is stricter than the latest earlier version that
evidenced the same legs (a finding after the same evidence could have been answered;
counted by ``late_findings_total``). The record itself is what it was before tiers whenever
the policy never relaxes on more evidence.

Concurrent recomputes of one interaction are serialized, not retried. Both
writes (the policy decision and the risk record) are a read → compare →
insert, and each runs under :data:`_LOCK_SQL`, a transaction-scoped advisory
lock keyed on the interaction: a second writer waits for the first to
commit, then reads what it committed — the cached decision under the same
evidence fingerprint, or a risk record identical to its own — and writes
nothing. Without the lock the two statements interleave: racers that insert
together collide on ``UNIQUE (interaction_id, version)``, and a racer that
read before the other committed but inserts after it writes an identical
duplicate version, which no retry can see because nothing failed.

The lock is what keeps the split path (issue #158) equivalent to the
self-contained one: a race is settled inside the interaction it concerns, so
the leg-ready observer never sees it and never holds the stream for the
unrelated legs queued behind it. Versions are still allocated in-statement
(``SELECT COALESCE(MAX(version), 0) + 1 ...``), so the unique index remains
the backstop against a writer that bypasses the lock — it fails loudly
instead of double-writing.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable

from data_governance import db
from data_governance.processors.leg_ready import metrics as leg_ready_metrics
from data_governance.risk.engine import labels, utils
from data_governance.risk.engine.evidence import gather_evidence
from data_governance.risk.engine.opa import OpaClient, OpaError
from data_governance.risk.rules.rego import FALLBACK_RULE_ID

__all__ = ["RecordWrite", "compute_interaction_risk", "prepare_early_block", "prepare_interaction_risk"]

# The write half prepare_interaction_risk returns: runs the idempotency-checked
# risk-record insert inside the transaction the CALLER owns (the leg-ready
# consumer's per-leg delivery transaction — issue #158).
RecordWrite = Callable[[db.Transaction], None]

# Serializes the writers of one interaction's risk state until the enclosing
# transaction ends (see the module docstring). Parameter: _lock_key(...).
_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"


def _lock_key(interaction_id: str) -> str:
    return f"risk:interaction:{interaction_id}"


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
    "opa_policy_versions_used, overall_confidence, trace_id, "
    "parent_interaction_id, caller_entity_id, callee_entity_id "
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

# The decision this version's evidence could have been answered with before it: the
# latest version that evidenced the same legs. A version that adds a leg (the response
# after the request) is a decision on new evidence, not a late finding.
_PREVIOUS_SAME_LEGS_DECISION_SQL = (
    "SELECT enforcement_type FROM interaction_risk_records "
    "WHERE interaction_id = %s AND legs_evidenced = %s "
    "ORDER BY version DESC LIMIT 1"
)

_INSERT_TIER_SQL = (
    "INSERT INTO interaction_risk_tiers (interaction_id, version, tier, late) VALUES (%s, %s, %s, %s)"
)

# An early block is only ever the first answer for the legs it evidences.
_ANY_SAME_LEGS_RECORD_SQL = (
    "SELECT 1 FROM interaction_risk_records WHERE interaction_id = %s AND legs_evidenced = %s LIMIT 1"
)


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


def _cached_decision(
    tx: db.Transaction, interaction_id: str, fingerprint: str
) -> utils.PolicyDecision | None:
    """The interaction's latest stored decision when it was made from the
    evidence *fingerprint* names, else ``None``."""
    row = tx.fetch_one(_LATEST_DECISION_SQL, (interaction_id,))
    if row is None:
        return None
    stored = _row_to_stored_decision(row)
    return stored.decision if stored.evidence_fingerprint == fingerprint else None


def _opa_input(evidence, inherited: labels.Inherited | None) -> dict:
    return utils.build_opa_input(
        legs=evidence.legs,
        span_ids=evidence.span_ids,
        classifications=evidence.classifications,
        caller_entity_id=evidence.caller_entity_id,
        callee_entity_id=evidence.callee_entity_id,
        anchor=evidence.anchor,
        inherited=inherited,
    )


def _ask(opa_client: OpaClient, interaction_id: str, opa_input: dict) -> utils.PolicyDecision:
    opa_decision = opa_client.evaluate(interaction_id=interaction_id, opa_input=opa_input)
    return utils.PolicyDecision(
        risk_level=opa_decision.risk_level,
        enforcement_type=opa_decision.enforcement_type,
        allowed_actions=opa_decision.allowed_actions,
        explanation=opa_decision.explanation,
        triggered_rules=opa_decision.triggered_rules,
        confidence=opa_decision.confidence,
        policy_version=opa_decision.policy_version,
    )


def _decide(*, interaction_id: str, opa_client: OpaClient, evidence) -> tuple[utils.PolicyDecision, int]:
    """The decision of one snapshot asked per tier of evidence (:func:`labels.tiers`)
    and the tier that decided it (:func:`utils.strictest`). The highest tier is the
    full evidence, decided as before through the cached decision
    (:func:`_get_or_refresh_decision`); a lower tier is asked only when its input
    differs from the tier below it and from the full evidence, and is not cached.
    With nothing inherited every tier's input is the same: one decision.

    A lower tier the policy cannot answer (:class:`~.opa.OpaError`) never discards a
    block already known: the full evidence's block stands, decided at the highest
    tier (what is known). Any other full decision is deferred with the error, as
    before: the missing tier could still be stricter."""
    views = labels.tiers(evidence.inherited)
    top = _get_or_refresh_decision(interaction_id=interaction_id, opa_client=opa_client, evidence=evidence)
    inputs = [_opa_input(evidence, view) for view in views]
    decisions: list[utils.PolicyDecision] = []
    try:
        for k, opa_input in enumerate(inputs[:-1]):
            if opa_input == inputs[-1]:
                decisions.append(top)
            elif k and opa_input == inputs[k - 1]:
                decisions.append(decisions[k - 1])
            else:
                decisions.append(_ask(opa_client, interaction_id, opa_input))
    except OpaError:
        if top.enforcement_type != "block":
            raise
        return top, len(views) - 1
    return utils.strictest(decisions + [top])


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
    insert re-checks the cache under the interaction lock first: a concurrent
    recompute that missed the cache at the same moment may have committed
    its decision meanwhile, and when it decided from the same evidence that
    decision is reused and this call's OPA answer discarded.

    The decision insert commits independently of any later risk-record
    write — deliberately: the decision is a cache keyed by evidence
    fingerprint, so if the record write later rolls back (leg re-delivered,
    ADR-0007), the committed decision is simply reused on the retry with no
    second OPA call.
    """
    fingerprint = utils.fingerprint(
        evidence.legs,
        evidence.classifications,
        evidence.anchor,
        evidence.inherited,
        caller_entity_id=evidence.caller_entity_id,
        callee_entity_id=evidence.callee_entity_id,
    )

    with db.transaction() as tx:
        cached = _cached_decision(tx, interaction_id, fingerprint)
    if cached is not None:
        return cached

    decision = _ask(opa_client, interaction_id, _opa_input(evidence, evidence.inherited))
    with db.transaction() as tx:
        tx.execute(_LOCK_SQL, (_lock_key(interaction_id),))
        cached = _cached_decision(tx, interaction_id, fingerprint)
        if cached is not None:
            return cached
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
        trace_id,
        parent_interaction_id,
        caller_entity_id,
        callee_entity_id,
    ) = row
    return {
        "trace_id": trace_id,
        "parent_interaction_id": parent_interaction_id,
        "caller_entity_id": caller_entity_id,
        "callee_entity_id": callee_entity_id,
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
    inherited: labels.Inherited | None = None,
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

    ``policy_event_count`` follows from the same list: a record carries the
    rules of the one decision it keeps (the full evidence's, cached, or a lower
    tier's, asked again on every computation when its input differs, never cached) — 1 when a
    catalog rule fired, 0 when only the fallback did (#222's definition: a
    policy event is an evaluation that returned at least one triggered rule).
    """
    legs_evidenced = utils.legs_evidenced(legs)
    classification_summary = utils.classification_summary(classifications, inherited)
    confidence = utils.quantize_confidence(decision.confidence)
    fired_rule_ids = [r for r in decision.triggered_rules if r != FALLBACK_RULE_ID]
    policy_event_count = 1 if fired_rule_ids else 0
    normalized = {
        "trace_id": trace_id,
        "parent_interaction_id": parent_interaction_id,
        "caller_entity_id": caller_entity_id,
        "callee_entity_id": callee_entity_id,
        "risk_level": decision.risk_level,
        "enforcement_type": decision.enforcement_type,
        "policy_event_count": policy_event_count,
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
        "policy_event_count": policy_event_count,
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
    at write time (FR-DAS-014), under the interaction lock so no concurrent
    writer can interleave: identical to the latest stored version ⇒ no
    insert, no NOTIFY. The lock is held until the caller's transaction ends.

    Raises whatever gathering or deciding raises —
    :class:`~.evidence.InteractionNotFoundError`, the typed
    :class:`~.opa.OpaError` family — the caller owns failure semantics
    (the leg-ready observer maps these onto hold/skip; see
    ``data_governance/risk/engine/observer.py``).
    """
    evidence = gather_evidence(interaction_id)

    decision, tier = _decide(interaction_id=interaction_id, opa_client=opa_client, evidence=evidence)

    params, normalized = _record_params(
        interaction_id=evidence.interaction_id,
        trace_id=evidence.trace_id,
        parent_interaction_id=evidence.parent_interaction_id,
        caller_entity_id=evidence.caller_entity_id,
        callee_entity_id=evidence.callee_entity_id,
        legs=evidence.legs,
        classifications=evidence.classifications,
        decision=decision,
        inherited=evidence.inherited,
    )

    def write(tx: db.Transaction) -> None:
        tx.execute(_LOCK_SQL, (_lock_key(interaction_id),))
        latest_row = tx.fetch_one(_LATEST_RISK_RECORD_SQL, (interaction_id,))
        if _normalized_latest_record(latest_row) == normalized:
            return
        previous = tx.fetch_one(_PREVIOUS_SAME_LEGS_DECISION_SQL, (interaction_id, params["legs_evidenced"]))
        (version,) = tx.fetch_one(_INSERT_RISK_RECORD_SQL + " RETURNING version", params)
        late = previous is not None and utils.stricter(decision.enforcement_type, previous[0])
        tx.execute(_INSERT_TIER_SQL, (interaction_id, version, tier, late))
        if late:
            leg_ready_metrics.late_findings_total.inc()

    return write


def prepare_early_block(interaction_id: str, *, opa_client: OpaClient) -> RecordWrite | None:
    """The early question, for an interaction a gate waits on whose label lineage is
    not ready: the decision on the evidence that is final, released only when it is a
    block. Returns the write of that block, or ``None`` when the policy does not block
    on this evidence (the full computation decides, once label lineage is ready).

    The evidence that is final is tier 0, the classification: a payload is classified
    once. Label rows that exist before label lineage is ready can still be corrected,
    so nothing inherited reaches the policy or the record (the evidence is gathered
    as ever; what is inherited in it is left out). A block on it stands whatever the later tiers find
    (:func:`utils.strictest`); any other decision could still be made stricter, so it
    is not written and the gate is not answered.

    The policy is asked once and the answer is not cached, as a lower tier's never
    is: the decision cache keeps the full evidence's answer only.

    The write inserts the record, with its tier row (tier 0, not late), only when no
    version evidences these legs yet, checked under the interaction lock in the
    caller's transaction: an early block is the first answer for its legs and never
    replaces a record of them, whatever was written between this computation and its
    write. The write returns whether it inserted. Raises what
    :func:`prepare_interaction_risk` raises."""
    evidence = gather_evidence(interaction_id)
    decision = _ask(opa_client, interaction_id, _opa_input(evidence, None))
    if decision.enforcement_type != "block":
        return None
    params, _ = _record_params(
        interaction_id=evidence.interaction_id,
        trace_id=evidence.trace_id,
        parent_interaction_id=evidence.parent_interaction_id,
        caller_entity_id=evidence.caller_entity_id,
        callee_entity_id=evidence.callee_entity_id,
        legs=evidence.legs,
        classifications=evidence.classifications,
        decision=decision,
        inherited=None,
    )

    def write(tx: db.Transaction) -> bool:
        tx.execute(_LOCK_SQL, (_lock_key(interaction_id),))
        if tx.fetch_one(_ANY_SAME_LEGS_RECORD_SQL, (interaction_id, params["legs_evidenced"])) is not None:
            return False
        (version,) = tx.fetch_one(_INSERT_RISK_RECORD_SQL + " RETURNING version", params)
        tx.execute(_INSERT_TIER_SQL, (interaction_id, version, 0, False))
        leg_ready_metrics.early_blocks_total.inc()
        return True

    return write


def compute_interaction_risk(interaction_id: str, *, opa_client: OpaClient) -> None:
    """Compute and persist the current risk record for *interaction_id* —
    the self-contained entry point (prepare + write in an own transaction).

    Idempotent: if the freshly-computed record is identical to the latest
    stored version, nothing is written and no NOTIFY fires — including when
    a concurrent recompute of the same interaction got there first (see the
    module docstring: writers are serialized, so the second one sees the
    first one's record and no-ops).
    """
    write = prepare_interaction_risk(interaction_id, opa_client=opa_client)
    with db.transaction() as tx:
        write(tx)
