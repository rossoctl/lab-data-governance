"""Fixtures for retrieval tests: migrated + pool-configured DB."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator

import psycopg
import pytest

from data_governance import db


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


@pytest.fixture()
def configured_db(migrated_dsn: str) -> Iterator[str]:
    db.close_pool()
    db.configure(migrated_dsn)
    try:
        yield migrated_dsn
    finally:
        db.close_pool()


@pytest.fixture()
def raw_conn(configured_db: str):
    """A direct psycopg connection for inserting test fixtures."""
    with psycopg.connect(configured_db) as conn:
        yield conn
