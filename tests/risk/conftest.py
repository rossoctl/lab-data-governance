"""Fixtures for DAS risk-package tests (issue #98)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from data_governance import db


@pytest.fixture()
def configured_db(migrated_dsn: str) -> Iterator[str]:
    db.close_pool()
    db.configure(migrated_dsn)
    try:
        yield migrated_dsn
    finally:
        db.close_pool()
