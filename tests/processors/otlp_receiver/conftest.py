"""Shared fixtures for the OTLP receiver tests.

The receiver tests need a real Postgres with the schema migrated to head,
plus the `data_governance.db` Layer 1 pool configured against it. The
session-scoped Postgres container fixture (`pg_dsn`) lives in the top-level
conftest; this module layers on the migration + pool configuration.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator

import pytest

from data_governance import db


@pytest.fixture()
def migrated_dsn(pg_dsn: str) -> str:
    """A fresh database with all migrations applied via the public CLI."""
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
    """Configure the Layer 1 pool against the migrated DB; tear down after."""
    db.close_pool()
    db.configure(migrated_dsn)
    try:
        yield migrated_dsn
    finally:
        db.close_pool()
