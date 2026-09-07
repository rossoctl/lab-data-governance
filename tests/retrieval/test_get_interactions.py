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

import dataclasses

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
            "payload_hash, error) "
            "VALUES (%s, 'request', '2026-01-01T00:00:00Z', 'reqhash', false)",
            (_IX_ID,),
        )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error) "
            "VALUES (%s, 'response', '2026-01-01T00:00:02Z', 'resphash', true)",
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


def _set_attrs(configured_db: str, span_id: str, attrs: str) -> None:
    """Replace one seeded span's stored attributes (the JSON the read-time
    derivations run over)."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "UPDATE spans SET attributes = %s::jsonb "
            "WHERE trace_id = %s AND span_id = %s",
            (attrs, _TID, span_id),
        )
        conn.commit()


# A sidecar-shaped anchor: lineage facts + the request-side facts the read-time
# derivations pick up (method, principal, a2a session).
_ANCHOR_ATTRS = (
    '{"lineage.role": "request", "lineage.direction": "outbound", '
    '"lineage.protocol": "a2a", "lineage.exchange.id": "s-anchor", '
    '"lineage.self.id": "svc-a", "http.method": "POST", '
    '"lineage.principal.sub": "alice", "a2a.session_id": "sess-7"}'
)
# Its paired response span: the response-side facts only.
_RESPONSE_ATTRS = (
    '{"lineage.role": "response", "lineage.direction": "outbound", '
    '"lineage.exchange.id": "s-anchor", "http.status_code": 200, '
    '"lineage.outcome": "ok"}'
)


def _pair_response_span(configured_db: str, span_id: str = "s-info") -> None:
    """Mark a seeded evidence span as the interaction's response leg (what the
    processor does for the paired response span, ADR-0025)."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "UPDATE interaction_spans SET leg_type = 'response' "
            "WHERE trace_id = %s AND span_id = %s",
            (_TID, span_id),
        )
        conn.commit()


def test_interaction_identity_and_counts(seeded: str) -> None:
    """Identity on the parent row (ADR-0025) + span/anchor counts."""
    result = retrieval.get_interactions(seeded)
    (ix,) = result.interactions
    assert ix.id == _IX_ID
    # The trace the interaction belongs to travels with it, so a consumer
    # holding one interaction can name its trace without the URL it came from.
    assert ix.trace_id == _TID
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
            "payload_hash, error) "
            "VALUES ('ix-0', 'request', '2025-12-31T23:00:00Z', NULL, false)",
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


def test_http_principal_and_session_from_the_span_pair(
    seeded: str, configured_db: str
) -> None:
    """The request-side facts come off the anchor, the response-side facts off
    the span the processor paired as the response leg (issue #155)."""
    _set_attrs(configured_db, "s-anchor", _ANCHOR_ATTRS)
    _set_attrs(configured_db, "s-info", _RESPONSE_ATTRS)
    _pair_response_span(configured_db)
    (ix,) = retrieval.get_interactions(seeded).interactions
    assert ix.http == retrieval.HttpView(method="POST", status_code=200, outcome="ok")
    assert ix.principal_sub == "alice"
    assert ix.session_id == "sess-7"


def test_response_facts_only_from_the_response_span(
    seeded: str, configured_db: str
) -> None:
    """Status/outcome are read from the response leg's span, never from the
    anchor: an anchor that (wrongly) carried them contributes nothing, and with
    no response span paired the two response-side fields stay null."""
    _set_attrs(
        configured_db,
        "s-anchor",
        _ANCHOR_ATTRS[:-1] + ', "http.status_code": 500, "lineage.outcome": "error"}',
    )
    _set_attrs(configured_db, "s-info", _RESPONSE_ATTRS)  # not paired as a leg
    (ix,) = retrieval.get_interactions(seeded).interactions
    assert ix.http == retrieval.HttpView(method="POST", status_code=None, outcome=None)


def test_http_null_for_non_sidecar_anchor(seeded: str) -> None:
    """The seeded anchor carries no lineage facts: http/principal/session share
    the kinds guard — absent facts, absent derivation, never a guess."""
    (ix,) = retrieval.get_interactions(seeded).interactions
    assert ix.http is None
    assert ix.principal_sub is None
    assert ix.session_id is None


def test_http_null_when_no_http_fact_present(seeded: str, configured_db: str) -> None:
    """A sidecar anchor with none of the three http facts (a listener that did
    not supply the method, response still in flight) has no http event at all —
    a null, not an object of nulls."""
    _set_attrs(
        configured_db,
        "s-anchor",
        '{"lineage.role": "request", "lineage.direction": "outbound", '
        '"lineage.exchange.id": "s-anchor", "lineage.self.id": "svc-a"}',
    )
    (ix,) = retrieval.get_interactions(seeded).interactions
    assert ix.kinds is not None  # the anchor IS sidecar-shaped
    assert ix.http is None
    assert ix.principal_sub is None
    assert ix.session_id is None


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
    # The span's own name travels with the evidence row (issue #155).
    assert by_id["s-anchor"].name == "call"
    assert by_id["s-info"].name == "child"


def test_entity_spans_have_no_leg(seeded: str) -> None:
    """Per-entity span evidence; entity_spans have no leg, so the view carries
    no ``leg_type`` field at all (it is a distinct type from the interaction
    ``SpanEvidenceView``, not that view with a null leg)."""
    result = retrieval.get_entity_spans(seeded, _ENT_ID)
    (s,) = result.spans
    assert isinstance(s, retrieval.EntitySpanEvidenceView)
    assert s.span_id == "s-ent"
    assert s.role == "identified_via"
    assert not hasattr(s, "leg_type")
    # The field set is exactly the five joined span-evidence fields — the
    # source of the wire shape the /entities/{eid}/spans endpoint serves.
    assert {f.name for f in dataclasses.fields(s)} == {
        "span_id", "role", "parent_id", "kind", "service_name"
    }


def test_unknown_ids_return_empty(seeded: str) -> None:
    """Unknown interaction/entity id → empty result (the UI renders an empty
    table, never an error)."""
    assert retrieval.get_interaction_spans(seeded, "nope").spans == []
    assert retrieval.get_entity_spans(seeded, "nope").spans == []


# ---------------------------------------------------------------------------
# The cross-trace cursor feed (issue #155): the same interaction shape, cursored
# on the interaction_legs seq stream (ADR-0007) instead of scoped to a trace.
# ---------------------------------------------------------------------------


def _max_leg_seq(configured_db: str) -> int:
    with psycopg.connect(configured_db) as conn:
        return conn.execute("SELECT MAX(seq) FROM interaction_legs").fetchone()[0]


def test_feed_empty_before_anything_is_derived(configured_db: str) -> None:
    """No legs yet → no interactions and the caller's own cursor back, so a
    consumer polling an idle system never rewinds."""
    result = retrieval.get_interactions_feed(0)
    assert result.interactions == []
    assert result.next_seq == 0
    assert retrieval.get_interactions_feed(7).next_seq == 7


def test_feed_from_zero_returns_everything(seeded: str, configured_db: str) -> None:
    """since_seq=0 is "give me the world": every interaction with any leg, the
    same enriched shape the per-trace read serves, and the highest leg seq as
    the next cursor."""
    result = retrieval.get_interactions_feed(0)
    (ix,) = result.interactions
    assert ix.id == _IX_ID
    assert ix.trace_id == _TID  # a feed consumer has no URL to read it from
    assert {leg.leg_type for leg in ix.legs} == {"request", "response"}
    assert ix.span_count == 2
    assert result.next_seq == _max_leg_seq(configured_db)


def test_feed_cursor_advances_past_seen_interactions(seeded: str) -> None:
    """Handing next_seq back yields nothing until something changes."""
    first = retrieval.get_interactions_feed(0)
    second = retrieval.get_interactions_feed(first.next_seq)
    assert second.interactions == []
    assert second.next_seq == first.next_seq


def test_feed_sees_only_new_interactions(seeded: str, configured_db: str) -> None:
    """A later interaction's legs carry higher seqs, so an advanced cursor sees
    it alone."""
    cursor = retrieval.get_interactions_feed(0).next_seq
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) VALUES ('ix-2', %s, %s, %s, 'later')",
            (_TID, _ENT_ID, _ENT_ID),
        )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error) "
            "VALUES ('ix-2', 'request', '2026-01-01T00:01:00Z', NULL, false)",
        )
        conn.commit()
    result = retrieval.get_interactions_feed(cursor)
    assert [ix.id for ix in result.interactions] == ["ix-2"]
    assert result.next_seq == _max_leg_seq(configured_db)


def test_feed_re_emits_on_a_late_response_leg(seeded: str, configured_db: str) -> None:
    """An interaction is mutable in place and carries no completion flag
    (ADR-0007): when its response leg lands later, the feed re-emits the whole
    interaction — that re-emission IS how the consumer learns the mutation."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "DELETE FROM interaction_legs WHERE interaction_id = %s "
            "AND leg_type = 'response'",
            (_IX_ID,),
        )
        conn.commit()
    first = retrieval.get_interactions_feed(0)
    (ix,) = first.interactions
    assert {leg.leg_type for leg in ix.legs} == {"request"}

    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error) "
            "VALUES (%s, 'response', '2026-01-01T00:00:02Z', 'resphash', true)",
            (_IX_ID,),
        )
        conn.commit()
    second = retrieval.get_interactions_feed(first.next_seq)
    (again,) = second.interactions
    assert again.id == _IX_ID
    assert {leg.leg_type for leg in again.legs} == {"request", "response"}
    assert again.duration_seconds == 2.0
    assert second.next_seq > first.next_seq


def test_feed_limit_caps_the_page(seeded: str, configured_db: str) -> None:
    """limit caps interactions (not legs), and next_seq stops at what the page
    actually included so the skipped interaction comes back next call."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) VALUES ('ix-2', %s, %s, %s, 'later')",
            (_TID, _ENT_ID, _ENT_ID),
        )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error) "
            "VALUES ('ix-2', 'request', '2026-01-01T00:01:00Z', NULL, false)",
        )
        conn.commit()
    page = retrieval.get_interactions_feed(0, limit=1)
    assert [ix.id for ix in page.interactions] == [_IX_ID]
    assert page.next_seq < _max_leg_seq(configured_db)
    assert [ix.id for ix in retrieval.get_interactions_feed(page.next_seq).interactions] == ["ix-2"]


def test_feed_rejects_bad_cursor_and_limit(seeded: str) -> None:
    """Out-of-range arguments are a caller error (the REST layer turns these
    into a 400), not a silently clamped page."""
    for bad in (-1,):
        with pytest.raises(ValueError):
            retrieval.get_interactions_feed(bad)
    for bad_limit in (0, 1001):
        with pytest.raises(ValueError):
            retrieval.get_interactions_feed(0, limit=bad_limit)


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
    feed = retrieval.get_interactions_feed(5)
    assert feed.interactions == [] and feed.next_seq == 5
    assert retrieval.get_entities(_TID).entities == []
    assert retrieval.get_interaction_spans(_TID, _IX_ID).spans == []
    assert retrieval.get_entity_spans(_TID, _ENT_ID).spans == []
