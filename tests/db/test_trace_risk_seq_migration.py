"""Tests for the trace-risk seq-cursor + NOTIFY migration (issue #175).

Migration 0023 makes ``trace_risk_records`` a cursorable, notifying stream
for a consumer of new trace risk versions (the alert generator, #104): a ``trace_risk_seq`` sequence backing a
``seq BIGINT`` column, a cursor-pagination index, and a statement-level
``dg_trace_risk_written`` NOTIFY trigger — the 0019 + 0016 shapes, one level
up the pipeline. Like the sibling migration tests, these run the real chain
against a real Postgres (testcontainers) and assert what it produces.
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


def _relkind_exists(dsn: str, name: str, relkind: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_class WHERE relname = %s AND relkind = %s",
            (name, relkind),
        ).fetchone()
    return row is not None


def _function_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (name,)).fetchone()
    return row is not None


def _trigger_row_level(dsn: str, name: str) -> bool | None:
    """``None`` when the trigger is absent; else tgtype bit 0 (row-level)."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT tgtype FROM pg_trigger WHERE tgname = %s AND NOT tgisinternal",
            (name,),
        ).fetchone()
    return None if row is None else (row[0] & 1) == 1


_INSERT_RECORD_SQL = (
    "INSERT INTO trace_risk_records ("
    "  trace_id, version, computed_at, trace_risk_level,"
    "  interaction_count, policy_event_count"
    ") VALUES (%s, %s, now(), 'none', 0, 0) RETURNING seq"
)


# --- seq column + sequence ---------------------------------------------------


def test_seq_column_and_sequence_exist(migrated_dsn: str) -> None:
    cols = _columns(migrated_dsn, "trace_risk_records")
    assert "seq" in cols
    assert cols["seq"]["data_type"] == "bigint"
    assert cols["seq"]["is_nullable"] == "NO"
    assert "trace_risk_seq" in str(cols["seq"]["column_default"])
    assert _relkind_exists(migrated_dsn, "trace_risk_seq", "S")


def test_cursor_index_on_seq_exists(migrated_dsn: str) -> None:
    assert _relkind_exists(migrated_dsn, "trace_risk_records_seq_idx", "i")


def test_insert_allocates_monotonic_seq(migrated_dsn: str) -> None:
    """Successive inserts (including new versions of the same trace) get
    strictly increasing seqs — the property the alerts cursor drain
    (`WHERE seq > cursor ORDER BY seq`) depends on."""
    with psycopg.connect(migrated_dsn) as conn:
        seqs = [
            conn.execute(_INSERT_RECORD_SQL, (tid, version)).fetchone()[0]
            for tid, version in [("trace-1", 1), ("trace-2", 1), ("trace-1", 2)]
        ]
        conn.commit()
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_no_arrival_seq_column(migrated_dsn: str) -> None:
    assert "arrival_seq" not in _columns(migrated_dsn, "trace_risk_records")


def test_existing_rows_are_numbered_in_stream_order(pg_dsn: str, monkeypatch) -> None:
    """Rows present before 0023 get seqs in stream order — the order an
    order-sensitive consumer must replay them in — and new inserts continue
    past them. The fixture separates the claimed key from its neighbours:
    t's v2 carries an earlier clock than its v1 (plain computed_at order
    would replay v2 first), a/b tie on time (trace_id breaks it), and c's
    v2 ties d's v1 (trace_id before version)."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "0022_content_kind_on_legs")
    with psycopg.connect(pg_dsn) as conn:
        # Inserted out of order, so heap order and stream order differ.
        for tid, version, when in [
            ("d", 1, "2026-01-05"), ("t", 2, "2026-01-01"), ("c", 2, "2026-01-05"),
            ("b", 1, "2026-01-03"), ("t", 1, "2026-01-02"), ("a", 1, "2026-01-03"),
            ("c", 1, "2026-01-04"), ("u", 1, "2026-01-01T12:00"),
        ]:
            conn.execute(
                "INSERT INTO trace_risk_records (trace_id, version, computed_at, "
                "trace_risk_level, interaction_count, policy_event_count) "
                "VALUES (%s, %s, %s::timestamptz, 'none', 0, 0)",
                (tid, version, when),
            )
        conn.commit()
    command.upgrade(cfg, "0023_trace_risk_seq")
    with psycopg.connect(pg_dsn) as conn:
        rows = conn.execute(
            "SELECT trace_id, version FROM trace_risk_records ORDER BY seq"
        ).fetchall()
        (new_seq,) = conn.execute(_INSERT_RECORD_SQL, ("v", 1)).fetchone()
    assert rows == [("u", 1), ("t", 1), ("t", 2), ("a", 1), ("b", 1), ("c", 1), ("c", 2), ("d", 1)]
    assert new_seq == 9


def test_sequence_restarts_above_a_stored_alerts_cursor(pg_dsn: str, monkeypatch) -> None:
    """After a downgrade below 0023 and back, the renumbered rows sit at 1..N
    while the alerts consumer's cursor may be past N; new inserts must land
    above that cursor or the consumer would not see them."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "0022_content_kind_on_legs")
    with psycopg.connect(pg_dsn) as conn:
        for version in (1, 2):
            conn.execute(
                "INSERT INTO trace_risk_records (trace_id, version, computed_at, "
                "trace_risk_level, interaction_count, policy_event_count) "
                "VALUES ('t', %s, now(), 'none', 0, 0)",
                (version,),
            )
        conn.execute(
            "INSERT INTO processor_state (processor_name, last_processed_seq) VALUES ('risk_alerts', 50)"
        )
        conn.commit()
    command.upgrade(cfg, "0023_trace_risk_seq")
    with psycopg.connect(pg_dsn) as conn:
        (new_seq,) = conn.execute(_INSERT_RECORD_SQL, ("v", 1)).fetchone()
    assert new_seq == 51


# --- notify trigger: dg_trace_risk_written -----------------------------------


def test_notify_function_and_statement_level_trigger_exist(migrated_dsn: str) -> None:
    assert _function_exists(migrated_dsn, "dg_notify_trace_risk")
    assert _trigger_row_level(migrated_dsn, "dg_trace_risk_notify") is False


def test_insert_fires_notification_with_empty_payload(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute("LISTEN dg_trace_risk_written")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            writer.execute(_INSERT_RECORD_SQL, ("n0", 1))
        notifies = list(listener.notifies(timeout=5.0, stop_after=1))
    assert notifies, "expected a notification after a trace_risk_records insert"
    assert notifies[0].channel == "dg_trace_risk_written"
    assert notifies[0].payload == ""


def test_multi_row_insert_notifies_once(migrated_dsn: str) -> None:
    """Statement-level: one INSERT of three rows is one notification."""
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute("LISTEN dg_trace_risk_written")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            writer.execute(
                "INSERT INTO trace_risk_records (trace_id, version, computed_at, "
                "trace_risk_level, interaction_count, policy_event_count) VALUES "
                "('m', 1, now(), 'none', 0, 0), ('m', 2, now(), 'none', 0, 0), "
                "('n', 1, now(), 'none', 0, 0)"
            )
        got = list(listener.notifies(timeout=1.5, stop_after=2))
    assert len(got) == 1


def test_interaction_risk_insert_does_not_notify_the_trace_channel(
    migrated_dsn: str,
) -> None:
    """The two DAS channels stay distinct: an interaction-level write wakes
    the trace trigger (0016), never the alerts consumer."""
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute("LISTEN dg_trace_risk_written")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            writer.execute(
                "INSERT INTO interaction_risk_records (interaction_id, trace_id, "
                "caller_entity_id, callee_entity_id, version, computed_at, "
                "risk_level, policy_event_count) "
                "VALUES ('i', 't', 'a', 'b', 1, now(), 'none', 0)"
            )
        got = list(listener.notifies(timeout=1.5, stop_after=1))
    assert got == [], f"interaction_risk_records insert must not notify dg_trace_risk_written; got {got!r}"


# --- migration chain ---------------------------------------------------------


def test_0023_is_applied_in_the_chain(migrated_dsn: str) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    walked = {rev.revision for rev in script.walk_revisions()}
    assert "0023_trace_risk_seq" in walked


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade(0022) -> upgrade cleanly removes and re-adds the
    seq column, its sequence, the index, and the trigger/function — without
    disturbing 0019's interaction_risk_records.seq below it."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    assert "seq" in _columns(pg_dsn, "trace_risk_records")

    command.downgrade(cfg, "0022_content_kind_on_legs")
    assert "seq" not in _columns(pg_dsn, "trace_risk_records")
    assert not _relkind_exists(pg_dsn, "trace_risk_seq", "S")
    assert not _relkind_exists(pg_dsn, "trace_risk_records_seq_idx", "i")
    assert not _function_exists(pg_dsn, "dg_notify_trace_risk")
    assert _trigger_row_level(pg_dsn, "dg_trace_risk_notify") is None
    assert "seq" in _columns(pg_dsn, "interaction_risk_records"), (
        "downgrading 0023 must not disturb 0019"
    )

    command.upgrade(cfg, "head")
    assert "seq" in _columns(pg_dsn, "trace_risk_records")
    assert _trigger_row_level(pg_dsn, "dg_trace_risk_notify") is False
