"""New legs reach data lineage without the dirty-trace queue (issue #279).

Migration 0026's queue holds corrections only. A new trace is derived through
the processor's other arms: its legs normally take a new ``seq`` past the
cursor. The graph algorithm writes each trace's own edge ordinals as ``seq``
instead, so a later trace's legs can sit at or below the cursor; the stale
sweep's missing-row predicate derives those.
"""

from __future__ import annotations

import psycopg

from data_governance import retrieval
from data_governance.processors.data_lineage import driver

QUEUE = "data_lineage_dirty_traces"


def _seed_entities(conn: psycopg.Connection) -> None:
    for eid, kind in (("client", "client"), ("agent", "agent"), ("tool", "tool")):
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, seq, original_seq) VALUES (%s, %s, %s, %s, 'observed', 1, 1)",
            (eid, kind, f"{kind}:{eid}", eid),
        )


def _insert_trace(
    conn: psycopg.Connection, trace_id: str, *, seqs: tuple[int, int] | None = None,
) -> None:
    parent, child = f"{trace_id}-parent", f"{trace_id}-child"
    conn.execute(
        "INSERT INTO interactions (id, trace_id, parent_interaction_id, "
        "caller_entity_id, callee_entity_id, summary) VALUES "
        "(%s, %s, NULL, 'client', 'agent', 'call'), "
        "(%s, %s, %s, 'agent', 'tool', 'call')",
        (parent, trace_id, child, trace_id, parent),
    )
    for content_hash in (f"{parent}-body", f"{child}-body"):
        conn.execute(
            "INSERT INTO interaction_payloads (content_hash, content, byte_size) "
            "VALUES (%s, '{}'::jsonb, 2)",
            (content_hash,),
        )
    rows = [
        (parent, "2026-01-01T00:00:00Z", f"{parent}-body"),
        (child, "2026-01-01T00:00:01Z", f"{child}-body"),
    ]
    for index, (iid, occurred_at, body) in enumerate(rows):
        if seqs is None:
            conn.execute(
                "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
                "payload_hash, error) VALUES (%s, 'request', %s, %s, false)",
                (iid, occurred_at, body),
            )
        else:
            conn.execute(
                "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
                "payload_hash, error, seq) VALUES (%s, 'request', %s, %s, false, %s)",
                (iid, occurred_at, body, seqs[index]),
            )


def _queued(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(f"SELECT count(*) FROM {QUEUE}").fetchone()[0]


def _client_reaches_the_child(trace_id: str) -> bool:
    lineage = retrieval.get_data_lineage(trace_id)
    child = next(leg for leg in lineage.legs if leg.interaction_id == f"{trace_id}-child")
    return (
        lineage.status == "complete"
        and child.lineage is not None
        and child.lineage.data_sources == ["client:client"]
    )


def test_a_new_trace_is_derived_by_the_cursor_without_queuing(configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        _seed_entities(conn)
        _insert_trace(conn, "fresh")
        conn.commit()
    assert _queued(configured_db) == 0

    with psycopg.connect(configured_db) as conn:
        (max_seq,) = conn.execute("SELECT max(seq) FROM interaction_legs").fetchone()
    assert driver.drain(0) == max_seq
    assert _client_reaches_the_child("fresh")
    assert _queued(configured_db) == 0


def test_a_new_trace_with_seqs_behind_the_cursor_is_still_derived(
    configured_db: str,
) -> None:
    """Graph-algorithm legs carry their trace's edge ordinals as ``seq``."""
    with psycopg.connect(configured_db) as conn:
        _seed_entities(conn)
        _insert_trace(conn, "first", seqs=(10, 11))
        conn.commit()
    cursor = driver.drain(0)
    assert cursor == 11

    with psycopg.connect(configured_db) as conn:
        _insert_trace(conn, "second", seqs=(1, 2))
        conn.commit()
    assert _queued(configured_db) == 0

    assert driver.drain(cursor) == cursor
    assert _client_reaches_the_child("second")
