"""Tests for the interaction-risk seq-cursor migration (issue #164).

Migration 0019 makes ``interaction_risk_records`` a cursorable stream for the
Trace Risk Processor (#102): an ``interaction_risk_seq`` sequence backing a
``seq BIGINT`` column plus a cursor-pagination index, mirroring the payloads
stream (migration 0007). The ``dg_interaction_risk_written`` NOTIFY trigger
this stream wakes on already exists (0016) and is asserted there; these tests
own only what 0019 adds.

Like the sibling migration tests, these run the real migration chain against a
real Postgres (testcontainers) and assert what it produces.
"""

from __future__ import annotations

import psycopg


# --- helpers -----------------------------------------------------------------


def _columns(dsn: str, table: str) -> dict[str, dict[str, object]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
    return {
        name: {"data_type": dt, "is_nullable": nn, "column_default": default}
        for name, dt, nn, default in rows
    }


def _index_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_class WHERE relname = %s AND relkind = 'i'",
            (name,),
        ).fetchone()
    return row is not None


def _sequence_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_class WHERE relname = %s AND relkind = 'S'",
            (name,),
        ).fetchone()
    return row is not None


_INSERT_RECORD_SQL = (
    "INSERT INTO interaction_risk_records ("
    "  interaction_id, trace_id, caller_entity_id, callee_entity_id,"
    "  version, computed_at, risk_level, policy_event_count"
    ") VALUES (%s, %s, 'caller', 'callee', %s, now(), 'none', 0) "
    "RETURNING seq"
)


# --- seq column + sequence ---------------------------------------------------


def test_seq_column_and_sequence_exist(migrated_dsn: str) -> None:
    cols = _columns(migrated_dsn, "interaction_risk_records")
    assert "seq" in cols
    assert cols["seq"]["data_type"] == "bigint"
    assert cols["seq"]["is_nullable"] == "NO"
    assert "interaction_risk_seq" in str(cols["seq"]["column_default"])
    assert _sequence_exists(migrated_dsn, "interaction_risk_seq")


def test_cursor_index_on_seq_exists(migrated_dsn: str) -> None:
    assert _index_exists(migrated_dsn, "interaction_risk_records_seq_idx")


def test_insert_allocates_monotonic_seq(migrated_dsn: str) -> None:
    """Successive inserts (including new versions of the same interaction)
    get strictly increasing seqs — the property the trace-trigger cursor
    drain (`WHERE seq > cursor ORDER BY seq`) depends on."""
    with psycopg.connect(migrated_dsn) as conn:
        seqs = [
            conn.execute(_INSERT_RECORD_SQL, (iid, tid, version)).fetchone()[0]
            for iid, tid, version in [
                ("int-a", "trace-1", 1),
                ("int-b", "trace-1", 1),
                ("int-a", "trace-1", 2),
            ]
        ]
        conn.commit()
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_no_arrival_seq_column(migrated_dsn: str) -> None:
    """Insert-only, immutably-versioned table (0016's invariant): seq never
    advances after insert, so a spans-style arrival_seq split would be two
    names for one value — deliberately absent, like 0007."""
    assert "arrival_seq" not in _columns(migrated_dsn, "interaction_risk_records")


# --- migration chain ---------------------------------------------------------


def test_0019_is_applied_in_the_chain(migrated_dsn: str) -> None:
    """A fresh migrate applies this revision (chain-reachability; the head
    assertion lives solely in ``test_latest_migration.py``)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    walked = {rev.revision for rev in script.walk_revisions()}
    assert "0019_interaction_risk_seq" in walked


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade(0018) -> upgrade cleanly removes and re-adds the
    seq column, its sequence, and the index — without disturbing the 0016/0017
    tables below it.

    The downgrade target is this revision's direct parent
    (``0018_trace_aggregation_modes``, since the 0018 -> 0019 renumber), so
    exactly one revision is unwound and the assertions below isolate its
    effects."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    assert "seq" in _columns(pg_dsn, "interaction_risk_records")

    command.downgrade(cfg, "0018_trace_aggregation_modes")
    assert "seq" not in _columns(pg_dsn, "interaction_risk_records")
    assert not _sequence_exists(pg_dsn, "interaction_risk_seq")
    assert not _index_exists(pg_dsn, "interaction_risk_records_seq_idx")
    assert _columns(pg_dsn, "interaction_risk_records"), (
        "downgrading 0019 must not drop the 0016 table itself"
    )
    assert _columns(pg_dsn, "interaction_policy_decisions"), (
        "downgrading 0019 must not disturb 0017"
    )

    command.upgrade(cfg, "head")
    assert "seq" in _columns(pg_dsn, "interaction_risk_records")
    assert _index_exists(pg_dsn, "interaction_risk_records_seq_idx")
