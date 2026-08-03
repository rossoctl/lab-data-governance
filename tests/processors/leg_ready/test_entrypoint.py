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
