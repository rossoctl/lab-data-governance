"""Tests for the ``dg_entity_ready`` NOTIFY trigger migration (issue #121, ADR-0027).

Migration 0010 makes ``entities`` a notify source for the consumer-facing
``dg_entity_ready`` stream. The ``entities`` table already had the cursorable-stream
infrastructure since migration 0004 — ``seq BIGINT DEFAULT nextval('entities_seq')``,
the ``entities_seq`` sequence OWNED BY it, and the ``entities_seq_idx`` cursor index —
so this revision adds *only* the notify half:

- a ``dg_notify_entities()`` plpgsql function firing a payload-less
  ``pg_notify('dg_entity_ready', '')`` (mirrors ``dg_notify_spans`` /
  ``dg_notify_payloads`` exactly);
- an ``AFTER INSERT ON entities FOR EACH ROW`` trigger executing it.

ADR-0027: ``dg_entity_ready`` is **first-detection only**. An entity's identity is
set once at creation and, for the current source, never mutated — the P-interactions
write path is ``INSERT ... ON CONFLICT (natural_key) DO UPDATE`` and rewrites only
identical values. Because the trigger is ``AFTER INSERT`` (not ``AFTER UPDATE``), the
``ON CONFLICT DO UPDATE`` conflict path — which fires ``AFTER UPDATE``, not
``AFTER INSERT`` — does not re-fire it, so a re-derive of an already-known entity is
silent: the notification is first-detection only regardless of whether a no-op
update touches ``seq``.

Like the sibling migration tests these run the real migration chain against a real
Postgres (testcontainers) and assert what it produces, not how — the test does not
care whether the DDL is one ``op.execute`` or twenty.
"""

from __future__ import annotations

import psycopg

CHANNEL = "dg_entity_ready"


# --- helpers -----------------------------------------------------------------


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


def _insert_entity(
    conn: psycopg.Connection,
    *,
    natural_key: str,
    entity_id: str | None = None,
    display_name: str = "n",
) -> int:
    """Insert an entity the way P-interactions does — a DEFAULT-allocated ``seq``,
    keyed by ``natural_key``. Returns the allocated ``seq``.

    Mirrors the ``entities`` insert in ``state.flush`` (id deterministic, kind an
    ENUM value, ``original_seq`` NOT NULL); the trigger keys only off the INSERT
    event, so the column values are otherwise incidental."""
    (seq,) = conn.execute(
        "INSERT INTO entities "
        "(id, kind, natural_key, display_name, detected_from, original_seq) "
        "VALUES (%s, 'agent', %s, %s, 'd', 1) RETURNING seq",
        (entity_id or natural_key, natural_key, display_name),
    ).fetchone()
    return int(seq)


def _upsert_entity_no_op(
    conn: psycopg.Connection, *, natural_key: str, seq: int
) -> None:
    """Re-derive an already-known entity exactly as ``state.flush`` does: an
    ``INSERT ... ON CONFLICT (natural_key) DO UPDATE`` rewriting *identical*
    values (including ``seq = EXCLUDED.seq``, the span-derived value the processor
    recomputes). The whole row is unchanged, so this is the no-op re-derive
    ADR-0027 calls first-detection's silent case."""
    conn.execute(
        "INSERT INTO entities "
        "(id, kind, natural_key, display_name, detected_from, seq, original_seq) "
        "VALUES (%s, 'agent', %s, 'n', 'd', %s, 1) "
        "ON CONFLICT (natural_key) DO UPDATE SET "
        "display_name = EXCLUDED.display_name, "
        "detected_from = EXCLUDED.detected_from, "
        "seq = EXCLUDED.seq",
        (natural_key, natural_key, seq),
    )


def _drain_notifies(
    listener: psycopg.Connection, *, timeout: float
) -> list[psycopg.Notify]:
    """Collect notifications arriving within ``timeout`` seconds.

    Unlike ``notifies(stop_after=1)``, this does not early-exit on the first
    notification, so it can also assert that *zero* arrive (the no-op case)."""
    got: list[psycopg.Notify] = []
    for n in listener.notifies(timeout=timeout):
        got.append(n)
    return got


# --- structure ---------------------------------------------------------------


def test_notify_function_and_trigger_exist(migrated_dsn: str) -> None:
    assert _function_exists(migrated_dsn, "dg_notify_entities")
    assert _trigger_exists(migrated_dsn, "dg_entities_notify")


def test_trigger_is_row_level(migrated_dsn: str) -> None:
    """ADR-0027 specifies ``AFTER INSERT ON entities FOR EACH ROW``. Row-level is
    what makes this first-detection only: the conflict path fires AFTER UPDATE,
    which an AFTER INSERT row-level trigger never sees."""
    assert _trigger_is_row_level(migrated_dsn, "dg_entities_notify")


# --- behaviour ---------------------------------------------------------------


def test_insert_fires_notification_with_empty_payload(migrated_dsn: str) -> None:
    """A fresh entity insert wakes a LISTENer; the notification carries an empty
    payload — 'something happened' is the whole signal, the consumer re-drains
    ``WHERE seq > cursor`` from its durable cursor (ADR-0007/ADR-0015)."""
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            _insert_entity(writer, natural_key="e0")
        notifies = list(listener.notifies(timeout=5.0, stop_after=1))
    assert notifies, "expected a notification after an entity insert"
    assert notifies[0].channel == CHANNEL
    assert notifies[0].payload == "", "payload must be empty"


def test_no_op_conflict_update_does_not_re_notify(migrated_dsn: str) -> None:
    """First-detection only (ADR-0027): a no-op ``ON CONFLICT (natural_key) DO
    UPDATE`` rewriting identical values re-derives an already-known entity and
    must NOT re-fire ``dg_entity_ready``. The conflict path fires AFTER UPDATE,
    not AFTER INSERT, so the AFTER INSERT trigger stays silent — the consumer is
    told about an entity exactly once, at creation."""
    # Create the entity while nobody is listening, then read its seq back.
    with psycopg.connect(migrated_dsn, autocommit=True) as writer:
        seq = _insert_entity(writer, natural_key="dup")

    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        # Re-derive the same entity: identical values, ON CONFLICT DO UPDATE.
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            _upsert_entity_no_op(writer, natural_key="dup", seq=seq)
        notifies = _drain_notifies(listener, timeout=1.5)
    assert notifies == [], (
        "a no-op ON CONFLICT DO UPDATE must not re-notify: dg_entity_ready is "
        "first-detection only (ADR-0027)"
    )


def test_second_distinct_entity_fires_again(migrated_dsn: str) -> None:
    """First detection of a *different* entity DOES notify — the trigger fires per
    genuinely-inserted row, so each newly-created entity is announced once."""
    with psycopg.connect(migrated_dsn, autocommit=True) as writer:
        _insert_entity(writer, natural_key="first")

    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            _insert_entity(writer, natural_key="second")
        notifies = list(listener.notifies(timeout=5.0, stop_after=1))
    assert notifies, "a second, distinct entity insert must fire dg_entity_ready"
    assert notifies[0].channel == CHANNEL


# --- migration chain ---------------------------------------------------------


def test_0010_is_applied_in_the_chain(migrated_dsn: str) -> None:
    """A fresh migrate applies 0010 (it is in the linear history the migrate CLI
    walks)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    walked = {rev.revision for rev in script.walk_revisions()}
    assert "0010_entity_ready_notify" in walked


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade(0009) -> upgrade cleanly removes and re-adds the
    function and trigger. Downgrade stops one revision below this one — the
    ``entities`` table and its 0004 stream infra (seq column, sequence, index)
    stay put; only the notify half is added and removed."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    # Up to head: the entity notify shape is present.
    command.upgrade(cfg, "head")
    assert _function_exists(pg_dsn, "dg_notify_entities")
    assert _trigger_exists(pg_dsn, "dg_entities_notify")

    # Down one revision to 0009: only the notify half is gone. The entities
    # table and its stream infra (from 0004) survive.
    command.downgrade(cfg, "0009_interaction_legs")
    assert not _function_exists(pg_dsn, "dg_notify_entities")
    assert not _trigger_exists(pg_dsn, "dg_entities_notify")
    # The table and its seq column from 0004 must still be there.
    with psycopg.connect(pg_dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'entities' AND column_name = 'seq'"
        ).fetchone()
    assert row is not None, "the entities.seq column (0004) must survive the downgrade"

    # Back up to head: the notify half returns.
    command.upgrade(cfg, "head")
    assert _function_exists(pg_dsn, "dg_notify_entities")
    assert _trigger_exists(pg_dsn, "dg_entities_notify")
