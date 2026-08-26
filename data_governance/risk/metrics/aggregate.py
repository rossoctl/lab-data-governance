"""Dashboard metrics aggregation — the DAS query layer for PRD §4.6 (issue #106).

MVP scope only, per the issue's own "Priority: MVP" line and issue #111's
downstream tiering of the endpoints that will wrap these functions:

  - FR-DAS-050  :func:`get_risk_distribution`
  - FR-DAS-050a :func:`get_summary`
  - FR-DAS-050b :func:`get_enforcement_distribution`
  - FR-DAS-051  :func:`get_top_rules`
  - FR-DAS-053  :func:`get_top_traces`
  - FR-DAS-054  :func:`get_risk_by_category`

Explicitly out of scope here (post-MVP or nice-to-have per #111): top-agents/
top-users (FR-DAS-052/052a — blocked on an undefined "weighted risk
contribution" formula), workflows-by-category (FR-DAS-054a), top-workflows,
latency (separate issue #108), execution-flow-data (already served by
:func:`data_governance.retrieval.get_trace_risk_detail`), and alert
management (separate issue #110).

This is the query layer only: every function returns plain dataclasses, not
JSON — the HTTP `/risk/metrics/*` routes that shape these into the PRD §7.4
response bodies are issue #111, not built here. Callers pass ``time_from``/
``time_to`` directly (mirroring :mod:`data_governance.retrieval.risk`'s
convention); resolving the PRD's ``window`` string (``24h``/``7d``/``30d``/
``custom``) into a concrete ``[time_from, time_to)`` pair is the HTTP layer's
job; this module has no ``now()`` dependency of its own, so it stays
testable with fixed timestamps.

Every query reduces to "latest version per key" (via the same
``DISTINCT ON (key) ... ORDER BY key, version DESC`` idiom used throughout
``retrieval/risk.py`` and ``risk/engine/trace_compute.py``) BEFORE applying
any time-window or other filter — AC-DAS-016: filtering the raw table first
risks surfacing a superseded version that happens to match while the current
version does not.

Computation is on-demand, per-request — no cache table, no Redis (the
architecture has neither; implementation-notes-v3 §4). The already-adopted
``LIMIT``-bounded, indexed queries here are expected to stay well within the
existing <2s query targets (#103) at demo-dataset scale; a future issue can
revisit this if real deployment volume proves otherwise.

**Summary tile semantics (FR-DAS-050a), resolved against the PRD's exact
wording** ("total distinct agents/entities observed", "total distinct users
observed", "total distinct workflows (traces) observed", "total evaluated
interactions", "total distinct rules fired"):

  - Agents/Users tiles count distinct :mod:`entities` rows (by ``kind``)
    that appear as ``caller_entity_id`` or ``callee_entity_id`` on a current
    interaction risk record within the window — not every entity in the
    ``entities`` table, only ones *observed* in-window. An entity is "risky"
    if it participated in at least one such record whose ``risk_level !=
    'none'``.
  - Workflows tile counts distinct ``trace_id`` across current trace risk
    records within the window; "risky" is that trace's current
    ``trace_risk_level != 'none'``.
  - Evaluated interactions counts current interaction risk records in the
    window; "risky" is ``risk_level != 'none'`` — the natural reading
    confirmed against the PRD's own risk-distribution example, where
    ``none`` is the dominant non-risky bucket.
  - Rules fired counts distinct ``triggered_rule_ids`` values appearing on
    any current interaction risk record in the window; the "critical" count
    is how many of those distinct rule ids have ``risk_level == "critical"``
    per the rule catalog (:mod:`data_governance.risk.rules.catalog`) — this
    is the *rule's* catalog risk level, not the triggering interaction's.
    A rule id absent from the catalog (or the catalog rule missing a
    ``risk_level``) is counted toward the total but never toward critical.

**Top rules (FR-DAS-051) / risk-by-category (FR-DAS-054)** both walk each
current interaction risk record's ``triggered_rule_ids`` array and join
against the rule catalog for ``rule_name``/``risk_level``/``categories``. A
rule id with no catalog entry is skipped for risk-by-category (no categories
to attribute to) but still counted (with ``rule_name``/``risk_level`` both
``None``) for top-rules, since a caller may still want frequency-by-id even
for an unrecognized rule.

**Top traces (FR-DAS-053)** intentionally does not reuse
:func:`data_governance.retrieval.risk.list_trace_risk` — that function's
``SORT_RISK_LEVEL_DESC`` breaks ties by ``computed_at DESC, trace_id ASC``,
but FR-DAS-053 requires the second tiebreak to be ``trace_enforcement_type``
severity specifically. This module runs its own bounded, no-cursor ranking
query instead (a top-N ranking has no meaningful "next page" — see
implementation-notes-v3 §8.2's exception for metrics endpoints).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from data_governance import db
from data_governance.retrieval.risk import TraceRiskView
from data_governance.risk.rules import catalog
from data_governance.risk.rules.catalog import ENFORCEMENT_ORDER, RISK_LEVEL_ORDER

__all__ = [
    "CategoryCount",
    "EnforcementDistribution",
    "RiskDistribution",
    "RulesFiredCounts",
    "SummaryMetrics",
    "TileCounts",
    "TopRuleView",
    "get_enforcement_distribution",
    "get_risk_by_category",
    "get_risk_distribution",
    "get_summary",
    "get_top_rules",
    "get_top_traces",
]

_RISK_LEVELS = ("critical", "high", "medium", "low", "none")
_RISK_RANK_UNKNOWN = len(RISK_LEVEL_ORDER) + 1
_ENFORCEMENT_RANK_UNKNOWN = len(ENFORCEMENT_ORDER) + 1


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskDistribution:
    """FR-DAS-050: counts of current interaction risk records by risk level."""

    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    none: int = 0

    @property
    def total(self) -> int:
        return self.critical + self.high + self.medium + self.low + self.none


@dataclass(frozen=True)
class TileCounts:
    """One FR-DAS-050a summary tile: total observed, how many risky, and the
    risky percentage (0.0 when total is 0, never a division error)."""

    total: int
    risky: int

    @property
    def risky_pct(self) -> float:
        if self.total == 0:
            return 0.0
        return round((self.risky / self.total) * 100, 1)


@dataclass(frozen=True)
class RulesFiredCounts:
    total: int
    critical: int


@dataclass(frozen=True)
class SummaryMetrics:
    """FR-DAS-050a: the five dashboard summary tiles."""

    agents: TileCounts
    users: TileCounts
    workflows: TileCounts
    evaluated_interactions: TileCounts
    rules_fired: RulesFiredCounts


@dataclass(frozen=True)
class EnforcementDistribution:
    """FR-DAS-050b: counts and percentages by enforcement_type."""

    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def pct(self) -> dict[str, float]:
        total = self.total
        if total == 0:
            return {}
        return {
            enforcement_type: round((count / total) * 100, 2)
            for enforcement_type, count in self.counts.items()
        }


@dataclass(frozen=True)
class TopRuleView:
    """FR-DAS-051: one ranked entry in the top-rules leaderboard."""

    rule_id: str
    rule_name: str | None
    count: int
    trace_count: int
    risk_level: str | None
    risk_level_distribution: dict[str, int]


@dataclass(frozen=True)
class CategoryCount:
    """FR-DAS-054: policy event count for one rule category."""

    category: str
    count: int


# ---------------------------------------------------------------------------
# Migration-presence guard (same convention as retrieval/risk.py)
# ---------------------------------------------------------------------------


def _risk_tables_exist(tx: db.Transaction) -> bool:
    return (
        tx.fetch_one(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'interaction_risk_records'"
        )
        is not None
    )


def _trace_risk_table_exists(tx: db.Transaction) -> bool:
    return (
        tx.fetch_one(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'trace_risk_records'"
        )
        is not None
    )


# Latest-version-per-interaction reduction, filtered by the caller's window.
# Selects only the columns any metric here actually needs.
_CURRENT_INTERACTIONS_CTE = """
current_interactions AS (
    SELECT DISTINCT ON (interaction_id)
        interaction_id, trace_id, caller_entity_id, callee_entity_id,
        risk_level, enforcement_type, triggered_rule_ids, computed_at
    FROM interaction_risk_records
    ORDER BY interaction_id, version DESC
)
"""

_CURRENT_TRACES_CTE = """
current_traces AS (
    SELECT DISTINCT ON (trace_id)
        trace_id, trace_risk_level, trace_enforcement_type, computed_at
    FROM trace_risk_records
    ORDER BY trace_id, version DESC
)
"""


def _window_predicate(
    time_from: dt.datetime | None,
    time_to: dt.datetime | None,
    *,
    column: str = "computed_at",
) -> tuple[str, list[Any]]:
    conditions = []
    params: list[Any] = []
    if time_from is not None:
        conditions.append(f"{column} >= %s")
        params.append(time_from)
    if time_to is not None:
        conditions.append(f"{column} <= %s")
        params.append(time_to)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return where, params


# ---------------------------------------------------------------------------
# FR-DAS-050 — risk distribution
# ---------------------------------------------------------------------------


def get_risk_distribution(
    *, time_from: dt.datetime | None = None, time_to: dt.datetime | None = None
) -> RiskDistribution:
    with db.transaction() as tx:
        if not _risk_tables_exist(tx):
            return RiskDistribution()

        where, params = _window_predicate(time_from, time_to)
        sql = (
            f"WITH {_CURRENT_INTERACTIONS_CTE} "
            f"SELECT risk_level, count(*) FROM current_interactions{where} "
            f"GROUP BY risk_level"
        )
        rows = tx.fetch_all(sql, params)

    counts = {level: 0 for level in _RISK_LEVELS}
    for risk_level, count in rows:
        if risk_level in counts:
            counts[risk_level] += count
    return RiskDistribution(**counts)


# ---------------------------------------------------------------------------
# FR-DAS-050a — summary tiles
# ---------------------------------------------------------------------------


def _entity_tile_sql(where: str) -> str:
    """Distinct entities of a given ``kind`` (bound as the final query param)
    observed as caller/callee on a current interaction within the window,
    and how many of those took part in at least one risky
    (``risk_level != 'none'``) current interaction. *where* is the window
    predicate, already written in terms of ``ci.computed_at``."""
    return (
        f"WITH {_CURRENT_INTERACTIONS_CTE} "
        f"SELECT count(*), count(*) FILTER (WHERE risky) FROM ("
        f"SELECT e.id, bool_or(ci.risk_level != 'none') AS risky "
        f"FROM entities e "
        f"JOIN current_interactions ci "
        f"ON e.id = ci.caller_entity_id OR e.id = ci.callee_entity_id"
        f"{where} "
        f"GROUP BY e.id"
        f") per_entity"
    )


def get_summary(
    *, time_from: dt.datetime | None = None, time_to: dt.datetime | None = None
) -> SummaryMetrics:
    empty_tiles = SummaryMetrics(
        agents=TileCounts(0, 0),
        users=TileCounts(0, 0),
        workflows=TileCounts(0, 0),
        evaluated_interactions=TileCounts(0, 0),
        rules_fired=RulesFiredCounts(0, 0),
    )

    with db.transaction() as tx:
        if not _risk_tables_exist(tx):
            return empty_tiles

        window_where, window_params = _window_predicate(time_from, time_to)
        entity_where, entity_params = _window_predicate(
            time_from, time_to, column="ci.computed_at"
        )
        entity_where = (
            (entity_where + " AND e.kind = %s")
            if entity_where
            else " WHERE e.kind = %s"
        )

        agents_row = tx.fetch_one(
            _entity_tile_sql(entity_where), entity_params + ["agent"]
        )
        users_row = tx.fetch_one(
            _entity_tile_sql(entity_where), entity_params + ["user"]
        )

        evaluated_row = tx.fetch_one(
            f"WITH {_CURRENT_INTERACTIONS_CTE} "
            f"SELECT count(*), count(*) FILTER (WHERE risk_level != 'none') "
            f"FROM current_interactions{window_where}",
            window_params,
        )

        rule_id_rows = tx.fetch_all(
            f"WITH {_CURRENT_INTERACTIONS_CTE} "
            f"SELECT DISTINCT unnest(triggered_rule_ids) AS rule_id "
            f"FROM current_interactions{window_where}",
            window_params,
        )

        if _trace_risk_table_exists(tx):
            traces_where, traces_params = _window_predicate(time_from, time_to)
            workflows_row = tx.fetch_one(
                f"WITH {_CURRENT_TRACES_CTE} "
                f"SELECT count(*), count(*) FILTER (WHERE trace_risk_level != 'none') "
                f"FROM current_traces{traces_where}",
                traces_params,
            )
        else:
            workflows_row = (0, 0)

    rule_ids = [row[0] for row in rule_id_rows]
    critical_rule_count = sum(
        1
        for rule_id in set(rule_ids)
        if (rule := catalog.get_rule(rule_id)) is not None
        and rule.get("risk_level") == "critical"
    )

    return SummaryMetrics(
        agents=TileCounts(total=agents_row[0], risky=agents_row[1]),
        users=TileCounts(total=users_row[0], risky=users_row[1]),
        workflows=TileCounts(total=workflows_row[0], risky=workflows_row[1]),
        evaluated_interactions=TileCounts(
            total=evaluated_row[0], risky=evaluated_row[1]
        ),
        rules_fired=RulesFiredCounts(
            total=len(rule_ids), critical=critical_rule_count
        ),
    )


# ---------------------------------------------------------------------------
# FR-DAS-050b — enforcement distribution
# ---------------------------------------------------------------------------


def get_enforcement_distribution(
    *, time_from: dt.datetime | None = None, time_to: dt.datetime | None = None
) -> EnforcementDistribution:
    with db.transaction() as tx:
        if not _risk_tables_exist(tx):
            return EnforcementDistribution()

        where, params = _window_predicate(time_from, time_to)
        enforcement_condition = "enforcement_type IS NOT NULL"
        full_where = (
            f"{where} AND {enforcement_condition}"
            if where
            else f" WHERE {enforcement_condition}"
        )
        sql = (
            f"WITH {_CURRENT_INTERACTIONS_CTE} "
            f"SELECT enforcement_type, count(*) FROM current_interactions"
            f"{full_where} GROUP BY enforcement_type"
        )
        rows = tx.fetch_all(sql, params)

    counts = {enforcement_type: count for enforcement_type, count in rows}
    return EnforcementDistribution(counts=counts)


# ---------------------------------------------------------------------------
# FR-DAS-051 — top rules
# ---------------------------------------------------------------------------


def get_top_rules(
    *,
    time_from: dt.datetime | None = None,
    time_to: dt.datetime | None = None,
    limit: int = 10,
) -> list[TopRuleView]:
    with db.transaction() as tx:
        if not _risk_tables_exist(tx):
            return []

        where, params = _window_predicate(time_from, time_to)
        sql = (
            f"WITH {_CURRENT_INTERACTIONS_CTE} "
            f"SELECT unnest(triggered_rule_ids) AS rule_id, trace_id, risk_level "
            f"FROM current_interactions{where}"
        )
        rows = tx.fetch_all(sql, params)

    per_rule: dict[str, dict[str, Any]] = {}
    for rule_id, trace_id, risk_level in rows:
        entry = per_rule.setdefault(
            rule_id, {"count": 0, "trace_ids": set(), "risk_level_distribution": {}}
        )
        entry["count"] += 1
        entry["trace_ids"].add(trace_id)
        entry["risk_level_distribution"][risk_level] = (
            entry["risk_level_distribution"].get(risk_level, 0) + 1
        )

    items = []
    for rule_id, entry in per_rule.items():
        rule = catalog.get_rule(rule_id)
        items.append(
            TopRuleView(
                rule_id=rule_id,
                rule_name=rule["rule_name"] if rule is not None else None,
                count=entry["count"],
                trace_count=len(entry["trace_ids"]),
                risk_level=rule["risk_level"] if rule is not None else None,
                risk_level_distribution=entry["risk_level_distribution"],
            )
        )

    items.sort(key=lambda item: item.count, reverse=True)
    return items[:limit]


# ---------------------------------------------------------------------------
# FR-DAS-053 — top traces
# ---------------------------------------------------------------------------


def _trace_risk_rank_sql() -> str:
    return "COALESCE(array_position(%s::text[], trace_risk_level), %s)"


def _trace_enforcement_rank_sql() -> str:
    return "COALESCE(array_position(%s::text[], trace_enforcement_type), %s)"


def get_top_traces(
    *,
    time_from: dt.datetime | None = None,
    time_to: dt.datetime | None = None,
    limit: int = 10,
) -> list[TraceRiskView]:
    """FR-DAS-053: top N current trace risk records ordered by
    ``trace_risk_level`` severity, then ``trace_enforcement_type`` severity.

    A bounded ranking, not a paginated list — no cursor (implementation-notes
    §8.2's documented exception for metrics top-N endpoints).
    """
    with db.transaction() as tx:
        if not _trace_risk_table_exists(tx):
            return []

        where, params = _window_predicate(time_from, time_to)
        sql = (
            "WITH current_traces AS ("
            "SELECT DISTINCT ON (trace_id) "
            "trace_risk_id, trace_id, version, computed_at, trace_risk_level, "
            "trace_enforcement_type, risk_compounding_mode, "
            "enforcement_aggregation_mode, interaction_count, "
            "policy_event_count, all_entity_ids, triggered_rule_ids, "
            "overall_confidence, contributing_interaction_risk_ids "
            "FROM trace_risk_records ORDER BY trace_id, version DESC), "
            "ranked AS ("
            f"SELECT *, {_trace_risk_rank_sql()} AS risk_rank, "
            f"{_trace_enforcement_rank_sql()} AS enforcement_rank "
            "FROM current_traces) "
            f"SELECT trace_risk_id, trace_id, version, computed_at, "
            f"trace_risk_level, trace_enforcement_type, risk_compounding_mode, "
            f"enforcement_aggregation_mode, interaction_count, "
            f"policy_event_count, all_entity_ids, triggered_rule_ids, "
            f"overall_confidence, contributing_interaction_risk_ids "
            f"FROM ranked{where} "
            f"ORDER BY risk_rank ASC, enforcement_rank ASC, computed_at DESC, "
            f"trace_id ASC LIMIT %s"
        )
        full_params = (
            [list(RISK_LEVEL_ORDER), _RISK_RANK_UNKNOWN]
            + [list(ENFORCEMENT_ORDER), _ENFORCEMENT_RANK_UNKNOWN]
            + params
            + [limit]
        )
        rows = tx.fetch_all(sql, full_params)

    return [_row_to_trace_risk_view(row) for row in rows]


def _row_to_trace_risk_view(row: tuple[Any, ...]) -> TraceRiskView:
    (
        trace_risk_id,
        trace_id,
        version,
        computed_at,
        trace_risk_level,
        trace_enforcement_type,
        risk_compounding_mode,
        enforcement_aggregation_mode,
        interaction_count,
        policy_event_count,
        all_entity_ids,
        triggered_rule_ids,
        overall_confidence,
        contributing_interaction_risk_ids,
    ) = row
    return TraceRiskView(
        trace_risk_id=str(trace_risk_id),
        trace_id=trace_id,
        version=version,
        computed_at=computed_at,
        trace_risk_level=trace_risk_level,
        trace_enforcement_type=trace_enforcement_type,
        risk_compounding_mode=risk_compounding_mode,
        enforcement_aggregation_mode=enforcement_aggregation_mode,
        interaction_count=interaction_count,
        policy_event_count=policy_event_count,
        all_entity_ids=list(all_entity_ids or []),
        triggered_rule_ids=list(triggered_rule_ids or []),
        overall_confidence=(
            float(overall_confidence) if overall_confidence is not None else None
        ),
        contributing_interaction_risk_ids=[
            str(v) for v in (contributing_interaction_risk_ids or [])
        ],
    )


# ---------------------------------------------------------------------------
# FR-DAS-054 — risk by category
# ---------------------------------------------------------------------------


def get_risk_by_category(
    *, time_from: dt.datetime | None = None, time_to: dt.datetime | None = None
) -> list[CategoryCount]:
    with db.transaction() as tx:
        if not _risk_tables_exist(tx):
            return []

        where, params = _window_predicate(time_from, time_to)
        sql = (
            f"WITH {_CURRENT_INTERACTIONS_CTE} "
            f"SELECT unnest(triggered_rule_ids) AS rule_id "
            f"FROM current_interactions{where}"
        )
        rows = tx.fetch_all(sql, params)

    rule_ids = [rule_id for (rule_id,) in rows]
    rules_by_id = {
        rule_id: rule
        for rule_id in set(rule_ids)
        if (rule := catalog.get_rule(rule_id)) is not None
    }

    counts: dict[str, int] = {}
    for rule_id in rule_ids:
        rule = rules_by_id.get(rule_id)
        if rule is None:
            continue
        for category in rule["categories"]:
            counts[category] = counts.get(category, 0) + 1

    return [
        CategoryCount(category=category, count=count)
        for category, count in sorted(counts.items())
    ]
