"""Integration tests for the receiver's startup schema-version check (#10).

These tests boot the actual receiver process via ``python -m
data_governance.processors.otlp_receiver`` and verify the two end-to-end
behaviours required by ADR-0002's defence-in-depth design:

- A deliberate mismatch (DB at an older revision than compiled head)
  must make the receiver process exit non-zero with an actionable error
  on stderr — surfacing as ``CrashLoopBackOff`` in k8s.
- A clean state where the DB head equals the compiled head must let the
  receiver start and serve ``/healthz``.

We deliberately avoid mocking the subprocess: the contract is "the
process refuses to run", and that is what the test asserts at the
process boundary.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import httpx
import psycopg
import pytest


# --- helpers -----------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port_open(host: str, port: int, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.connect((host, port))
                return
            except OSError:
                time.sleep(0.05)
    raise TimeoutError(f"port {host}:{port} did not open within {timeout}s")


def _spawn_receiver(
    dsn: str,
    *,
    grpc_port: int,
    http_port: int,
    metrics_port: int,
) -> subprocess.Popen[str]:
    """Launch the receiver as a subprocess against *dsn*.

    Ports are passed via env so each test gets its own non-overlapping
    bindings; the receiver entry point reads them where supported and
    falls back to its module defaults otherwise. We override only what
    the in-process server lifecycle understands today; tests that need a
    different port surface drive the subprocess via the env they would
    set in production.
    """
    env = {
        "DATABASE_URL": dsn,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LOG_LEVEL": "INFO",
        # Tests share a session-scoped Postgres container; keep the pool
        # snug to minimise teardown lag.
        "DB_POOL_MIN_SIZE": "1",
        "DB_POOL_MAX_SIZE": "2",
        "DB_POOL_TIMEOUT": "5",
        # Receiver entry point honours these to avoid colliding with the
        # in-process tests that run alongside.
        "RECEIVER_GRPC_PORT": str(grpc_port),
        "RECEIVER_HTTP_PORT": str(http_port),
        "RECEIVER_METRICS_PORT": str(metrics_port),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "data_governance.processors.otlp_receiver"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_exit(proc: subprocess.Popen[str], timeout: float = 10.0) -> int:
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2.0)
        raise


@pytest.fixture()
def free_ports() -> tuple[int, int, int]:
    return _free_port(), _free_port(), _free_port()


# --- mismatch: process exits non-zero ----------------------------------------


class TestStartupCheckMismatch:
    def test_receiver_exits_nonzero_when_db_at_older_revision(
        self, migrated_dsn: str, free_ports: tuple[int, int, int]
    ) -> None:
        """Deliberate mismatch — DB revision is forced to an older value.
        The receiver must exit non-zero and the stderr must name *both* the
        DB and compiled revisions, so an operator can see at a glance
        what's wrong."""
        # Force the alembic_version row to a stale revision.
        # Alembic's version_num column is varchar(32); keep it short.
        stale = "0000_stale_rev"
        with psycopg.connect(migrated_dsn) as conn:
            conn.execute(
                "UPDATE alembic_version SET version_num = %s", (stale,)
            )

        from data_governance.db.schema_version import compiled_head

        compiled = compiled_head()

        grpc_port, http_port, metrics_port = free_ports
        proc = _spawn_receiver(
            migrated_dsn,
            grpc_port=grpc_port,
            http_port=http_port,
            metrics_port=metrics_port,
        )
        rc = _wait_exit(proc)
        assert rc != 0, (
            f"receiver should refuse to run on mismatch; got rc={rc}\n"
            f"stdout:\n{proc.stdout.read() if proc.stdout else ''}\n"
            f"stderr:\n{proc.stderr.read() if proc.stderr else ''}"
        )
        stderr = proc.stderr.read() if proc.stderr else ""
        # Actionable error must name both revisions.
        assert stale in stderr, f"stale revision not in stderr:\n{stderr}"
        assert compiled in stderr, (
            f"compiled head not in stderr:\n{stderr}"
        )

    def test_receiver_exits_nonzero_when_alembic_version_table_missing(
        self, pg_dsn: str, free_ports: tuple[int, int, int]
    ) -> None:
        """Migrations were never run — the table doesn't exist. The
        receiver must exit non-zero with an actionable message rather
        than crash with an opaque relation-does-not-exist error later."""
        grpc_port, http_port, metrics_port = free_ports
        proc = _spawn_receiver(
            pg_dsn,
            grpc_port=grpc_port,
            http_port=http_port,
            metrics_port=metrics_port,
        )
        rc = _wait_exit(proc)
        assert rc != 0, (
            f"receiver should refuse to run when alembic_version is "
            f"missing; got rc={rc}\n"
            f"stderr:\n{proc.stderr.read() if proc.stderr else ''}"
        )
        stderr = proc.stderr.read() if proc.stderr else ""
        assert "alembic_version" in stderr, (
            f"missing-table message not actionable:\n{stderr}"
        )


# --- clean state: receiver starts and /healthz serves ------------------------


class TestStartupCheckCleanState:
    def test_receiver_starts_and_serves_healthz_when_versions_match(
        self, migrated_dsn: str, free_ports: tuple[int, int, int]
    ) -> Iterator[None]:
        """End-to-end clean path: migrate → spawn receiver → /healthz=200.

        This is the positive control for the negative tests above. If the
        startup check passes, ``/healthz`` must serve normally; the check
        does not gate ``/healthz`` itself (per ADR-0002, by the time the
        receiver answers /healthz the version check has already passed)."""
        grpc_port, http_port, metrics_port = free_ports
        proc = _spawn_receiver(
            migrated_dsn,
            grpc_port=grpc_port,
            http_port=http_port,
            metrics_port=metrics_port,
        )
        try:
            _wait_port_open("127.0.0.1", http_port, timeout=10.0)
            r = httpx.get(
                f"http://127.0.0.1:{http_port}/healthz", timeout=2.0
            )
            assert r.status_code == 200, (
                f"healthz returned {r.status_code}: {r.text}"
            )
            assert r.text == "ok"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
