"""End-to-end tests for the on-demand P-interactions processing endpoint.

`POST /proto/process/<trace_id>` backs the "Process this trace" button: it
runs the same extractor the CLI runs (single-trace semantics — drops &
recreates the proto_* scratch tables and writes only this trace) and
returns the resulting counts. This is the layer that exercises the full
fetch -> extract -> write path against real Postgres; the extractor itself
is unit-tested as a pure function under tests/processors/.

Spans are seeded from the captured canonical travel-advisor trace fixture
(shared with the processors tests) so the extraction has realistic input.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import psycopg
import pytest

from data_governance.api import SpansApiServer

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "processors" / "p_interactions_proto" / "fixtures" / "travel_agent_III.json"
)
_TRACE_ID = "8ae1f64d4bb51b750168c6ef1e11a2d8"


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


def _parse_dt(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def _seed_fixture_spans(dsn: str) -> int:
    """Load the captured trace fixture into the spans table."""
    spans = json.loads(_FIXTURE.read_text())
    with psycopg.connect(dsn) as conn:
        for s in spans:
            conn.execute(
                """
                INSERT INTO spans (
                    trace_id, span_id, parent_id, kind, name, service_name,
                    started_at, ended_at, error, status_message,
                    attributes, events, links, otlp, scope, resource_attributes,
                    arrival_seq, observed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    nextval('spans_seq'), now()
                )
                """,
                (
                    s["trace_id"], s["span_id"], s.get("parent_id"),
                    s.get("kind") or "INTERNAL", s["name"], s.get("service_name"),
                    _parse_dt(s.get("started_at")) or dt.datetime.now(dt.timezone.utc),
                    _parse_dt(s.get("ended_at")), s.get("error"), s.get("status_message"),
                    json.dumps(s.get("attributes") or {}),
                    json.dumps(s["events"]) if s.get("events") is not None else None,
                    json.dumps(s["links"]) if s.get("links") is not None else None,
                    json.dumps(s["otlp"]) if s.get("otlp") is not None else None,
                    json.dumps(s["scope"]) if s.get("scope") is not None else None,
                    json.dumps(s["resource_attributes"])
                    if s.get("resource_attributes") is not None else None,
                ),
            )
        conn.commit()
    return len(spans)


def test_process_endpoint_populates_proto_tables(api_server, configured_db):
    """POST processes the trace, returns counts, and the GET endpoints then
    surface the populated entities/interactions/graphs."""
    n = _seed_fixture_spans(configured_db)
    assert n > 0

    base = _base_url(api_server)
    resp = httpx.post(f"{base}/proto/process/{_TRACE_ID}", timeout=30.0)
    assert resp.status_code == 200, resp.text
    counts = resp.json()
    assert counts["entities"] > 0
    assert counts["interactions"] > 0
    assert counts["spans"] == n  # one base-graph node per span

    # The read endpoints now return the freshly-written rows.
    flow = httpx.get(f"{base}/proto/interactions/{_TRACE_ID}").json()
    assert len(flow["entities"]) == counts["entities"]
    assert len(flow["interactions"]) == counts["interactions"]

    graphs = httpx.get(f"{base}/proto/graphs/{_TRACE_ID}").json()
    assert len(graphs["base"]["nodes"]) == n
    assert len(graphs["entity"]["nodes"]) == counts["entities"]


def test_process_endpoint_rejects_get(api_server, configured_db):
    """The route is POST-only — a GET must not trigger processing."""
    resp = httpx.get(f"{_base_url(api_server)}/proto/process/{_TRACE_ID}")
    assert resp.status_code == 405
