"""Contract tests for ``GET /api/traces/{tid}/interactions/{iid}/labels``.

The per-leg **Labels** read over HTTP: found here (the classifier) beside carried
in (label lineage), one response per interaction. The read logic is pinned in
``tests/retrieval/test_get_leg_labels.py`` and ``test_label_merge.py``; this file
pins the wire shape, the error paths and the route wiring.
"""

from __future__ import annotations

import json

import psycopg
import pytest
from starlette.requests import Request

from data_governance import retrieval
from data_governance.api import SpansApiServer, _interaction_labels_handler, build_app

pytest.importorskip("httpx")
import httpx  # noqa: E402

_TID = "trace-labels-api-1"
_IX = "ix-1"
_ORIGIN = "ix-0"


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


@pytest.fixture()
def seeded(configured_db: str) -> str:
    """One interaction whose request leg carries a classified payload with one
    finding and one carried-in label of another type on the same span."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, detected_from, "
            "original_seq) VALUES ('e1', 'agent', 'nk-1', 'booking-agent', 'span-attr', 1), "
            "('e2', 'tool', 'nk-2', 'create-booking', 'span-attr', 1)"
        )
        for ix, h, seq in [(_ORIGIN, "originhash", 1), (_IX, "reqhash", 2)]:
            conn.execute(
                "INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, summary) "
                "VALUES (%s, %s, 'e1', 'e2', 'x')",
                (ix, _TID),
            )
            conn.execute(
                "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, payload_hash, "
                "error, seq) VALUES (%s, 'request', now(), %s, false, %s)",
                (ix, h, seq),
            )
        conn.execute(
            "INSERT INTO payload_classifications (content_hash, sensitivity_level, regulatory_tags, "
            "findings, model_version) VALUES ('reqhash', 'INTERNAL', '{PI}', %s::jsonb, 2)",
            (json.dumps([{"entity_type": "OPID", "start": 16, "end": 25, "text": "BK-2850EB",
                          "sensitivity_level": "INTERNAL", "regulatory_tags": ["PI"]}]),),
        )
        conn.execute(
            "INSERT INTO payload_label_edges (edge_id, trace_id, to_interaction_id, to_leg_type, "
            "to_payload_hash, from_interaction_id, from_leg_type, from_payload_hash, "
            "origin_interaction_id, origin_leg_type, origin_payload_hash, unit_start, unit_end, "
            "unit_text, unit_value, entity_type, regulatory_tags, sensitivity_level, matched_text, "
            "matched_start, matched_end, matcher, match_kind, score, held_from, held_to, seq) "
            "VALUES ('edge-1', %s, %s, 'request', 'reqhash', %s, 'request', 'originhash', %s, "
            "'request', 'originhash', 107, 116, 'BK-2850EB', 'BK-2850EB', 'PID', '{PII}', "
            "'RESTRICTED', 'BK-2850EB', 16, 25, 'exact', 'whole', 1, 0, 9, 2)",
            (_TID, _IX, _ORIGIN, _ORIGIN),
        )
        conn.execute(
            "INSERT INTO label_lineage_trace_status (trace_id, status, unclassified_legs, "
            "unanchored_findings, derived_legs, edge_count, matcher) "
            "VALUES (%s, 'complete', 0, 0, 2, 1, 'exact')",
            (_TID,),
        )
        conn.commit()
    return _TID


def test_labels_wire_shape(seeded, api_server):
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{seeded}/interactions/{_IX}/labels")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lineage_status"] == "complete"
    (leg,) = body["legs"]
    assert set(leg) == {"leg_type", "payload_hash", "seq", "found", "carried", "labels"}
    assert leg["found"] == {"sensitivity_level": "INTERNAL", "regulatory_tags": ["PI"]}
    assert leg["carried"] == {"max_level": "RESTRICTED", "regulatory_tags": ["PII"], "origin_count": 1}
    pid, opid = leg["labels"]
    assert pid == {
        "text": "BK-2850EB", "start": 16, "end": 25, "entity_type": "PID",
        "sensitivity_level": "RESTRICTED", "regulatory_tags": ["PII"], "found_here": False,
        "carried_in": [{
            "origin_interaction_id": _ORIGIN, "origin_leg_type": "request", "origin_seq": 1,
            "caller": "booking-agent", "callee": "create-booking",
            "unit_text": "BK-2850EB", "unit_value": "BK-2850EB", "match_kind": "whole",
            "origin_trace_id": None,
            # The one chain: born at the origin (the unit, no stored payload here so
            # no excerpt), carried straight to this leg (the matched span).
            "chains": [[
                {"interaction_id": _ORIGIN, "leg_type": "request", "seq": 1, "trace_id": _TID,
                 "caller": "booking-agent", "callee": "create-booking",
                 "text": "BK-2850EB", "start": 107, "end": 116, "role": "origin", "excerpt": None},
                {"interaction_id": _IX, "leg_type": "request", "seq": 2, "trace_id": _TID,
                 "caller": "booking-agent", "callee": "create-booking",
                 "text": "BK-2850EB", "start": 16, "end": 25, "role": "here", "excerpt": None},
            ]],
        }],
    }
    assert (opid["entity_type"], opid["found_here"], opid["carried_in"]) == ("OPID", True, [])


def test_unknown_interaction_is_empty_not_404(seeded, api_server):
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{seeded}/interactions/nope/labels")
    assert resp.status_code == 200
    assert resp.json() == {"legs": [], "lineage_status": None}


def test_the_spans_route_is_not_shadowed(seeded, api_server):
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{seeded}/interactions/{_IX}/spans")
    assert resp.status_code == 200
    assert set(resp.json()) == {"spans"}


@pytest.mark.asyncio
async def test_retrieval_failure_is_a_500(monkeypatch) -> None:
    def boom(trace_id: str, interaction_id: str):
        raise RuntimeError("db down")

    monkeypatch.setattr(retrieval, "get_leg_labels", boom)
    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/traces/{_TID}/interactions/{_IX}/labels")
    assert resp.status_code == 500
    assert resp.json() == {"error": "db down"}


@pytest.mark.asyncio
async def test_missing_ids_are_a_400() -> None:
    # The route's {iid:str} never matches an empty segment, so the guard is
    # reached only by calling the handler directly.
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [],
                       "query_string": b"", "path_params": {"tid": _TID}})
    resp = await _interaction_labels_handler(request)
    assert resp.status_code == 400
