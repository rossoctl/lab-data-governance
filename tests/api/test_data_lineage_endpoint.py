"""Contract tests for the **Data lineage** REST resource (issue #118).

``GET /api/traces/{tid}/data-lineage`` → ``{"legs": [DataLineageLeg], ...}`` — the
persisted per-leg **Data lineage metadata** for one trace, served as a pure
lookup against ``lineage_metadata`` (ADR-0027 D7: matching runs at ingest, so no
matcher call and no traversal happen here).

This file owns the per-leg half of the contract. The envelope's trace-level
``status`` / ``stopped_at_seq`` (ADR-0027 D6, issue #120) are covered in
``test_data_lineage_status_endpoint.py``.

Each element keys the leg (``interaction_id`` + ``leg_type``, ADR-0027 D5),
carries the leg's ``payload_hash``, and nests the metadata triple under a
nullable ``lineage``:

- ``null`` while the leg exists but P-data-lineage has not written its row (the
  eventual-consistency window — exactly the nullable-``classification``
  precedent on ``GET /api/payloads/{hash}``, ADR-0024);
- ``{data_sources, source_transformations, entity_path, seq}`` once the row
  lands.

These exercise the public HTTP surface end to end (a real server + httpx),
mirroring ``tests/api/test_flow_endpoints.py`` /
``tests/api/test_payload_classification.py``. This is the shape the UI ticket
(#119) consumes.
"""

from __future__ import annotations

import json

import psycopg
import pytest

from data_governance.api import SpansApiServer

pytest.importorskip("httpx")
import httpx  # noqa: E402


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


_TID = "trace-lineage-api-1"
_ENT_ID = "ent-1"
_IX_ID = "ix-1"


@pytest.fixture()
def seeded(configured_db: str) -> str:
    """One interaction, two legs, and a lineage row for the REQUEST leg only —
    so one wire element is populated and one is in the not-yet-computed window.
    """
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, original_seq) "
            "VALUES (%s, 'agent', 'nk-1', 'Agent One', 'span-attr', 1)",
            (_ENT_ID,),
        )
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) "
            "VALUES (%s, %s, %s, %s, 'did a thing')",
            (_IX_ID, _TID, _ENT_ID, _ENT_ID),
        )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error, original_seq) "
            "VALUES (%s, 'request', '2026-01-01T00:00:00Z', 'reqhash', false, 1)",
            (_IX_ID,),
        )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error, original_seq) "
            "VALUES (%s, 'response', '2026-01-01T00:00:02Z', 'resphash', true, 2)",
            (_IX_ID,),
        )
        conn.execute(
            "INSERT INTO lineage_metadata (interaction_id, leg_type, data_sources, "
            "source_transformations, entity_path, payload_hash, seq) "
            "VALUES (%s, 'request', %s, %s::jsonb, %s, 'reqhash', 1)",
            (
                _IX_ID,
                ["kb", "user"],
                json.dumps({"kb": [], "user": ["anonymization", "summarization"]}),
                ["user", "agent-one"],
            ),
        )
        conn.commit()
    return _TID


def test_data_lineage_wire_shape(seeded, api_server):
    """The envelope carries ``legs`` (each element exactly the leg key + payload
    hash + nullable nested triple) plus the trace-level coverage fields #120 added
    beside it — see ``test_data_lineage_status_endpoint.py`` for those."""
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{seeded}/data-lineage")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"legs", "status", "stopped_at_seq"}
    by_key = {(r["interaction_id"], r["leg_type"]): r for r in body["legs"]}
    assert set(by_key) == {(_IX_ID, "request"), (_IX_ID, "response")}
    req = by_key[(_IX_ID, "request")]
    assert set(req) == {"interaction_id", "leg_type", "payload_hash", "lineage"}
    assert req["payload_hash"] == "reqhash"
    assert set(req["lineage"]) == {
        "data_sources", "source_transformations", "entity_path", "seq"
    }


def test_populated_triple_serializes(seeded, api_server):
    """The triple crosses the wire intact: sources, the per-source
    transformation map (JSONB → object of arrays), and the ordered path."""
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{seeded}/data-lineage")
    by_key = {
        (r["interaction_id"], r["leg_type"]): r for r in resp.json()["legs"]
    }
    lineage = by_key[(_IX_ID, "request")]["lineage"]
    assert sorted(lineage["data_sources"]) == ["kb", "user"]
    assert lineage["source_transformations"] == {
        "kb": [],
        "user": ["anonymization", "summarization"],
    }
    assert lineage["entity_path"] == ["user", "agent-one"]
    assert lineage["seq"] == 1


def test_leg_without_lineage_is_null_not_404(seeded, api_server):
    """The response leg has no lineage row yet → ``lineage: null`` on a 200,
    the eventual-consistency window (never a 404, never a 500)."""
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{seeded}/data-lineage")
    assert resp.status_code == 200
    by_key = {
        (r["interaction_id"], r["leg_type"]): r for r in resp.json()["legs"]
    }
    resp_leg = by_key[(_IX_ID, "response")]
    assert resp_leg["lineage"] is None
    assert resp_leg["payload_hash"] == "resphash"


def test_unknown_trace_is_empty_not_404(seeded, api_server):
    """An unknown trace → 200 with an empty list, matching the flow resources'
    empty-shape convention. Coverage is ``null`` — unknown, not ``complete`` (#120)."""
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/no-such-trace/data-lineage")
    assert resp.status_code == 200
    assert resp.json() == {"legs": [], "status": None, "stopped_at_seq": None}


def test_empty_when_lineage_table_absent(seeded, api_server, configured_db):
    """A not-yet-migrated deployment serves an empty list, not a 500."""
    with psycopg.connect(configured_db) as conn:
        conn.execute("DROP TABLE IF EXISTS lineage_metadata CASCADE")
        conn.commit()
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{seeded}/data-lineage")
    assert resp.status_code == 200
    assert resp.json() == {"legs": [], "status": None, "stopped_at_seq": None}
