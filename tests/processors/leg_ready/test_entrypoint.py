"""Entrypoint behaviour for the P-leg-ready consumer (issue #123, ADR-0027).

Boots ``python -m data_governance.processors.leg_ready`` as a subprocess and
asserts the process-boundary contract (exit codes, clean SIGTERM), mirroring the
P-entity-ready entrypoint tests: the entrypoint reads ``DATABASE_URL``, runs the
defence-in-depth schema-version check (ADR-0002), and drives the wake-driven
readiness drain loop until SIGINT/SIGTERM. There is no per-item model, so — like
entity-ready — there is nothing to construct at startup.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import psycopg


def _spawn(dsn: str | None) -> subprocess.Popen[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LOG_LEVEL": "INFO",
        "DB_POOL_MIN_SIZE": "1",
        "DB_POOL_MAX_SIZE": "2",
        "DB_POOL_TIMEOUT": "5",
        # Ephemeral metrics port so a co-located processor (or a parallel test)
        # does not collide on the default 9094.
        "LEG_READY_METRICS_PORT": "0",
    }
    if dsn is not None:
        env["DATABASE_URL"] = dsn
    return subprocess.Popen(
        [sys.executable, "-m", "data_governance.processors.leg_ready"],
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


def test_entrypoint_exits_3_on_schema_mismatch(migrated_dsn: str) -> None:
    """DB forced to a stale revision → process refuses to start (ADR-0002): a
    ``SchemaVersionMismatch`` exits with code 3 specifically, naming both the stale
    DB revision and the compiled head (the CrashLoopBackOff signal k8s surfaces)."""
    from data_governance.db.schema_version import compiled_head

    stale = "0000_stale_rev"
    compiled = compiled_head()
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute("UPDATE alembic_version SET version_num = %s", (stale,))
    proc = _spawn(migrated_dsn)
    rc = proc.wait(timeout=10)
    stderr = proc.stderr.read() if proc.stderr else ""
    assert rc == 3, f"expected SchemaVersionMismatch exit 3, got {rc}\nstderr:\n{stderr}"
    assert stale in stderr, f"stale revision not named in stderr:\n{stderr}"
    assert compiled in stderr, f"compiled head not named in stderr:\n{stderr}"


def test_entrypoint_exits_nonzero_when_alembic_version_missing(pg_dsn: str) -> None:
    """Migrations never ran (no ``alembic_version`` table) → the process refuses to
    start with an actionable message rather than crashing opaquely later (ADR-0002).
    The "init container forgotten" failure mode."""
    proc = _spawn(pg_dsn)
    rc = proc.wait(timeout=10)
    stderr = proc.stderr.read() if proc.stderr else ""
    assert rc != 0, f"expected non-zero exit on missing schema; got {rc}\nstderr:\n{stderr}"
    assert "alembic_version" in stderr, f"missing-table message not actionable:\n{stderr}"


def test_entrypoint_runs_then_exits_clean_on_sigterm(migrated_dsn: str) -> None:
    """Clean path: starts against a migrated DB, runs the wake loop, exits 0 on
    SIGTERM."""
    proc = _spawn(migrated_dsn)
    # Give it a moment to pass the schema check and enter the loop.
    time.sleep(2.0)
    assert proc.poll() is None, (
        "consumer exited prematurely:\n"
        f"stderr:\n{proc.stderr.read() if proc.stderr else ''}"
    )
    proc.send_signal(signal.SIGTERM)
    rc = proc.wait(timeout=10)
    assert rc == 0, f"stderr:\n{proc.stderr.read() if proc.stderr else ''}"


def _advisory_lock_states(dsn: str) -> list[bool]:
    """The granted flag of every advisory lock held or awaited right now."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT granted FROM pg_locks WHERE locktype = 'advisory' AND database = "
            " (SELECT oid FROM pg_database WHERE datname = current_database())"
            " ORDER BY granted DESC"
        ).fetchall()
    return [r[0] for r in rows]


def _wait_for(predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def _draining_instances(dsn: str) -> int:
    """How many leg-ready processes are inside the drain loop: each one holds
    a dedicated LISTEN connection for its wake-up (ADR-0015)."""
    with psycopg.connect(dsn) as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM pg_stat_activity"
            " WHERE datname = current_database() AND query ILIKE 'LISTEN %%'"
        ).fetchone()
    return int(n)


def test_two_instances_never_drain_at_once(migrated_dsn: str) -> None:
    """Two leg-ready processes started together (a replacement pod beside one
    that is still exiting) must not both drain: each would deliver the same
    legs and race on the same interactions' risk writes."""
    procs = [_spawn(migrated_dsn), _spawn(migrated_dsn)]
    try:
        assert _wait_for(lambda: _draining_instances(migrated_dsn) >= 1)
        time.sleep(3.0)  # both past startup
        assert all(p.poll() is None for p in procs)
        assert _draining_instances(migrated_dsn) == 1, "both instances are draining"
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=10)


def test_a_second_instance_waits_for_the_first_to_exit(migrated_dsn: str) -> None:
    """The risk writes are order-sensitive, so two leg-ready processes must
    never drain together — an evicted or still-exiting pod can overlap its
    replacement despite maxSurge: 0. The second process blocks on the
    sole-consumer lock until the first one's connection closes, then takes
    over."""
    first = _spawn(migrated_dsn)
    second = None
    try:
        assert _wait_for(lambda: _advisory_lock_states(migrated_dsn) == [True]), (
            "the first instance should hold the sole-consumer lock"
        )
        holder = _lock_holder_pid(migrated_dsn)
        second = _spawn(migrated_dsn)
        time.sleep(3.0)  # past startup, into the lock wait
        assert second.poll() is None
        assert _advisory_lock_states(migrated_dsn) == [True]
        assert _lock_holder_pid(migrated_dsn) == holder, "the first instance still holds it"

        first.send_signal(signal.SIGTERM)
        assert first.wait(timeout=10) == 0
        assert _wait_for(
            lambda: _advisory_lock_states(migrated_dsn) == [True]
            and _lock_holder_pid(migrated_dsn) != holder
        ), "the second instance should take the lock once the first exits"
        assert second.poll() is None
    finally:
        for proc in (first, second):
            if proc is not None and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=10)


def _lock_holder_pid(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        (pid,) = conn.execute(
            "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted"
            " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
        ).fetchone()
    return int(pid)


def test_an_instance_that_loses_its_lock_stops_draining(migrated_dsn: str) -> None:
    """If the lock's connection dies (a Postgres restart, a terminated
    backend), the lock is gone and another instance may take it. The holder
    must notice at its next wake and exit rather than keep draining
    unprotected; its restart then waits for the lock like any other."""
    proc = _spawn(migrated_dsn)
    try:
        assert _wait_for(lambda: _advisory_lock_states(migrated_dsn) == [True])
        with psycopg.connect(migrated_dsn, autocommit=True) as admin:
            admin.execute("SELECT pg_terminate_backend(%s)", (_lock_holder_pid(migrated_dsn),))
        # The next wake — at the latest the 5 s poll backstop — checks the lock.
        rc = proc.wait(timeout=20)
        assert rc == 5
        assert "LockLost" in (proc.stderr.read() if proc.stderr else "")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_a_waiting_instance_exits_clean_on_sigterm(migrated_dsn: str) -> None:
    """A replacement deleted while it waits for the lock must not hang until
    SIGKILL: SIGTERM ends the wait and the process exits 0."""
    holder = _spawn(migrated_dsn)
    waiter = None
    try:
        assert _wait_for(lambda: _advisory_lock_states(migrated_dsn) == [True])
        waiter = _spawn(migrated_dsn)
        time.sleep(2.0)  # past startup, into the lock wait
        assert waiter.poll() is None
        waiter.send_signal(signal.SIGTERM)
        assert waiter.wait(timeout=5) == 0
    finally:
        for proc in (holder, waiter):
            if proc is not None and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=10)
