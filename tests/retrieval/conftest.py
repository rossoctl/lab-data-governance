"""Fixtures for retrieval tests: migrated + pool-configured DB."""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
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


@pytest.fixture()
def raw_conn(configured_db: str):
    """A direct psycopg connection for inserting test fixtures."""
    with psycopg.connect(configured_db) as conn:
        yield conn


@pytest.fixture()
def insert_span(raw_conn):
    """Return a helper that inserts a minimal span row directly."""
    def _insert(*, trace_id: str, span_id: str, name: str, parent_id=None):
        raw_conn.execute(
            """
            INSERT INTO spans (
                trace_id, span_id, parent_id, kind, name,
                started_at, attributes, seq, arrival_seq, observed_at
            ) VALUES (
                %s, %s, %s, 'INTERNAL', %s,
                now(), '{}'::jsonb, nextval('spans_seq'), currval('spans_seq'), now()
            )
            """,
            (trace_id, span_id, parent_id, name),
        )
        raw_conn.commit()
    return _insert
