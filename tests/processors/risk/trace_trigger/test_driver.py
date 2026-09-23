"""Cursor-drain loop for the trace-risk-trigger processor (issues #102/#164).

The trace trigger is a Layer-2 DAS consumer that drains
``interaction_risk_records`` by ``seq`` (migration 0018) via the shared
cursor-driven driver and recomputes the trace risk rollup
(:func:`data_governance.risk.engine.trace_compute.compute_trace_risk`) for
each delivered record's ``trace_id`` — inside the per-item transaction, so the
rollup write commits atomically with the cursor advance (ADR-0007).

These tests prove the acceptance criteria:

- AC-DAS-008: every interaction risk record insert drives exactly one trace
  recomputation, aggregating all current interaction risk records.
- AC-DAS-008a is structural (this processor is the only ``compute_trace_risk``
  caller; the interaction write path never invokes it inline) — asserted here
  by the drain being the thing that produces the trace record, with none
  existing before it runs.
- AC-DAS-009: ``contributing_interaction_risk_ids`` resolve to the records
  that were current at computation time.
- AC-DAS-011 (spirit): a trace holding one critical and one none interaction
  rolls up to critical/block.
- AC-DAS-018 (trace-trigger portion): the NOTIFY wake delivers, and with
  LISTEN disabled the poll backstop alone still delivers.
- §9.4 partial-evidence: interaction risk v1 → v2 drives exactly one further
  trace recompute, producing trace risk v2 from the new current version.
- A recompute failure does not advance the cursor past the failed record
  (the record is re-delivered once the failure clears) — the transaction
  atomicity #164's cursoring exists to guarantee.

The drain tests run in-process against a migrated DB (fast), mirroring
``tests/processors/entity_ready/test_driver.py``.
"""

from __future__ import annotations

import threading
import time
import uuid

import psycopg
import pytest

from data_governance import db
from data_governance.processors.risk.trace_trigger import driver
from data_governance.risk.engine import trace_compute


# --- helpers -----------------------------------------------------------------


def _insert_risk_record(
    dsn: str,
    *,
    interaction_id: str,
    trace_id: str,
    version: int = 1,
    risk_level: str = "none",
    enforcement_type: str | None = None,
    policy_event_count: int = 1,
    triggered_rule_ids: list[str] | None = None,
    with_interaction: bool = True,
) -> tuple[str, int]:
    """Insert an interaction risk record the way the interaction engine does
    (DEFAULT-allocated seq); return ``(interaction_risk_id, seq)``.

    Also upserts the ``interactions`` row (unless *with_interaction* is
    False — the ghost-record case): the rollup counts only records whose
    interaction still exists."""
    with psycopg.connect(dsn) as conn:
        if with_interaction:
            conn.execute(
                "INSERT INTO interactions (id, trace_id, caller_entity_id, "
                "callee_entity_id, summary) VALUES (%s, %s, 'caller', 'callee', 's') "
                "ON CONFLICT (id) DO NOTHING",
                (interaction_id, trace_id),
            )
        row = conn.execute(
            "INSERT INTO interaction_risk_records ("
            "  interaction_id, trace_id, caller_entity_id, callee_entity_id,"
            "  version, computed_at, risk_level, enforcement_type,"
            "  policy_event_count, triggered_rule_ids"
            ") VALUES (%s, %s, 'caller', 'callee', %s, now(), %s, %s, %s, %s) "
            "RETURNING interaction_risk_id, seq",
            (
                interaction_id,
                trace_id,
                version,
                risk_level,
                enforcement_type,
                policy_event_count,
                triggered_rule_ids or [],
            ),
        ).fetchone()
        conn.commit()
    return str(row[0]), int(row[1])


def _cursor(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT last_processed_seq FROM processor_state "
            "WHERE processor_name = %s",
            (driver.PROCESSOR_NAME,),
        ).fetchone()
    return int(row[0]) if row else 0


def _trace_records(dsn: str, trace_id: str) -> list[tuple]:
    """All trace risk rows for *trace_id*, oldest version first:
    (version, level, enforcement, interaction_count, policy_event_count,
    contributing_ids)."""
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT version, trace_risk_level, trace_enforcement_type, "
            "interaction_count, policy_event_count, "
            "contributing_interaction_risk_ids "
            "FROM trace_risk_records WHERE trace_id = %s ORDER BY version ASC",
            (trace_id,),
        ).fetchall()


# --- drain: recompute per record, cursor advance ------------------------------


def test_drain_recomputes_trace_for_each_record(configured_db: str) -> None:
    """One drain over two records (two traces) produces one current trace
    risk record per trace and advances the cursor to the last seq."""
    _, s0 = _insert_risk_record(
        configured_db, interaction_id="i-a", trace_id="t-1", risk_level="high"
    )
    _, s1 = _insert_risk_record(
        configured_db, interaction_id="i-b", trace_id="t-2", risk_level="low"
    )

    new_cursor = driver.drain(0)

    assert new_cursor == s1
    assert _cursor(configured_db) == s1
    (v1_t1,) = _trace_records(configured_db, "t-1")
    assert v1_t1[0] == 1 and v1_t1[1] == "high" and v1_t1[3] == 1
    (v1_t2,) = _trace_records(configured_db, "t-2")
    assert v1_t2[0] == 1 and v1_t2[1] == "low"


def test_drain_from_empty_is_noop(configured_db: str) -> None:
    assert driver.drain(0) == 0
    assert _cursor(configured_db) == 0


def test_rollup_is_highest_across_current_records(configured_db: str) -> None:
    """AC-DAS-011 spirit: a trace with one critical/block and one none
    interaction rolls up to critical/block, counting both interactions."""
    id_none, _ = _insert_risk_record(
        configured_db, interaction_id="i-ok", trace_id="t-mix", risk_level="none"
    )
    id_crit, s1 = _insert_risk_record(
        configured_db,
        interaction_id="i-bad",
        trace_id="t-mix",
        risk_level="critical",
        enforcement_type="block",
        triggered_rule_ids=["DG-001"],
    )

    driver.drain(0)

    records = _trace_records(configured_db, "t-mix")
    current = records[-1]
    assert current[1] == "critical" and current[2] == "block"
    assert current[3] == 2  # interaction_count
    # AC-DAS-009: contributing ids are exactly the current record versions.
    assert sorted(str(u) for u in current[5]) == sorted([id_none, id_crit])


def test_new_interaction_risk_version_drives_new_trace_version(
    configured_db: str,
) -> None:
    """§9.4 partial-evidence walkthrough: interaction risk v1 (none) is rolled
    up, then v2 (critical) arrives — the drain recomputes exactly once more,
    writing trace risk v2 from the new current version. v1 stays immutable."""
    _insert_risk_record(
        configured_db, interaction_id="i-x", trace_id="t-v", risk_level="none"
    )
    cursor = driver.drain(0)
    (v1,) = _trace_records(configured_db, "t-v")
    assert v1[0] == 1 and v1[1] == "none"

    id_v2, _ = _insert_risk_record(
        configured_db,
        interaction_id="i-x",
        trace_id="t-v",
        version=2,
        risk_level="critical",
        enforcement_type="block",
    )
    driver.drain(cursor)

    records = _trace_records(configured_db, "t-v")
    assert [r[0] for r in records] == [1, 2]
    assert records[0][1] == "none", "prior trace version is immutable"
    assert records[1][1] == "critical" and records[1][2] == "block"
    assert records[1][3] == 1, "same interaction, new version — count stays 1"
    assert [str(u) for u in records[1][5]] == [id_v2], (
        "contributing ids must be the current (v2) record, not v1 (AC-DAS-009)"
    )


def test_redelivery_with_unchanged_evidence_writes_nothing(
    configured_db: str,
) -> None:
    """Re-draining the same records (simulating a restart that lost the
    cursor advance but kept the rollup write — or any re-delivery) recomputes
    idempotently: no new trace version when the rollup is unchanged."""
    _insert_risk_record(
        configured_db, interaction_id="i-r", trace_id="t-r", risk_level="medium"
    )
    driver.drain(0)
    assert len(_trace_records(configured_db, "t-r")) == 1

    driver.drain(0)  # re-deliver from scratch
    assert len(_trace_records(configured_db, "t-r")) == 1, (
        "unchanged rollup must not write a new version (FR-DAS-014 discipline)"
    )


def test_exactly_once_across_restart(configured_db: str) -> None:
    """After a full drain, a restart resuming from the durable cursor delivers
    nothing new; a record inserted after the restart is delivered once."""
    _insert_risk_record(
        configured_db, interaction_id="i-1", trace_id="t-a", risk_level="low"
    )
    first_cursor = driver.drain(0)

    with db.transaction() as tx:
        resumed = driver.read_cursor(tx)
    assert resumed == first_cursor
    assert driver.drain(resumed) == first_cursor

    _, s2 = _insert_risk_record(
        configured_db, interaction_id="i-2", trace_id="t-a", risk_level="high"
    )
    assert driver.drain(first_cursor) == s2
    records = _trace_records(configured_db, "t-a")
    assert records[-1][1] == "high" and records[-1][3] == 2


def test_ghost_interaction_records_do_not_contribute(configured_db: str) -> None:
    """Observed live: the sidecar lineage derivation rewrites a trace's
    interactions wholesale, so an exchange can be re-keyed mid-derivation,
    leaving immutable risk records for interaction ids that no longer exist.
    Those ghost records must not count toward the rollup — otherwise a
    stale (possibly critical) level freezes into every future trace
    version. The gather re-reads the live `interactions` table (FR-DAS-004
    discipline)."""
    live_id, _ = _insert_risk_record(
        configured_db, interaction_id="i-live", trace_id="t-ghost", risk_level="low"
    )
    _insert_risk_record(
        configured_db,
        interaction_id="i-ghost",
        trace_id="t-ghost",
        risk_level="critical",
        enforcement_type="block",
        with_interaction=False,
    )

    driver.drain(0)

    current = _trace_records(configured_db, "t-ghost")[-1]
    assert current[1] == "low", "the ghost's critical must not poison the rollup"
    assert current[3] == 1, "interaction_count counts only live interactions"
    assert [str(u) for u in current[5]] == [live_id]


# --- failure atomicity: recompute failure holds the cursor --------------------


def test_recompute_failure_does_not_advance_cursor(
    configured_db: str, monkeypatch
) -> None:
    """A failing recompute rolls back with the cursor advance (shared
    transaction): the record stays past the cursor and is re-delivered once
    the failure clears — never silently skipped."""
    _insert_risk_record(
        configured_db, interaction_id="i-f", trace_id="t-f", risk_level="critical"
    )

    def _boom(trace_id: str, *, tx=None) -> None:  # noqa: ANN001 — test stub
        raise RuntimeError("recompute failed (injected)")

    monkeypatch.setattr(driver.trace_compute, "compute_trace_risk", _boom)
    with pytest.raises(RuntimeError, match="injected"):
        driver.drain(0)

    assert _cursor(configured_db) == 0, "failed record must not advance the cursor"
    assert _trace_records(configured_db, "t-f") == []

    monkeypatch.undo()
    driver.drain(0)
    records = _trace_records(configured_db, "t-f")
    assert len(records) == 1 and records[0][1] == "critical"
    assert _cursor(configured_db) > 0


# --- run(): wakes on both the LISTEN notification and the poll backstop -------


def _drain_via_run(
    dsn: str, *, disable_listen: bool, monkeypatch, insert_after_start: bool
) -> list[tuple]:
    """Drive the real ``run()`` loop in a thread; return the trace risk rows
    written for the test trace. A written rollup proves the wake path (the
    notification, or the poll backstop when LISTEN is disabled) reached the
    drain."""
    # Shrink the poll backstop so the test is fast.
    monkeypatch.setattr(driver, "POLL_SECONDS", 0.2)

    if disable_listen:

        def _boom(channel, dsn):  # noqa: ANN001 — test stub
            raise db.ConnectionTimeout("listen disabled (injected)")

        monkeypatch.setattr(db, "listen", _boom)

    trace_id = f"t-run-{uuid.uuid4().hex[:8]}"
    if not insert_after_start:
        _insert_risk_record(
            dsn, interaction_id="i-run", trace_id=trace_id, risk_level="high"
        )

    stop = threading.Event()
    t = threading.Thread(target=driver.run, args=(stop, dsn), daemon=True)
    t.start()
    try:
        if insert_after_start:
            # Give the loop a moment to register its LISTEN / enter the wait.
            time.sleep(0.5)
            _insert_risk_record(
                dsn, interaction_id="i-run", trace_id=trace_id, risk_level="high"
            )

        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if _trace_records(dsn, trace_id):
                break
            time.sleep(0.05)
    finally:
        stop.set()
        t.join(timeout=10)
        assert not t.is_alive(), "run() must return promptly on stop_event"
    return _trace_records(dsn, trace_id)


def test_run_wakes_on_notify_and_recomputes(configured_db: str, monkeypatch) -> None:
    """``run()`` with LISTEN active rolls up a record inserted AFTER the loop
    started — migration 0016's ``dg_interaction_risk_written`` notification
    wakes the drain (low latency)."""
    records = _drain_via_run(
        configured_db,
        disable_listen=False,
        monkeypatch=monkeypatch,
        insert_after_start=True,
    )
    assert records and records[-1][1] == "high"


def test_run_poll_only_still_recomputes(configured_db: str, monkeypatch) -> None:
    """With LISTEN disabled, ``run()`` falls back to the poll backstop and
    still recomputes — AC-DAS-018: correctness never depends on the
    notification (notify is latency-only)."""
    records = _drain_via_run(
        configured_db,
        disable_listen=True,
        monkeypatch=monkeypatch,
        insert_after_start=True,
    )
    assert records and records[-1][1] == "high"


def test_run_drains_backlog_before_waiting(configured_db: str, monkeypatch) -> None:
    """``run()`` drains records already past the cursor before it starts
    waiting, so a record that landed before the loop started is not stranded
    until the first poll timeout."""
    records = _drain_via_run(
        configured_db,
        disable_listen=False,
        monkeypatch=monkeypatch,
        insert_after_start=False,
    )
    assert records and records[-1][1] == "high"
