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
    ``error`` populates the promoted error column. Optional ``kind``,
    ``status_message``, ``events``, ``links`` populate the trace-tree
    columns added by issue #14.
    """
    import json as _json

    def _insert(
        *,
        trace_id: str,
        span_id: str,
        name: str,
        parent_id: str | None = None,
        started_at: dt.datetime | None = None,
        ended_at: dt.datetime | None = None,
        error: bool | None = None,
        kind: str = "INTERNAL",
        status_message: str | None = None,
        events: list | None = None,
        links: list | None = None,
        otlp: dict | None = None,
        scope: dict | None = None,
        resource_attributes: dict | None = None,
    ) -> None:
        events_json = _json.dumps(events) if events is not None else None
        links_json = _json.dumps(links) if links is not None else None
        otlp_json = _json.dumps(otlp) if otlp is not None else None
        scope_json = _json.dumps(scope) if scope is not None else None
        res_json = (
            _json.dumps(resource_attributes)
            if resource_attributes is not None
            else None
        )
        ts_expr = "now()" if started_at is None else "%s"
        extra_params = (
            ()
            if started_at is None
            else (started_at,)
        )
        raw_conn.execute(
            f"""
            INSERT INTO spans (
                trace_id, span_id, parent_id, kind, name,
                started_at, ended_at, error, status_message, events, links,
                otlp, scope, resource_attributes,
                attributes, seq, arrival_seq, observed_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                {ts_expr}, %s, %s, %s, %s::jsonb, %s::jsonb,
                %s::jsonb, %s::jsonb, %s::jsonb,
                '{{}}'::jsonb,
                nextval('spans_seq'), currval('spans_seq'), now()
            )
            """,
            (
                trace_id, span_id, parent_id, kind, name,
                *extra_params,
                ended_at, error, status_message, events_json, links_json,
                otlp_json, scope_json, res_json,
            ),
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
