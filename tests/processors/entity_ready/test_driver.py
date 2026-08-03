"""Cursor-drain loop for the P-entity-ready consumer (issue #121, ADR-0027).

The entity-ready consumer is a Layer-2 consumer (sibling of P-classification)
that drains the ``entities`` table by ``seq`` via the shared cursor-driven driver
(:mod:`data_governance.processors._driver`) and delivers each newly-created
**Entity** to a downstream governance consumer (risk / data-lineage / the
eventual PDP). This is the clean, independent half of ADR-0027: the ``entities``
stream has **no readiness gate** (an entity is ready the moment it is created —
first-detection only), so the consumer reuses the shared ``_driver``/``StreamSpec``
**verbatim** — no bespoke drain logic.

For this ticket the consumer's "processing" is just *observing* the entity: there
is no real downstream yet (the risk/lineage/PDP consumers are future work). The
drain records each delivered entity via an injected observer (a stand-in for the
downstream) and a Prometheus counter, so these tests can assert exactly-once
delivery and correct resume across a restart.

These tests prove the acceptance criteria (issue #121):

- a drain delivers every entity past the cursor, advancing the durable
  ``entity_ready`` ``processor_state`` cursor;
- an inserted entity is delivered exactly once; after a simulated restart
  (re-read the cursor from ``processor_state``) the drain resumes with no
  duplicate and no miss;
- a no-op ``ON CONFLICT (natural_key) DO UPDATE`` re-derive is NOT re-delivered
  (first-detection only — the ``AFTER INSERT`` trigger never fires on the
  conflict/UPDATE path, and the drain sees no new ``seq``);
- ``run()`` wakes on the ``dg_entity_ready`` LISTEN notification and on the poll
  backstop, both reaching the same drain; with LISTEN disabled the poll backstop
  alone still delivers (notify is latency-only, ADR-0015/ADR-0027).

The drain tests run in-process against a migrated DB (fast), mirroring
``tests/processors/classification/test_driver.py`` and
``tests/processors/test_driver.py``.
"""

from __future__ import annotations

import threading
import time

import psycopg
import pytest

from data_governance import db
from data_governance.processors.entity_ready import driver


# --- helpers -----------------------------------------------------------------


def _insert_entity(
    dsn: str,
    *,
    natural_key: str,
    entity_id: str | None = None,
    display_name: str = "n",
) -> int:
    """Insert a new entity the way P-interactions does (DEFAULT-allocated seq,
    keyed by ``natural_key``); return its allocated ``seq``."""
    with psycopg.connect(dsn) as conn:
        (seq,) = conn.execute(
            "INSERT INTO entities "
            "(id, kind, natural_key, display_name, detected_from, original_seq) "
            "VALUES (%s, 'agent', %s, %s, 'd', 1) RETURNING seq",
            (entity_id or natural_key, natural_key, display_name),
        ).fetchone()
        conn.commit()
    return int(seq)


def _rederive_entity_no_op(dsn: str, *, natural_key: str, seq: int) -> None:
    """Re-derive an already-known entity exactly as ``state.flush`` does: an
    ``INSERT ... ON CONFLICT (natural_key) DO UPDATE`` rewriting identical values
    (including ``seq = EXCLUDED.seq``). The row is unchanged — the first-detection
    no-op case (ADR-0027)."""
    with psycopg.connect(dsn) as conn:
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
        conn.commit()


def _cursor(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT last_processed_seq FROM processor_state "
            "WHERE processor_name = %s",
            (driver.PROCESSOR_NAME,),
        ).fetchone()
    return int(row[0]) if row else 0


class _Sink:
    """A stand-in downstream governance consumer: records the seq of every
    entity delivered to it, so a test can assert exactly-once delivery."""

    def __init__(self) -> None:
        self.delivered: list[int] = []

    def __call__(self, entity: "driver.Entity") -> None:
        self.delivered.append(entity.seq)


# --- drain delivers each entity, advances the cursor -------------------------


def test_drain_delivers_each_new_entity(configured_db: str) -> None:
    """A drain delivers every entity past the cursor to the downstream observer
    and advances the durable ``entity_ready`` cursor to the last entity's seq."""
    s0 = _insert_entity(configured_db, natural_key="e0")
    sink = _Sink()

    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    new_cursor = driver.drain(cursor, observer=sink)

    assert new_cursor == s0
    assert _cursor(configured_db) == s0
    assert sink.delivered == [s0]


def test_drain_from_empty_is_noop(configured_db: str) -> None:
    """No entities past the cursor → cursor stays at 0, nothing delivered."""
    sink = _Sink()
    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    assert driver.drain(cursor, observer=sink) == 0
    assert sink.delivered == []


def test_drain_is_incremental_across_two_batches(configured_db: str) -> None:
    """A drain advances the cursor to the last entity; a second drain delivers
    only entities that arrived since — exactly-once, no re-delivery of the
    already-seen entity."""
    s0 = _insert_entity(configured_db, natural_key="a")
    sink = _Sink()
    with db.transaction() as tx:
        c0 = driver.read_cursor(tx)
    c1 = driver.drain(c0, observer=sink)
    assert c1 == s0
    assert sink.delivered == [s0]

    s1 = _insert_entity(configured_db, natural_key="b")
    c2 = driver.drain(c1, observer=sink)
    assert c2 == s1 and c2 > c1
    # "a" was NOT re-delivered by the second drain.
    assert sink.delivered == [s0, s1]


def test_drain_delivers_entities_in_seq_order(configured_db: str) -> None:
    """Entities are delivered in ascending ``seq`` order — the cursor semantics
    the drain relies on (``WHERE seq > cursor ORDER BY seq ASC``)."""
    seqs = [_insert_entity(configured_db, natural_key=f"k{i}") for i in range(4)]
    sink = _Sink()
    driver.drain(0, observer=sink)
    assert sink.delivered == sorted(seqs) == seqs


# --- exactly-once + restart (headline acceptance) ----------------------------


def test_inserted_entity_delivered_exactly_once_across_restart(
    configured_db: str,
) -> None:
    """The headline acceptance case (issue #121): an inserted entity is delivered
    exactly once, and after a simulated restart — re-reading the durable cursor
    from ``processor_state`` — the drain resumes with NO duplicate and NO miss.
    """
    s0 = _insert_entity(configured_db, natural_key="one")
    s1 = _insert_entity(configured_db, natural_key="two")

    # First run: drain both from cursor 0.
    sink1 = _Sink()
    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    driver.drain(cursor, observer=sink1)
    assert sink1.delivered == [s0, s1]

    # A third entity arrives after the first run.
    s2 = _insert_entity(configured_db, natural_key="three")

    # Simulated restart: a *fresh* process re-reads the cursor from the durable
    # processor_state (not from in-memory), then drains. It must resume at s2 —
    # no duplicate of s0/s1 (already past the durable cursor), no miss of s2.
    sink2 = _Sink()
    with db.transaction() as tx:
        resumed_cursor = driver.read_cursor(tx)
    assert resumed_cursor == s1, "restart must resume from the durable cursor"
    driver.drain(resumed_cursor, observer=sink2)
    assert sink2.delivered == [s2], "no duplicate of already-delivered entities, no miss"


def test_restart_from_durable_cursor_after_full_drain_delivers_nothing(
    configured_db: str,
) -> None:
    """After a full drain, a restart that re-reads the durable cursor and finds no
    new entities delivers nothing — the cursor is the complete record of what has
    been delivered (idempotent replay, ADR-0007)."""
    _insert_entity(configured_db, natural_key="only")
    driver.drain(0, observer=_Sink())

    sink = _Sink()
    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    driver.drain(cursor, observer=sink)
    assert sink.delivered == []


# --- first-detection only (ADR-0027) -----------------------------------------


def test_no_op_rederive_is_not_re_delivered(configured_db: str) -> None:
    """First-detection only (ADR-0027): a no-op ``ON CONFLICT (natural_key) DO
    UPDATE`` re-derive of an already-known entity must NOT be re-delivered. The
    conflict path rewrites identical values (including the same span-derived
    ``seq``), so the drain — reading ``WHERE seq > cursor`` — sees nothing new,
    and the ``AFTER INSERT`` trigger never fired on the UPDATE path anyway."""
    s0 = _insert_entity(configured_db, natural_key="stable")
    sink = _Sink()
    c1 = driver.drain(0, observer=sink)
    assert sink.delivered == [s0]

    # Re-derive the same entity: identical values, same seq. No new row, no seq
    # advance.
    _rederive_entity_no_op(configured_db, natural_key="stable", seq=s0)

    c2 = driver.drain(c1, observer=sink)
    assert c2 == c1, "a no-op re-derive must not advance the cursor"
    assert sink.delivered == [s0], "a no-op re-derive must not be re-delivered"


# --- crash mid-item: cursor advances atomically with delivery ----------------


def test_crash_mid_entity_re_processes_from_same_cursor(
    configured_db: str,
) -> None:
    """ADR-0007 atomicity: the cursor advance commits in the SAME transaction as
    the per-entity delivery. A crash mid-entity rolls back the cursor advance, so
    the restart re-delivers that entity from the same cursor — never skipping it.
    """
    good = _insert_entity(configured_db, natural_key="good")
    _insert_entity(configured_db, natural_key="boom")

    calls: list[int] = []

    def _observer(entity: "driver.Entity") -> None:
        calls.append(entity.seq)
        if entity.natural_key == "boom":
            raise RuntimeError("crash mid-entity")

    with pytest.raises(RuntimeError, match="crash mid-entity"):
        driver.drain(0, observer=_observer)

    # "good" committed its cursor advance; "boom" rolled back — the cursor did
    # not advance past "good".
    assert _cursor(configured_db) == good

    # Recovery: restart re-drains from the durable cursor. "boom" is re-delivered
    # (it was never past the cursor); "good" is not re-delivered.
    sink = _Sink()
    driver.drain(_cursor(configured_db), observer=sink)
    boom_seq = max(sink.delivered)
    assert sink.delivered == [boom_seq], "recovery re-delivers boom, not good"


# --- run(): wakes on both the LISTEN notification and the poll backstop -------


def _drain_via_run(
    dsn: str, *, disable_listen: bool, monkeypatch, insert_after_start: bool
) -> list[int]:
    """Drive the real ``run()`` loop in a thread and return the seqs delivered.

    If *insert_after_start* is True the entity is inserted AFTER the loop has
    started and registered its LISTEN — so a delivery proves the wake path
    (either the notification, or the poll backstop when LISTEN is disabled)
    reached the drain, not just the pre-loop drain."""
    delivered: list[int] = []
    delivered_lock = threading.Lock()

    def _observer(entity: "driver.Entity") -> None:
        with delivered_lock:
            delivered.append(entity.seq)

    # Shrink the poll backstop so the test is fast.
    monkeypatch.setattr(driver, "POLL_SECONDS", 0.2)

    if disable_listen:
        def _boom(channel, dsn):  # noqa: ANN001 — test stub
            raise db.ConnectionTimeout("listen disabled (injected)")

        monkeypatch.setattr(db, "listen", _boom)

    seq_holder: dict[str, int] = {}
    if not insert_after_start:
        seq_holder["seq"] = _insert_entity(dsn, natural_key="pre")

    stop = threading.Event()
    t = threading.Thread(
        target=driver.run, args=(stop, dsn), kwargs={"observer": _observer}, daemon=True
    )
    t.start()
    try:
        if insert_after_start:
            # Give the loop a moment to register its LISTEN / enter the wait.
            time.sleep(0.5)
            seq_holder["seq"] = _insert_entity(dsn, natural_key="post")

        target = seq_holder["seq"]
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            with delivered_lock:
                if target in delivered:
                    break
            time.sleep(0.05)
    finally:
        stop.set()
        t.join(timeout=10)
        assert not t.is_alive(), "run() must return promptly on stop_event"
    with delivered_lock:
        return list(delivered)


def test_run_wakes_on_notify_and_delivers(configured_db: str, monkeypatch) -> None:
    """``run()`` with LISTEN active delivers an entity inserted AFTER the loop
    started — the ``dg_entity_ready`` notification wakes the drain (low latency).
    """
    delivered = _drain_via_run(
        configured_db,
        disable_listen=False,
        monkeypatch=monkeypatch,
        insert_after_start=True,
    )
    assert delivered, "run() must deliver an entity inserted after start (notify wake)"


def test_run_poll_only_still_delivers(configured_db: str, monkeypatch) -> None:
    """With LISTEN disabled, ``run()`` falls back to the poll backstop and still
    delivers — correctness never depends on the notification (notify is
    latency-only; ADR-0015/ADR-0027)."""
    delivered = _drain_via_run(
        configured_db,
        disable_listen=True,
        monkeypatch=monkeypatch,
        insert_after_start=True,
    )
    assert delivered, "poll backstop must deliver even with LISTEN disabled"


def test_run_drains_backlog_before_waiting(configured_db: str, monkeypatch) -> None:
    """``run()`` drains anything already past the cursor before it starts waiting,
    so an entity that landed before the loop started is not stranded until the
    first poll timeout (the pre-loop drain in ``_driver.run``)."""
    delivered = _drain_via_run(
        configured_db,
        disable_listen=False,
        monkeypatch=monkeypatch,
        insert_after_start=False,
    )
    assert delivered, "run() must drain the pre-existing backlog"
