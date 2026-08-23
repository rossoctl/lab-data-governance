"""In-process tests for the DAS trace-risk forest read (issue #109).

Drives ``retrieval.get_trace_risk_detail`` directly against a migrated DB
(migrations 0004/0009/0016/0018). This is the forest read: the trace risk
record plus every interaction of that trace, each with its legs, span
counts, and current risk record (if any). Unlike ``list_trace_risk`` /
``get_trace_risk`` (``test_trace_risk_reads.py``), this joins the DAS risk
tables against the P-interactions derived forest
(``interactions`` / ``interaction_legs`` / ``interaction_spans``).

Per the plan's decision #3, the forest carries span COUNTS, not span rows —
a deliberate deviation from the issue's literal "+ evidencing spans",
mirroring ``InteractionView.span_count``/``anchor_count`` in
``retrieval/interactions.py``.
"""

from __future__ import annotations

import datetime as dt
import uuid

import psycopg
import pytest

from data_governance import retrieval

_TID = "trace-detail-1"


def _uuid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def seed(configured_db: str):
    """Seed helpers for the forest: interactions, legs, spans, risk records.

    Each helper inserts exactly the columns the forest read needs; unrelated
    columns get innocuous defaults so tests only vary what they're actually
    testing.
    """

    class _Seed:
        def __init__(self, dsn: str):
            self._dsn = dsn

        def interaction(
            self,
            *,
            interaction_id: str,
            trace_id: str = _TID,
            parent_interaction_id: str | None = None,
            caller_entity_id: str = "ent-caller",
            callee_entity_id: str = "ent-callee",
            summary: str = "did a thing",
        ) -> None:
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    "INSERT INTO interactions (id, trace_id, "
                    "parent_interaction_id, caller_entity_id, "
                    "callee_entity_id, summary) VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        interaction_id,
                        trace_id,
                        parent_interaction_id,
                        caller_entity_id,
                        callee_entity_id,
                        summary,
                    ),
                )
                conn.commit()

        def leg(
            self,
            *,
            interaction_id: str,
            leg_type: str,
            occurred_at: dt.datetime | None = None,
            payload_hash: str | None = None,
            error: bool | None = None,
        ) -> None:
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    "INSERT INTO interaction_legs (interaction_id, leg_type, "
                    "occurred_at, payload_hash, error) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (interaction_id, leg_type, occurred_at, payload_hash, error),
                )
                conn.commit()

        def span(
            self,
            *,
            interaction_id: str,
            span_id: str,
            trace_id: str = _TID,
            role: str = "anchor",
            leg_type: str | None = "request",
        ) -> None:
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    "INSERT INTO interaction_spans (interaction_id, trace_id, "
                    "span_id, role, leg_type) VALUES (%s, %s, %s, %s, %s)",
                    (interaction_id, trace_id, span_id, role, leg_type),
                )
                conn.commit()

        def trace_risk(
            self,
            *,
            trace_id: str = _TID,
            version: int = 1,
            trace_risk_level: str = "low",
        ) -> None:
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    "INSERT INTO trace_risk_records (trace_risk_id, trace_id, "
                    "version, computed_at, trace_risk_level, interaction_count, "
                    "policy_event_count) VALUES (%s, %s, %s, now(), %s, %s, %s)",
                    (_uuid(), trace_id, version, trace_risk_level, 1, 1),
                )
                conn.commit()

        def interaction_risk(
            self,
            *,
            interaction_id: str,
            trace_id: str = _TID,
            version: int = 1,
            risk_level: str = "low",
        ) -> None:
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    "INSERT INTO interaction_risk_records ("
                    "interaction_risk_id, interaction_id, trace_id, "
                    "caller_entity_id, callee_entity_id, version, computed_at, "
                    "risk_level, policy_event_count) "
                    "VALUES (%s, %s, %s, %s, %s, %s, now(), %s, %s)",
                    (
                        _uuid(),
                        interaction_id,
                        trace_id,
                        "ent-caller",
                        "ent-callee",
                        version,
                        risk_level,
                        1,
                    ),
                )
                conn.commit()

    return _Seed(configured_db)


# ---------------------------------------------------------------------------
# 404 when no trace risk record
# ---------------------------------------------------------------------------


def test_returns_none_when_no_trace_risk_record(seed):
    assert retrieval.get_trace_risk_detail("nope") is None


def test_returns_none_when_interactions_exist_but_no_trace_risk_record(seed):
    seed.interaction(interaction_id="ix-1")

    assert retrieval.get_trace_risk_detail(_TID) is None


# ---------------------------------------------------------------------------
# Includes every interaction, ordered by request leg ASC
# ---------------------------------------------------------------------------


def test_includes_every_interaction_of_the_trace(seed):
    seed.trace_risk()
    seed.interaction(interaction_id="ix-1")
    seed.interaction(interaction_id="ix-2")

    detail = retrieval.get_trace_risk_detail(_TID)

    assert detail is not None
    assert {ix.interaction_id for ix in detail.interactions} == {"ix-1", "ix-2"}


def test_excludes_other_traces_interactions(seed):
    seed.trace_risk()
    seed.interaction(interaction_id="ix-1", trace_id=_TID)
    seed.interaction(interaction_id="ix-other", trace_id="other-trace")

    detail = retrieval.get_trace_risk_detail(_TID)

    assert [ix.interaction_id for ix in detail.interactions] == ["ix-1"]


def test_ordered_by_request_leg_occurred_at_ascending(seed):
    seed.trace_risk()
    seed.interaction(interaction_id="ix-late")
    seed.leg(
        interaction_id="ix-late",
        leg_type="request",
        occurred_at=dt.datetime(2026, 1, 10, tzinfo=dt.timezone.utc),
    )
    seed.interaction(interaction_id="ix-early")
    seed.leg(
        interaction_id="ix-early",
        leg_type="request",
        occurred_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    )

    detail = retrieval.get_trace_risk_detail(_TID)

    assert [ix.interaction_id for ix in detail.interactions] == [
        "ix-early",
        "ix-late",
    ]


def test_legless_interaction_sorts_last(seed):
    seed.trace_risk()
    seed.interaction(interaction_id="ix-legless")
    seed.interaction(interaction_id="ix-timed")
    seed.leg(
        interaction_id="ix-timed",
        leg_type="request",
        occurred_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    )

    detail = retrieval.get_trace_risk_detail(_TID)

    assert [ix.interaction_id for ix in detail.interactions] == [
        "ix-timed",
        "ix-legless",
    ]


# ---------------------------------------------------------------------------
# Legs: request then response
# ---------------------------------------------------------------------------


def test_legs_ordered_request_then_response(seed):
    seed.trace_risk()
    seed.interaction(interaction_id="ix-1")
    seed.leg(
        interaction_id="ix-1",
        leg_type="response",
        occurred_at=dt.datetime(2026, 1, 1, 0, 0, 1, tzinfo=dt.timezone.utc),
    )
    seed.leg(
        interaction_id="ix-1",
        leg_type="request",
        occurred_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    )

    detail = retrieval.get_trace_risk_detail(_TID)

    ix = detail.interactions[0]
    assert [leg.leg_type for leg in ix.legs] == ["request", "response"]


# ---------------------------------------------------------------------------
# Span counts distinguish anchor from total, no span rows carried
# ---------------------------------------------------------------------------


def test_span_counts_distinguish_anchor_from_total(seed):
    seed.trace_risk()
    seed.interaction(interaction_id="ix-1")
    seed.span(interaction_id="ix-1", span_id="sp-1", role="anchor")
    seed.span(interaction_id="ix-1", span_id="sp-2", role="info")
    seed.span(interaction_id="ix-1", span_id="sp-3", role="connector")

    detail = retrieval.get_trace_risk_detail(_TID)

    ix = detail.interactions[0]
    assert ix.span_count == 3
    assert ix.anchor_count == 1


def test_forest_carries_no_span_rows(seed):
    """Deliberate deviation from the issue's literal '+ evidencing spans' —
    the forest carries counts, not rows (plan decision #3)."""
    seed.trace_risk()
    seed.interaction(interaction_id="ix-1")
    seed.span(interaction_id="ix-1", span_id="sp-1")

    detail = retrieval.get_trace_risk_detail(_TID)

    ix = detail.interactions[0]
    assert not hasattr(ix, "spans")


def test_interaction_with_no_spans_has_zero_counts(seed):
    seed.trace_risk()
    seed.interaction(interaction_id="ix-1")

    detail = retrieval.get_trace_risk_detail(_TID)

    ix = detail.interactions[0]
    assert ix.span_count == 0
    assert ix.anchor_count == 0


# ---------------------------------------------------------------------------
# Risk: latest version carried, None when uncomputed
# ---------------------------------------------------------------------------


def test_interaction_risk_is_none_when_uncomputed(seed):
    seed.trace_risk()
    seed.interaction(interaction_id="ix-1")

    detail = retrieval.get_trace_risk_detail(_TID)

    assert detail.interactions[0].risk is None


def test_interaction_risk_carries_latest_version(seed):
    seed.trace_risk()
    seed.interaction(interaction_id="ix-1")
    seed.interaction_risk(interaction_id="ix-1", version=1, risk_level="low")
    seed.interaction_risk(interaction_id="ix-1", version=2, risk_level="critical")

    detail = retrieval.get_trace_risk_detail(_TID)

    risk = detail.interactions[0].risk
    assert risk is not None
    assert risk.risk_level == "critical"
    assert risk.version == 2


def test_interaction_risk_only_from_this_trace(seed):
    """An interaction_risk_records row for the same interaction_id under a
    different trace_id must not leak in (defensive; interaction_id should be
    trace-scoped, but the forest join must still be trace_id-qualified)."""
    seed.trace_risk()
    seed.interaction(interaction_id="ix-1")

    detail = retrieval.get_trace_risk_detail(_TID)

    assert detail.interactions[0].risk is None


# ---------------------------------------------------------------------------
# trace_risk carried at the top level, no reconciliation with forest length
# ---------------------------------------------------------------------------


def test_trace_risk_record_is_the_latest_version(seed):
    seed.trace_risk(version=1, trace_risk_level="low")
    seed.trace_risk(version=2, trace_risk_level="high")

    detail = retrieval.get_trace_risk_detail(_TID)

    assert detail.trace_risk.trace_risk_level == "high"
    assert detail.trace_risk.version == 2


def test_interaction_count_mismatch_is_not_reconciled_or_flagged(seed):
    """trace_risk.interaction_count is a snapshot at computed_at; the forest
    is read now. FR-DAS-084 forbids a completeness/warning field, so the two
    can disagree with no flag surfaced."""
    seed.trace_risk()  # interaction_count=1 by the seed helper's default
    seed.interaction(interaction_id="ix-1")
    seed.interaction(interaction_id="ix-2")
    seed.interaction(interaction_id="ix-3")

    detail = retrieval.get_trace_risk_detail(_TID)

    assert detail.trace_risk.interaction_count == 1
    assert len(detail.interactions) == 3
    assert not hasattr(detail, "is_complete")
    assert not hasattr(detail, "complete")
    assert not hasattr(detail, "completeness")


# ---------------------------------------------------------------------------
# Migration guard
# ---------------------------------------------------------------------------


def test_returns_none_before_risk_tables_migration(configured_db: str):
    with psycopg.connect(configured_db) as conn:
        conn.execute("DROP TABLE IF EXISTS trace_risk_records CASCADE")
        conn.commit()

    assert retrieval.get_trace_risk_detail(_TID) is None
