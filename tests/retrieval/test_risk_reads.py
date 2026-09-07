"""In-process tests for the DAS risk retrieval seam (issue #109).

Drives ``retrieval.list_interaction_risk`` / ``get_interaction_risk`` /
``get_interaction_risk_history`` directly against a migrated DB (migration
0016). This is the interaction-risk half of the seam; the trace-risk half
(``list_trace_risk`` / ``get_trace_risk`` / ``get_trace_risk_history``) and
the forest read (``get_trace_risk_detail``) land in later slices of this
same issue.

Seeding is direct SQL against ``interaction_risk_records`` — there is no
processor that writes this table in-test (issue #101's ``compute.py`` calls
out to OPA), so tests insert rows exactly as the write path would.
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid

import psycopg
import pytest

from data_governance import retrieval

_TID = "trace-risk-1"


def _uuid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def insert_interaction_risk(configured_db: str):
    """Insert one ``interaction_risk_records`` row, returning its id.

    Every column has a sensible default so tests only pass what they're
    actually varying. ``computed_at`` defaults to ``now()`` in SQL when not
    given explicitly, so successive inserts without an explicit timestamp are
    still strictly ordered by insertion (real-clock resolution).
    """

    def _insert(
        *,
        interaction_id: str,
        trace_id: str = _TID,
        version: int = 1,
        risk_level: str = "low",
        enforcement_type: str | None = None,
        caller_entity_id: str = "ent-caller",
        callee_entity_id: str = "ent-callee",
        parent_interaction_id: str | None = None,
        policy_event_count: int = 1,
        triggered_rule_ids: list[str] | None = None,
        classification_summary: dict | None = None,
        overall_confidence: str | None = "0.900",
        computed_at: dt.datetime | None = None,
    ) -> str:
        interaction_risk_id = _uuid()
        with psycopg.connect(configured_db) as conn:
            conn.execute(
                "INSERT INTO interaction_risk_records ("
                "interaction_risk_id, interaction_id, trace_id, "
                "parent_interaction_id, caller_entity_id, callee_entity_id, "
                "version, computed_at, risk_level, enforcement_type, "
                "policy_event_count, triggered_rule_ids, "
                "classification_summary, overall_confidence"
                ") VALUES (%s, %s, %s, %s, %s, %s, %s, "
                "COALESCE(%s, now()), %s, %s, %s, %s, %s::jsonb, %s)",
                (
                    interaction_risk_id,
                    interaction_id,
                    trace_id,
                    parent_interaction_id,
                    caller_entity_id,
                    callee_entity_id,
                    version,
                    computed_at,
                    risk_level,
                    enforcement_type,
                    policy_event_count,
                    triggered_rule_ids or [],
                    _dumps(classification_summary),
                    overall_confidence,
                ),
            )
            conn.commit()
        return interaction_risk_id

    return _insert


def _dumps(value: dict | None) -> str | None:
    import json

    return json.dumps(value) if value is not None else None


# ---------------------------------------------------------------------------
# AC-DAS-016 — latest version only
# ---------------------------------------------------------------------------


def test_list_returns_only_latest_version_per_interaction(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", version=1, risk_level="low")
    insert_interaction_risk(interaction_id="ix-1", version=2, risk_level="high")

    page = retrieval.list_interaction_risk()

    assert len(page.items) == 1
    assert page.items[0].risk_level == "high"
    assert page.items[0].version == 2


def test_list_latest_version_wins_even_when_an_older_version_is_more_severe(
    insert_interaction_risk,
):
    """Filtering must happen AFTER the latest-per-key reduction: v1 is
    critical, v2 (latest) is low — a risk_level=critical filter must not
    surface the superseded v1."""
    insert_interaction_risk(interaction_id="ix-1", version=1, risk_level="critical")
    insert_interaction_risk(interaction_id="ix-1", version=2, risk_level="low")

    page = retrieval.list_interaction_risk(risk_level=["critical"])

    assert page.items == []


def test_get_returns_latest_version(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", version=1, risk_level="low")
    insert_interaction_risk(interaction_id="ix-1", version=2, risk_level="high")

    view = retrieval.get_interaction_risk("ix-1")

    assert view is not None
    assert view.risk_level == "high"
    assert view.version == 2


def test_get_unknown_interaction_id_returns_none(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1")

    assert retrieval.get_interaction_risk("nope") is None


# ---------------------------------------------------------------------------
# AC-DAS-017 — history: all versions, ascending
# ---------------------------------------------------------------------------


def test_history_returns_all_versions_ascending(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", version=1, risk_level="low")
    insert_interaction_risk(interaction_id="ix-1", version=2, risk_level="medium")
    insert_interaction_risk(interaction_id="ix-1", version=3, risk_level="high")

    page = retrieval.get_interaction_risk_history("ix-1")

    assert [item.version for item in page.items] == [1, 2, 3]


def test_history_of_unknown_id_is_empty_page(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1")

    page = retrieval.get_interaction_risk_history("nope")

    assert page.items == []
    assert page.next_key is None


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_filter_by_trace_id(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", trace_id="trace-a")
    insert_interaction_risk(interaction_id="ix-2", trace_id="trace-b")

    page = retrieval.list_interaction_risk(trace_id="trace-a")

    assert [item.interaction_id for item in page.items] == ["ix-1"]


def test_filter_by_entity_id_matches_caller_or_callee(insert_interaction_risk):
    insert_interaction_risk(
        interaction_id="ix-1", caller_entity_id="ent-x", callee_entity_id="ent-y"
    )
    insert_interaction_risk(
        interaction_id="ix-2", caller_entity_id="ent-y", callee_entity_id="ent-z"
    )
    insert_interaction_risk(
        interaction_id="ix-3", caller_entity_id="ent-p", callee_entity_id="ent-q"
    )

    page = retrieval.list_interaction_risk(entity_id="ent-y")

    assert {item.interaction_id for item in page.items} == {"ix-1", "ix-2"}


def test_filter_by_entity_id_matching_both_caller_and_callee_returns_row_once(
    insert_interaction_risk,
):
    insert_interaction_risk(
        interaction_id="ix-1", caller_entity_id="ent-x", callee_entity_id="ent-x"
    )

    page = retrieval.list_interaction_risk(entity_id="ent-x")

    assert [item.interaction_id for item in page.items] == ["ix-1"]


def test_filter_by_risk_level_csv_union(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", risk_level="critical")
    insert_interaction_risk(interaction_id="ix-2", risk_level="high")
    insert_interaction_risk(interaction_id="ix-3", risk_level="low")

    page = retrieval.list_interaction_risk(risk_level=["critical", "high"])

    assert {item.interaction_id for item in page.items} == {"ix-1", "ix-2"}


def test_filter_by_risk_level_empty_list_matches_nothing(insert_interaction_risk):
    """?risk_level= -> parse_csv_param returns [] -> matches nothing, not
    'no filter' — same semantics as catalog._accepts."""
    insert_interaction_risk(interaction_id="ix-1", risk_level="critical")

    page = retrieval.list_interaction_risk(risk_level=[])

    assert page.items == []


def test_filter_by_risk_level_unknown_value_is_empty_not_error(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", risk_level="critical")

    page = retrieval.list_interaction_risk(risk_level=["fuchsia"])

    assert page.items == []


def test_filter_by_enforcement_type(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", enforcement_type="block")
    insert_interaction_risk(interaction_id="ix-2", enforcement_type="allow")
    insert_interaction_risk(interaction_id="ix-3", enforcement_type=None)

    page = retrieval.list_interaction_risk(enforcement_type="block")

    assert [item.interaction_id for item in page.items] == ["ix-1"]


def test_filter_by_rule_id(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", triggered_rule_ids=["rule-a", "rule-b"])
    insert_interaction_risk(interaction_id="ix-2", triggered_rule_ids=["rule-c"])

    page = retrieval.list_interaction_risk(rule_id="rule-a")

    assert [item.interaction_id for item in page.items] == ["ix-1"]


def test_filters_combine_conjunctively(insert_interaction_risk):
    insert_interaction_risk(
        interaction_id="ix-1", risk_level="critical", enforcement_type="block"
    )
    insert_interaction_risk(
        interaction_id="ix-2", risk_level="critical", enforcement_type="allow"
    )

    page = retrieval.list_interaction_risk(
        risk_level=["critical"], enforcement_type="block"
    )

    assert [item.interaction_id for item in page.items] == ["ix-1"]


def test_filter_by_time_from_to_inclusive(insert_interaction_risk):
    early = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    mid = dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc)
    late = dt.datetime(2026, 1, 10, tzinfo=dt.timezone.utc)
    insert_interaction_risk(interaction_id="ix-early", computed_at=early)
    insert_interaction_risk(interaction_id="ix-mid", computed_at=mid)
    insert_interaction_risk(interaction_id="ix-late", computed_at=late)

    page = retrieval.list_interaction_risk(time_from=mid, time_to=mid)
    assert [item.interaction_id for item in page.items] == ["ix-mid"]

    page = retrieval.list_interaction_risk(time_from=mid, time_to=late)
    assert {item.interaction_id for item in page.items} == {"ix-mid", "ix-late"}


def test_filter_time_from_after_time_to_is_empty_not_error(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1")

    page = retrieval.list_interaction_risk(
        time_from=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
        time_to=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    )

    assert page.items == []


# ---------------------------------------------------------------------------
# regulatory_tag
# ---------------------------------------------------------------------------


def test_regulatory_tag_matches_a_tag_on_any_leg(insert_interaction_risk):
    insert_interaction_risk(
        interaction_id="ix-1",
        classification_summary={
            "request": {"regulatory_tags": ["GDPR"]},
            "response": {"regulatory_tags": []},
        },
    )

    page = retrieval.list_interaction_risk(regulatory_tag="GDPR")

    assert [item.interaction_id for item in page.items] == ["ix-1"]


def test_regulatory_tag_row_returned_once_when_several_legs_match(insert_interaction_risk):
    insert_interaction_risk(
        interaction_id="ix-1",
        classification_summary={
            "request": {"regulatory_tags": ["GDPR"]},
            "response": {"regulatory_tags": ["GDPR"]},
        },
    )

    page = retrieval.list_interaction_risk(regulatory_tag="GDPR")

    assert [item.interaction_id for item in page.items] == ["ix-1"]


def test_regulatory_tag_does_not_match_pending_leg(insert_interaction_risk):
    insert_interaction_risk(
        interaction_id="ix-1",
        classification_summary={"request": {"classification_pending": True}},
    )

    page = retrieval.list_interaction_risk(regulatory_tag="GDPR")

    assert page.items == []


def test_regulatory_tag_does_not_match_no_payload_leg(insert_interaction_risk):
    insert_interaction_risk(
        interaction_id="ix-1",
        classification_summary={"request": {"payload": None}},
    )

    page = retrieval.list_interaction_risk(regulatory_tag="GDPR")

    assert page.items == []


def test_regulatory_tag_does_not_match_null_classification_summary(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", classification_summary=None)

    page = retrieval.list_interaction_risk(regulatory_tag="GDPR")

    assert page.items == []


def test_regulatory_tag_is_case_sensitive(insert_interaction_risk):
    insert_interaction_risk(
        interaction_id="ix-1",
        classification_summary={"request": {"regulatory_tags": ["GDPR"]}},
    )

    page = retrieval.list_interaction_risk(regulatory_tag="gdpr")

    assert page.items == []


def test_regulatory_tag_does_not_match_under_a_different_key(insert_interaction_risk):
    """A tag-looking value under e.g. 'primary_domain' must not match — only
    the 'regulatory_tags' key is consulted."""
    insert_interaction_risk(
        interaction_id="ix-1",
        classification_summary={"request": {"primary_domain": "GDPR"}},
    )

    page = retrieval.list_interaction_risk(regulatory_tag="GDPR")

    assert page.items == []


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------


def test_default_sort_is_computed_at_desc(insert_interaction_risk):
    early = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    late = dt.datetime(2026, 1, 10, tzinfo=dt.timezone.utc)
    insert_interaction_risk(interaction_id="ix-early", computed_at=early)
    insert_interaction_risk(interaction_id="ix-late", computed_at=late)

    page = retrieval.list_interaction_risk()

    assert [item.interaction_id for item in page.items] == ["ix-late", "ix-early"]


def test_sort_risk_level_desc_is_most_severe_first(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-low", risk_level="low")
    insert_interaction_risk(interaction_id="ix-critical", risk_level="critical")
    insert_interaction_risk(interaction_id="ix-medium", risk_level="medium")

    page = retrieval.list_interaction_risk(sort=retrieval.SORT_RISK_LEVEL_DESC)

    assert [item.interaction_id for item in page.items] == [
        "ix-critical",
        "ix-medium",
        "ix-low",
    ]


def test_sort_risk_level_desc_places_novel_value_last(insert_interaction_risk):
    """A risk_level outside RISK_LEVEL_ORDER must rank after every known
    value, including 'unknown' — the len(order)+1 off-by-one guard."""
    insert_interaction_risk(interaction_id="ix-unknown", risk_level="unknown")
    insert_interaction_risk(interaction_id="ix-novel", risk_level="fuchsia")
    insert_interaction_risk(interaction_id="ix-low", risk_level="low")

    page = retrieval.list_interaction_risk(sort=retrieval.SORT_RISK_LEVEL_DESC)

    assert [item.interaction_id for item in page.items] == [
        "ix-low",
        "ix-unknown",
        "ix-novel",
    ]


def test_sort_ties_break_by_computed_at_desc_then_id(insert_interaction_risk):
    same_time = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    insert_interaction_risk(
        interaction_id="ix-b", risk_level="high", computed_at=same_time
    )
    insert_interaction_risk(
        interaction_id="ix-a", risk_level="high", computed_at=same_time
    )

    page = retrieval.list_interaction_risk(sort=retrieval.SORT_RISK_LEVEL_DESC)

    assert [item.interaction_id for item in page.items] == ["ix-a", "ix-b"]


# ---------------------------------------------------------------------------
# AC-DAS-031-034 — keyset pagination: gapless, duplicate-free, terminal
# ---------------------------------------------------------------------------


def test_walk_computed_at_desc_is_gapless_and_duplicate_free(insert_interaction_risk):
    for i in range(10):
        insert_interaction_risk(
            interaction_id=f"ix-{i}",
            computed_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
            + dt.timedelta(minutes=i),
        )

    seen: list[str] = []
    next_key = None
    for _ in range(20):
        page = retrieval.list_interaction_risk(cursor=next_key, limit=3)
        seen.extend(item.interaction_id for item in page.items)
        next_key = page.next_key
        if next_key is None:
            break

    assert sorted(seen) == sorted(f"ix-{i}" for i in range(10))
    assert len(seen) == len(set(seen))


def test_walk_risk_level_desc_is_gapless_and_duplicate_free_across_a_tie(
    insert_interaction_risk,
):
    same_time = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    for i in range(6):
        insert_interaction_risk(
            interaction_id=f"ix-{i}", risk_level="high", computed_at=same_time
        )

    seen: list[str] = []
    next_key = None
    for _ in range(20):
        page = retrieval.list_interaction_risk(
            cursor=next_key, limit=2, sort=retrieval.SORT_RISK_LEVEL_DESC
        )
        seen.extend(item.interaction_id for item in page.items)
        next_key = page.next_key
        if next_key is None:
            break

    assert sorted(seen) == sorted(f"ix-{i}" for i in range(6))
    assert len(seen) == len(set(seen))


def test_walk_with_limit_1_visits_every_row_exactly_once(insert_interaction_risk):
    for i in range(5):
        insert_interaction_risk(interaction_id=f"ix-{i}")

    seen: list[str] = []
    next_key = None
    for _ in range(20):
        page = retrieval.list_interaction_risk(cursor=next_key, limit=1)
        seen.extend(item.interaction_id for item in page.items)
        next_key = page.next_key
        if next_key is None:
            break

    assert sorted(seen) == sorted(f"ix-{i}" for i in range(5))


def test_next_key_is_none_on_last_page(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1")
    insert_interaction_risk(interaction_id="ix-2")

    page = retrieval.list_interaction_risk(limit=10)

    assert page.next_key is None


def test_cursor_past_end_returns_empty_items_not_error(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1")
    page1 = retrieval.list_interaction_risk(limit=10)
    assert page1.next_key is None

    # Manually construct a cursor keyed past every row. The default sort is
    # computed_at DESC, so "past the end" of that walk is a timestamp
    # *earlier* than any real row (a future timestamp would instead be
    # "before the first page" and match everything).
    far_key = {
        "computed_at": (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=365)
        ).isoformat(),
        "interaction_id": "",
    }
    page2 = retrieval.list_interaction_risk(cursor=far_key, limit=10)

    assert page2.items == []
    assert page2.next_key is None


# ---------------------------------------------------------------------------
# Encoding: Decimal -> float, UUID -> str
# ---------------------------------------------------------------------------


def test_overall_confidence_is_float_not_decimal(insert_interaction_risk):
    insert_interaction_risk(interaction_id="ix-1", overall_confidence="0.750")

    view = retrieval.get_interaction_risk("ix-1")

    assert isinstance(view.overall_confidence, float)
    assert not isinstance(view.overall_confidence, decimal.Decimal)
    assert view.overall_confidence == pytest.approx(0.75)


def test_interaction_risk_id_is_a_string(insert_interaction_risk):
    inserted_id = insert_interaction_risk(interaction_id="ix-1")

    view = retrieval.get_interaction_risk("ix-1")

    assert isinstance(view.interaction_risk_id, str)
    assert view.interaction_risk_id == inserted_id


def test_view_is_json_encodable_by_the_unmodified_json_default(insert_interaction_risk):
    """The seam converts Decimal/UUID before the view leaves it — the HTTP
    layer's `_json_default` must not need any risk-specific handling."""
    import json

    from data_governance.risk.api.http import _json_default

    insert_interaction_risk(interaction_id="ix-1")
    view = retrieval.get_interaction_risk("ix-1")

    json.dumps(view.__dict__, default=_json_default)


# ---------------------------------------------------------------------------
# Migration guard
# ---------------------------------------------------------------------------


def test_list_and_get_empty_before_risk_tables_migration(configured_db: str):
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "DROP TABLE IF EXISTS interaction_risk_records CASCADE"
        )
        conn.commit()

    page = retrieval.list_interaction_risk()
    assert page.items == []
    assert page.next_key is None

    assert retrieval.get_interaction_risk("ix-1") is None

    history = retrieval.get_interaction_risk_history("ix-1")
    assert history.items == []
