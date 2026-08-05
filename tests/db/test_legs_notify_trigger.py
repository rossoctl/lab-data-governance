"""Tests for the interaction_legs NOTIFY trigger migration (issue #115).

Migration 0010 makes ``interaction_legs`` a *notified* stream. The cursorable
half already exists (migration 0009 gave the table ``seq`` from
``interaction_legs_seq`` plus the ``interaction_legs_seq_idx`` cursor index);
this revision adds the announcement so a downstream lineage deriver can
``LISTEN`` instead of polling blindly:

- ``dg_notify_legs()`` — plpgsql, ``PERFORM pg_notify('dg_legs_inserted', '')``,
  ``RETURNS trigger`` returning NULL (0005/0007's function shape verbatim);
- ``dg_legs_notify`` — a **statement-level** ``AFTER INSERT OR UPDATE`` trigger.

The ``OR UPDATE`` is the load-bearing difference from the payloads trigger
(0007), which is INSERT-only. Legs are written with an **upsert**
(``processors/interactions/state.py:flush`` →
``ON CONFLICT (interaction_id, leg_type) DO UPDATE``), so re-deriving a trace
rewrites its legs through the UPDATE path — and an ``ON CONFLICT DO UPDATE``
that lands on the update path fires UPDATE triggers, *not* INSERT triggers. An
INSERT-only trigger would therefore leave every re-derived trace unannounced.
The ``test_upsert_that_updates_fires_notification`` case below is the whole
point of the revision.

Like the sibling migration tests these run the real migration chain against a
real Postgres (testcontainers) and assert what it produces, not how.
"""

from __future__ import annotations

import datetime as dt

import psycopg

CHANNEL = "dg_legs_inserted"


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


def _trigger_events(dsn: str, name: str) -> set[str]:
    """The event set the trigger fires on, from ``information_schema.triggers``.

    Postgres reports one row per event for a multi-event trigger, so
    ``AFTER INSERT OR UPDATE`` yields ``{"INSERT", "UPDATE"}``.
    """
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT event_manipulation FROM information_schema.triggers "
            "WHERE trigger_schema = 'public' AND trigger_name = %s",
            (name,),
        ).fetchall()
    return {r[0] for r in rows}


def _index_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname = 'public' AND indexname = %s",
            (name,),
        ).fetchone()
    return row is not None


# Mirrors ``processors/interactions/state.py:flush``'s leg write verbatim in the
# parts the trigger keys on: the ON CONFLICT target and the DO UPDATE branch.
# Note it takes an *unconditional* DO UPDATE (no WHERE predicate) — unlike the
# spans upsert, a re-derivation always rewrites the leg.
_UPSERT_LEG_SQL = """
    INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at,
                                  payload_hash, error, seq)
    VALUES (%s, %s, %s, %s, %s, nextval('interaction_legs_seq'))
    ON CONFLICT (interaction_id, leg_type) DO UPDATE SET
        occurred_at  = EXCLUDED.occurred_at,
        payload_hash = EXCLUDED.payload_hash,
        error        = EXCLUDED.error,
        seq          = EXCLUDED.seq
"""


def _upsert_leg(
    conn: psycopg.Connection,
    *,
    interaction_id: str,
    leg_type: str = "request",
    payload_hash: str | None = None,
) -> None:
    # `original_seq` was dropped from `interaction_legs` by
    # 0011_drop_leg_original_seq (issue #133), and `state.py:flush` no longer writes
    # it — which this SQL mirrors verbatim in the parts the trigger keys on. The
    # trigger fires on the write itself, so the removed column never mattered here.
    conn.execute(
        _UPSERT_LEG_SQL,
        (
            interaction_id,
            leg_type,
            dt.datetime.now(dt.timezone.utc),
            payload_hash,
            False,
        ),
    )


def _drain_notifies(
    listener: psycopg.Connection, *, timeout: float
) -> list[psycopg.Notify]:
    """Collect notifications arriving within ``timeout`` seconds.

    Unlike ``notifies(stop_after=1)``, this does not early-exit on the first
    notification, so it can also assert that *zero* arrive.
    """
    return list(listener.notifies(timeout=timeout))


# --- structure ---------------------------------------------------------------


def test_notify_function_and_trigger_exist(migrated_dsn: str) -> None:
    """Acceptance: the function + trigger follow the payloads/spans naming
    (``dg_notify_legs()`` / ``dg_legs_notify``)."""
    assert _function_exists(migrated_dsn, "dg_notify_legs")
    assert _trigger_exists(migrated_dsn, "dg_legs_notify")


def test_trigger_fires_on_insert_and_update(migrated_dsn: str) -> None:
    """The load-bearing structural assertion: legs are upserted, so the trigger
    must cover the UPDATE path as well as INSERT. An INSERT-only trigger (the
    payloads shape) would silently skip every re-derived trace."""
    assert _trigger_events(migrated_dsn, "dg_legs_notify") == {"INSERT", "UPDATE"}


def test_trigger_is_statement_level(migrated_dsn: str) -> None:
    """Statement-level, like 0007: the signal is "something happened" and the
    consumer re-drains ``WHERE seq > cursor`` on any wake, so per-row precision
    buys nothing. (0006's row-level split for spans existed to *suppress*
    no-op duplicate wakes; the legs upsert has no WHERE predicate, so there is
    no no-op path to suppress.)"""
    assert not _trigger_is_row_level(migrated_dsn, "dg_legs_notify")


def test_cursorable_half_already_present(migrated_dsn: str) -> None:
    """Sanity: 0009 already supplies the cursor (``seq`` + sequence + index);
    this revision adds only the notification. Pinned here so a regression in
    0009 does not silently turn the legs stream un-drainable."""
    assert _index_exists(migrated_dsn, "interaction_legs_seq_idx")


# --- behaviour ---------------------------------------------------------------


def test_insert_fires_notification_with_empty_payload(migrated_dsn: str) -> None:
    """A leg insert wakes a LISTENer; the notification carries an empty payload
    ('something happened' is the whole signal — the consumer re-drains from its
    durable cursor). Mirrors the spans/payloads notify tests."""
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            _upsert_leg(writer, interaction_id="i0")
        notifies = list(listener.notifies(timeout=5.0, stop_after=1))
    assert notifies, "expected a notification after a leg insert"
    assert notifies[0].channel == CHANNEL
    assert notifies[0].payload == "", "payload must be empty"


def test_upsert_that_updates_fires_notification(migrated_dsn: str) -> None:
    """**The point of issue #115.** Re-deriving a trace rewrites its legs via
    ``ON CONFLICT (interaction_id, leg_type) DO UPDATE`` — which fires UPDATE
    triggers, not INSERT triggers. The re-derived leg must be re-announced, so
    the lineage deriver picks up the rewritten row instead of going unnoticed."""
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            # First derivation: a plain insert (notifies via the INSERT event).
            _upsert_leg(writer, interaction_id="i1", payload_hash="h_first")
        first = list(listener.notifies(timeout=5.0, stop_after=1))
        assert first, "sanity: the initial insert should have notified"

        # Re-derivation of the same trace: same PK, so the upsert lands on the
        # DO UPDATE branch. Only an AFTER UPDATE trigger can see this.
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            _upsert_leg(writer, interaction_id="i1", payload_hash="h_rederived")
        got = _drain_notifies(listener, timeout=5.0)

    assert got, (
        "a re-derived leg (upsert landing on DO UPDATE) must re-announce; "
        "an AFTER INSERT-only trigger would leave this silent"
    )
    assert all(n.channel == CHANNEL for n in got)
    assert all(n.payload == "" for n in got)

    # And the rewrite really happened (the update was not a no-op that only
    # *appeared* to notify).
    with psycopg.connect(migrated_dsn) as conn:
        (payload_hash,) = conn.execute(
            "SELECT payload_hash FROM interaction_legs "
            "WHERE interaction_id = 'i1' AND leg_type = 'request'"
        ).fetchone()
    assert payload_hash == "h_rederived"


def test_plain_update_fires_notification(migrated_dsn: str) -> None:
    """The same guarantee via a bare UPDATE statement — pins the trigger's
    UPDATE event independently of the upsert's conflict machinery."""
    with psycopg.connect(migrated_dsn, autocommit=True) as writer:
        _upsert_leg(writer, interaction_id="i2")

    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        with psycopg.connect(migrated_dsn, autocommit=True) as w:
            w.execute(
                "UPDATE interaction_legs SET payload_hash = 'h2' "
                "WHERE interaction_id = 'i2'"
            )
        got = list(listener.notifies(timeout=5.0, stop_after=1))
    assert got, "expected a notification on a leg UPDATE"
    assert got[0].payload == ""


def test_burst_of_writes_coalesces_to_at_least_one_wake(migrated_dsn: str) -> None:
    """A burst still wakes the listener; the consumer cursor-drains on any wake,
    so the notification count is irrelevant (ADR-0007)."""
    with psycopg.connect(migrated_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        with psycopg.connect(migrated_dsn, autocommit=True) as writer:
            for i in range(5):
                _upsert_leg(writer, interaction_id=f"b{i}")
        got = list(listener.notifies(timeout=5.0, stop_after=1))
    assert got, "expected at least one notification from the write burst"
    assert all(n.payload == "" for n in got)


# --- seq-cursor drain --------------------------------------------------------


def test_seq_cursor_drain_resumes_from_stored_position(migrated_dsn: str) -> None:
    """Acceptance: a consumer can read the legs stream in ``seq`` order and
    resume from a stored cursor. Drains ``WHERE seq > cursor ORDER BY seq``,
    stores the last seq, then drains again after more writes and sees only the
    new rows — the shape the shared processor loop uses (ADR-0007)."""

    def drain(conn: psycopg.Connection, cursor: int) -> list[tuple[str, int]]:
        return [
            (ix_id, seq)
            for ix_id, seq in conn.execute(
                "SELECT interaction_id, seq FROM interaction_legs "
                "WHERE seq > %s ORDER BY seq",
                (cursor,),
            ).fetchall()
        ]

    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        for i in range(3):
            _upsert_leg(conn, interaction_id=f"c{i}")

        cursor = 0
        first = drain(conn, cursor)
        assert [ix for ix, _ in first] == ["c0", "c1", "c2"]
        seqs = [s for _, s in first]
        assert seqs == sorted(seqs), "drain must come back in seq order"
        cursor = seqs[-1]

        # Nothing new yet: a re-drain from the stored cursor is empty.
        assert drain(conn, cursor) == []

        # More work arrives — one fresh leg and one *re-derived* leg (the upsert
        # bumps seq to a new nextval, so the rewritten row reappears past the
        # cursor and the consumer re-processes it).
        _upsert_leg(conn, interaction_id="c3")
        _upsert_leg(conn, interaction_id="c0", payload_hash="rewritten")

        second = drain(conn, cursor)
        assert [ix for ix, _ in second] == ["c3", "c0"], (
            "resume must yield only rows past the cursor, in seq order, "
            "including the re-derived leg"
        )
        assert all(s > cursor for _, s in second)


# --- migration chain / downgrade round-trip ----------------------------------


def test_revision_is_in_the_chain(migrated_dsn: str) -> None:
    """A fresh migrate applies the revision (it is in the linear history the
    migrate CLI walks)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    walked = {rev.revision for rev in script.walk_revisions()}
    assert "0012_legs_notify_trigger" in walked


# NOTE: the "applying the chain lands on head" assertion moved on to
# ``test_data_lineage_migration.py`` when 0011 (``lineage_metadata``) landed —
# that pin travels with whichever revision is head, exactly as this file
# inherited it from ``test_classifications_migration.py``. What stays here is
# this revision's own place in the chain (above) and the downgrade round-trip
# below, both of which are about 0010 specifically rather than about head.


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade(0009) -> upgrade cleanly removes and re-adds the
    function and trigger, leaving the legs table (and its cursor) intact.
    Drives Alembic through the project's own config builder (the migrate CLI's)."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    # Up to head: the notification is present.
    command.upgrade(cfg, "head")
    assert _function_exists(pg_dsn, "dg_notify_legs")
    assert _trigger_exists(pg_dsn, "dg_legs_notify")

    # Down one revision to 0009: function + trigger gone, table + cursor stay.
    command.downgrade(cfg, "0009_interaction_legs")
    assert not _function_exists(pg_dsn, "dg_notify_legs")
    assert not _trigger_exists(pg_dsn, "dg_legs_notify")
    assert _index_exists(pg_dsn, "interaction_legs_seq_idx"), (
        "the interaction_legs cursor from 0009 must survive the downgrade"
    )
    # The table itself still accepts writes — the downgrade only un-announces.
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        _upsert_leg(conn, interaction_id="d0")

    # Back up to head: the notification returns and works.
    command.upgrade(cfg, "head")
    assert _function_exists(pg_dsn, "dg_notify_legs")
    assert _trigger_exists(pg_dsn, "dg_legs_notify")
    with psycopg.connect(pg_dsn, autocommit=True) as listener:
        listener.execute(f"LISTEN {CHANNEL}")
        with psycopg.connect(pg_dsn, autocommit=True) as writer:
            _upsert_leg(writer, interaction_id="d1")
        assert list(listener.notifies(timeout=5.0, stop_after=1))
