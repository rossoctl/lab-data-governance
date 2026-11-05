"""Cursor-drain loop for the alerts processor (issues #104/#175).

The alerts processor drains ``trace_risk_records`` by ``seq`` (migration
0023) via the shared driver and runs alert generation per delivered record
inside the per-item transaction, so the alert write commits atomically with
the cursor advance (ADR-0007) and every alert follows a durable trace risk
write (NFR-DAS-003).

Covered here: drain creates/dedups/supersedes across delivered records; a
failing procedure holds the cursor; restart resumes exactly-once; ``run()``
wakes on the 0023 NOTIFY and, with LISTEN disabled, on the poll backstop.
The generation rules themselves are proven in tests/risk/alerts/.
"""

from __future__ import annotations

import threading
import time
import uuid

import psycopg
import pytest

from data_governance import db
from data_governance.processors.risk.alerts import driver, metrics
from data_governance.risk import config


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(config, "ALERT_MIN_RISK_LEVEL", "low")
    return metrics.make_registry()


def _insert_trace_risk(dsn: str, *, trace_id: str, level: str, version: int = 1) -> tuple[str, int]:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "INSERT INTO trace_risk_records (trace_id, version, computed_at, "
            "trace_risk_level, interaction_count, policy_event_count) "
            "VALUES (%s, %s, now(), %s, 1, 1) RETURNING trace_risk_id, seq",
            (trace_id, version, level),
        ).fetchone()
        conn.commit()
    return str(row[0]), int(row[1])


def _cursor(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT last_processed_seq FROM processor_state WHERE processor_name = %s",
            (driver.PROCESSOR_NAME,),
        ).fetchone()
    return int(row[0]) if row else 0


def _alerts(dsn: str, trace_id: str) -> list[tuple]:
    """(risk_level, duplicate_count, superseded_by) oldest first."""
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT risk_level, duplicate_count, superseded_by FROM alerts "
            "WHERE trace_id = %s ORDER BY \"timestamp\", alert_id",
            (trace_id,),
        ).fetchall()


def test_drain_generates_alerts_and_advances_cursor(configured_db: str, registry) -> None:
    _insert_trace_risk(configured_db, trace_id="t-1", level="high")
    _insert_trace_risk(configured_db, trace_id="t-2", level="none")
    _, last = _insert_trace_risk(configured_db, trace_id="t-1", level="high", version=2)

    assert driver.drain(0) == last
    assert _cursor(configured_db) == last
    assert _alerts(configured_db, "t-1") == [("high", 1, None)]
    assert _alerts(configured_db, "t-2") == []
    outcome = lambda action: registry.get_sample_value(  # noqa: E731
        "alert_outcomes_total", {"action": action}
    )
    assert (outcome("created"), outcome("duplicate"), outcome("skipped")) == (1.0, 1.0, 1.0)


def test_drain_from_empty_is_noop(configured_db: str) -> None:
    assert driver.drain(0) == 0


def test_raised_level_across_drains_supersedes(configured_db: str) -> None:
    _insert_trace_risk(configured_db, trace_id="t-r", level="low")
    cursor = driver.drain(0)
    _insert_trace_risk(configured_db, trace_id="t-r", level="critical", version=2)
    driver.drain(cursor)
    old, new = _alerts(configured_db, "t-r")
    assert old[0] == "low" and old[2] is not None
    assert new[0] == "critical" and new[2] is None


def test_procedure_failure_holds_the_cursor(configured_db: str, monkeypatch) -> None:
    _insert_trace_risk(configured_db, trace_id="t-f", level="high")

    def _boom(tx, record_id):  # noqa: ANN001 — test stub
        raise RuntimeError("alert generation failed (injected)")

    monkeypatch.setattr(driver.generate, "generate_for_trace_risk_record", _boom)
    with pytest.raises(RuntimeError, match="injected"):
        driver.drain(0)
    assert _cursor(configured_db) == 0
    assert _alerts(configured_db, "t-f") == []

    monkeypatch.undo()
    driver.drain(0)
    assert _alerts(configured_db, "t-f") == [("high", 0, None)]


def test_exactly_once_across_restart(configured_db: str) -> None:
    _insert_trace_risk(configured_db, trace_id="t-a", level="medium")
    first = driver.drain(0)
    with db.transaction() as tx:
        assert driver.read_cursor(tx) == first
    assert driver.drain(first) == first
    assert _alerts(configured_db, "t-a") == [("medium", 0, None)]


def _drain_via_run(dsn: str, *, disable_listen: bool, monkeypatch) -> list[tuple]:
    monkeypatch.setattr(driver, "POLL_SECONDS", 0.2)
    if disable_listen:

        def _boom(channel, dsn):  # noqa: ANN001 — test stub
            raise db.ConnectionTimeout("listen disabled (injected)")

        monkeypatch.setattr(db, "listen", _boom)

    trace_id = f"t-run-{uuid.uuid4().hex[:8]}"
    stop = threading.Event()
    t = threading.Thread(target=driver.run, args=(stop, dsn), daemon=True)
    t.start()
    try:
        time.sleep(0.5)
        _insert_trace_risk(dsn, trace_id=trace_id, level="high")
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if _alerts(dsn, trace_id):
                break
            time.sleep(0.05)
    finally:
        stop.set()
        t.join(timeout=10)
        assert not t.is_alive(), "run() must return promptly on stop_event"
    return _alerts(dsn, trace_id)


def test_run_wakes_on_notify(configured_db: str, monkeypatch) -> None:
    """Migration 0023's dg_trace_risk_written notification wakes the drain."""
    assert _drain_via_run(configured_db, disable_listen=False, monkeypatch=monkeypatch) == [
        ("high", 0, None)
    ]


def test_run_poll_only_still_alerts(configured_db: str, monkeypatch) -> None:
    assert _drain_via_run(configured_db, disable_listen=True, monkeypatch=monkeypatch) == [
        ("high", 0, None)
    ]
