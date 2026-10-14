"""The two concurrency guards of alert generation (issue #104), each with a
second writer actually in flight — two real connections, the second one
blocked behind the first.

- The per-trace advisory lock: two consumers handed the same record (an
  involuntary two-pod overlap) converge on one alert.
- ``FOR UPDATE`` on the open alert: a status change the REST API (#110) has
  in flight is waited for and seen, so a recurrence after the operator
  resolved the alert opens a new one instead of counting onto the closed one.
"""

from __future__ import annotations

import threading

import psycopg
import pytest

from data_governance import db
from data_governance.risk import config
from data_governance.risk.alerts.generate import AlertOutcome, generate_for_trace_risk_record

_BLOCK_SECONDS = 1.5


@pytest.fixture(autouse=True)
def _default_floor(monkeypatch):
    monkeypatch.setattr(config, "ALERT_MIN_RISK_LEVEL", "low")


def _trace_risk(dsn: str, trace_id: str, level: str, version: int = 1) -> str:
    with psycopg.connect(dsn) as conn:
        (record_id,) = conn.execute(
            "INSERT INTO trace_risk_records (trace_id, version, computed_at, trace_risk_level, "
            "interaction_count, policy_event_count) VALUES (%s, %s, now(), %s, 1, 1) "
            "RETURNING trace_risk_id::text",
            (trace_id, version, level),
        ).fetchone()
        conn.commit()
    return record_id


def _alerts(dsn: str, trace_id: str) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            'SELECT status, duplicate_count, superseded_by FROM alerts WHERE trace_id = %s '
            'ORDER BY "timestamp", alert_id',
            (trace_id,),
        ).fetchall()


def _in_thread(dsn: str, record_id: str) -> tuple[threading.Thread, dict]:
    """Generate for *record_id* on its own connection in a thread; the result
    lands in the returned dict once the thread's transaction commits."""
    out: dict[str, AlertOutcome] = {}

    def run() -> None:
        with psycopg.connect(dsn) as conn:
            out["outcome"] = generate_for_trace_risk_record(db.Transaction(conn), record_id)
            conn.commit()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, out


def test_two_consumers_on_one_record_make_one_alert(configured_db: str):
    record = _trace_risk(configured_db, "t-pair", "high")
    first_conn = psycopg.connect(configured_db)
    try:
        first = generate_for_trace_risk_record(db.Transaction(first_conn), record)
        thread, out = _in_thread(configured_db, record)
        thread.join(_BLOCK_SECONDS)
        assert thread.is_alive(), "the second consumer must wait for the first's trace lock"
        first_conn.commit()
    finally:
        first_conn.close()
    thread.join(10)

    assert first.action == "created"
    assert out["outcome"].action == "skipped" and out["outcome"].reason == "already_alerted"
    assert len(_alerts(configured_db, "t-pair")) == 1


def test_a_status_change_in_flight_is_seen(configured_db: str):
    trace = "t-api"
    with db.transaction() as tx:
        first = generate_for_trace_risk_record(tx, _trace_risk(configured_db, trace, "high"))
    recurrence = _trace_risk(configured_db, trace, "high", version=2)

    api = psycopg.connect(configured_db)
    try:
        api.execute("UPDATE alerts SET status = 'resolved' WHERE alert_id = %s", (first.alert_id,))
        thread, out = _in_thread(configured_db, recurrence)
        thread.join(_BLOCK_SECONDS)
        assert thread.is_alive(), "the generator must wait for the API's row lock"
        api.commit()
    finally:
        api.close()
    thread.join(10)

    assert out["outcome"].action == "created", "a resolved alert is not the trace's open alert"
    resolved, fresh = _alerts(configured_db, trace)
    assert resolved == ("resolved", 0, None)
    assert fresh[0] == "new"
