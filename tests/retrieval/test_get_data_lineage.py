"""In-process tests for the **Data lineage** retrieval seam (issue #118).

These drive ``retrieval.get_data_lineage`` directly against a migrated DB — the
seam that owns the read *logic*: the pure lookup against ``lineage_metadata``
(no matcher call, no traversal at read time — ADR-0027 D7), the trace scoping
through ``interactions`` (the derived table has no ``trace_id`` of its own), the
JSONB ``source_transformations`` map shaped back into a
``data_source -> list<transformation>`` dict, and the two graceful-absence
shapes: a leg whose lineage row has not landed yet (``lineage: None`` — the
eventual-consistency window, exactly the ``get_payload`` classification
precedent) and a DB where the lineage migration has not run (empty typed
result, never a raise).

Seed shape extends ``test_get_interactions.py``'s ``seeded`` fixture with
``lineage_metadata`` rows: the request leg gets a populated triple (two sources,
one of them carrying two transformations and the other an empty set — the JSONB
round-trip cases), the response leg is deliberately left WITHOUT a row so the
not-yet-computed shape is covered by the same fixture.
"""

from __future__ import annotations

import json

import psycopg
import pytest

from data_governance import retrieval

_TID = "trace-lineage-1"
_OTHER_TID = "trace-lineage-2"
_ENT_ID = "ent-1"
_IX_ID = "ix-1"


def _seed_trace(conn: psycopg.Connection, trace_id: str, ix_id: str) -> None:
    """One interaction with a request + response leg, both payload-bearing."""
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, "
        "callee_entity_id, summary) "
        "VALUES (%s, %s, %s, %s, 'did a thing')",
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
        "VALUES (%s, 'response', '2026-01-01T00:00:02Z', 'resphash', true, 2)",
        (ix_id,),
    )


def _seed_lineage(
    conn: psycopg.Connection,
    *,
    interaction_id: str,
    leg_type: str,
    data_sources: list[str],
    source_transformations: dict[str, list[str]],
    entity_path: list[str],
    payload_hash: str | None,
    seq: int,
) -> None:
    """Write one ``lineage_metadata`` row directly — the shape the
    P-data-lineage driver's upsert produces (``driver._upsert``)."""
    conn.execute(
        "INSERT INTO lineage_metadata (interaction_id, leg_type, data_sources, "
        "source_transformations, entity_path, payload_hash, seq) "
        "VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)",
        (
            interaction_id,
            leg_type,
            data_sources,
            json.dumps(source_transformations),
            entity_path,
            payload_hash,
            seq,
        ),
    )


@pytest.fixture()
def seeded(configured_db: str) -> str:
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, original_seq) "
            "VALUES (%s, 'agent', 'nk-1', 'Agent One', 'span-attr', 1)",
            (_ENT_ID,),
        )
        _seed_trace(conn, _TID, _IX_ID)
        # Request leg: a populated triple. 'user' carries two transformations
        # (the multi-transformation JSONB case), 'kb' an empty set (an origin's
        # real empty value, not a null).
        _seed_lineage(
            conn,
            interaction_id=_IX_ID,
            leg_type="request",
            data_sources=["kb", "user"],
            source_transformations={
                "kb": [],
                "user": ["anonymization", "summarization"],
            },
            entity_path=["user", "agent-one"],
            payload_hash="reqhash",
            seq=1,
        )
        # Response leg: NO lineage row — the eventual-consistency window.
        conn.commit()
    return _TID


def test_lineage_triple_round_trips(seeded: str) -> None:
    """The full metadata triple comes back: data sources, the per-source
    transformation map, and the ORDERED entity path (ADR-0027 "Lineage
    metadata")."""
    result = retrieval.get_data_lineage(seeded)
    by_key = {(row.interaction_id, row.leg_type): row for row in result.legs}
    req = by_key[(_IX_ID, "request")]
    assert req.lineage is not None
    assert sorted(req.lineage.data_sources) == ["kb", "user"]
    assert req.lineage.source_transformations == {
        "kb": [],
        "user": ["anonymization", "summarization"],
    }
    # Order is the whole point of entity_path — preserved verbatim.
    assert req.lineage.entity_path == ["user", "agent-one"]
    assert req.payload_hash == "reqhash"
    assert req.lineage.seq == 1


def test_leg_without_lineage_row_reads_as_not_yet_computed(seeded: str) -> None:
    """A leg whose lineage row has not landed yet is present with
    ``lineage=None`` — "not yet computed", never a 404 or an exception (the
    ``get_payload`` nullable-classification precedent, ADR-0024)."""
    result = retrieval.get_data_lineage(seeded)
    by_key = {(row.interaction_id, row.leg_type): row for row in result.legs}
    assert set(by_key) == {(_IX_ID, "request"), (_IX_ID, "response")}
    resp = by_key[(_IX_ID, "response")]
    assert resp.lineage is None
    # The leg's own facts still ride along, so a consumer can show the row.
    assert resp.payload_hash == "resphash"


def test_legs_ordered_by_leg_seq(seeded: str) -> None:
    """Rows come back in leg ``seq`` order — the only execution order the schema
    offers (ADR-0027 D6 reasons in leg seq)."""
    result = retrieval.get_data_lineage(seeded)
    assert [row.leg_type for row in result.legs] == ["request", "response"]


def test_scoped_to_one_trace(seeded: str, configured_db: str) -> None:
    """``lineage_metadata`` has no ``trace_id``; scoping goes through
    ``interactions``. A second trace's legs must not leak in — a false
    cross-trace data-flow claim is the one thing this must never make."""
    with psycopg.connect(configured_db) as conn:
        _seed_trace(conn, _OTHER_TID, "ix-2")
        _seed_lineage(
            conn,
            interaction_id="ix-2",
            leg_type="request",
            data_sources=["other"],
            source_transformations={"other": []},
            entity_path=[],
            payload_hash="otherhash",
            seq=3,
        )
        conn.commit()
    result = retrieval.get_data_lineage(seeded)
    assert {row.interaction_id for row in result.legs} == {_IX_ID}
    other = retrieval.get_data_lineage(_OTHER_TID)
    assert {row.interaction_id for row in other.legs} == {"ix-2"}


def test_empty_triple_is_an_origin_not_an_absence(seeded: str, configured_db: str) -> None:
    """An origin's lineage is a REAL empty triple (empty path, one-key map with
    an empty set) — distinguishable from the absent row that means "not yet
    computed"."""
    with psycopg.connect(configured_db) as conn:
        _seed_lineage(
            conn,
            interaction_id=_IX_ID,
            leg_type="response",
            data_sources=["agent-one"],
            source_transformations={"agent-one": []},
            entity_path=[],
            payload_hash="resphash",
            seq=2,
        )
        conn.commit()
    result = retrieval.get_data_lineage(seeded)
    by_key = {(row.interaction_id, row.leg_type): row for row in result.legs}
    resp = by_key[(_IX_ID, "response")]
    assert resp.lineage is not None
    assert resp.lineage.data_sources == ["agent-one"]
    assert resp.lineage.source_transformations == {"agent-one": []}
    assert resp.lineage.entity_path == []


def test_trace_with_no_interactions_is_empty(seeded: str) -> None:
    """An unknown / interaction-free trace reads as an empty typed result, not a
    404 — same convention as the flow reads."""
    result = retrieval.get_data_lineage("no-such-trace")
    assert result.legs == []
    assert isinstance(result, retrieval.GetDataLineageResult)


def test_empty_when_lineage_migration_absent(seeded: str, configured_db: str) -> None:
    """With ``lineage_metadata`` absent (a not-yet-migrated deployment) the read
    returns an empty typed result rather than raising — mirroring the
    interactions path's ``_derived_tables_exist`` guard."""
    with psycopg.connect(configured_db) as conn:
        conn.execute("DROP TABLE IF EXISTS lineage_metadata CASCADE")
        conn.commit()
    result = retrieval.get_data_lineage(seeded)
    assert result.legs == []


def test_empty_when_interactions_migration_absent(seeded: str, configured_db: str) -> None:
    """Trace scoping goes through ``interactions``; with the interactions
    migration absent the read is empty too, never a raise."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "DROP TABLE IF EXISTS interaction_spans, interaction_legs, "
            "entity_spans, interaction_payloads, interactions, entities CASCADE"
        )
        conn.commit()
    assert retrieval.get_data_lineage(seeded).legs == []
