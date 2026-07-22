"""Contract tests for the execution-flow REST resources.

The flow graph is served as four lean resources nested under a trace
(namespaced under ``/api/`` per ADR-0017):

- ``GET /api/traces/{tid}/interactions``        → ``{"interactions": [...]}``
- ``GET /api/traces/{tid}/entities``            → ``{"entities": [...]}``
- ``GET /api/traces/{tid}/interactions/{iid}/spans`` → ``{"spans": [...]}``
- ``GET /api/traces/{tid}/entities/{eid}/spans``      → ``{"spans": [...]}``

The list responses are lean: interactions carry a ``span_count`` /
``anchor_count`` summary (so the flow table can size evidence without pulling
every span) but NOT the span rows themselves, and neither list bundles the
other resource. Span provenance is a per-id sub-resource, fetched lazily on
drill-in. These tests pin that split and the empty-on-unknown convention.
"""

from __future__ import annotations

import psycopg
import pytest

from data_governance.api import SpansApiServer

pytest.importorskip("httpx")
import httpx  # noqa: E402


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


_TID = "trace-flow-1"
_ENT_ID = "ent-1"
_IX_ID = "ix-1"


@pytest.fixture()
def seeded(configured_db: str) -> str:
    """Seed one trace with an entity + interaction and their span evidence.

    Shapes match migration 0004 (interactions_schema): entity/interaction span
    roles come from their respective ENUMs (``entity_span_role`` has no
    ``anchor``; ``interaction_span_role`` does). The interaction gets two
    evidence spans (one ``anchor``, one ``info``) so anchor_count (1) is
    distinguishable from span_count (2); ``interaction_spans`` PK is
    ``(trace_id, span_id)`` so the two rows use distinct span_ids.
    """
    with psycopg.connect(configured_db) as conn:
        # Three spans the evidence rows join back to (spans.kind / .name /
        # .started_at are NOT NULL).
        for sid, name in (("s-anchor", "call"), ("s-info", "child"), ("s-ent", "auth")):
            conn.execute(
                "INSERT INTO spans (trace_id, span_id, parent_id, kind, name, "
                "started_at, attributes, seq, arrival_seq, observed_at) "
                "VALUES (%s, %s, NULL, 'INTERNAL', %s, now(), '{}'::jsonb, "
                "nextval('spans_seq'), currval('spans_seq'), now())",
                (_TID, sid, name),
            )
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, original_seq) "
            "VALUES (%s, 'agent', 'nk-1', 'Agent One', 'span-attr', 1)",
            (_ENT_ID,),
        )
        conn.execute(
            "INSERT INTO entity_spans (entity_id, trace_id, span_id, role) "
            "VALUES (%s, %s, 's-ent', 'identified_via')",
            (_ENT_ID, _TID),
        )
        # Parent interactions row is identity only (ADR-0025).
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) "
            "VALUES (%s, %s, %s, %s, 'did a thing')",
            (_IX_ID, _TID, _ENT_ID, _ENT_ID),
        )
        # Two legs: a request leg with a payload and a NULL-payload response leg
        # (occurred_at brackets the call; duration = response - request = 2s).
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
            "INSERT INTO interaction_spans (interaction_id, trace_id, span_id, "
            "role, leg_type) VALUES (%s, %s, 's-anchor', 'anchor', 'request')",
            (_IX_ID, _TID),
        )
        conn.execute(
            "INSERT INTO interaction_spans (interaction_id, trace_id, span_id, "
            "role, leg_type) VALUES (%s, %s, 's-info', 'info', 'request')",
            (_IX_ID, _TID),
        )
        conn.commit()
    return _TID


def test_interactions_list_has_counts_and_no_bulk_maps(seeded, api_server):
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{seeded}/interactions")
    assert resp.status_code == 200
    body = resp.json()
    # Lean shape: interactions only — no entities, no spans_by_* maps.
    assert set(body) == {"interactions"}
    (ix,) = body["interactions"]
    assert ix["id"] == _IX_ID
    assert ix["span_count"] == 2
    assert ix["anchor_count"] == 1
    # The span rows themselves are NOT inlined on the list.
    assert "spans" not in ix
    assert "spans_by_interaction" not in body


def test_interactions_carry_nested_legs(seeded, api_server):
    """ADR-0025: each interaction carries its request/response legs nested,
    plus a computed duration and an aggregate any_error over the legs."""
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{seeded}/interactions")
    (ix,) = resp.json()["interactions"]
    # Identity on the interaction itself.
    assert ix["caller_entity_id"] == _ENT_ID
    assert ix["callee_entity_id"] == _ENT_ID
    assert ix["summary"] == "did a thing"
    # The leg-dependent fields moved off the interaction (ADR-0025).
    for gone in ("started_at", "ended_at", "request_payload_hash",
                 "response_payload_hash", "error"):
        assert gone not in ix, gone
    # Legs nested, keyed by leg_type, request first.
    legs = {leg["leg_type"]: leg for leg in ix["legs"]}
    assert set(legs) == {"request", "response"}
    assert legs["request"]["payload_hash"] == "reqhash"
    assert legs["request"]["error"] is False
    assert legs["response"]["payload_hash"] == "resphash"
    assert legs["response"]["error"] is True
    # Duration = response.occurred_at - request.occurred_at = 2 seconds.
    assert ix["duration_seconds"] == 2.0
    # any_error aggregates the legs (the response leg errored).
    assert ix["any_error"] is True


def test_interaction_duration_null_when_response_leg_absent(
    seeded, api_server, configured_db
):
    """A request-only interaction (response still in flight) has null duration —
    the 'response pending' signal, not an error (ADR-0025)."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "DELETE FROM interaction_legs WHERE interaction_id = %s "
            "AND leg_type = 'response'",
            (_IX_ID,),
        )
        conn.commit()
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{seeded}/interactions")
    (ix,) = resp.json()["interactions"]
    assert {leg["leg_type"] for leg in ix["legs"]} == {"request"}
    assert ix["duration_seconds"] is None


def test_entities_list_is_lean(seeded, api_server):
    resp = httpx.get(f"{_base_url(api_server)}/api/traces/{seeded}/entities")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"entities"}
    (ent,) = body["entities"]
    assert ent["id"] == _ENT_ID
    assert ent["display_name"] == "Agent One"
    assert "spans_by_entity" not in body
    assert "interactions" not in body


def test_interaction_spans_sub_resource(seeded, api_server):
    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces/{seeded}/interactions/{_IX_ID}/spans"
    )
    assert resp.status_code == 200
    spans = resp.json()["spans"]
    assert len(spans) == 2
    by_id = {s["span_id"]: s for s in spans}
    assert by_id["s-anchor"]["role"] == "anchor"
    assert by_id["s-info"]["role"] == "info"
    # Provenance shape: joined-through span fields present, plus the leg the
    # span evidences (ADR-0025).
    assert set(spans[0]) == {
        "span_id", "role", "parent_id", "kind", "service_name", "leg_type"
    }
    assert by_id["s-anchor"]["leg_type"] == "request"


def test_entity_spans_sub_resource(seeded, api_server):
    resp = httpx.get(
        f"{_base_url(api_server)}/api/traces/{seeded}/entities/{_ENT_ID}/spans"
    )
    assert resp.status_code == 200
    spans = resp.json()["spans"]
    assert len(spans) == 1
    assert spans[0]["span_id"] == "s-ent"
    assert spans[0]["role"] == "identified_via"


def test_unknown_ids_return_empty_spans_not_404(seeded, api_server):
    """Unknown id → 200 with an empty list, so the UI renders an empty table
    rather than an error. Mirrors the empty-shape convention on the lists."""
    base = _base_url(api_server)
    ix = httpx.get(f"{base}/api/traces/{seeded}/interactions/nope/spans")
    ent = httpx.get(f"{base}/api/traces/{seeded}/entities/nope/spans")
    assert ix.status_code == 200 and ix.json() == {"spans": []}
    assert ent.status_code == 200 and ent.json() == {"spans": []}

    # NOTE: the flow view's trace-id parsing moved from
    # execution_flow_logic.js's getTraceId() into the React SPA (React Router
    # useParams over ``/ui/traces/:tid``); its page-prefix contract is covered
    # by the SPA's own tests. The API-boundary tests above are what this module
    # continues to own.
