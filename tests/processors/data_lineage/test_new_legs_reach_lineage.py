"""New legs reach data lineage (issue #279).

A leg normally takes a new ``seq`` past every stored leg and reaches the
processor through its cursor, without the dirty-trace queue. The graph algorithm
writes each trace's own edge ordinals as ``seq`` instead, so a new leg can sit at
or below the cursor, including in a trace that is already derived. The stale
sweep cannot derive every such leg: it skips a leg without a payload, and in a
partial trace a leg outside the stored causal prefix. Migration 0026 therefore
queues the trace of a new leg the cursor might never offer.
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


def _add_call(
    conn: psycopg.Connection,
    trace_id: str,
    interaction_id: str,
    *,
    occurred_at: str,
    body: str | None,
    seq: int | None = None,
) -> None:
    """Add an agent-to-tool call under *trace_id*'s parent, with a request leg.

    ``body=None`` leaves the payload absent (ADR-0028 D6). ``seq=None`` takes the
    column default, as the streaming and sidecar writers do."""
    conn.execute(
        "INSERT INTO interactions (id, trace_id, parent_interaction_id, "
        "caller_entity_id, callee_entity_id, summary) "
        "VALUES (%s, %s, %s, 'agent', 'tool', 'call')",
        (interaction_id, trace_id, f"{trace_id}-parent"),
    )
    if body is not None:
        conn.execute(
            "INSERT INTO interaction_payloads (content_hash, content, byte_size) "
            "VALUES (%s, '{}'::jsonb, 2)",
            (body,),
        )
    if seq is None:
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error) VALUES (%s, 'request', %s, %s, false)",
            (interaction_id, occurred_at, body),
        )
    else:
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error, seq) VALUES (%s, 'request', %s, %s, false, %s)",
            (interaction_id, occurred_at, body, seq),
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


def test_a_new_leg_past_the_cursor_joins_a_derived_trace_without_queuing(
    configured_db: str,
) -> None:
    """A leg taking the next ``seq`` lies past every stored leg, so the cursor
    offers it. Queuing its trace as well would derive the trace twice."""
    with psycopg.connect(configured_db) as conn:
        _seed_entities(conn)
        _insert_trace(conn, "fresh")
        conn.commit()
    cursor = driver.drain(0)

    with psycopg.connect(configured_db) as conn:
        _add_call(
            conn, "fresh", "fresh-late",
            occurred_at="2026-01-01T00:00:02Z", body="fresh-late-body",
        )
        conn.commit()
    assert _queued(configured_db) == 0

    assert driver.drain(cursor) > cursor
    late = next(
        leg for leg in retrieval.get_data_lineage("fresh").legs
        if leg.interaction_id == "fresh-late"
    )
    assert late.lineage is not None
    assert late.lineage.data_sources == ["client:client"]


def test_a_new_trace_with_seqs_behind_the_cursor_is_queued_and_derived(
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
    assert _queued(configured_db) == 1

    assert driver.drain(cursor) == cursor
    assert _client_reaches_the_child("second")
    assert _queued(configured_db) == 0


def test_a_payload_less_leg_behind_the_cursor_makes_a_complete_trace_partial(
    configured_db: str,
) -> None:
    """A graph re-derivation can add a call with no payload to a trace already
    derived as complete. Keeping that claim would assert coverage over a payload
    that was never seen (ADR-0028 D6). The stale sweep cannot see this leg:
    re-deriving cannot invent its payload, so the sweep skips it."""
    with psycopg.connect(configured_db) as conn:
        _seed_entities(conn)
        _insert_trace(conn, "first", seqs=(40, 50))
        _insert_trace(conn, "graph", seqs=(1, 2))
        conn.commit()
    cursor = driver.drain(0)
    assert cursor == 50
    assert retrieval.get_data_lineage("graph").status == "complete"

    with psycopg.connect(configured_db) as conn:
        _add_call(
            conn, "graph", "graph-late",
            occurred_at="2026-01-01T00:00:02Z", body=None, seq=3,
        )
        conn.commit()

    assert driver.drain(cursor) == cursor
    lineage = retrieval.get_data_lineage("graph")
    assert lineage.status == "partial"
    assert lineage.stopped_at_seq == 3


def test_a_leg_behind_the_cursor_before_a_partial_cutoff_gains_lineage(
    configured_db: str,
) -> None:
    """A partial trace's stored causal prefix predates the new leg, so the stale
    sweep does not count a missing row for it as unexplained. Ordered before the
    cutoff, the leg has lineage to derive."""
    with psycopg.connect(configured_db) as conn:
        _seed_entities(conn)
        _insert_trace(conn, "first", seqs=(40, 50))
        _insert_trace(conn, "graph", seqs=(1, 2))
        _add_call(
            conn, "graph", "graph-cutoff",
            occurred_at="2026-01-01T00:00:05Z", body=None, seq=3,
        )
        conn.commit()
    cursor = driver.drain(0)
    assert retrieval.get_data_lineage("graph").status == "partial"

    with psycopg.connect(configured_db) as conn:
        _add_call(
            conn, "graph", "graph-late",
            occurred_at="2026-01-01T00:00:02Z", body="graph-late-body", seq=4,
        )
        conn.commit()

    assert driver.drain(cursor) == cursor
    lineage = retrieval.get_data_lineage("graph")
    late = next(leg for leg in lineage.legs if leg.interaction_id == "graph-late")
    assert late.lineage is not None
    assert late.lineage.data_sources == ["client:client"]
    assert lineage.status == "partial"
    assert lineage.stopped_at_seq == 3


def test_a_leg_whose_seq_the_cursor_passes_before_it_commits_is_derived(
    configured_db: str,
) -> None:
    """The cursor can pass a leg's ``seq`` while the leg's writer is still open,
    by deriving committed legs of other traces that carry higher ``seq`` values.
    Graph ordinals make that ordinary. Then the leg lies above the cursor when
    written and below it when committed, and the cursor never offers it."""
    with psycopg.connect(configured_db) as conn:
        _seed_entities(conn)
        _insert_trace(conn, "graph", seqs=(1, 2))
        conn.commit()
    cursor = driver.drain(0)
    assert cursor == 2
    assert retrieval.get_data_lineage("graph").status == "complete"

    with psycopg.connect(configured_db) as conn:
        _insert_trace(conn, "other", seqs=(30, 31))
        conn.commit()

    with psycopg.connect(configured_db) as writer:
        _add_call(
            writer, "graph", "graph-late",
            occurred_at="2026-01-01T00:00:02Z", body=None, seq=20,
        )
        # The processor drains "other" while the graph leg is uncommitted.
        cursor = driver.drain(cursor)
        assert cursor == 31
        writer.commit()

    assert driver.drain(cursor) == cursor
    assert retrieval.get_data_lineage("graph").status == "partial"
