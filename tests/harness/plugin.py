"""Session-scoped wiring for the OTLP test harness.

These fixtures bring up, exactly once per test session:

- A real Postgres container (reused from the top-level conftest's
  ``_postgres_container``).
- A dedicated harness database, migrated to head via the public ``migrate``
  CLI from issue #2 — the same code path production runs.
- A :class:`GrpcOtlpServer` bound to an ephemeral 127.0.0.1 port.
- A :class:`HttpOtlpServer` bound to an ephemeral 127.0.0.1 port.

The receiver pool (Layer 1 ``data_governance.db``) is *not* configured here
— per-test setup in :mod:`tests.harness.fixtures` reconfigures it against
the harness DSN so that tests using other fixtures (e.g. the per-test
``configured_db`` in the legacy receiver tests) cannot leave the pool
stomped on between harness tests.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

from data_governance.processors.otlp_receiver.server import (
    GrpcOtlpServer,
    HttpOtlpServer,
    MetricsServer,
)
from data_governance.processors.otlp_receiver import metrics as _metrics


def _free_port() -> int:
    """Return a port number that was free at the moment of asking.

    The kernel may hand the same port out to a different process between
    this call and the server binding to it; in practice the window is
    short enough that ephemeral-port allocation works for tests.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port_open(host: str, port: int, timeout: float = 5.0) -> None:
    """Block until *host:port* accepts a TCP connection or *timeout* elapses."""
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


def _admin_url_to_db(admin_dsn: str, dbname: str) -> str:
    """Replace the database name in a libpq URL."""
    head, _, _ = admin_dsn.rpartition("/")
    return f"{head}/{dbname}"


# --- Postgres + migrated harness DB -----------------------------------------


@pytest.fixture(scope="session")
def _harness_pg_container(_postgres_container: PostgresContainer) -> PostgresContainer:
    """Reuse the top-level conftest's session-scoped Postgres container.

    The harness does not need its own container — the top-level
    ``_postgres_container`` is already session-scoped and shared across
    every test that touches Postgres.
    """
    return _postgres_container


@pytest.fixture(scope="session")
def _harness_admin_dsn(_admin_dsn: str) -> str:
    """Reuse the top-level conftest's admin DSN (psycopg-3-friendly libpq URL)."""
    return _admin_dsn


@pytest.fixture(scope="session")
def _harness_pg_dsn(_harness_admin_dsn: str) -> Iterator[str]:
    """One harness database, created at session start, migrated, dropped at end.

    The harness DB is intentionally session-scoped — re-migrating per test
    would dwarf the per-test cost of a TRUNCATE (issue #5 acceptance
    criterion: "Per-test reset truncates ``spans`` and
    ``blocked_span_counts`` without re-migrating").
    """
    dbname = f"harness_{uuid.uuid4().hex[:12]}"

    # Create the database.
    with psycopg.connect(_harness_admin_dsn, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')

    harness_dsn = _admin_url_to_db(_harness_admin_dsn, dbname)

    # Apply migrations via the public CLI — same code path production runs.
    result = subprocess.run(
        [sys.executable, "-m", "data_governance.db.migrate"],
        env={"DATABASE_URL": harness_dsn, "PATH": "/usr/bin:/bin"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"harness migrate exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    try:
        yield harness_dsn
    finally:
        # Force-drop any leftover connections from the receiver/test pools.
        with psycopg.connect(_harness_admin_dsn, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


# --- OTLP gRPC + HTTP servers -----------------------------------------------


@pytest.fixture(scope="session")
def _harness_servers(
    _harness_pg_dsn: str,
) -> Iterator[tuple[GrpcOtlpServer, HttpOtlpServer]]:
    """Boot the gRPC and HTTP/protobuf OTLP servers on ephemeral ports.

    The receiver's Layer 1 pool is configured here against the harness DSN
    so that the servers' ``write_span`` calls and ``/healthz`` probes hit
    the migrated harness database. The per-test ``otlp_harness`` fixture
    re-asserts this configuration to defend against any other fixture in
    the session having reconfigured the pool against a different DSN.
    """
    from data_governance import db

    db.close_pool()
    db.configure(_harness_pg_dsn)

    grpc_port = _free_port()
    http_port = _free_port()
    grpc_server = GrpcOtlpServer(host="127.0.0.1", port=grpc_port)
    http_server = HttpOtlpServer(host="127.0.0.1", port=http_port)
    grpc_server.start()
    http_server.start()
    try:
        _wait_port_open("127.0.0.1", grpc_port)
        _wait_port_open("127.0.0.1", http_port)
        yield grpc_server, http_server
    finally:
        grpc_server.stop(grace=0.5)
        http_server.stop(grace=1.0)
        db.close_pool()


@pytest.fixture(scope="session")
def _harness_metrics_server() -> Iterator[MetricsServer]:
    """Boot the Prometheus metrics server on an ephemeral port."""
    metrics_port = _free_port()
    metrics_server = MetricsServer(host="127.0.0.1", port=metrics_port)
    metrics_server.start()
    try:
        _wait_port_open("127.0.0.1", metrics_port)
        yield metrics_server
    finally:
        metrics_server.stop(grace=1.0)


@pytest.fixture(scope="session")
def _harness_grpc_endpoint(
    _harness_servers: tuple[GrpcOtlpServer, HttpOtlpServer],
) -> str:
    """OTLP gRPC endpoint as a ``host:port`` string (no scheme)."""
    grpc_server, _ = _harness_servers
    return f"127.0.0.1:{grpc_server.port}"


@pytest.fixture(scope="session")
def _harness_http_endpoint(
    _harness_servers: tuple[GrpcOtlpServer, HttpOtlpServer],
) -> str:
    """OTLP HTTP/protobuf endpoint URL (without the ``/v1/traces`` suffix)."""
    _, http_server = _harness_servers
    return f"http://127.0.0.1:{http_server.port}"


@pytest.fixture(scope="session")
def _harness_metrics_endpoint(_harness_metrics_server: MetricsServer) -> str:
    """Prometheus metrics server base URL (without the ``/metrics`` suffix)."""
    return f"http://127.0.0.1:{_harness_metrics_server.port}"
