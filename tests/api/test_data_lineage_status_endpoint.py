"""Wire contract for the trace-level lineage status (issue #120, ADR-0027 D6).

``GET /api/traces/{tid}/data-lineage`` gains two sibling fields beside ``legs``:

    {"legs": [...], "status": "complete" | "partial" | null,
     "stopped_at_seq": int | null}

The ``{"legs": [...]}`` envelope #118 introduced was shaped for exactly this. The
status is a whole-trace fact, so it sits on the envelope rather than repeated on
every leg — and the legs a truncation removes have no element to carry it anyway.

Three status values on the wire, not two: ``null`` is **unknown**, never
``"complete"`` (ADR-0027 D6 "Reading the status"). The tests below pin all three, so
a handler that defaulted the absent status would fail here rather than in
production.
"""

from __future__ import annotations

import psycopg
import pytest

from data_governance.api import SpansApiServer

pytest.importorskip("httpx")
import httpx  # noqa: E402


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


_TID = "trace-status-api-1"
_ENT_ID = "ent-1"
_IX_ID = "ix-1"


def _seed(conn: psycopg.Connection, trace_id: str, ix_id: str) -> None:
    """One interaction whose RESPONSE leg carries no payload — the D6 gap (per
    ADR-0025 the request leg always exists, so a missing response is the realistic
    trigger). The request leg gets lineage; the response leg gets none."""
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, "
        "callee_entity_id, summary) VALUES (%s, %s, %s, %s, 'did a thing')",
        (ix_id, trace_id, _ENT_ID, _ENT_ID),
    )
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error, original_seq) "
        "VALUES (%s, 'request', '2026-01-01T00:00:00Z', 'reqhash', false, 1)",
        (ix_id,),
    )
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error, original_seq) "
        "VALUES (%s, 'response', '2026-01-01T00:00:02Z', NULL, false, 2)",
        (ix_id,),
    )
    conn.execute(
        "INSERT INTO lineage_metadata (interaction_id, leg_type, data_sources, "
        "source_transformations, entities, payload_hash, seq) "
        "VALUES (%s, 'request', ARRAY['user'], '{\"user\": []}'::jsonb, "
        "ARRAY[]::text[], 'reqhash', 1)",
        (ix_id,),
    )


@pytest.fixture()
def partial_trace(configured_db: str) -> str:
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, original_seq) "
            "VALUES (%s, 'agent', 'nk-1', 'Agent One', 'span-attr', 1)",
            (_ENT_ID,),
        )
        _seed(conn, _TID, _IX_ID)
        (stop,) = conn.execute(
            "SELECT seq FROM interaction_legs WHERE interaction_id = %s "
            "AND leg_type = 'response'",
            (_IX_ID,),
        ).fetchone()
        conn.execute(
            "INSERT INTO lineage_trace_status (trace_id, status, stopped_at_seq) "
            "VALUES (%s, 'partial', %s)",
            (_TID, stop),
        )
        conn.commit()
    return _TID


def test_envelope_carries_status_beside_legs(partial_trace, api_server):
    """The wire shape: ``legs`` plus the two trace-level fields, and nothing else."""
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{partial_trace}/data-lineage")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"legs", "status", "stopped_at_seq"}


def test_partial_status_and_stop_seq_serialize(partial_trace, api_server, configured_db):
    """A truncated trace says so, and says where — the stop is the leg ``seq`` of
    the payload-less response."""
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{partial_trace}/data-lineage")
    body = resp.json()
    with psycopg.connect(configured_db) as conn:
        (stop,) = conn.execute(
            "SELECT seq FROM interaction_legs WHERE interaction_id = %s "
            "AND leg_type = 'response'",
            (_IX_ID,),
        ).fetchone()
    assert body["status"] == "partial"
    assert body["stopped_at_seq"] == stop


def test_partial_trace_serves_only_the_prefix_legs_with_lineage(
    partial_trace, api_server
):
    """``partial`` truncates the *lineage*, not the leg list (ADR-0027 D6). Both legs
    are listed — they exist — but only the pre-gap one carries a lineage object, so
    the truncation is visible in the payload *and* announced by the status. Do not
    "fix" this to expect the post-gap leg to be absent: the read has no ``seq``
    filter."""
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{partial_trace}/data-lineage")
    by_key = {(r["interaction_id"], r["leg_type"]): r for r in resp.json()["legs"]}
    assert by_key[(_IX_ID, "request")]["lineage"] is not None
    assert by_key[(_IX_ID, "response")]["lineage"] is None


def test_complete_status_serializes_with_a_null_stop(configured_db, api_server):
    """The complete case: the flag is explicit rather than implied by the absence
    of a warning, and there is no stop position."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, original_seq) "
            "VALUES (%s, 'agent', 'nk-1', 'Agent One', 'span-attr', 1)",
            (_ENT_ID,),
        )
        _seed(conn, _TID, _IX_ID)
        conn.execute(
            "INSERT INTO lineage_trace_status (trace_id, status, stopped_at_seq) "
            "VALUES (%s, 'complete', NULL)",
            (_TID,),
        )
        conn.commit()

    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{_TID}/data-lineage")
    body = resp.json()
    assert body["status"] == "complete"
    assert body["stopped_at_seq"] is None


def test_not_yet_derived_status_is_null_not_complete(configured_db, api_server):
    """No status row → ``null``. The distinction from ``"complete"`` is the whole
    contract: a client must be able to tell "coverage unknown" from "coverage
    total"."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, original_seq) "
            "VALUES (%s, 'agent', 'nk-1', 'Agent One', 'span-attr', 1)",
            (_ENT_ID,),
        )
        _seed(conn, _TID, _IX_ID)
        conn.commit()

    body = httpx.get(f"{_base_url(api_server)}/api/traces/{_TID}/data-lineage").json()
    assert body["status"] is None
    assert body["stopped_at_seq"] is None
    assert body["legs"], "sanity: the legs are served, only the coverage is unknown"


def test_unknown_trace_is_empty_with_a_null_status(api_server):
    """The empty-shape convention extends to the new fields — 200, no legs, no
    claim about coverage."""
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/no-such-trace/data-lineage")
    assert resp.status_code == 200
    assert resp.json() == {"legs": [], "status": None, "stopped_at_seq": None}


def test_missing_status_table_still_serves_the_legs(partial_trace, api_server, configured_db):
    """A deployment with 0011 but not 0012: the per-leg lineage is still served
    with ``status: null``, rather than a 500 or an empty list."""
    with psycopg.connect(configured_db) as conn:
        conn.execute("DROP TABLE IF EXISTS lineage_trace_status CASCADE")
        conn.commit()

    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{partial_trace}/data-lineage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] is None
    assert len(body["legs"]) == 2
