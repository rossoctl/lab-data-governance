"""Migration 0026's dirty-trace queue: what fills it, and how it is read.

New legs normally reach the data-lineage processor through its ingestion cursor
(``seq > cursor``), so the queue is for what the cursor cannot see: an in-place
correction or a deletion, which leaves no new ``seq``, and a new leg whose
supplied ``seq`` the cursor may already have passed. Queuing every insert would
derive every fresh trace twice. Interaction triggers therefore fire on UPDATE
and DELETE only; legs add a statement-level INSERT trigger that queues only legs
at or below the cursor or another leg's ``seq``. A correction to an interaction
that has no legs yet queues nothing.
"""

from __future__ import annotations

import psycopg
import pytest
from alembic import command

from data_governance.db.migrate import _alembic_config

TRACE = "trace-a"
OTHER = "trace-b"
QUEUE = "data_lineage_dirty_traces"
CHANNEL = "dg_legs_inserted"


# --- helpers -----------------------------------------------------------------


def _queued(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        return {r[0] for r in conn.execute(f"SELECT trace_id FROM {QUEUE}").fetchall()}


def _generation(dsn: str, trace_id: str) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            f"SELECT generation FROM {QUEUE} WHERE trace_id = %s", (trace_id,)
        ).fetchone()
    assert row is not None, trace_id
    return int(row[0])


def _lineage_keys(dsn: str) -> set[tuple[str, str]]:
    with psycopg.connect(dsn) as conn:
        return {
            (r[0], r[1])
            for r in conn.execute(
                "SELECT interaction_id, leg_type::text FROM lineage_metadata"
            ).fetchall()
        }


def _insert_trace(conn: psycopg.Connection) -> None:
    """A parent call and one child call, each with both legs."""
    conn.execute(
        "INSERT INTO interactions (id, trace_id, parent_interaction_id, "
        "caller_entity_id, callee_entity_id, summary) VALUES "
        "('parent', %s, NULL, 'client', 'agent', 'call'), "
        "('child', %s, 'parent', 'agent', 'tool', 'call')",
        (TRACE, TRACE),
    )
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error) VALUES "
        "('parent', 'request', '2026-01-01T00:00:00Z', 'p-req', false), "
        "('child', 'request', '2026-01-01T00:00:01Z', 'c-req', false), "
        "('child', 'response', '2026-01-01T00:00:02Z', 'c-resp', false), "
        "('parent', 'response', '2026-01-01T00:00:03Z', 'p-resp', false)"
    )


def _seed(dsn: str, *, lineage: bool = False) -> None:
    """Write the trace, optionally its derived lineage, then empty the queue so
    each test sees only what its own correction queues."""
    with psycopg.connect(dsn) as conn:
        _insert_trace(conn)
        if lineage:
            for iid, leg_type, seq in conn.execute(
                "SELECT interaction_id, leg_type, seq FROM interaction_legs"
            ).fetchall():
                conn.execute(
                    "INSERT INTO lineage_metadata (interaction_id, leg_type, "
                    "data_sources, source_transformations, entities, payload_hash, seq) "
                    "VALUES (%s, %s, '{}', '{}'::jsonb, '{}', NULL, %s)",
                    (iid, leg_type, seq),
                )
        conn.execute(f"DELETE FROM {QUEUE}")
        conn.commit()


def _dirty_trigger_events(dsn: str, table: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT event_manipulation FROM information_schema.triggers "
            "WHERE trigger_schema = 'public' AND event_object_table = %s "
            "AND trigger_name LIKE 'dg_lineage_%%'",
            (table,),
        ).fetchall()
    return {r[0] for r in rows}


def _indexes(dsn: str, table: str) -> dict[str, str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = %s",
            (table,),
        ).fetchall()
    return dict(rows)


# --- shape -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "events"),
    [
        ("interactions", {"UPDATE", "DELETE"}),
        ("interaction_legs", {"INSERT", "UPDATE", "DELETE"}),
    ],
)
def test_dirty_trigger_events(migrated_dsn: str, table: str, events: set[str]) -> None:
    assert _dirty_trigger_events(migrated_dsn, table) == events


def test_the_queue_has_an_index_for_its_generation_order(migrated_dsn: str) -> None:
    """The processor reads ``ORDER BY generation LIMIT n``. After the backfill
    queues every trace, that read must not scan and sort the whole queue."""
    idx = _indexes(migrated_dsn, QUEUE)
    assert any(
        d.replace(" ", "").endswith("(generation)")
        for name, d in idx.items()
        if name != f"{QUEUE}_pkey"
    ), sorted(idx.items())


def test_the_processor_queue_read_can_use_the_generation_index(
    migrated_dsn: str,
) -> None:
    """The planner can answer the driver's queue read from the index without a
    sort. A small test table would be scanned anyway, so sequential scans and
    explicit sorts are disabled for the EXPLAIN."""
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute("SET enable_seqscan = off")
        conn.execute("SET enable_sort = off")
        plan = "\n".join(
            r[0]
            for r in conn.execute(
                f"EXPLAIN SELECT trace_id, generation FROM {QUEUE} "
                "ORDER BY generation LIMIT 500"
            ).fetchall()
        )
    assert f"Index Scan using {QUEUE}_generation_idx" in plan, plan
    assert "Sort" not in plan, plan


# --- what does not queue -----------------------------------------------------


def test_inserting_a_fresh_trace_leaves_the_queue_empty(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        _insert_trace(conn)
        conn.commit()
    assert _queued(migrated_dsn) == set()


def test_a_next_seq_leg_past_the_cursor_is_not_queued(migrated_dsn: str) -> None:
    """A leg taking ``nextval`` lies past the cursor and every stored leg, so the
    cursor offers it, also when its trace is already derived."""
    _seed(migrated_dsn)
    with psycopg.connect(migrated_dsn) as conn:
        _set_cursor(conn, _max_seq(conn))
        conn.execute(
            "INSERT INTO interactions (id, trace_id, parent_interaction_id, "
            "caller_entity_id, callee_entity_id, summary) "
            "VALUES ('late', %s, 'parent', 'agent', 'tool', 'call')",
            (TRACE,),
        )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error) VALUES ('late', 'request', now(), 'l-req', false)"
        )
        conn.commit()
    assert _queued(migrated_dsn) == set()


def test_linking_a_parent_before_the_child_has_legs_is_not_queued(
    migrated_dsn: str,
) -> None:
    """The sidecar writer inserts each interaction with no parent and links the
    parent in a second pass, before the legs exist. An interaction without legs
    has no lineage and constrains no leg's order, so nothing is queued, and its
    legs then arrive through the cursor."""
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO interactions (id, trace_id, parent_interaction_id, "
            "caller_entity_id, callee_entity_id, summary) VALUES "
            "('parent', %s, NULL, 'client', 'agent', 'call'), "
            "('child', %s, NULL, 'agent', 'tool', 'call')",
            (TRACE, TRACE),
        )
        conn.execute(
            "UPDATE interactions SET parent_interaction_id = 'parent' WHERE id = 'child'"
        )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error) VALUES "
            "('parent', 'request', now(), 'p-req', false), "
            "('child', 'request', now(), 'c-req', false)"
        )
        conn.commit()
    assert _queued(migrated_dsn) == set()


def test_an_unchanged_upsert_is_not_queued(migrated_dsn: str) -> None:
    """A re-derivation rewrites every row of its trace through
    ``ON CONFLICT DO UPDATE``. Rows whose causal inputs are unchanged queue
    nothing."""
    _seed(migrated_dsn)
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO interactions (id, trace_id, parent_interaction_id, "
            "caller_entity_id, callee_entity_id, summary) "
            "VALUES ('child', %s, 'parent', 'agent', 'tool', 'renamed') "
            "ON CONFLICT (id) DO UPDATE SET "
            "parent_interaction_id = EXCLUDED.parent_interaction_id, "
            "caller_entity_id = EXCLUDED.caller_entity_id, "
            "callee_entity_id = EXCLUDED.callee_entity_id, "
            "summary = EXCLUDED.summary",
            (TRACE,),
        )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error) "
            "VALUES ('child', 'request', '2026-01-01T00:00:01Z', 'c-req', true) "
            "ON CONFLICT (interaction_id, leg_type) DO UPDATE SET "
            "occurred_at = EXCLUDED.occurred_at, "
            "payload_hash = EXCLUDED.payload_hash, error = EXCLUDED.error"
        )
        conn.commit()
    assert _queued(migrated_dsn) == set()


# --- inserts the cursor cannot reach queue -----------------------------------


def _set_cursor(conn: psycopg.Connection, seq: int) -> None:
    conn.execute(
        "INSERT INTO processor_state (processor_name, last_processed_seq) "
        "VALUES ('data_lineage', %s) ON CONFLICT (processor_name) "
        "DO UPDATE SET last_processed_seq = EXCLUDED.last_processed_seq",
        (seq,),
    )


def _max_seq(conn: psycopg.Connection) -> int:
    return int(conn.execute("SELECT max(seq) FROM interaction_legs").fetchone()[0])


def _insert_graph_leg(conn: psycopg.Connection, seq: int) -> None:
    """A leg with a supplied ``seq``, as the graph writer stores its edge
    ordinal, on a new call in the seeded trace."""
    conn.execute(
        "INSERT INTO interactions (id, trace_id, parent_interaction_id, "
        "caller_entity_id, callee_entity_id, summary) "
        "VALUES ('late', %s, 'parent', 'agent', 'tool', 'call')",
        (TRACE,),
    )
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error, seq) VALUES ('late', 'request', now(), NULL, false, %s)",
        (seq,),
    )


@pytest.mark.parametrize("offset", [0, -1], ids=["at", "below"])
def test_a_leg_inserted_at_or_below_the_cursor_queues_its_trace(
    migrated_dsn: str, offset: int,
) -> None:
    """The cursor arm reads ``seq > cursor`` and will never offer this leg."""
    _seed(migrated_dsn)
    with psycopg.connect(migrated_dsn) as conn:
        cursor = _max_seq(conn) + 100
        _set_cursor(conn, cursor)
        _insert_graph_leg(conn, cursor + offset)
        conn.commit()
    assert _queued(migrated_dsn) == {TRACE}


def test_a_leg_inserted_below_another_legs_seq_queues_its_trace(
    migrated_dsn: str,
) -> None:
    """The cursor can pass a ``seq`` before its leg commits, by deriving a
    committed leg that carries a higher one."""
    _seed(migrated_dsn)
    with psycopg.connect(migrated_dsn) as conn:
        _set_cursor(conn, 0)
        _insert_graph_leg(conn, _max_seq(conn) - 1)
        conn.commit()
    assert _queued(migrated_dsn) == {TRACE}


def test_re_deriving_a_stored_leg_behind_the_cursor_is_not_queued(
    migrated_dsn: str,
) -> None:
    """A graph re-derivation upserts every leg of its trace with the same
    supplied ``seq``. A leg that already exists takes the conflict path, which
    inserts no row, so the insert trigger queues nothing."""
    _seed(migrated_dsn)
    with psycopg.connect(migrated_dsn) as conn:
        _set_cursor(conn, _max_seq(conn) + 100)
        (seq,) = conn.execute(
            "SELECT seq FROM interaction_legs "
            "WHERE interaction_id = 'child' AND leg_type = 'request'"
        ).fetchone()
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error, seq) "
            "VALUES ('child', 'request', '2026-01-01T00:00:01Z', 'c-req', true, %s) "
            "ON CONFLICT (interaction_id, leg_type) DO UPDATE SET "
            "occurred_at = EXCLUDED.occurred_at, "
            "payload_hash = EXCLUDED.payload_hash, error = EXCLUDED.error",
            (seq,),
        )
        conn.commit()
    assert _queued(migrated_dsn) == set()


def test_a_leg_inserted_past_the_cursor_and_every_leg_is_not_queued(
    migrated_dsn: str,
) -> None:
    _seed(migrated_dsn)
    with psycopg.connect(migrated_dsn) as conn:
        _set_cursor(conn, 0)
        _insert_graph_leg(conn, _max_seq(conn) + 10)
        conn.commit()
    assert _queued(migrated_dsn) == set()


# --- corrections queue -------------------------------------------------------


@pytest.mark.parametrize(
    "assignment",
    [
        "parent_interaction_id = NULL",
        "caller_entity_id = 'other-agent'",
        "callee_entity_id = 'other-tool'",
    ],
)
def test_an_interaction_correction_queues_its_trace(
    migrated_dsn: str, assignment: str,
) -> None:
    _seed(migrated_dsn)
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(f"UPDATE interactions SET {assignment} WHERE id = 'child'")
        conn.commit()
    assert _queued(migrated_dsn) == {TRACE}


def test_moving_an_interaction_to_another_trace_queues_both(migrated_dsn: str) -> None:
    _seed(migrated_dsn)
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute("UPDATE interactions SET trace_id = %s WHERE id = 'child'", (OTHER,))
        conn.commit()
    assert _queued(migrated_dsn) == {TRACE, OTHER}


@pytest.mark.parametrize(
    "assignment",
    [
        "occurred_at = '2026-01-01T00:00:09Z'",
        "payload_hash = 'c-req-v2'",
        "payload_hash = NULL",
        "seq = nextval('interaction_legs_seq')",
    ],
)
def test_a_leg_correction_queues_its_trace(migrated_dsn: str, assignment: str) -> None:
    _seed(migrated_dsn)
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(
            f"UPDATE interaction_legs SET {assignment} "
            "WHERE interaction_id = 'child' AND leg_type = 'request'"
        )
        conn.commit()
    assert _queued(migrated_dsn) == {TRACE}


def test_a_conflict_path_upsert_queues_the_correction(migrated_dsn: str) -> None:
    """Both writers correct rows through ``INSERT ... ON CONFLICT DO UPDATE``.
    Its conflict path fires the AFTER UPDATE row triggers."""
    _seed(migrated_dsn)
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO interactions (id, trace_id, parent_interaction_id, "
            "caller_entity_id, callee_entity_id, summary) "
            "VALUES ('child', %s, 'parent', 'agent', 'canonical-tool', 'call') "
            "ON CONFLICT (id) DO UPDATE SET "
            "callee_entity_id = EXCLUDED.callee_entity_id",
            (TRACE,),
        )
        conn.commit()
    assert _queued(migrated_dsn) == {TRACE}

    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(f"DELETE FROM {QUEUE}")
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error) "
            "VALUES ('child', 'response', '2026-01-01T00:00:05Z', 'c-resp', false) "
            "ON CONFLICT (interaction_id, leg_type) DO UPDATE SET "
            "occurred_at = EXCLUDED.occurred_at"
        )
        conn.commit()
    assert _queued(migrated_dsn) == {TRACE}


def test_a_later_correction_replaces_the_generation(migrated_dsn: str) -> None:
    _seed(migrated_dsn)
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute("UPDATE interactions SET parent_interaction_id = NULL WHERE id = 'child'")
        conn.commit()
    first = _generation(migrated_dsn, TRACE)
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(
            "UPDATE interaction_legs SET payload_hash = 'c-req-v2' "
            "WHERE interaction_id = 'child' AND leg_type = 'request'"
        )
        conn.commit()
    assert _generation(migrated_dsn, TRACE) > first


# --- deletions ---------------------------------------------------------------


def test_deleting_a_leg_queues_its_trace_and_drops_its_lineage(migrated_dsn: str) -> None:
    _seed(migrated_dsn, lineage=True)
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(
            "DELETE FROM interaction_legs "
            "WHERE interaction_id = 'child' AND leg_type = 'response'"
        )
        conn.commit()
    assert _queued(migrated_dsn) == {TRACE}
    assert ("child", "response") not in _lineage_keys(migrated_dsn)
    assert ("child", "request") in _lineage_keys(migrated_dsn)


def test_deleting_an_interaction_queues_its_trace_and_drops_its_lineage(
    migrated_dsn: str,
) -> None:
    """Without its interaction a leg leaves the trace, even if the leg row stays."""
    _seed(migrated_dsn, lineage=True)
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute("DELETE FROM interactions WHERE id = 'child'")
        conn.commit()
    assert _queued(migrated_dsn) == {TRACE}
    assert _lineage_keys(migrated_dsn) == {("parent", "request"), ("parent", "response")}


def test_moving_a_leg_queues_both_traces_and_drops_the_old_keys_lineage(
    migrated_dsn: str,
) -> None:
    _seed(migrated_dsn, lineage=True)
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) VALUES ('elsewhere', %s, 'a', 'b', 'call')",
            (OTHER,),
        )
        conn.execute(
            "UPDATE interaction_legs SET interaction_id = 'elsewhere' "
            "WHERE interaction_id = 'child' AND leg_type = 'response'"
        )
        conn.commit()
    assert _queued(migrated_dsn) == {TRACE, OTHER}
    assert ("child", "response") not in _lineage_keys(migrated_dsn)


# --- wake --------------------------------------------------------------------


def _wakes_after(dsn: str, statement: str) -> bool:
    with psycopg.connect(dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        with psycopg.connect(dsn, autocommit=True) as writer:
            writer.execute(statement)
        return bool(list(listener.notifies(timeout=5.0, stop_after=1)))


@pytest.mark.parametrize(
    "statement",
    [
        # Migration 0012 notifies on leg INSERT/UPDATE only. Corrections to an
        # interaction and deletions are announced by the dirty triggers.
        "UPDATE interactions SET parent_interaction_id = NULL WHERE id = 'child'",
        "DELETE FROM interaction_legs WHERE interaction_id = 'child'",
        "DELETE FROM interactions WHERE id = 'child'",
    ],
)
def test_a_queued_correction_wakes_the_processor(migrated_dsn: str, statement: str) -> None:
    _seed(migrated_dsn)
    assert _wakes_after(migrated_dsn, statement)


# --- downgrade round-trip ----------------------------------------------------


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0022_content_kind_on_legs")
    with psycopg.connect(pg_dsn) as conn:
        assert conn.execute("SELECT to_regclass(%s)", (QUEUE,)).fetchone() == (None,)
        assert conn.execute(
            "SELECT count(*) FROM pg_proc WHERE proname LIKE 'dg\\_%%lineage%%'"
        ).fetchone() == (0,)
    assert _dirty_trigger_events(pg_dsn, "interactions") == set()
    assert _dirty_trigger_events(pg_dsn, "interaction_legs") == set()

    command.upgrade(cfg, "head")
    assert _dirty_trigger_events(pg_dsn, "interactions") == {"UPDATE", "DELETE"}
    assert any("(generation)" in d for d in _indexes(pg_dsn, QUEUE).values())
