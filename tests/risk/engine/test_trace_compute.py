"""Tests for the trace risk engine's write path (issue #102).

``data_governance.risk.engine.trace_compute`` ties the pieces together:
gather the trace's current (latest-version) interaction risk records ->
aggregate (FR-DAS-021, via ``trace_aggregate.aggregate_trace_risk``) -> write
a new ``trace_risk_records`` version only when the computed record actually
differs from the latest stored one (mirrors #101's FR-DAS-014 idempotency
discipline), never mutating a prior version.

Real DB via ``configured_db``. No OPA client involved — trace risk is a pure
aggregation over already-computed interaction risk records, not a fresh
policy evaluation.

Per #102's dependency on #164 (still open — the trigger and its cursoring
infrastructure are being redesigned there), this only tests
``compute_trace_risk`` as a directly-callable function. No StreamSpec
driver/``__main__.py`` wiring exists yet; that is #164's/a follow-up issue's
concern, not this one's.
"""

from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest

from data_governance.risk.engine.trace_compute import compute_trace_risk

_TID = "trace-tx-1"
_ENT_A = "ent-tx-a"
_ENT_B = "ent-tx-b"
_ENT_C = "ent-tx-c"


def _insert_interaction(
    conn: psycopg.Connection,
    *,
    interaction_id: str,
    trace_id: str = _TID,
    caller_entity_id: str = _ENT_A,
    callee_entity_id: str = _ENT_B,
) -> None:
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, "
        "callee_entity_id, summary) "
        "VALUES (%s, %s, %s, %s, 'did a thing')",
        (interaction_id, trace_id, caller_entity_id, callee_entity_id),
    )


def _insert_risk_record(
    conn: psycopg.Connection,
    *,
    interaction_id: str,
    trace_id: str = _TID,
    version: int = 1,
    risk_level: str = "low",
    enforcement_type: str | None = None,
    policy_event_count: int = 1,
    triggered_rule_ids: list[str] | None = None,
    caller_entity_id: str = _ENT_A,
    callee_entity_id: str = _ENT_B,
    overall_confidence: float | None = None,
) -> str:
    row = conn.execute(
        "INSERT INTO interaction_risk_records ("
        "interaction_id, trace_id, caller_entity_id, callee_entity_id, "
        "version, computed_at, risk_level, enforcement_type, "
        "policy_event_count, triggered_rule_ids, overall_confidence"
        ") VALUES (%s, %s, %s, %s, %s, now(), %s, %s, %s, %s, %s) "
        "RETURNING interaction_risk_id",
        (
            interaction_id,
            trace_id,
            caller_entity_id,
            callee_entity_id,
            version,
            risk_level,
            enforcement_type,
            policy_event_count,
            triggered_rule_ids or [],
            overall_confidence,
        ),
    ).fetchone()
    return str(row[0])


@pytest.fixture()
def seeded(configured_db: str) -> str:
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn, interaction_id="ix-tx-1")
        conn.commit()
    return configured_db


def _latest_trace_row(dsn: str, trace_id: str = _TID) -> tuple | None:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT version, trace_risk_level, trace_enforcement_type, "
            "interaction_count, policy_event_count, all_entity_ids, "
            "triggered_rule_ids, overall_confidence, "
            "contributing_interaction_risk_ids, "
            "risk_compounding_mode, enforcement_aggregation_mode "
            "FROM trace_risk_records WHERE trace_id = %s "
            "ORDER BY version DESC LIMIT 1",
            (trace_id,),
        ).fetchone()


def _all_trace_versions(dsn: str, trace_id: str = _TID) -> list[int]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT version FROM trace_risk_records "
            "WHERE trace_id = %s ORDER BY version",
            (trace_id,),
        ).fetchall()
    return [r[0] for r in rows]


# --- first compute ---------------------------------------------------------------


def test_first_compute_writes_version_1(seeded: str):
    with psycopg.connect(seeded) as conn:
        _insert_risk_record(conn, interaction_id="ix-tx-1", risk_level="high")
        conn.commit()

    compute_trace_risk(_TID)

    row = _latest_trace_row(seeded)
    assert row is not None
    version, trace_risk_level, _enf, interaction_count, policy_event_count, *_ = row
    assert version == 1
    assert trace_risk_level == "high"
    assert interaction_count == 1
    assert policy_event_count == 1


def test_written_record_persists_the_aggregation_modes_used(seeded: str):
    with psycopg.connect(seeded) as conn:
        _insert_risk_record(conn, interaction_id="ix-tx-1", risk_level="high")
        conn.commit()

    compute_trace_risk(_TID)

    row = _latest_trace_row(seeded)
    risk_compounding_mode, enforcement_aggregation_mode = row[9], row[10]
    assert risk_compounding_mode == "severity_max"
    assert enforcement_aggregation_mode == "severity_max"


def test_no_is_complete_equivalent_field_is_ever_written(seeded: str):
    with psycopg.connect(seeded) as conn:
        _insert_risk_record(conn, interaction_id="ix-tx-1")
        conn.commit()

    compute_trace_risk(_TID)

    with psycopg.connect(seeded) as conn:
        cols = [
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'trace_risk_records'"
            ).fetchall()
        ]
    assert not any("complete" in c or c == "status" for c in cols)


# --- aggregation across multiple interactions (AC-DAS-011, DG-008 scenario) ----


def test_aggregates_highest_risk_and_strictest_enforcement_across_interactions(seeded: str):
    with psycopg.connect(seeded) as conn:
        _insert_interaction(conn, interaction_id="ix-tx-2")
        _insert_risk_record(
            conn,
            interaction_id="ix-tx-1",
            risk_level="high",
            enforcement_type="escalate",
            triggered_rule_ids=["DG-008"],
        )
        _insert_risk_record(
            conn,
            interaction_id="ix-tx-2",
            risk_level="critical",
            enforcement_type="block",
            triggered_rule_ids=["DG-008"],
        )
        conn.commit()

    compute_trace_risk(_TID)

    row = _latest_trace_row(seeded)
    _version, trace_risk_level, trace_enforcement_type, interaction_count, policy_event_count, *_ = row
    assert trace_risk_level == "critical"
    assert trace_enforcement_type == "block"
    assert interaction_count == 2
    assert policy_event_count == 2


def test_all_entity_ids_union_across_interactions(seeded: str):
    with psycopg.connect(seeded) as conn:
        _insert_interaction(conn, interaction_id="ix-tx-2")
        _insert_risk_record(
            conn,
            interaction_id="ix-tx-1",
            caller_entity_id=_ENT_A,
            callee_entity_id=_ENT_B,
        )
        _insert_risk_record(
            conn,
            interaction_id="ix-tx-2",
            caller_entity_id=_ENT_B,
            callee_entity_id=_ENT_C,
        )
        conn.commit()

    compute_trace_risk(_TID)

    row = _latest_trace_row(seeded)
    all_entity_ids = row[5]
    assert sorted(all_entity_ids) == sorted([_ENT_A, _ENT_B, _ENT_C])


def test_contributing_interaction_risk_ids_are_the_current_record_ids(seeded: str):
    with psycopg.connect(seeded) as conn:
        risk_id = _insert_risk_record(conn, interaction_id="ix-tx-1")
        conn.commit()

    compute_trace_risk(_TID)

    row = _latest_trace_row(seeded)
    contributing_ids = row[8]
    assert [str(c) for c in contributing_ids] == [risk_id]


def test_only_current_latest_version_interaction_risk_records_are_used(seeded: str):
    """A superseded (non-latest) version of an interaction's risk record must
    not contribute to the trace rollup — only the latest version per
    interaction_id counts as "current"."""
    with psycopg.connect(seeded) as conn:
        _insert_risk_record(
            conn, interaction_id="ix-tx-1", version=1, risk_level="critical"
        )
        _insert_risk_record(
            conn, interaction_id="ix-tx-1", version=2, risk_level="low"
        )
        conn.commit()

    compute_trace_risk(_TID)

    row = _latest_trace_row(seeded)
    _version, trace_risk_level, _enf, interaction_count, *_ = row
    assert trace_risk_level == "low"
    assert interaction_count == 1


# --- idempotency ------------------------------------------------------------------


def test_unchanged_current_records_write_nothing_on_recompute(seeded: str):
    with psycopg.connect(seeded) as conn:
        _insert_risk_record(conn, interaction_id="ix-tx-1", risk_level="high")
        conn.commit()

    compute_trace_risk(_TID)
    assert _all_trace_versions(seeded) == [1]

    compute_trace_risk(_TID)
    assert _all_trace_versions(seeded) == [1]


def test_legacy_null_mode_row_bumps_once_then_stabilizes(seeded: str):
    """A trace_risk_records row written before the mode columns existed has
    risk_compounding_mode/enforcement_aggregation_mode = NULL. Since the
    idempotency comparison includes both columns, the freshly computed
    "severity_max" differs from that stored NULL — the trace picks up exactly
    one extra version the next time it is recomputed, and stabilizes after
    that (a one-time, self-correcting bump, not a repeat-forever mismatch)."""
    with psycopg.connect(seeded) as conn:
        _insert_risk_record(conn, interaction_id="ix-tx-1", risk_level="high")
        conn.execute(
            "INSERT INTO trace_risk_records ("
            "trace_id, version, computed_at, trace_risk_level, "
            "interaction_count, policy_event_count"
            ") VALUES (%s, 1, now(), 'high', 1, 1)",
            (_TID,),
        )
        conn.commit()
    assert _all_trace_versions(seeded) == [1]

    compute_trace_risk(_TID)
    assert _all_trace_versions(seeded) == [1, 2]
    row = _latest_trace_row(seeded)
    assert row[9] == "severity_max"
    assert row[10] == "severity_max"

    compute_trace_risk(_TID)
    assert _all_trace_versions(seeded) == [1, 2]


# --- new interaction risk -> new version ------------------------------------------


def test_new_interaction_risk_record_writes_version_2(seeded: str):
    with psycopg.connect(seeded) as conn:
        _insert_risk_record(conn, interaction_id="ix-tx-1", risk_level="low")
        conn.commit()

    compute_trace_risk(_TID)
    assert _all_trace_versions(seeded) == [1]

    with psycopg.connect(seeded) as conn:
        _insert_interaction(conn, interaction_id="ix-tx-2")
        _insert_risk_record(conn, interaction_id="ix-tx-2", risk_level="high")
        conn.commit()

    compute_trace_risk(_TID)
    assert _all_trace_versions(seeded) == [1, 2]

    row = _latest_trace_row(seeded)
    version, trace_risk_level, *_ = row
    assert version == 2
    assert trace_risk_level == "high"


def test_version_monotonically_increases_across_several_recomputes(seeded: str):
    with psycopg.connect(seeded) as conn:
        _insert_risk_record(conn, interaction_id="ix-tx-1", risk_level="low")
        conn.commit()
    compute_trace_risk(_TID)

    with psycopg.connect(seeded) as conn:
        _insert_interaction(conn, interaction_id="ix-tx-2")
        _insert_risk_record(conn, interaction_id="ix-tx-2", risk_level="medium")
        conn.commit()
    compute_trace_risk(_TID)

    with psycopg.connect(seeded) as conn:
        _insert_interaction(conn, interaction_id="ix-tx-3")
        _insert_risk_record(conn, interaction_id="ix-tx-3", risk_level="high")
        conn.commit()
    compute_trace_risk(_TID)

    assert _all_trace_versions(seeded) == [1, 2, 3]


# --- NOTIFY / immutability ---------------------------------------------------------


def test_prior_version_row_is_never_mutated(seeded: str):
    with psycopg.connect(seeded) as conn:
        _insert_risk_record(conn, interaction_id="ix-tx-1", risk_level="low")
        conn.commit()
    compute_trace_risk(_TID)
    version_1_row = _latest_trace_row(seeded)

    with psycopg.connect(seeded) as conn:
        _insert_interaction(conn, interaction_id="ix-tx-2")
        _insert_risk_record(conn, interaction_id="ix-tx-2", risk_level="high")
        conn.commit()
    compute_trace_risk(_TID)

    with psycopg.connect(seeded) as conn:
        row = conn.execute(
            "SELECT version, trace_risk_level FROM trace_risk_records "
            "WHERE trace_id = %s AND version = 1",
            (_TID,),
        ).fetchone()
    assert row == (version_1_row[0], version_1_row[1])


# --- corner cases -------------------------------------------------------------------


def test_trace_with_no_interaction_risk_records_yet_still_computes(configured_db: str):
    """A trace_id with zero current interaction risk records (e.g. called
    speculatively, or all interactions for the trace have been superseded to
    nothing) still writes a valid record — the least-severe rollup, not an
    error."""
    compute_trace_risk("trace-with-nothing")

    row = _latest_trace_row(configured_db, "trace-with-nothing")
    version, trace_risk_level, trace_enforcement_type, interaction_count, policy_event_count, *_ = row
    assert version == 1
    assert trace_risk_level == "none"
    assert trace_enforcement_type is None
    assert interaction_count == 0
    assert policy_event_count == 0


def test_overall_confidence_is_averaged_across_current_records(seeded: str):
    with psycopg.connect(seeded) as conn:
        _insert_interaction(conn, interaction_id="ix-tx-2")
        _insert_risk_record(
            conn, interaction_id="ix-tx-1", overall_confidence=0.8
        )
        _insert_risk_record(
            conn, interaction_id="ix-tx-2", overall_confidence=0.6
        )
        conn.commit()

    compute_trace_risk(_TID)

    row = _latest_trace_row(seeded)
    overall_confidence = row[7]
    assert overall_confidence == Decimal("0.700")
