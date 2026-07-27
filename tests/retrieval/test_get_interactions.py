"""In-process tests for the Interaction retrieval seam.

These drive ``retrieval.get_interactions`` / ``get_entities`` /
``get_interaction_spans`` / ``get_entity_spans`` directly against a migrated
DB — the fast seam that owns the flow read *logic* (nested legs, computed
**Duration**, aggregated ``error``, chronological ordering, the
not-yet-migrated empty shape). The HTTP flow tests
(``tests/api/test_flow_endpoints.py``) then only check that the handler wires
this module to the wire.

Seed shape mirrors ``tests/api/test_flow_endpoints.py`` (migration 0004 +
0009 legs): one trace with an entity, one interaction, two legs (request with
a payload + errorless, response with a payload + errored, bracketing a 2s
call), and two evidence spans (anchor + info) so anchor_count (1) is
distinguishable from span_count (2).
"""

from __future__ import annotations

import psycopg
import pytest

from data_governance import retrieval

_TID = "trace-flow-1"
_ENT_ID = "ent-1"
_IX_ID = "ix-1"


@pytest.fixture()
def seeded(configured_db: str) -> str:
    with psycopg.connect(configured_db) as conn:
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


def test_interaction_identity_and_counts(seeded: str) -> None:
    """Identity on the parent row (ADR-0025) + span/anchor counts."""
    result = retrieval.get_interactions(seeded)
    (ix,) = result.interactions
    assert ix.id == _IX_ID
    assert ix.caller_entity_id == _ENT_ID
    assert ix.callee_entity_id == _ENT_ID
    assert ix.summary == "did a thing"
    assert ix.parent_interaction_id is None
    assert ix.span_count == 2
    assert ix.anchor_count == 1


def test_interaction_carries_nested_legs(seeded: str) -> None:
    """ADR-0025: request/response legs nested, request first."""
    (ix,) = retrieval.get_interactions(seeded).interactions
    legs = {leg.leg_type: leg for leg in ix.legs}
    assert set(legs) == {"request", "response"}
    assert legs["request"].payload_hash == "reqhash"
    assert legs["request"].error is False
    assert legs["response"].payload_hash == "resphash"
    assert legs["response"].error is True


def test_duration_is_response_minus_request(seeded: str) -> None:
    """Duration computed on read = response.occurred_at - request.occurred_at."""
    (ix,) = retrieval.get_interactions(seeded).interactions
    assert ix.duration_seconds == 2.0
    assert ix.any_error is True


def test_duration_null_when_response_leg_absent(seeded: str, configured_db: str) -> None:
    """Request-only interaction (response in flight) → null duration, not error."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "DELETE FROM interaction_legs WHERE interaction_id = %s "
            "AND leg_type = 'response'",
            (_IX_ID,),
        )
        conn.commit()
    (ix,) = retrieval.get_interactions(seeded).interactions
    assert {leg.leg_type for leg in ix.legs} == {"request"}
    assert ix.duration_seconds is None


def test_interactions_ordered_by_request_occurrence(seeded: str, configured_db: str) -> None:
    """Rows are chronological by the request leg's occurred_at (the parent no
    longer carries started_at)."""
    with psycopg.connect(configured_db) as conn:
        # A second interaction whose request leg is EARLIER, so it must sort first.
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) VALUES ('ix-0', %s, %s, %s, 'earlier')",
            (_TID, _ENT_ID, _ENT_ID),
        )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error, original_seq) "
            "VALUES ('ix-0', 'request', '2025-12-31T23:00:00Z', NULL, false, 3)",
        )
        conn.commit()
    ids = [ix.id for ix in retrieval.get_interactions(seeded).interactions]
    assert ids == ["ix-0", _IX_ID]


def test_entities_scoped_to_trace(seeded: str) -> None:
    """Entities are cross-trace stable (ADR-0013): scoped via entity_spans."""
    (ent,) = retrieval.get_entities(seeded).entities
    assert ent.id == _ENT_ID
    assert ent.kind == "agent"
    assert ent.natural_key == "nk-1"
    assert ent.display_name == "Agent One"
    assert ent.detected_from == "span-attr"


def test_interaction_spans_carry_leg_type(seeded: str) -> None:
    """Per-interaction span evidence, with the leg the span evidences (ADR-0025)."""
    result = retrieval.get_interaction_spans(seeded, _IX_ID)
    by_id = {s.span_id: s for s in result.spans}
    assert set(by_id) == {"s-anchor", "s-info"}
    assert by_id["s-anchor"].role == "anchor"
    assert by_id["s-anchor"].leg_type == "request"
    assert by_id["s-info"].role == "info"
    assert by_id["s-anchor"].parent_id is None
    assert by_id["s-anchor"].kind == "INTERNAL"


def test_entity_spans_have_no_leg(seeded: str) -> None:
    """Per-entity span evidence; entity_spans have no leg_type."""
    result = retrieval.get_entity_spans(seeded, _ENT_ID)
    (s,) = result.spans
    assert s.span_id == "s-ent"
    assert s.role == "identified_via"
    assert s.leg_type is None


def test_unknown_ids_return_empty(seeded: str) -> None:
    """Unknown interaction/entity id → empty result (the UI renders an empty
    table, never an error)."""
    assert retrieval.get_interaction_spans(seeded, "nope").spans == []
    assert retrieval.get_entity_spans(seeded, "nope").spans == []


def test_empty_when_interactions_migration_absent(configured_db: str) -> None:
    """Before the interactions migration has run, the reads return empty typed
    results, never raise — a fresh DB serves an empty flow, not a 500.

    This is the first test to pin the not-yet-migrated guard (it was only ever
    an untested production short-circuit in the HTTP handlers).
    """
    with psycopg.connect(configured_db) as conn:
        # Drop the derived tables (all land in migration 0004). CASCADE takes
        # their dependent link tables + FKs with them.
        conn.execute(
            "DROP TABLE IF EXISTS interaction_spans, interaction_legs, "
            "entity_spans, interaction_payloads, interactions, entities CASCADE"
        )
        conn.commit()
    assert retrieval.get_interactions(_TID).interactions == []
    assert retrieval.get_entities(_TID).entities == []
    assert retrieval.get_interaction_spans(_TID, _IX_ID).spans == []
    assert retrieval.get_entity_spans(_TID, _ENT_ID).spans == []
