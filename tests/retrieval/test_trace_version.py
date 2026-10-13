"""``get_trace_version`` — the Trace version token (ADR-0034).

The token is compared for equality only, so the tests assert exactly that:
it is stable while nothing changes, and it changes on each kind of derived
write a live page cares about — a leg, an entity attribution, a lineage row,
a lineage status flip — and never because of another trace.
"""

from __future__ import annotations

import psycopg

from data_governance import retrieval

_TID = "ver-trace"


def _seed_entity(conn, *, entity_id: str, trace_id: str, span_id: str) -> None:
    conn.execute(
        "INSERT INTO spans (trace_id, span_id, parent_id, kind, name, "
        "started_at, attributes, seq, arrival_seq, observed_at) "
        "VALUES (%s, %s, NULL, 'INTERNAL', 'call', now(), '{}'::jsonb, "
        "nextval('spans_seq'), currval('spans_seq'), now())",
        (trace_id, span_id),
    )
    conn.execute(
        "INSERT INTO entities (id, kind, natural_key, display_name, namespace, "
        "detected_from, original_seq) "
        "VALUES (%s, 'agent', %s, %s, 'team1', 'span-attr', 1) "
        "ON CONFLICT (id) DO NOTHING",
        (entity_id, f"nk-{entity_id}", entity_id),
    )
    conn.execute(
        "INSERT INTO entity_spans (entity_id, trace_id, span_id, role) "
        "VALUES (%s, %s, %s, 'identified_via')",
        (entity_id, trace_id, span_id),
    )
    conn.commit()


def _add_interaction(conn, *, ix_id: str, trace_id: str, entity_id: str) -> None:
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, "
        "callee_entity_id, summary) VALUES (%s, %s, %s, %s, 'did a thing')",
        (ix_id, trace_id, entity_id, entity_id),
    )
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error) VALUES (%s, 'request', now(), NULL, false)",
        (ix_id,),
    )
    conn.commit()


def test_fresh_trace_is_all_zero_and_stable(configured_db: str) -> None:
    assert retrieval.get_trace_version(_TID) == "legs=0;ix=0;es=0;lin=0;st=@0"
    assert retrieval.get_trace_version(_TID) == retrieval.get_trace_version(_TID)


def test_each_derived_write_changes_the_token(configured_db: str, raw_conn) -> None:
    seen = [retrieval.get_trace_version(_TID)]

    def step() -> None:
        token = retrieval.get_trace_version(_TID)
        assert token not in seen, f"token did not change: {token}"
        seen.append(token)

    _seed_entity(raw_conn, entity_id="e1", trace_id=_TID, span_id="s1")
    step()  # an entity attributed before any interaction exists
    _add_interaction(raw_conn, ix_id="ix1", trace_id=_TID, entity_id="e1")
    step()  # a leg
    raw_conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error) VALUES ('ix1', 'response', now(), NULL, false)"
    )
    raw_conn.commit()
    step()  # the other leg of the same interaction
    raw_conn.execute(
        "INSERT INTO lineage_metadata (interaction_id, leg_type, data_sources, seq) "
        "VALUES ('ix1', 'request', '{}', 1)"
    )
    raw_conn.commit()
    step()  # a lineage row, which fires no notification of its own
    raw_conn.execute(
        "INSERT INTO lineage_trace_status (trace_id, status, stopped_at_seq) "
        "VALUES (%s, 'partial', 1)",
        (_TID,),
    )
    raw_conn.commit()
    step()  # the coverage status
    raw_conn.execute(
        "UPDATE lineage_trace_status SET status = 'complete', stopped_at_seq = NULL "
        "WHERE trace_id = %s",
        (_TID,),
    )
    raw_conn.commit()
    step()  # …and its flip to complete, with no new row anywhere


def test_sidecar_style_rederivation_changes_the_token(configured_db: str, raw_conn) -> None:
    """The sidecar derivation deletes and re-inserts a trace's rows per span
    (ADR-0030): same row count, same ids, new seqs — must still read as changed."""
    _seed_entity(raw_conn, entity_id="e1", trace_id=_TID, span_id="s1")
    _add_interaction(raw_conn, ix_id="ix1", trace_id=_TID, entity_id="e1")
    before = retrieval.get_trace_version(_TID)
    raw_conn.execute("DELETE FROM interaction_legs WHERE interaction_id = 'ix1'")
    raw_conn.execute("DELETE FROM interactions WHERE id = 'ix1'")
    raw_conn.commit()
    _add_interaction(raw_conn, ix_id="ix1", trace_id=_TID, entity_id="e1")
    assert retrieval.get_trace_version(_TID) != before


def test_another_traces_writes_do_not_change_the_token(configured_db: str, raw_conn) -> None:
    _seed_entity(raw_conn, entity_id="e1", trace_id=_TID, span_id="s1")
    before = retrieval.get_trace_version(_TID)
    _seed_entity(raw_conn, entity_id="e2", trace_id="elsewhere", span_id="s2")
    _add_interaction(raw_conn, ix_id="ix-else", trace_id="elsewhere", entity_id="e2")
    assert retrieval.get_trace_version(_TID) == before


def test_before_any_migration_the_token_is_all_zero(pg_dsn: str) -> None:
    """An unmigrated DB is never an error, like every other retrieval read."""
    from data_governance import db

    db.close_pool()
    db.configure(pg_dsn)
    try:
        assert retrieval.get_trace_version(_TID) == "legs=0;ix=0;es=0;lin=0;st=@0"
    finally:
        db.close_pool()
