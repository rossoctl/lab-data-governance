"""Tests for the DAS backbone migration (issue #98).

Migration 0010 adds the three DAS-owned derived tables per PRD-SENTRY-001 v8
§6.2-6.4 — ``interaction_risk_records``, ``trace_risk_records``, ``alerts`` —
plus the one NOTIFY trigger this issue owns: ``dg_interaction_risk_written``
(FR-DAS-019), fired on ``interaction_risk_records`` inserts so the (not yet
implemented) Trace Risk Processor can wake on new/recomputed interaction risk.

The other two NOTIFY channels described by the PRD (``dg_interaction_legs_inserted``,
``dg_classifications_inserted``) are deliberately NOT added by this migration —
deferred to a later issue, since ``payload_classifications`` has no ordering
column to cursor over yet (open design question, not resolved here).

All three tables are FK-free (0004 convention: derived tables re-derive
idempotently, never enforced by a hard FK) and use ``UUID DEFAULT
gen_random_uuid()`` primary keys per the PRD schema — a deliberate deviation
from this repo's TEXT-id convention (spans/interactions/entities), since the
PRD specifies UUID explicitly and Postgres 16 provides ``gen_random_uuid()``
natively (no ``pgcrypto`` extension needed).

Like the sibling migration tests, these run the real migration chain against a
real Postgres (testcontainers) and assert what it produces.
"""

from __future__ import annotations

import psycopg


# --- helpers -----------------------------------------------------------------


def _columns(dsn: str, table: str) -> dict[str, dict[str, object]]:
    """Map column-name -> {data_type, is_nullable, column_default,
    numeric_precision, numeric_scale}."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default, "
            "numeric_precision, numeric_scale "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
    return {
        name: {
            "data_type": dt,
            "is_nullable": nn,
            "column_default": default,
            "numeric_precision": precision,
            "numeric_scale": scale,
        }
        for name, dt, nn, default, precision, scale in rows
    }


def _primary_key_columns(dsn: str, table: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT a.attname "
            "FROM pg_index i "
            "JOIN pg_attribute a "
            "  ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = %s::regclass AND i.indisprimary",
            (table,),
        ).fetchall()
    return sorted(r[0] for r in rows)


def _index_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_class WHERE relname = %s AND relkind = 'i'",
            (name,),
        ).fetchone()
    return row is not None


def _index_is_unique(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT ix.indisunique FROM pg_index ix "
            "JOIN pg_class c ON c.oid = ix.indexrelid "
            "WHERE c.relname = %s",
            (name,),
        ).fetchone()
    assert row is not None, name
    return bool(row[0])


def _index_access_method(dsn: str, name: str) -> str:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT am.amname FROM pg_class c "
            "JOIN pg_am am ON am.oid = c.relam "
            "WHERE c.relname = %s",
            (name,),
        ).fetchone()
    assert row is not None, name
    return row[0]


def _function_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_proc WHERE proname = %s", (name,)
        ).fetchone()
    return row is not None


def _trigger_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_trigger WHERE tgname = %s AND NOT tgisinternal",
            (name,),
        ).fetchone()
    return row is not None


def _trigger_is_row_level(dsn: str, name: str) -> bool:
    """tgtype bit 0 (value 1) set => row-level; clear => statement-level."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT tgtype FROM pg_trigger WHERE tgname = %s AND NOT tgisinternal",
            (name,),
        ).fetchone()
    assert row is not None, name
    return (row[0] & 1) == 1


def _insert_min_interaction_risk_record(
    conn: psycopg.Connection, *, interaction_id: str, version: int = 1
) -> None:
    conn.execute(
        """
        INSERT INTO interaction_risk_records (
            interaction_id, trace_id, caller_entity_id, callee_entity_id,
            version, computed_at, risk_level, policy_event_count
        ) VALUES (%s, 't1', 'agent:a', 'agent:b', %s, now(), 'low', 0)
        """,
        (interaction_id, version),
    )


def _drain_notifies(
    listener: psycopg.Connection, *, timeout: float
) -> list[psycopg.Notify]:
    """Collect notifications arriving within ``timeout`` seconds.

    Unlike ``notifies(stop_after=1)``, this does not early-exit on the first
    notification, so it can also assert that *zero* arrive.
    """
    got: list[psycopg.Notify] = []
    for n in listener.notifies(timeout=timeout):
        got.append(n)
    return got


# --- structure: interaction_risk_records --------------------------------------


def test_interaction_risk_records_table_exists_with_expected_columns(
    migrated_dsn: str,
) -> None:
    cols = _columns(migrated_dsn, "interaction_risk_records")
    assert cols, "interaction_risk_records table must exist"
    for name in (
        "interaction_risk_id",
        "interaction_id",
        "trace_id",
        "parent_interaction_id",
        "caller_entity_id",
        "callee_entity_id",
        "version",
        "computed_at",
        "risk_level",
        "enforcement_type",
        "policy_event_count",
        "triggered_rule_ids",
        "legs_evidenced",
        "classification_summary",
        "opa_policy_versions_used",
        "overall_confidence",
    ):
        assert name in cols, f"missing column {name!r}"
    assert cols["interaction_risk_id"]["data_type"] == "uuid"
    assert cols["interaction_risk_id"]["column_default"] == "gen_random_uuid()"
    assert cols["classification_summary"]["data_type"] == "jsonb"
    assert cols["triggered_rule_ids"]["data_type"] == "ARRAY"
    assert cols["legs_evidenced"]["data_type"] == "ARRAY"
    assert cols["opa_policy_versions_used"]["data_type"] == "ARRAY"
    # NUMERIC(4,3), not DOUBLE PRECISION/FLOAT — overall_confidence is a
    # bounded [0, 1] probability expressed to three decimal places; fixed
    # precision/scale avoids float rounding drift and rejects out-of-range
    # values at the DB layer.
    assert cols["overall_confidence"]["data_type"] == "numeric"
    assert cols["overall_confidence"]["numeric_precision"] == 4
    assert cols["overall_confidence"]["numeric_scale"] == 3


def test_interaction_risk_records_primary_key(migrated_dsn: str) -> None:
    assert _primary_key_columns(migrated_dsn, "interaction_risk_records") == [
        "interaction_risk_id"
    ]


def test_interaction_risk_records_indexes_exist(migrated_dsn: str) -> None:
    assert _index_exists(migrated_dsn, "interaction_risk_records_version_uq")
    assert _index_is_unique(migrated_dsn, "interaction_risk_records_version_uq")
    assert _index_exists(migrated_dsn, "interaction_risk_records_latest_idx")
    assert _index_exists(migrated_dsn, "interaction_risk_records_trace_idx")
    assert _index_exists(migrated_dsn, "interaction_risk_records_risk_level_idx")
    assert _index_exists(migrated_dsn, "interaction_risk_records_rule_ids_gin")
    assert (
        _index_access_method(migrated_dsn, "interaction_risk_records_rule_ids_gin")
        == "gin"
    )


def test_interaction_id_version_unique_constraint_rejects_duplicate(
    migrated_dsn: str,
) -> None:
    """Corner case: the same (interaction_id, version) pair cannot be written
    twice — that would mean two different risk computations claim to be the
    same immutable version."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        _insert_min_interaction_risk_record(conn, interaction_id="ix1", version=1)
        with conn.transaction(), __import__("pytest").raises(
            psycopg.errors.UniqueViolation
        ):
            _insert_min_interaction_risk_record(conn, interaction_id="ix1", version=1)


def test_overall_confidence_rejects_out_of_range_value(migrated_dsn: str) -> None:
    """NUMERIC(4,3) caps overall_confidence at 4 total digits / 3 after the
    decimal point — a value like 12.345 (5 significant digits) must be
    rejected at the DB layer, not silently truncated."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        with conn.transaction(), __import__("pytest").raises(
            psycopg.errors.NumericValueOutOfRange
        ):
            conn.execute(
                "INSERT INTO interaction_risk_records ("
                "interaction_id, trace_id, caller_entity_id, callee_entity_id, "
                "version, computed_at, risk_level, policy_event_count, "
                "overall_confidence"
                ") VALUES (%s, 't1', 'agent:a', 'agent:b', 1, now(), 'low', 0, 12.345)",
                ("ix-oor",),
            )


def test_interaction_id_allows_multiple_versions(migrated_dsn: str) -> None:
    """The same interaction_id at different versions is the normal recompute
    case and must be allowed (each version is an immutable snapshot)."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        _insert_min_interaction_risk_record(conn, interaction_id="ix2", version=1)
        _insert_min_interaction_risk_record(conn, interaction_id="ix2", version=2)
        (count,) = conn.execute(
            "SELECT count(*) FROM interaction_risk_records WHERE interaction_id = %s",
            ("ix2",),
        ).fetchone()
    assert count == 2


# --- structure: trace_risk_records ---------------------------------------------


def test_trace_risk_records_table_exists_with_expected_columns(
    migrated_dsn: str,
) -> None:
    cols = _columns(migrated_dsn, "trace_risk_records")
    assert cols, "trace_risk_records table must exist"
    for name in (
        "trace_risk_id",
        "trace_id",
        "version",
        "computed_at",
        "trace_risk_level",
        "trace_enforcement_type",
        "risk_compounding_mode",
        "interaction_count",
        "policy_event_count",
        "all_entity_ids",
        "triggered_rule_ids",
        "overall_confidence",
        "contributing_interaction_risk_ids",
    ):
        assert name in cols, f"missing column {name!r}"
    assert cols["trace_risk_id"]["data_type"] == "uuid"
    assert cols["contributing_interaction_risk_ids"]["data_type"] == "ARRAY"
    assert cols["overall_confidence"]["data_type"] == "numeric"
    assert cols["overall_confidence"]["numeric_precision"] == 4
    assert cols["overall_confidence"]["numeric_scale"] == 3


def test_trace_risk_records_primary_key(migrated_dsn: str) -> None:
    assert _primary_key_columns(migrated_dsn, "trace_risk_records") == [
        "trace_risk_id"
    ]


def test_trace_risk_records_indexes_exist(migrated_dsn: str) -> None:
    assert _index_exists(migrated_dsn, "trace_risk_records_version_uq")
    assert _index_is_unique(migrated_dsn, "trace_risk_records_version_uq")
    assert _index_exists(migrated_dsn, "trace_risk_records_latest_idx")


def test_trace_id_version_unique_constraint_rejects_duplicate(
    migrated_dsn: str,
) -> None:
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO trace_risk_records (trace_id, version, computed_at, "
            "trace_risk_level, interaction_count, policy_event_count) "
            "VALUES ('t1', 1, now(), 'low', 0, 0)"
        )
        with conn.transaction(), __import__("pytest").raises(
            psycopg.errors.UniqueViolation
        ):
            conn.execute(
                "INSERT INTO trace_risk_records (trace_id, version, computed_at, "
                "trace_risk_level, interaction_count, policy_event_count) "
                "VALUES ('t1', 1, now(), 'low', 0, 0)"
            )


# --- structure: alerts ----------------------------------------------------------


def test_alerts_table_exists_with_expected_columns(migrated_dsn: str) -> None:
    cols = _columns(migrated_dsn, "alerts")
    assert cols, "alerts table must exist"
    for name in (
        "alert_id",
        "timestamp",
        "trace_id",
        "trace_risk_record_id",
        "risk_level",
        "enforcement_type",
        "title",
        "description",
        "involved_entities",
        "triggered_rule_ids",
        "status",
        "alert_type",
        "assigned_to",
        "dedup_key",
        "duplicate_count",
        "superseded_by",
    ):
        assert name in cols, f"missing column {name!r}"
    assert cols["involved_entities"]["data_type"] == "jsonb"
    assert cols["superseded_by"]["data_type"] == "uuid"
    assert cols["superseded_by"]["is_nullable"] == "YES"


def test_alerts_primary_key(migrated_dsn: str) -> None:
    assert _primary_key_columns(migrated_dsn, "alerts") == ["alert_id"]


def test_alerts_indexes_exist(migrated_dsn: str) -> None:
    assert _index_exists(migrated_dsn, "alerts_dedup_key_idx")
    assert not _index_is_unique(migrated_dsn, "alerts_dedup_key_idx"), (
        "dedup_key index must NOT be unique — superseded alerts share a dedup "
        "key with the alert that supersedes them"
    )
    assert _index_exists(migrated_dsn, "alerts_status_risk_level_idx")
    assert _index_exists(migrated_dsn, "alerts_trace_idx")


def test_alerts_dedup_key_allows_duplicates(migrated_dsn: str) -> None:
    """Corner case proving the non-unique index above actually behaves as
    non-unique: two alerts sharing a dedup_key (superseded + superseding) must
    both insert successfully."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO alerts (timestamp, trace_id, risk_level, title, "
            "description, dedup_key) VALUES "
            "(now(), 't1', 'low', 'a', 'b', 'dk1')"
        )
        conn.execute(
            "INSERT INTO alerts (timestamp, trace_id, risk_level, title, "
            "description, dedup_key) VALUES "
            "(now(), 't1', 'low', 'a', 'b', 'dk1')"
        )
        (count,) = conn.execute(
            "SELECT count(*) FROM alerts WHERE dedup_key = 'dk1'"
        ).fetchone()
    assert count == 2


# --- notify trigger: dg_interaction_risk_written -------------------------------


def test_notify_function_and_trigger_exist(migrated_dsn: str) -> None:
    assert _function_exists(migrated_dsn, "dg_notify_interaction_risk")
    assert _trigger_exists(migrated_dsn, "dg_interaction_risk_notify")


def test_notify_trigger_is_statement_level(migrated_dsn: str) -> None:
    """Interaction risk records are insert-only and immutably versioned (a
    recompute writes a NEW version row, never mutates one) — no
    finalization/duplicate distinction to draw, so statement-level (the 0007
    payloads shape) is the right fit, not row-level (0006's shape)."""
    assert not _trigger_is_row_level(migrated_dsn, "dg_interaction_risk_notify")


def test_insert_fires_notification_with_empty_payload(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute("LISTEN dg_interaction_risk_written")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            _insert_min_interaction_risk_record(writer, interaction_id="n0")
        notifies = list(listener.notifies(timeout=5.0, stop_after=1))
    assert notifies, "expected a notification after an interaction_risk_records insert"
    assert notifies[0].channel == "dg_interaction_risk_written"
    assert notifies[0].payload == ""


def test_burst_of_inserts_coalesces_to_at_least_one_wake(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute("LISTEN dg_interaction_risk_written")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            for i in range(5):
                _insert_min_interaction_risk_record(writer, interaction_id=f"b{i}")
        got = list(listener.notifies(timeout=5.0, stop_after=1))
    assert got, "expected at least one notification from the insert burst"
    assert all(n.payload == "" for n in got)


def test_insert_into_unrelated_table_does_not_notify(migrated_dsn: str) -> None:
    """Corner case: writing to a different table must not fire this channel."""
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute("LISTEN dg_interaction_risk_written")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            writer.execute(
                "INSERT INTO trace_risk_records (trace_id, version, computed_at, "
                "trace_risk_level, interaction_count, policy_event_count) "
                "VALUES ('unrelated', 1, now(), 'low', 0, 0)"
            )
        got = _drain_notifies(listener, timeout=1.5)
    assert got == [], f"trace_risk_records insert must not notify; got {got!r}"


# --- migration chain -----------------------------------------------------------


def test_head_is_0012(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as conn:
        (version,) = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert version == "0012_das_risk_tables"


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade(0011) -> upgrade cleanly removes and re-adds the
    three DAS tables + trigger. Downgrading past this revision must not
    disturb interaction_legs (0009, as amended by 0011's dropped
    original_seq) or anything below it."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    assert _columns(pg_dsn, "interaction_risk_records")
    assert _columns(pg_dsn, "trace_risk_records")
    assert _columns(pg_dsn, "alerts")
    assert _trigger_exists(pg_dsn, "dg_interaction_risk_notify")

    command.downgrade(cfg, "0011_drop_leg_original_seq")
    assert not _columns(pg_dsn, "interaction_risk_records")
    assert not _columns(pg_dsn, "trace_risk_records")
    assert not _columns(pg_dsn, "alerts")
    assert not _function_exists(pg_dsn, "dg_notify_interaction_risk")
    assert "seq" in _columns(pg_dsn, "interaction_legs"), (
        "downgrading past 0012 must not disturb interaction_legs"
    )

    command.upgrade(cfg, "head")
    assert _columns(pg_dsn, "interaction_risk_records")
    assert _columns(pg_dsn, "trace_risk_records")
    assert _columns(pg_dsn, "alerts")
