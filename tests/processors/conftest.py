"""Shared fixtures for processor tests.

``configured_db`` gives a migrated Postgres with the process-wide connection
pool pointed at it — the shape every Layer-2 processor (P-interactions,
P-classification) needs to drive its cursor loop. It lives here (rather than in
one processor's conftest) so the shared-driver tests and each processor's tests
can all depend on it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from data_governance import db


@pytest.fixture()
def configured_db(migrated_dsn: str) -> Iterator[str]:
    """A migrated DB with the process-wide connection pool pointed at it."""
    db.close_pool()
    db.configure(migrated_dsn)
    try:
        yield migrated_dsn
    finally:
        db.close_pool()
