"""Migration 0026's dirty-trace queue: what fills it, and how it is read.

New legs reach the data-lineage processor through its ingestion cursor
(``seq > cursor``), so the queue is for what the cursor cannot see: an in-place
correction or a deletion, which leaves no new ``seq``. Queuing inserts as well
would derive every fresh trace twice and run a row-level plpgsql function on
every write. The triggers therefore fire on UPDATE and DELETE only, and a
correction to an interaction that has no legs yet queues nothing.
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


@pytest.mark.parametrize("table", ["interactions", "interaction_legs"])
def test_dirty_triggers_fire_on_update_and_delete_only(
    migrated_dsn: str, table: str,
) -> None:
    assert _dirty_trigger_events(migrated_dsn, table) == {"UPDATE", "DELETE"}


# --- what does not queue -----------------------------------------------------


def test_inserting_a_fresh_trace_leaves_the_queue_empty(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        _insert_trace(conn)
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
