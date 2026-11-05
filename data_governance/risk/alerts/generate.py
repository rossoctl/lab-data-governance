"""Alert generation & supersession from trace risk records (issue #104).

One entry point, :func:`generate_for_trace_risk_record`: given a durable
``trace_risk_records`` row (by id), decide — inside the caller's
transaction — whether it opens a new alert, counts as a duplicate of the
trace's open alert, or supersedes it. The caller is the alerts consumer
(:mod:`data_governance.processors.risk.alerts`), which drains the trace risk
stream by ``seq`` (migration 0023, #175) and passes its per-item
transaction, so the alert write commits atomically with the cursor advance.

Rules (FR-DAS-040..043, MVP scope):

- **Floor** — only ``low``/``medium``/``high``/``critical`` at or above
  ``RISK_ALERT_MIN_RISK_LEVEL`` alert; ``none``/``unknown`` never do.
- **First alert for a trace** — create it, ``status='new'`` (the PRD §6.4
  vocabulary #110's status endpoint walks), ``alert_type='standard'``.
- **Recomputation, same or lower level** than the trace's open alert — no
  new alert; ``duplicate_count += 1`` on the open one.
- **Recomputation, higher level** — new alert referencing the new record;
  the prior alert's ``superseded_by`` is set to it. Its ``status`` (and
  everything else) is untouched: nothing is ever deleted or invalidated.
- **Prior alert closed** (``resolved``/``false_positive``) — it is no longer
  the open alert for the trace, so a recurrence opens a fresh, unlinked
  alert.

"The open alert for a trace" is the newest ``standard`` alert of that trace
not yet superseded. The issue matches it by #105's ``dedup_key``; #105 is
out of MVP scope, so the trace id is the match and ``dedup_key`` stays
NULL.

Title/description (FR-DAS-042, ``standard``): title is
``"<rule_name> — <entity_1>, <entity_2>, ..."`` with the rule name of the
most severe triggered rule (catalog risk level, ties by rule id) and the
display names of the entities on the trace's *risky* interactions — those
whose interaction risk record, among the ones this trace risk record
aggregated, fired a rule — read from the current ``interactions`` row, like
``involved_entities``, so the title never names an entity its own alert
does not list (a record keeps the callee it was computed with after a
re-key, #279). Naming every entity of the trace made the title the whole
trace's cast (17 names on a travel turn); the full set stays in
``involved_entities``, read live from ``entities`` via the trace's current
``interactions`` (FR-DAS-004). Description concatenates every triggered
rule's catalog ``explanation`` in descending risk-level order, joined by
``"; "``. A rule id absent from the catalog is named by its id and
contributes no explanation. The catalog is the deployed one
(``RISK_RULES_SOURCE``, the ``opa-policy`` ConfigMap; see
``deploy/k8s/92-risk-alerts.yaml``).

Every decision for a trace runs under a transaction-scoped advisory lock on
that trace (the ``trace_compute`` discipline), so the "already alerted?"
and "open alert?" reads cannot race a second writer. A record already
referenced by an alert (it created or superseded one) is skipped, so a
second delivery of it never makes a second alert. A record whose outcome
was ``duplicate`` leaves no such reference: with the single consumer the
cursor advance commits with the count, so it is never delivered twice, but
an involuntary overlap of two consumer pods can count it twice.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Literal

from data_governance import db
from data_governance.risk import config
from data_governance.risk.rules import catalog
from data_governance.risk.rules.catalog import RISK_LEVEL_ORDER

__all__ = [
    "ALERTABLE_LEVELS",
    "AlertOutcome",
    "generate_for_trace_risk_record",
    "meets_floor",
    "validate_config",
]

ALERT_TYPE_STANDARD = "standard"

# FR-DAS-040: only these levels generate alerts, whatever the floor.
ALERTABLE_LEVELS: frozenset[str] = frozenset({"low", "medium", "high", "critical"})

# Statuses that end an alert's workflow (#110 sets them): an alert in one of
# these is no longer "the alert currently open for that trace".
TERMINAL_STATUSES: frozenset[str] = frozenset({"resolved", "false_positive"})

Action = Literal["created", "duplicate", "superseded", "skipped"]


@dataclasses.dataclass(frozen=True)
class AlertOutcome:
    """What one trace risk record did to the alerts table. ``alert_id`` is
    the alert created (``created``/``superseded``) or the one whose
    ``duplicate_count`` grew (``duplicate``); ``superseded_alert_id`` is the
    prior alert a ``superseded`` outcome linked. ``reason`` explains a
    ``skipped``."""

    action: Action
    alert_id: str | None = None
    superseded_alert_id: str | None = None
    reason: str | None = None


@dataclasses.dataclass(frozen=True)
class _TraceRisk:
    trace_risk_id: str
    trace_id: str
    risk_level: str
    enforcement_type: str | None
    triggered_rule_ids: list[str]
    contributing_interaction_risk_ids: list[str]


_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"

_TRACE_RISK_SQL = (
    "SELECT trace_risk_id, trace_id, trace_risk_level, trace_enforcement_type, "
    "triggered_rule_ids, contributing_interaction_risk_ids "
    "FROM trace_risk_records WHERE trace_risk_id = %s"
)

_ALREADY_ALERTED_SQL = (
    "SELECT alert_id FROM alerts WHERE trace_id = %s AND trace_risk_record_id = %s LIMIT 1"
)

# Newest by the stream order of the record that produced it (monotonic), not
# by timestamp (a tie or clock step would pick at random). FOR UPDATE: a
# status change committed by the REST API (#110) between our read and our
# write is seen, never overwritten by a decision made on a stale status.
_OPEN_ALERT_SQL = """
SELECT a.alert_id, a.risk_level, a.status
FROM alerts a
LEFT JOIN trace_risk_records r ON r.trace_risk_id = a.trace_risk_record_id
WHERE a.trace_id = %s AND a.alert_type = %s AND a.superseded_by IS NULL
ORDER BY r.seq DESC NULLS LAST, a."timestamp" DESC, a.alert_id DESC
LIMIT 1
FOR UPDATE OF a
"""

_INVOLVED_ENTITIES_SQL = """
SELECT DISTINCT e.id, e.kind::text, e.display_name
FROM interactions i
JOIN entities e ON e.id IN (i.caller_entity_id, i.callee_entity_id)
WHERE i.trace_id = %s
ORDER BY e.display_name, e.id
"""

# The entities on the interactions that fired a rule, among the interaction
# risk records this trace risk record aggregated, named from the current
# interactions row (as involved_entities is), not the record's own caller /
# callee, which can be stale after a re-key (#279).
_RISKY_ENTITY_NAMES_SQL = """
SELECT DISTINCT e.display_name
FROM interaction_risk_records r
JOIN interactions i ON i.id = r.interaction_id
JOIN entities e ON e.id IN (i.caller_entity_id, i.callee_entity_id)
WHERE r.interaction_risk_id = ANY(%s::uuid[]) AND cardinality(r.triggered_rule_ids) > 0
ORDER BY e.display_name
"""

_INSERT_ALERT_SQL = """
INSERT INTO alerts (
    "timestamp", trace_id, trace_risk_record_id, risk_level, enforcement_type,
    title, description, involved_entities, triggered_rule_ids,
    status, alert_type, duplicate_count
) VALUES (
    now(), %(trace_id)s, %(trace_risk_record_id)s, %(risk_level)s,
    %(enforcement_type)s, %(title)s, %(description)s, %(involved_entities)s,
    %(triggered_rule_ids)s, 'new', %(alert_type)s, 0
) RETURNING alert_id
"""

_COUNT_DUPLICATE_SQL = (
    "UPDATE alerts SET duplicate_count = duplicate_count + 1 WHERE alert_id = %s"
)

_SUPERSEDE_SQL = "UPDATE alerts SET superseded_by = %s WHERE alert_id = %s"


def _severity_rank(risk_level: str | None) -> int:
    """Position on :data:`RISK_LEVEL_ORDER` (0 = most severe); a value
    outside the order ranks least severe."""
    try:
        return RISK_LEVEL_ORDER.index(risk_level)  # type: ignore[arg-type]
    except ValueError:
        return len(RISK_LEVEL_ORDER)


def meets_floor(risk_level: str | None) -> bool:
    """FR-DAS-040: an alertable level at or above ``RISK_ALERT_MIN_RISK_LEVEL``."""
    return risk_level in ALERTABLE_LEVELS and _severity_rank(risk_level) <= _severity_rank(
        config.ALERT_MIN_RISK_LEVEL
    )


def validate_config() -> None:
    """Fail fast on a floor the generator would silently misread: an unknown
    ``RISK_ALERT_MIN_RISK_LEVEL`` ranks below every real level, which would
    turn the floor off (``High`` would alert on ``low``). The alerts entry
    point exits non-zero on the ``ValueError``."""
    if config.ALERT_MIN_RISK_LEVEL not in ALERTABLE_LEVELS:
        raise ValueError(
            f"RISK_ALERT_MIN_RISK_LEVEL must be one of {sorted(ALERTABLE_LEVELS)}, "
            f"got {config.ALERT_MIN_RISK_LEVEL!r}"
        )


def _load_trace_risk(tx: db.Transaction, trace_risk_id: str) -> _TraceRisk | None:
    row = tx.fetch_one(_TRACE_RISK_SQL, (trace_risk_id,))
    if row is None:
        return None
    record_id, trace_id, level, enforcement, rule_ids, contributing = row
    return _TraceRisk(
        trace_risk_id=str(record_id),
        trace_id=trace_id,
        risk_level=level,
        enforcement_type=enforcement,
        triggered_rule_ids=list(rule_ids or []),
        contributing_interaction_risk_ids=[str(i) for i in contributing or []],
    )


def _involved_entities(tx: db.Transaction, trace_id: str) -> list[dict[str, str]]:
    return [
        {"entity_id": entity_id, "entity_kind": kind, "display_name": display_name}
        for entity_id, kind, display_name in tx.fetch_all(_INVOLVED_ENTITIES_SQL, (trace_id,))
    ]


def _ranked_rules(rule_ids: list[str]) -> list[dict]:
    """Catalog entries for *rule_ids* (an id-only stub for unknown ids),
    most severe catalog risk level first, ties by rule id."""
    rules = [catalog.get_rule(rule_id) or {"rule_id": rule_id} for rule_id in rule_ids]
    return sorted(rules, key=lambda r: (_severity_rank(r.get("risk_level")), r["rule_id"]))


def _risky_entity_names(tx: db.Transaction, record: _TraceRisk) -> list[str]:
    return [
        name
        for (name,) in tx.fetch_all(
            _RISKY_ENTITY_NAMES_SQL, (record.contributing_interaction_risk_ids,)
        )
    ]


def _title(record: _TraceRisk, rules: list[dict], names: list[str]) -> str:
    if rules:
        lead = rules[0].get("rule_name") or rules[0]["rule_id"]
    else:
        lead = f"Trace risk {record.risk_level}"
    return f"{lead} — {', '.join(names)}" if names else lead


def _description(record: _TraceRisk, rules: list[dict]) -> str:
    explanations = [r["explanation"] for r in rules if r.get("explanation")]
    if explanations:
        return "; ".join(explanations)
    return f"Trace risk level {record.risk_level} for trace {record.trace_id}"


def _create_alert(tx: db.Transaction, record: _TraceRisk) -> str:
    rules = _ranked_rules(record.triggered_rule_ids)
    entities = _involved_entities(tx, record.trace_id)
    row = tx.fetch_one(
        _INSERT_ALERT_SQL,
        {
            "trace_id": record.trace_id,
            "trace_risk_record_id": record.trace_risk_id,
            "risk_level": record.risk_level,
            "enforcement_type": record.enforcement_type,
            "title": _title(record, rules, _risky_entity_names(tx, record)),
            "description": _description(record, rules),
            "involved_entities": json.dumps(entities),
            "triggered_rule_ids": sorted(record.triggered_rule_ids),
            "alert_type": ALERT_TYPE_STANDARD,
        },
    )
    assert row is not None
    return str(row[0])


def generate_for_trace_risk_record(tx: db.Transaction, trace_risk_id: str) -> AlertOutcome:
    """Apply the module's rules to the trace risk record *trace_risk_id*
    inside *tx*. A record that already has an alert referencing it is
    skipped (see the module docstring for the ``duplicate`` case).
    """
    record = _load_trace_risk(tx, trace_risk_id)
    if record is None:
        return AlertOutcome("skipped", reason="trace_risk_record_missing")
    if not meets_floor(record.risk_level):
        return AlertOutcome("skipped", reason="below_floor")

    tx.execute(_LOCK_SQL, (f"risk:alerts:{record.trace_id}",))
    already = tx.fetch_one(_ALREADY_ALERTED_SQL, (record.trace_id, record.trace_risk_id))
    if already is not None:
        return AlertOutcome("skipped", alert_id=str(already[0]), reason="already_alerted")

    prior = tx.fetch_one(_OPEN_ALERT_SQL, (record.trace_id, ALERT_TYPE_STANDARD))
    if prior is None or prior[2] in TERMINAL_STATUSES:
        return AlertOutcome("created", alert_id=_create_alert(tx, record))

    prior_id, prior_level = str(prior[0]), prior[1]
    if _severity_rank(record.risk_level) >= _severity_rank(prior_level):
        tx.execute(_COUNT_DUPLICATE_SQL, (prior_id,))
        return AlertOutcome("duplicate", alert_id=prior_id)
    new_id = _create_alert(tx, record)
    tx.execute(_SUPERSEDE_SQL, (new_id, prior_id))
    return AlertOutcome("superseded", alert_id=new_id, superseded_alert_id=prior_id)
