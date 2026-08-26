"""Dashboard metrics aggregation (issue #106, PRD §4.6).

Re-exports :mod:`.aggregate`'s public surface — see that module's docstring
for the query-layer contract this package implements.
"""

from __future__ import annotations

from data_governance.risk.metrics.aggregate import (
    CategoryCount,
    EnforcementDistribution,
    RiskDistribution,
    RulesFiredCounts,
    SummaryMetrics,
    TileCounts,
    TopRuleView,
    get_enforcement_distribution,
    get_risk_by_category,
    get_risk_distribution,
    get_summary,
    get_top_rules,
    get_top_traces,
)

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
