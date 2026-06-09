"""Fixtures for the graph-builder tests.

Spans are seeded into a real (testcontainers) Postgres migrated to head; the
backfill then reads them through the Layer-1 pool, exactly as it does in
production. ``seq``/``arrival_seq`` are drawn from ``spans_seq`` (mirroring the
receiver) so every seeded span gets a unique, monotonic ``arrival_seq`` — the
backfill's keyset-paging axis — without the tests managing allocation.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest

from data_governance import db


_SEED_SQL = """
    INSERT INTO spans (
        trace_id, span_id, parent_id, name, kind, service_name,
        started_at, attributes, seq, arrival_seq
    )
    VALUES (
        %s, %s, %s, %s, %s, %s,
        %s, %s::jsonb, nextval('spans_seq'), currval('spans_seq')
    )
"""


@pytest.fixture()
def configured_db(migrated_dsn: str) -> Iterator[str]:
    """A migrated DB with the Layer-1 pool pointed at it; returns the DSN."""
    db.close_pool()
    db.configure(migrated_dsn)
    try:
        yield migrated_dsn
    finally:
        db.close_pool()


@pytest.fixture()
def make_span() -> Callable[..., dict[str, Any]]:
    """Build a span dict with sane defaults and an auto-incrementing
    ``started_at`` (so ordering is deterministic without callers specifying
    timestamps)."""
    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    counter = {"i": 0}

    def _make(
        span_id: str,
        *,
        trace: str = "t",
        parent: str | None = None,
        name: str = "n",
        kind: str | None = "INTERNAL",
        service: str | None = None,
        attrs: dict[str, Any] | None = None,
        started_at: Any = None,
    ) -> dict[str, Any]:
        counter["i"] += 1
        return {
            "trace_id": trace,
            "span_id": span_id,
            "parent_id": parent,
            "name": name,
            "kind": kind,
            "service_name": service,
            "started_at": started_at or base + dt.timedelta(seconds=counter["i"]),
            "attributes": attrs or {},
        }

    return _make


@pytest.fixture()
def seed_spans(configured_db: str) -> Callable[[list[dict[str, Any]]], None]:
    """Insert a list of span dicts into the migrated DB."""

    def _seed(spans: list[dict[str, Any]]) -> None:
        with psycopg.connect(configured_db) as conn:
            for s in spans:
                conn.execute(
                    _SEED_SQL,
                    (
                        s["trace_id"],
                        s["span_id"],
                        s.get("parent_id"),
                        s.get("name", "n"),
                        s.get("kind"),
                        s.get("service_name"),
                        s["started_at"],
                        json.dumps(s.get("attributes") or {}),
                    ),
                )

    return _seed
