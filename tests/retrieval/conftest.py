"""Fixtures for retrieval tests: migrated + pool-configured DB."""

from __future__ import annotations

import datetime as dt
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
    """Return a helper that inserts a minimal span row directly.

    Optional ``started_at`` lets callers place the span at a specific
    trace-clock time for window-filter tests; defaults to ``now()`` when
    omitted (matching the issue-#4 fixture's behaviour). Optional
    ``error`` populates the promoted error column.
    """

    def _insert(
        *,
        trace_id: str,
        span_id: str,
        name: str,
        parent_id: str | None = None,
        started_at: dt.datetime | None = None,
        error: bool | None = None,
    ) -> None:
        if started_at is None:
            raw_conn.execute(
                """
                INSERT INTO spans (
                    trace_id, span_id, parent_id, kind, name,
                    started_at, error, attributes, seq, arrival_seq, observed_at
                ) VALUES (
                    %s, %s, %s, 'INTERNAL', %s,
                    now(), %s, '{}'::jsonb,
                    nextval('spans_seq'), currval('spans_seq'), now()
                )
                """,
                (trace_id, span_id, parent_id, name, error),
            )
        else:
            raw_conn.execute(
                """
                INSERT INTO spans (
                    trace_id, span_id, parent_id, kind, name,
                    started_at, error, attributes, seq, arrival_seq, observed_at
                ) VALUES (
                    %s, %s, %s, 'INTERNAL', %s,
                    %s, %s, '{}'::jsonb,
                    nextval('spans_seq'), currval('spans_seq'), now()
                )
                """,
                (trace_id, span_id, parent_id, name, started_at, error),
            )
        raw_conn.commit()

    return _insert


@pytest.fixture()
def insert_span_with_service_name(raw_conn):
    """Helper that inserts a span row with an explicit ``service_name``.

    The receiver-fixture in :func:`insert_span` predates issue #13 and
    leaves ``service_name`` NULL — that's still the right default for
    most existing tests. This fixture is the small extension the
    issue-#13 listing-row tests need.
    """

    def _insert(
        *,
        trace_id: str,
        span_id: str,
        name: str,
        parent_id: str | None = None,
        started_at: dt.datetime | None = None,
        error: bool | None = None,
        service_name: str | None = None,
    ) -> None:
        if started_at is None:
            raw_conn.execute(
                """
                INSERT INTO spans (
                    trace_id, span_id, parent_id, kind, name, service_name,
                    started_at, error, attributes, seq, arrival_seq, observed_at
                ) VALUES (
                    %s, %s, %s, 'INTERNAL', %s, %s,
                    now(), %s, '{}'::jsonb,
                    nextval('spans_seq'), currval('spans_seq'), now()
                )
                """,
                (trace_id, span_id, parent_id, name, service_name, error),
            )
        else:
            raw_conn.execute(
                """
                INSERT INTO spans (
                    trace_id, span_id, parent_id, kind, name, service_name,
                    started_at, error, attributes, seq, arrival_seq, observed_at
                ) VALUES (
                    %s, %s, %s, 'INTERNAL', %s, %s,
                    %s, %s, '{}'::jsonb,
                    nextval('spans_seq'), currval('spans_seq'), now()
                )
                """,
                (trace_id, span_id, parent_id, name, service_name,
                 started_at, error),
            )
        raw_conn.commit()

    return _insert
