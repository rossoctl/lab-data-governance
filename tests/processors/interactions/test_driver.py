"""Cursor-drain loop + entrypoint behaviour for the P-interactions processor.

The drain tests run in-process against a migrated DB (fast). The entrypoint
tests boot ``python -m data_governance.processors.interactions`` as a subprocess
and assert the process-boundary contract (exit codes, clean SIGTERM), mirroring
the receiver's startup-check tests.
"""

from __future__ import annotations

import datetime as dt
import os
import signal
import subprocess
import sys
import threading
import time

import psycopg
import pytest

from data_governance import db
from data_governance.processors.interactions import driver


# --- cursor drain (in-process) ----------------------------------------------


def _insert_min_span(dsn: str, *, trace_id: str, span_id: str, parent_id: str | None) -> None:
    """Insert a minimal span (DEFAULT-allocated seq) directly."""
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO spans (trace_id, span_id, parent_id, kind, name,
                               started_at, attributes, seq, arrival_seq, observed_at)
            VALUES (%s, %s, %s, 'INTERNAL', %s, %s, '{}'::jsonb,
                    nextval('spans_seq'), currval('spans_seq'), now())
            """,
            (trace_id, span_id, parent_id, "n", dt.datetime.now(dt.timezone.utc)),
        )
        conn.commit()


def _cursor(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT last_processed_seq FROM processor_state "
            "WHERE processor_name = 'interactions'"
        ).fetchone()
    return int(row[0]) if row else 0


def test_drain_from_empty_is_noop(configured_db: str) -> None:
    """No spans → cursor stays at 0, no rows written."""
    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    new_cursor = driver.drain(cursor)
    assert new_cursor == 0


def test_drain_advances_cursor_over_inserted_spans(configured_db: str) -> None:
    """A drain processes every span past the cursor and advances it to max(seq)."""
    for i in range(3):
        _insert_min_span(
            configured_db, trace_id="t1", span_id=f"s{i}",
            parent_id=None if i == 0 else f"s{i - 1}",
        )
    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    new_cursor = driver.drain(cursor)
    with psycopg.connect(configured_db) as conn:
        max_seq = conn.execute("SELECT max(seq) FROM spans").fetchone()[0]
    assert new_cursor == max_seq
    assert _cursor(configured_db) == max_seq


def test_drain_is_incremental_across_two_batches(configured_db: str) -> None:
    """A second drain only processes spans inserted after the first drain
    (WHERE seq > cursor), and the cursor advances monotonically."""
    _insert_min_span(configured_db, trace_id="t1", span_id="a", parent_id=None)
    with db.transaction() as tx:
        c0 = driver.read_cursor(tx)
    c1 = driver.drain(c0)

    # Insert more spans; the next drain starts from c1.
    _insert_min_span(configured_db, trace_id="t1", span_id="b", parent_id="a")
    c2 = driver.drain(c1)
    assert c2 > c1
    with psycopg.connect(configured_db) as conn:
        max_seq = conn.execute("SELECT max(seq) FROM spans").fetchone()[0]
    assert c2 == max_seq


# --- LISTEN/NOTIFY wake loop (issue #71) -------------------------------------


def _run_in_thread(stop_event: threading.Event, dsn: str) -> threading.Thread:
    t = threading.Thread(target=driver.run, args=(stop_event, dsn), daemon=True)
    t.start()
    return t


def _wait_until(predicate, timeout: float, interval: float = 0.05) -> bool:
    """Poll *predicate* until true or *timeout* elapses. Returns the result."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_run_wakes_on_notify_faster_than_poll(
    configured_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A span inserted after run() is listening drains well within one poll
    interval — i.e. the NOTIFY wake fired, not the POLL_SECONDS backstop.

    The insert is gated on the "listening on …" log line, not a fixed sleep:
    NOTIFY only reaches sessions already LISTENing, so inserting before
    registration would miss the notification and flake the timing assertion.
    """
    assert driver.POLL_SECONDS >= 5.0, "test assumes a multi-second poll backstop"
    caplog.set_level("INFO", logger="data_governance.processors.interactions.driver")
    stop_event = threading.Event()
    thread = _run_in_thread(stop_event, configured_db)
    try:
        # Wait until run() has registered LISTEN (logged) before inserting, so
        # the notification is guaranteed to reach the listening session.
        listening = _wait_until(
            lambda: any("listening on" in r.message for r in caplog.records),
            timeout=5.0,
        )
        assert listening, "run() did not register LISTEN within 5s"
        _insert_min_span(configured_db, trace_id="t1", span_id="s0", parent_id=None)
        with psycopg.connect(configured_db) as conn:
            max_seq = conn.execute("SELECT max(seq) FROM spans").fetchone()[0]
        # Must advance far faster than the poll backstop — give it 3s, well
        # under POLL_SECONDS (5s), so a pass means the notify woke the drain.
        woke = _wait_until(lambda: _cursor(configured_db) == max_seq, timeout=3.0)
        assert woke, (
            f"cursor did not reach {max_seq} within 3s; NOTIFY wake did not fire "
            f"(cursor={_cursor(configured_db)})"
        )
    finally:
        stop_event.set()
        thread.join(timeout=10)
        assert not thread.is_alive(), "run() did not exit on stop_event"


def test_run_drains_span_inserted_before_listen(configured_db: str) -> None:
    """A span already present when run() starts is drained on the initial
    pre-LISTEN drain, not stranded until a later wake."""
    _insert_min_span(configured_db, trace_id="t1", span_id="pre", parent_id=None)
    with psycopg.connect(configured_db) as conn:
        max_seq = conn.execute("SELECT max(seq) FROM spans").fetchone()[0]
    stop_event = threading.Event()
    thread = _run_in_thread(stop_event, configured_db)
    try:
        drained = _wait_until(
            lambda: _cursor(configured_db) == max_seq, timeout=3.0
        )
        assert drained, f"pre-existing span not drained (cursor={_cursor(configured_db)})"
    finally:
        stop_event.set()
        thread.join(timeout=10)
        assert not thread.is_alive()


def test_run_falls_back_to_poll_when_listen_unavailable(
    configured_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the LISTEN connection can't be opened, run() must fall back to the
    poll loop and still drain (correctness never depends on NOTIFY, #71).

    Force db.listen() to raise a connection-class error; the drain must still
    advance the cursor on the poll path.
    """
    def _boom(channel, dsn):  # noqa: ANN001 — test stub
        raise db.ConnectionTimeout("listen connection refused (injected)")

    monkeypatch.setattr(db, "listen", _boom)
    # Shrink the poll backstop so the fallback drains promptly in the test.
    monkeypatch.setattr(driver, "POLL_SECONDS", 0.2)

    _insert_min_span(configured_db, trace_id="t1", span_id="s0", parent_id=None)
    with psycopg.connect(configured_db) as conn:
        max_seq = conn.execute("SELECT max(seq) FROM spans").fetchone()[0]

    stop_event = threading.Event()
    thread = _run_in_thread(stop_event, configured_db)
    try:
        drained = _wait_until(
            lambda: _cursor(configured_db) == max_seq, timeout=5.0
        )
        assert drained, (
            "poll-only fallback did not drain after LISTEN failure "
            f"(cursor={_cursor(configured_db)})"
        )
    finally:
        stop_event.set()
        thread.join(timeout=10)
        assert not thread.is_alive(), "fallback poll loop did not exit on stop_event"


def test_run_falls_back_to_poll_when_listen_drops_mid_run(
    configured_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A LISTEN connection that DROPS after it was opened (not just a failure
    to open) must also fall back to poll-only and keep draining (#71).

    db.listen() opens normally; the first listener.wait() raises a
    connection-class error, simulating a severed connection mid-run. The span
    is inserted by the stub itself so it lands AFTER run()'s initial pre-LISTEN
    drain — meaning ONLY the poll fallback can pick it up, so the assertion
    genuinely exercises the fallback rather than the initial drain.
    """
    real_wait = db.Listener.wait

    def _wait_then_drop(self, timeout):  # noqa: ANN001 — test stub
        # Insert the span now (after the pre-LISTEN drain has already run), so
        # only the poll fallback can drain it.
        _insert_min_span(configured_db, trace_id="t1", span_id="s0", parent_id=None)
        # Restore the real method so the poll fallback and any later listener
        # are unaffected, then raise as if the connection dropped on this wait.
        monkeypatch.setattr(db.Listener, "wait", real_wait)
        raise db.ConnectionTimeout("listen connection dropped mid-run (injected)")

    monkeypatch.setattr(db.Listener, "wait", _wait_then_drop)
    monkeypatch.setattr(driver, "POLL_SECONDS", 0.2)

    stop_event = threading.Event()
    thread = _run_in_thread(stop_event, configured_db)
    try:
        drained = _wait_until(
            lambda: _cursor(configured_db) > 0, timeout=5.0
        )
        assert drained, (
            "poll-only fallback did not drain the span inserted at the mid-run "
            f"LISTEN drop (cursor={_cursor(configured_db)})"
        )
    finally:
        stop_event.set()
        thread.join(timeout=10)
        assert not thread.is_alive(), "fallback poll loop did not exit on stop_event"


# --- entrypoint (subprocess) -------------------------------------------------


def _spawn(dsn: str | None) -> subprocess.Popen[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LOG_LEVEL": "INFO",
        "DB_POOL_MIN_SIZE": "1",
        "DB_POOL_MAX_SIZE": "2",
        "DB_POOL_TIMEOUT": "5",
    }
    if dsn is not None:
        env["DATABASE_URL"] = dsn
    return subprocess.Popen(
        [sys.executable, "-m", "data_governance.processors.interactions"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_entrypoint_exits_2_without_database_url() -> None:
    proc = _spawn(None)
    rc = proc.wait(timeout=10)
    assert rc == 2
    assert "DATABASE_URL" in (proc.stderr.read() if proc.stderr else "")


def test_entrypoint_exits_nonzero_on_schema_mismatch(migrated_dsn: str) -> None:
    """DB forced to a stale revision → process refuses to start (exit 3)."""
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute("UPDATE alembic_version SET version_num = '0000_stale_rev'")
    proc = _spawn(migrated_dsn)
    rc = proc.wait(timeout=10)
    assert rc != 0
    assert "0000_stale_rev" in (proc.stderr.read() if proc.stderr else "")


def test_entrypoint_runs_then_exits_clean_on_sigterm(migrated_dsn: str) -> None:
    """Clean path: starts against a migrated DB, runs the poll loop, and exits
    0 on SIGTERM."""
    proc = _spawn(migrated_dsn)
    # Give it a moment to pass the schema check and enter the loop.
    time.sleep(2.0)
    assert proc.poll() is None, (
        "processor exited prematurely:\n"
        f"stderr:\n{proc.stderr.read() if proc.stderr else ''}"
    )
    proc.send_signal(signal.SIGTERM)
    rc = proc.wait(timeout=10)
    assert rc == 0, f"stderr:\n{proc.stderr.read() if proc.stderr else ''}"
