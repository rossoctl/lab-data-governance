"""Entrypoint behaviour for the P-classification processor (issue #77).

Boots ``python -m data_governance.processors.classification`` as a subprocess
and asserts the process-boundary contract (exit codes, clean SIGTERM), mirroring
the P-interactions entrypoint tests (``tests/processors/interactions/
test_driver.py``) and the receiver's startup-check tests: the entrypoint reads
``DATABASE_URL``, runs the defence-in-depth schema-version check (ADR-0002), and
drives the shared wake-driven drain loop until SIGINT/SIGTERM.
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
        # Distinct metrics port so a co-located interactions processor (or a
        # parallel test) does not collide on the default.
        "CLASSIFICATION_METRICS_PORT": "0",
    }
    if dsn is not None:
        env["DATABASE_URL"] = dsn
    return subprocess.Popen(
        [sys.executable, "-m", "data_governance.processors.classification"],
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
    """DB forced to a stale revision → process refuses to start (ADR-0002)."""
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute("UPDATE alembic_version SET version_num = '0000_stale_rev'")
    proc = _spawn(migrated_dsn)
    rc = proc.wait(timeout=10)
    assert rc != 0
    assert "0000_stale_rev" in (proc.stderr.read() if proc.stderr else "")


def test_entrypoint_runs_then_exits_clean_on_sigterm(migrated_dsn: str) -> None:
    """Clean path: starts against a migrated DB, runs the wake loop, exits 0 on
    SIGTERM."""
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
