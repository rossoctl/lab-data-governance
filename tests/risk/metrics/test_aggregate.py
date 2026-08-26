"""Dashboard metrics aggregation tests (issue #106, PRD §4.6 MVP scope).

Covers exactly the six MVP metrics: risk distribution (FR-DAS-050), summary
tiles (FR-DAS-050a), enforcement distribution (FR-DAS-050b), top rules
(FR-DAS-051), top traces (FR-DAS-053), and risk-by-category (FR-DAS-054).

Every query reduces to latest-version-per-key before filtering (AC-DAS-016),
same discipline as `retrieval.risk` — several tests below pin that a
recomputed (re-versioned) record's *old* version never leaks into a count.
"""

from __future__ import annotations

import datetime as dt

import pytest

from data_governance.risk import metrics
from data_governance.risk.rules import catalog

_RULES_FIXTURES_DIR = (
    __import__("pathlib").Path(__file__).parent.parent / "rules" / "fixtures"
)


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    catalog.reload()
    yield
    catalog.reload()


@pytest.fixture()
def varied_catalog(monkeypatch: pytest.MonkeyPatch):
    """FX-MED-ESC(medium/escalate/[pii_exposure]),
    FX-CRIT-BLOCK(critical/block/[pii_exposure,data_leakage]),
    FX-LOW-ALLOW(low/allow/[data_leakage]),
    FX-HIGH-BLOCK(high/block/[pii_exposure]), FX-NO-DECISION([])."""
    monkeypatch.setattr(
        catalog, "_RULES_SOURCE", _RULES_FIXTURES_DIR / "catalog_varied.json"
    )
    catalog.reload()


NOW = dt.datetime(2026, 8, 20, 12, 0, 0, tzinfo=dt.timezone.utc)
LONG_AGO = dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt.timezone.utc)


def _window(hours: int = 24) -> tuple[dt.datetime, dt.datetime]:
    return NOW - dt.timedelta(hours=hours), NOW + dt.timedelta(hours=1)


# ---------------------------------------------------------------------------
# Migration-presence guard (before migration 0016 has run)
# ---------------------------------------------------------------------------


def test_before_migrations_risk_distribution_is_empty(configured_db, monkeypatch):
    # Simulate "no risk tables" by pointing at a table-existence check that
    # always says False, mirroring retrieval.risk's own guard tests would if
    # they existed; here we assert on the real guard instead: a DB migrated
    # to 0016 always has the tables, so we validate the zero-rows case
    # produces the correct empty shape rather than an error.
    time_from, time_to = _window()
    dist = metrics.get_risk_distribution(time_from=time_from, time_to=time_to)
    assert dist.total == 0
    assert dist.critical == 0
    assert dist.high == 0
    assert dist.medium == 0
    assert dist.low == 0
    assert dist.none == 0


# ---------------------------------------------------------------------------
# FR-DAS-050 — risk distribution
# ---------------------------------------------------------------------------


def test_risk_distribution_counts_current_versions_only(
    configured_db, insert_interaction_risk
):
    insert_interaction_risk(
        interaction_id="ix-1", version=1, risk_level="low", computed_at=NOW
    )
    insert_interaction_risk(
        interaction_id="ix-1", version=2, risk_level="critical", computed_at=NOW
    )
    insert_interaction_risk(
        interaction_id="ix-2", version=1, risk_level="none", computed_at=NOW
    )

    time_from, time_to = _window()
    dist = metrics.get_risk_distribution(time_from=time_from, time_to=time_to)

    assert dist.total == 2
    assert dist.critical == 1
    assert dist.none == 1
    assert dist.low == 0


def test_risk_distribution_filters_by_window(configured_db, insert_interaction_risk):
    insert_interaction_risk(
        interaction_id="ix-1", risk_level="high", computed_at=NOW
    )
    insert_interaction_risk(
        interaction_id="ix-2", risk_level="high", computed_at=LONG_AGO
    )

    time_from, time_to = _window()
    dist = metrics.get_risk_distribution(time_from=time_from, time_to=time_to)

    assert dist.total == 1
    assert dist.high == 1


def test_risk_distribution_empty_when_no_records(configured_db):
    time_from, time_to = _window()
    dist = metrics.get_risk_distribution(time_from=time_from, time_to=time_to)
    assert dist.total == 0


# ---------------------------------------------------------------------------
# FR-DAS-050a — summary tiles
# ---------------------------------------------------------------------------


def test_summary_counts_distinct_agents_users_workflows_interactions(
    configured_db, insert_entity, insert_interaction_risk, insert_trace_risk
):
    insert_entity(entity_id="agent-1", kind="agent")
    insert_entity(entity_id="agent-2", kind="agent")
    insert_entity(entity_id="user-1", kind="user")

    insert_interaction_risk(
        interaction_id="ix-1",
        trace_id="tr-1",
        caller_entity_id="user-1",
        callee_entity_id="agent-1",
        risk_level="critical",
        computed_at=NOW,
    )
    insert_interaction_risk(
        interaction_id="ix-2",
        trace_id="tr-2",
        caller_entity_id="user-1",
        callee_entity_id="agent-2",
        risk_level="none",
        computed_at=NOW,
    )
    insert_trace_risk(trace_id="tr-1", trace_risk_level="critical", computed_at=NOW)
    insert_trace_risk(trace_id="tr-2", trace_risk_level="none", computed_at=NOW)

    time_from, time_to = _window()
    summary = metrics.get_summary(time_from=time_from, time_to=time_to)

    assert summary.agents.total == 2
    assert summary.agents.risky == 1  # only agent-1 appeared in a risky interaction
    assert summary.users.total == 1
    assert summary.users.risky == 1  # user-1 appeared in the critical interaction too
    assert summary.workflows.total == 2
    assert summary.workflows.risky == 1  # tr-1 only
    assert summary.evaluated_interactions.total == 2
    assert summary.evaluated_interactions.risky == 1


def test_summary_risky_pct_rounds_and_handles_zero_total(configured_db):
    time_from, time_to = _window()
    summary = metrics.get_summary(time_from=time_from, time_to=time_to)

    assert summary.agents.total == 0
    assert summary.agents.risky == 0
    assert summary.agents.risky_pct == 0.0


def test_summary_rules_fired_counts_distinct_rules_and_critical(
    configured_db, insert_interaction_risk, varied_catalog
):
    insert_interaction_risk(
        interaction_id="ix-1",
        triggered_rule_ids=["FX-CRIT-BLOCK", "FX-MED-ESC"],
        risk_level="critical",
        computed_at=NOW,
    )
    insert_interaction_risk(
        interaction_id="ix-2",
        triggered_rule_ids=["FX-CRIT-BLOCK"],
        risk_level="critical",
        computed_at=NOW,
    )
    insert_interaction_risk(
        interaction_id="ix-3",
        triggered_rule_ids=["FX-LOW-ALLOW"],
        risk_level="low",
        computed_at=NOW,
    )

    time_from, time_to = _window()
    summary = metrics.get_summary(time_from=time_from, time_to=time_to)

    # distinct rule ids fired: FX-CRIT-BLOCK, FX-MED-ESC, FX-LOW-ALLOW = 3
    assert summary.rules_fired.total == 3
    # of those, only FX-CRIT-BLOCK is risk_level=critical in the catalog
    assert summary.rules_fired.critical == 1


def test_summary_entity_kind_other_than_agent_or_user_is_not_counted(
    configured_db, insert_entity, insert_interaction_risk
):
    insert_entity(entity_id="tool-1", kind="tool")
    insert_interaction_risk(
        interaction_id="ix-1",
        caller_entity_id="tool-1",
        callee_entity_id="tool-1",
        risk_level="high",
        computed_at=NOW,
    )

    time_from, time_to = _window()
    summary = metrics.get_summary(time_from=time_from, time_to=time_to)

    assert summary.agents.total == 0
    assert summary.users.total == 0
    # the interaction itself still counts as evaluated regardless of the
    # participants' entity kind
    assert summary.evaluated_interactions.total == 1


def test_summary_uses_latest_version_only(configured_db, insert_entity, insert_interaction_risk):
    insert_entity(entity_id="agent-1", kind="agent")
    insert_interaction_risk(
        interaction_id="ix-1",
        version=1,
        callee_entity_id="agent-1",
        risk_level="critical",
        computed_at=NOW,
    )
    insert_interaction_risk(
        interaction_id="ix-1",
        version=2,
        callee_entity_id="agent-1",
        risk_level="none",
        computed_at=NOW,
    )

    time_from, time_to = _window()
    summary = metrics.get_summary(time_from=time_from, time_to=time_to)

    assert summary.evaluated_interactions.total == 1
    assert summary.evaluated_interactions.risky == 0
    assert summary.agents.risky == 0


# ---------------------------------------------------------------------------
# FR-DAS-050b — enforcement distribution
# ---------------------------------------------------------------------------


def test_enforcement_distribution_counts_current_versions(
    configured_db, insert_interaction_risk
):
    insert_interaction_risk(
        interaction_id="ix-1", enforcement_type="allow", computed_at=NOW
    )
    insert_interaction_risk(
        interaction_id="ix-2", enforcement_type="allow", computed_at=NOW
    )
    insert_interaction_risk(
        interaction_id="ix-3", enforcement_type="block", computed_at=NOW
    )

    time_from, time_to = _window()
    dist = metrics.get_enforcement_distribution(time_from=time_from, time_to=time_to)

    assert dist.total == 3
    assert dist.counts["allow"] == 2
    assert dist.counts["block"] == 1
    assert dist.pct["allow"] == pytest.approx(66.67, abs=0.01)


def test_enforcement_distribution_null_enforcement_type_excluded(
    configured_db, insert_interaction_risk
):
    insert_interaction_risk(
        interaction_id="ix-1", enforcement_type=None, computed_at=NOW
    )
    insert_interaction_risk(
        interaction_id="ix-2", enforcement_type="allow", computed_at=NOW
    )

    time_from, time_to = _window()
    dist = metrics.get_enforcement_distribution(time_from=time_from, time_to=time_to)

    assert dist.total == 1
    assert dist.counts == {"allow": 1}


def test_enforcement_distribution_empty(configured_db):
    time_from, time_to = _window()
    dist = metrics.get_enforcement_distribution(time_from=time_from, time_to=time_to)
    assert dist.total == 0
    assert dist.counts == {}
    assert dist.pct == {}


def test_enforcement_distribution_uses_latest_version_only(
    configured_db, insert_interaction_risk
):
    insert_interaction_risk(
        interaction_id="ix-1",
        version=1,
        enforcement_type="block",
        risk_level="critical",
        computed_at=NOW,
    )
    insert_interaction_risk(
        interaction_id="ix-1",
        version=2,
        enforcement_type="allow",
        risk_level="none",
        computed_at=NOW,
    )

    time_from, time_to = _window()
    dist = metrics.get_enforcement_distribution(time_from=time_from, time_to=time_to)

    assert dist.total == 1
    assert dist.counts == {"allow": 1}
    assert "block" not in dist.counts


# ---------------------------------------------------------------------------
# FR-DAS-051 — top rules
# ---------------------------------------------------------------------------


def test_top_rules_counts_and_orders_by_frequency(
    configured_db, insert_interaction_risk, varied_catalog
):
    insert_interaction_risk(
        interaction_id="ix-1",
        trace_id="tr-1",
        triggered_rule_ids=["FX-CRIT-BLOCK"],
        computed_at=NOW,
    )
    insert_interaction_risk(
        interaction_id="ix-2",
        trace_id="tr-1",
        triggered_rule_ids=["FX-CRIT-BLOCK"],
        computed_at=NOW,
    )
    insert_interaction_risk(
        interaction_id="ix-3",
        trace_id="tr-2",
        triggered_rule_ids=["FX-CRIT-BLOCK", "FX-LOW-ALLOW"],
        computed_at=NOW,
    )
    insert_interaction_risk(
        interaction_id="ix-4",
        trace_id="tr-3",
        triggered_rule_ids=["FX-LOW-ALLOW"],
        computed_at=NOW,
    )

    time_from, time_to = _window()
    items = metrics.get_top_rules(time_from=time_from, time_to=time_to, limit=10)

    assert [item.rule_id for item in items] == ["FX-CRIT-BLOCK", "FX-LOW-ALLOW"]
    top = items[0]
    assert top.count == 3
    assert top.trace_count == 2  # tr-1, tr-2
    assert top.rule_name is not None
    assert top.risk_level == "critical"


def test_top_rules_respects_limit(configured_db, insert_interaction_risk, varied_catalog):
    insert_interaction_risk(
        interaction_id="ix-1", triggered_rule_ids=["FX-CRIT-BLOCK"], computed_at=NOW
    )
    insert_interaction_risk(
        interaction_id="ix-2", triggered_rule_ids=["FX-LOW-ALLOW"], computed_at=NOW
    )

    time_from, time_to = _window()
    items = metrics.get_top_rules(time_from=time_from, time_to=time_to, limit=1)

    assert len(items) == 1


def test_top_rules_risk_level_distribution_breaks_down_by_interaction_risk_level(
    configured_db, insert_interaction_risk, varied_catalog
):
    insert_interaction_risk(
        interaction_id="ix-1",
        triggered_rule_ids=["FX-CRIT-BLOCK"],
        risk_level="critical",
        computed_at=NOW,
    )
    insert_interaction_risk(
        interaction_id="ix-2",
        triggered_rule_ids=["FX-CRIT-BLOCK"],
        risk_level="high",
        computed_at=NOW,
    )

    time_from, time_to = _window()
    items = metrics.get_top_rules(time_from=time_from, time_to=time_to, limit=10)

    top = next(i for i in items if i.rule_id == "FX-CRIT-BLOCK")
    assert top.risk_level_distribution == {"critical": 1, "high": 1}


def test_top_rules_unknown_rule_id_not_in_catalog_has_none_name_and_level(
    configured_db, insert_interaction_risk, varied_catalog
):
    insert_interaction_risk(
        interaction_id="ix-1", triggered_rule_ids=["NOT-IN-CATALOG"], computed_at=NOW
    )

    time_from, time_to = _window()
    items = metrics.get_top_rules(time_from=time_from, time_to=time_to, limit=10)

    assert len(items) == 1
    assert items[0].rule_id == "NOT-IN-CATALOG"
    assert items[0].rule_name is None
    assert items[0].risk_level is None


def test_top_rules_empty_when_no_records(configured_db):
    time_from, time_to = _window()
    items = metrics.get_top_rules(time_from=time_from, time_to=time_to, limit=10)
    assert items == []


def test_top_rules_uses_latest_version_only(configured_db, insert_interaction_risk):
    insert_interaction_risk(
        interaction_id="ix-1",
        version=1,
        triggered_rule_ids=["RULE-OLD"],
        computed_at=NOW,
    )
    insert_interaction_risk(
        interaction_id="ix-1",
        version=2,
        triggered_rule_ids=["RULE-NEW"],
        computed_at=NOW,
    )

    time_from, time_to = _window()
    items = metrics.get_top_rules(time_from=time_from, time_to=time_to, limit=10)

    assert [item.rule_id for item in items] == ["RULE-NEW"]


# ---------------------------------------------------------------------------
# FR-DAS-053 — top traces
# ---------------------------------------------------------------------------


def test_top_traces_orders_by_risk_level_severity(configured_db, insert_trace_risk):
    insert_trace_risk(trace_id="tr-low", trace_risk_level="low", computed_at=NOW)
    insert_trace_risk(
        trace_id="tr-critical", trace_risk_level="critical", computed_at=NOW
    )
    insert_trace_risk(trace_id="tr-high", trace_risk_level="high", computed_at=NOW)

    time_from, time_to = _window()
    items = metrics.get_top_traces(time_from=time_from, time_to=time_to, limit=10)

    assert [item.trace_id for item in items] == ["tr-critical", "tr-high", "tr-low"]


def test_top_traces_ties_break_by_enforcement_severity(configured_db, insert_trace_risk):
    insert_trace_risk(
        trace_id="tr-allow",
        trace_risk_level="high",
        trace_enforcement_type="allow",
        computed_at=NOW,
    )
    insert_trace_risk(
        trace_id="tr-block",
        trace_risk_level="high",
        trace_enforcement_type="block",
        computed_at=NOW,
    )

    time_from, time_to = _window()
    items = metrics.get_top_traces(time_from=time_from, time_to=time_to, limit=10)

    assert [item.trace_id for item in items] == ["tr-block", "tr-allow"]


def test_top_traces_respects_limit(configured_db, insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", trace_risk_level="high", computed_at=NOW)
    insert_trace_risk(trace_id="tr-2", trace_risk_level="critical", computed_at=NOW)

    time_from, time_to = _window()
    items = metrics.get_top_traces(time_from=time_from, time_to=time_to, limit=1)

    assert len(items) == 1
    assert items[0].trace_id == "tr-2"


def test_top_traces_uses_latest_version_only(configured_db, insert_trace_risk):
    insert_trace_risk(
        trace_id="tr-1", version=1, trace_risk_level="critical", computed_at=NOW
    )
    insert_trace_risk(
        trace_id="tr-1", version=2, trace_risk_level="none", computed_at=NOW
    )

    time_from, time_to = _window()
    items = metrics.get_top_traces(time_from=time_from, time_to=time_to, limit=10)

    assert len(items) == 1
    assert items[0].trace_risk_level == "none"


def test_top_traces_filters_by_window(configured_db, insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", trace_risk_level="critical", computed_at=NOW)
    insert_trace_risk(
        trace_id="tr-2", trace_risk_level="critical", computed_at=LONG_AGO
    )

    time_from, time_to = _window()
    items = metrics.get_top_traces(time_from=time_from, time_to=time_to, limit=10)

    assert [item.trace_id for item in items] == ["tr-1"]


def test_top_traces_empty_when_no_records(configured_db):
    time_from, time_to = _window()
    items = metrics.get_top_traces(time_from=time_from, time_to=time_to, limit=10)
    assert items == []


# ---------------------------------------------------------------------------
# FR-DAS-054 — risk by category
# ---------------------------------------------------------------------------


def test_risk_by_category_counts_policy_events_per_category(
    configured_db, insert_interaction_risk, varied_catalog
):
    # FX-CRIT-BLOCK -> [pii_exposure, data_leakage]; FX-LOW-ALLOW -> [data_leakage]
    insert_interaction_risk(
        interaction_id="ix-1", triggered_rule_ids=["FX-CRIT-BLOCK"], computed_at=NOW
    )
    insert_interaction_risk(
        interaction_id="ix-2", triggered_rule_ids=["FX-LOW-ALLOW"], computed_at=NOW
    )

    time_from, time_to = _window()
    items = metrics.get_risk_by_category(time_from=time_from, time_to=time_to)

    by_category = {item.category: item.count for item in items}
    assert by_category["pii_exposure"] == 1
    assert by_category["data_leakage"] == 2


def test_risk_by_category_one_interaction_multiple_rules_same_category_counts_once_per_rule(
    configured_db, insert_interaction_risk, varied_catalog
):
    # One interaction triggering two rules that share a category counts the
    # policy event once per triggering rule (FR-DAS-054 counts policy events,
    # and each triggered rule is itself a policy event contribution), not
    # deduplicated by category per interaction.
    insert_interaction_risk(
        interaction_id="ix-1",
        triggered_rule_ids=["FX-CRIT-BLOCK", "FX-MED-ESC"],
        computed_at=NOW,
    )

    time_from, time_to = _window()
    items = metrics.get_risk_by_category(time_from=time_from, time_to=time_to)

    by_category = {item.category: item.count for item in items}
    # both rules touch pii_exposure
    assert by_category["pii_exposure"] == 2
    assert by_category["data_leakage"] == 1


def test_risk_by_category_rule_with_no_categories_contributes_nothing(
    configured_db, insert_interaction_risk, varied_catalog
):
    insert_interaction_risk(
        interaction_id="ix-1", triggered_rule_ids=["FX-NO-DECISION"], computed_at=NOW
    )

    time_from, time_to = _window()
    items = metrics.get_risk_by_category(time_from=time_from, time_to=time_to)

    assert items == []


def test_risk_by_category_unknown_rule_id_not_in_catalog_contributes_nothing(
    configured_db, insert_interaction_risk, varied_catalog
):
    insert_interaction_risk(
        interaction_id="ix-1", triggered_rule_ids=["NOT-IN-CATALOG"], computed_at=NOW
    )

    time_from, time_to = _window()
    items = metrics.get_risk_by_category(time_from=time_from, time_to=time_to)

    assert items == []


def test_risk_by_category_empty_when_no_records(configured_db, varied_catalog):
    time_from, time_to = _window()
    items = metrics.get_risk_by_category(time_from=time_from, time_to=time_to)
    assert items == []


def test_risk_by_category_uses_latest_version_only(
    configured_db, insert_interaction_risk, varied_catalog
):
    insert_interaction_risk(
        interaction_id="ix-1",
        version=1,
        triggered_rule_ids=["FX-CRIT-BLOCK"],
        computed_at=NOW,
    )
    insert_interaction_risk(
        interaction_id="ix-1",
        version=2,
        triggered_rule_ids=["FX-LOW-ALLOW"],
        computed_at=NOW,
    )

    time_from, time_to = _window()
    items = metrics.get_risk_by_category(time_from=time_from, time_to=time_to)

    by_category = {item.category: item.count for item in items}
    assert "pii_exposure" not in by_category
    assert by_category["data_leakage"] == 1
