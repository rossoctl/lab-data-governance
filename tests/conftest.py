"""Shared test fixtures.

Tests use a real Postgres via testcontainers — Layer 1's job is to wrap a real
driver, so a mocked test would only assert the mock. See ADR-0005.

The Postgres container is session-scoped (one container for the whole test
run); each test gets a fresh database via the `pg_dsn` fixture. That keeps
the per-test cost down to a `CREATE DATABASE` rather than a container start.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

# Local podman setups need the socket explicitly. Set this before testcontainers
# imports its docker client. Honor an existing DOCKER_HOST (e.g. CI).
_uid = os.getuid()
os.environ.setdefault("DOCKER_HOST", f"unix:///run/user/{_uid}/podman/podman.sock")
# testcontainers' ryuk reaper needs privileged container creation; disable on
# rootless podman where it tends to fail.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


@pytest.fixture(scope="session")
def _postgres_container() -> Iterator[PostgresContainer]:
    """One Postgres container per test session."""
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def _admin_dsn(_postgres_container: PostgresContainer) -> str:
    """Admin DSN against the container's default `test` database.

    Testcontainers returns a SQLAlchemy-style URL with `+psycopg2`; rewrite to
    a plain libpq URL psycopg 3 accepts.
    """
    return _postgres_container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


def _admin_url_to_db(admin_dsn: str, dbname: str) -> str:
    """Replace the database name in a libpq URL."""
    head, _, _ = admin_dsn.rpartition("/")
    return f"{head}/{dbname}"


@pytest.fixture()
def pg_dsn(_admin_dsn: str) -> Iterator[str]:
    """A fresh database per test. Returns a libpq URL."""
    dbname = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_admin_dsn, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    try:
        yield _admin_url_to_db(_admin_dsn, dbname)
    finally:
        with psycopg.connect(_admin_dsn, autocommit=True) as conn:
            # Force-drop any leftover connections from the pool under test.
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture()
def migrated_dsn(pg_dsn: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "data_governance.db.migrate"],
        env={"DATABASE_URL": pg_dsn, "PATH": "/usr/bin:/bin"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"migrate exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return pg_dsn
