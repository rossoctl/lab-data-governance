"""In-process tests for the DAS trace-risk retrieval seam (issue #109).

Drives ``retrieval.list_trace_risk`` / ``get_trace_risk`` /
``get_trace_risk_history`` directly against a migrated DB (migrations
0016/0018). This is the trace-risk half of the seam; the interaction-risk
half lives in ``test_risk_reads.py``, and the forest read
(``get_trace_risk_detail``) lands in a later slice of this same issue.

Seeding is direct SQL against ``trace_risk_records`` — there is no
processor that writes this table in-test (issue #102's ``trace_compute.py``
calls out to OPA-derived interaction risk), so tests insert rows exactly as
the write path would.

``trace_risk_records`` has no ``classification_summary`` column, so there is
no ``regulatory_tag`` filter here — that is a deliberate omission mirroring
the table shape, not an oversight (see the plan's endpoints table).
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid

import psycopg
import pytest

from data_governance import retrieval


def _uuid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def insert_trace_risk(configured_db: str):
    """Insert one ``trace_risk_records`` row, returning its id.

    Every column has a sensible default so tests only pass what they're
    actually varying. ``computed_at`` defaults to ``now()`` in SQL when not
    given explicitly, so successive inserts without an explicit timestamp are
    still strictly ordered by insertion (real-clock resolution).
    """

    def _insert(
        *,
        trace_id: str,
        version: int = 1,
        trace_risk_level: str = "low",
        trace_enforcement_type: str | None = None,
        risk_compounding_mode: str | None = None,
        enforcement_aggregation_mode: str | None = None,
        interaction_count: int = 1,
        policy_event_count: int = 1,
        all_entity_ids: list[str] | None = None,
        triggered_rule_ids: list[str] | None = None,
        overall_confidence: str | None = "0.900",
        contributing_interaction_risk_ids: list[str] | None = None,
        computed_at: dt.datetime | None = None,
    ) -> str:
        trace_risk_id = _uuid()
        with psycopg.connect(configured_db) as conn:
            conn.execute(
                "INSERT INTO trace_risk_records ("
                "trace_risk_id, trace_id, version, computed_at, "
                "trace_risk_level, trace_enforcement_type, "
                "risk_compounding_mode, enforcement_aggregation_mode, "
                "interaction_count, policy_event_count, all_entity_ids, "
                "triggered_rule_ids, overall_confidence, "
                "contributing_interaction_risk_ids"
                ") VALUES (%s, %s, %s, COALESCE(%s, now()), %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s)",
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
                    all_entity_ids or [],
                    triggered_rule_ids or [],
                    overall_confidence,
                    contributing_interaction_risk_ids or [],
                ),
            )
            conn.commit()
        return trace_risk_id

    return _insert


# ---------------------------------------------------------------------------
# AC-DAS-016 — latest version only
# ---------------------------------------------------------------------------


def test_list_returns_only_latest_version_per_trace(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", version=1, trace_risk_level="low")
    insert_trace_risk(trace_id="tr-1", version=2, trace_risk_level="high")

    page = retrieval.list_trace_risk()

    assert len(page.items) == 1
    assert page.items[0].trace_risk_level == "high"
    assert page.items[0].version == 2


def test_list_latest_version_wins_even_when_an_older_version_is_more_severe(
    insert_trace_risk,
):
    insert_trace_risk(trace_id="tr-1", version=1, trace_risk_level="critical")
    insert_trace_risk(trace_id="tr-1", version=2, trace_risk_level="low")

    page = retrieval.list_trace_risk(risk_level=["critical"])

    assert page.items == []


def test_get_returns_latest_version(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", version=1, trace_risk_level="low")
    insert_trace_risk(trace_id="tr-1", version=2, trace_risk_level="high")

    view = retrieval.get_trace_risk("tr-1")

    assert view is not None
    assert view.trace_risk_level == "high"
    assert view.version == 2


def test_get_unknown_trace_id_returns_none(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1")

    assert retrieval.get_trace_risk("nope") is None


# ---------------------------------------------------------------------------
# AC-DAS-017 — history: all versions, ascending
# ---------------------------------------------------------------------------


def test_history_returns_all_versions_ascending(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", version=1, trace_risk_level="low")
    insert_trace_risk(trace_id="tr-1", version=2, trace_risk_level="medium")
    insert_trace_risk(trace_id="tr-1", version=3, trace_risk_level="high")

    page = retrieval.get_trace_risk_history("tr-1")

    assert [item.version for item in page.items] == [1, 2, 3]


def test_history_of_unknown_id_is_empty_page(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1")

    page = retrieval.get_trace_risk_history("nope")

    assert page.items == []
    assert page.next_key is None


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_filter_by_entity_id_matches_all_entity_ids_membership(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", all_entity_ids=["ent-x", "ent-y"])
    insert_trace_risk(trace_id="tr-2", all_entity_ids=["ent-p", "ent-q"])

    page = retrieval.list_trace_risk(entity_id="ent-y")

    assert [item.trace_id for item in page.items] == ["tr-1"]


def test_filter_by_risk_level_csv_union(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", trace_risk_level="critical")
    insert_trace_risk(trace_id="tr-2", trace_risk_level="high")
    insert_trace_risk(trace_id="tr-3", trace_risk_level="low")

    page = retrieval.list_trace_risk(risk_level=["critical", "high"])

    assert {item.trace_id for item in page.items} == {"tr-1", "tr-2"}


def test_filter_by_risk_level_empty_list_matches_nothing(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", trace_risk_level="critical")

    page = retrieval.list_trace_risk(risk_level=[])

    assert page.items == []


def test_filter_by_risk_level_unknown_value_is_empty_not_error(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", trace_risk_level="critical")

    page = retrieval.list_trace_risk(risk_level=["fuchsia"])

    assert page.items == []


def test_filter_by_enforcement_type(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", trace_enforcement_type="block")
    insert_trace_risk(trace_id="tr-2", trace_enforcement_type="allow")
    insert_trace_risk(trace_id="tr-3", trace_enforcement_type=None)

    page = retrieval.list_trace_risk(enforcement_type="block")

    assert [item.trace_id for item in page.items] == ["tr-1"]


def test_filter_by_rule_id(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", triggered_rule_ids=["rule-a", "rule-b"])
    insert_trace_risk(trace_id="tr-2", triggered_rule_ids=["rule-c"])

    page = retrieval.list_trace_risk(rule_id="rule-a")

    assert [item.trace_id for item in page.items] == ["tr-1"]


def test_filters_combine_conjunctively(insert_trace_risk):
    insert_trace_risk(
        trace_id="tr-1", trace_risk_level="critical", trace_enforcement_type="block"
    )
    insert_trace_risk(
        trace_id="tr-2", trace_risk_level="critical", trace_enforcement_type="allow"
    )

    page = retrieval.list_trace_risk(risk_level=["critical"], enforcement_type="block")

    assert [item.trace_id for item in page.items] == ["tr-1"]


def test_filter_by_time_from_to_inclusive(insert_trace_risk):
    early = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    mid = dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc)
    late = dt.datetime(2026, 1, 10, tzinfo=dt.timezone.utc)
    insert_trace_risk(trace_id="tr-early", computed_at=early)
    insert_trace_risk(trace_id="tr-mid", computed_at=mid)
    insert_trace_risk(trace_id="tr-late", computed_at=late)

    page = retrieval.list_trace_risk(time_from=mid, time_to=mid)
    assert [item.trace_id for item in page.items] == ["tr-mid"]

    page = retrieval.list_trace_risk(time_from=mid, time_to=late)
    assert {item.trace_id for item in page.items} == {"tr-mid", "tr-late"}


def test_filter_time_from_after_time_to_is_empty_not_error(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1")

    page = retrieval.list_trace_risk(
        time_from=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
        time_to=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    )

    assert page.items == []


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------


def test_default_sort_is_computed_at_desc(insert_trace_risk):
    early = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    late = dt.datetime(2026, 1, 10, tzinfo=dt.timezone.utc)
    insert_trace_risk(trace_id="tr-early", computed_at=early)
    insert_trace_risk(trace_id="tr-late", computed_at=late)

    page = retrieval.list_trace_risk()

    assert [item.trace_id for item in page.items] == ["tr-late", "tr-early"]


def test_sort_risk_level_desc_is_most_severe_first(insert_trace_risk):
    insert_trace_risk(trace_id="tr-low", trace_risk_level="low")
    insert_trace_risk(trace_id="tr-critical", trace_risk_level="critical")
    insert_trace_risk(trace_id="tr-medium", trace_risk_level="medium")

    page = retrieval.list_trace_risk(sort=retrieval.SORT_RISK_LEVEL_DESC)

    assert [item.trace_id for item in page.items] == [
        "tr-critical",
        "tr-medium",
        "tr-low",
    ]


def test_sort_risk_level_desc_places_novel_value_last(insert_trace_risk):
    insert_trace_risk(trace_id="tr-unknown", trace_risk_level="unknown")
    insert_trace_risk(trace_id="tr-novel", trace_risk_level="fuchsia")
    insert_trace_risk(trace_id="tr-low", trace_risk_level="low")

    page = retrieval.list_trace_risk(sort=retrieval.SORT_RISK_LEVEL_DESC)

    assert [item.trace_id for item in page.items] == [
        "tr-low",
        "tr-unknown",
        "tr-novel",
    ]


def test_sort_ties_break_by_computed_at_desc_then_id(insert_trace_risk):
    same_time = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    insert_trace_risk(trace_id="tr-b", trace_risk_level="high", computed_at=same_time)
    insert_trace_risk(trace_id="tr-a", trace_risk_level="high", computed_at=same_time)

    page = retrieval.list_trace_risk(sort=retrieval.SORT_RISK_LEVEL_DESC)

    assert [item.trace_id for item in page.items] == ["tr-a", "tr-b"]


# ---------------------------------------------------------------------------
# AC-DAS-031-034 — keyset pagination: gapless, duplicate-free, terminal
# ---------------------------------------------------------------------------


def test_walk_computed_at_desc_is_gapless_and_duplicate_free(insert_trace_risk):
    for i in range(10):
        insert_trace_risk(
            trace_id=f"tr-{i}",
            computed_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
            + dt.timedelta(minutes=i),
        )

    seen: list[str] = []
    next_key = None
    for _ in range(20):
        page = retrieval.list_trace_risk(cursor=next_key, limit=3)
        seen.extend(item.trace_id for item in page.items)
        next_key = page.next_key
        if next_key is None:
            break

    assert sorted(seen) == sorted(f"tr-{i}" for i in range(10))
    assert len(seen) == len(set(seen))


def test_walk_risk_level_desc_is_gapless_and_duplicate_free_across_a_tie(
    insert_trace_risk,
):
    same_time = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    for i in range(6):
        insert_trace_risk(
            trace_id=f"tr-{i}", trace_risk_level="high", computed_at=same_time
        )

    seen: list[str] = []
    next_key = None
    for _ in range(20):
        page = retrieval.list_trace_risk(
            cursor=next_key, limit=2, sort=retrieval.SORT_RISK_LEVEL_DESC
        )
        seen.extend(item.trace_id for item in page.items)
        next_key = page.next_key
        if next_key is None:
            break

    assert sorted(seen) == sorted(f"tr-{i}" for i in range(6))
    assert len(seen) == len(set(seen))


def test_walk_with_limit_1_visits_every_row_exactly_once(insert_trace_risk):
    for i in range(5):
        insert_trace_risk(trace_id=f"tr-{i}")

    seen: list[str] = []
    next_key = None
    for _ in range(20):
        page = retrieval.list_trace_risk(cursor=next_key, limit=1)
        seen.extend(item.trace_id for item in page.items)
        next_key = page.next_key
        if next_key is None:
            break

    assert sorted(seen) == sorted(f"tr-{i}" for i in range(5))


def test_next_key_is_none_on_last_page(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1")
    insert_trace_risk(trace_id="tr-2")

    page = retrieval.list_trace_risk(limit=10)

    assert page.next_key is None


def test_cursor_past_end_returns_empty_items_not_error(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1")
    page1 = retrieval.list_trace_risk(limit=10)
    assert page1.next_key is None

    far_key = {
        "computed_at": (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=365)
        ).isoformat(),
        "trace_id": "",
    }
    page2 = retrieval.list_trace_risk(cursor=far_key, limit=10)

    assert page2.items == []
    assert page2.next_key is None


# ---------------------------------------------------------------------------
# Encoding: Decimal -> float, UUID -> str
# ---------------------------------------------------------------------------


def test_overall_confidence_is_float_not_decimal(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", overall_confidence="0.750")

    view = retrieval.get_trace_risk("tr-1")

    assert isinstance(view.overall_confidence, float)
    assert not isinstance(view.overall_confidence, decimal.Decimal)
    assert view.overall_confidence == pytest.approx(0.75)


def test_trace_risk_id_is_a_string(insert_trace_risk):
    inserted_id = insert_trace_risk(trace_id="tr-1")

    view = retrieval.get_trace_risk("tr-1")

    assert isinstance(view.trace_risk_id, str)
    assert view.trace_risk_id == inserted_id


def test_contributing_interaction_risk_ids_are_strings(insert_trace_risk):
    contrib_id = str(uuid.uuid4())
    insert_trace_risk(
        trace_id="tr-1", contributing_interaction_risk_ids=[contrib_id]
    )

    view = retrieval.get_trace_risk("tr-1")

    assert view.contributing_interaction_risk_ids == [contrib_id]
    assert isinstance(view.contributing_interaction_risk_ids[0], str)


def test_view_is_json_encodable_by_the_unmodified_json_default(insert_trace_risk):
    """The seam converts Decimal/UUID before the view leaves it — the HTTP
    layer's `_json_default` must not need any risk-specific handling."""
    import json

    from data_governance.risk.api.http import _json_default

    insert_trace_risk(trace_id="tr-1")
    view = retrieval.get_trace_risk("tr-1")

    json.dumps(view.__dict__, default=_json_default)


# ---------------------------------------------------------------------------
# enforcement_aggregation_mode (migration 0018)
# ---------------------------------------------------------------------------


def test_enforcement_aggregation_mode_is_surfaced(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1", enforcement_aggregation_mode="severity_max")

    view = retrieval.get_trace_risk("tr-1")

    assert view.enforcement_aggregation_mode == "severity_max"


def test_enforcement_aggregation_mode_defaults_to_none(insert_trace_risk):
    insert_trace_risk(trace_id="tr-1")

    view = retrieval.get_trace_risk("tr-1")

    assert view.enforcement_aggregation_mode is None


# ---------------------------------------------------------------------------
# Migration guard
# ---------------------------------------------------------------------------


def test_list_and_get_empty_before_risk_tables_migration(configured_db: str):
    with psycopg.connect(configured_db) as conn:
        conn.execute("DROP TABLE IF EXISTS trace_risk_records CASCADE")
        conn.commit()

    page = retrieval.list_trace_risk()
    assert page.items == []
    assert page.next_key is None

    assert retrieval.get_trace_risk("tr-1") is None

    history = retrieval.get_trace_risk_history("tr-1")
    assert history.items == []
