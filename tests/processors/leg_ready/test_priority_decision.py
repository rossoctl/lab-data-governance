"""Leg-ready's priority decision arm (``dg_priority_decision``) on a real Postgres, through the
real observer and the real cursor arm:

- a named leg behind an unready prefix is decided first — the cursor holds, the
  named interaction gets its record;
- idempotent with the cursor arm — the cursor arm reaching the same leg later
  writes no second version and makes no second OPA call;
- a lost notify still delivers — the cursor arm decides the leg in order;
- a named leg not ready yet stays pending and is decided once it is ready;
  past its TTL it is dropped (the cursor arm still delivers);
- an OPA outage keeps the named id pending;
- the write is fenced on the cursor row (seq and stamp): a replacement's write,
  whether it moved the cursor or not, makes it roll back, and the id stays pending;
  the cursor arm and the recompute arm are fenced the same way;
- a lost lock ends the arm, before any computing; a fault on one id does not
  starve the ids behind it;
- every arm computes inside a turn and stamps the row;
- with inherited labels on, the arm waits for label lineage;
- the notify itself drives it (the listener thread), with the cursor arm idle;
- a priority turn goes before a waiting cursor turn.
"""

from __future__ import annotations

import threading
import time

import psycopg
import pytest

from data_governance import db
from data_governance.processors.leg_ready import driver, priority_decision
from data_governance.risk.engine.observer import make_risk_observer
from data_governance.risk.engine.opa import OpaRequestError

from .test_risk_observer import (
    _FakeOpaClient,
    _classify,
    _cursor,
    _insert_leg,
    _risk_versions,
)

_A = "aaaaaaaa-0000-4000-8000-000000000001"  # head of the stream, payload not classified
_B = "bbbbbbbb-0000-4000-8000-000000000002"  # behind it, the gate's exchange


def _unready_prefix_then_ready_leg(dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO interaction_payloads (content_hash, content, byte_size) "
            "VALUES ('h-a', '{}'::jsonb, 2), ('h-b', '{}'::jsonb, 2)"
        )
        conn.commit()
    _insert_leg(dsn, interaction_id=_A, leg_type="request", payload_hash="h-a")
    _insert_leg(dsn, interaction_id=_B, leg_type="request", payload_hash="h-b")
    _classify(dsn, "h-b")


def test_a_named_leg_behind_an_unready_prefix_is_decided_first(configured_db: str) -> None:
    _unready_prefix_then_ready_leg(configured_db)
    opa = _FakeOpaClient()
    observer = make_risk_observer(opa)

    assert driver.drain(0, observer=observer) == 0, "the head is unready: the cursor holds"
    assert _risk_versions(configured_db, _B) == []

    arm = priority_decision.PriorityDecisionArm(observer)
    arm.want(_B)
    assert arm.serve() == 1
    assert _risk_versions(configured_db, _B) == [(1, ["request"])]
    assert _risk_versions(configured_db, _A) == []
    assert _cursor(configured_db) == 0, "priority decision never moves the cursor"
    assert arm.pending == {}


def test_idempotent_with_the_cursor_arm(configured_db: str) -> None:
    _unready_prefix_then_ready_leg(configured_db)
    opa = _FakeOpaClient()
    observer = make_risk_observer(opa)
    arm = priority_decision.PriorityDecisionArm(observer)
    arm.want(_B)
    arm.serve()
    calls_for_b = [c for c in opa.calls if c["interaction_id"] == _B]
    assert len(calls_for_b) == 1

    _classify(configured_db, "h-a")
    cursor = driver.drain(0, observer=observer)
    assert cursor > 0
    assert _risk_versions(configured_db, _A) == [(1, ["request"])]
    assert _risk_versions(configured_db, _B) == [(1, ["request"])], "no duplicate version"
    assert len([c for c in opa.calls if c["interaction_id"] == _B]) == 1, "the cached decision is reused"

    # And the other way round: wanting what the cursor arm already decided writes nothing.
    arm.want(_B)
    arm.serve()
    assert _risk_versions(configured_db, _B) == [(1, ["request"])]


def test_a_lost_notify_still_delivers_by_cursor(configured_db: str) -> None:
    _unready_prefix_then_ready_leg(configured_db)
    observer = make_risk_observer(_FakeOpaClient())
    _classify(configured_db, "h-a")
    driver.drain(0, observer=observer)
    assert _risk_versions(configured_db, _B) == [(1, ["request"])]


def test_an_unready_named_leg_waits_then_is_decided(configured_db: str) -> None:
    observer = make_risk_observer(_FakeOpaClient())
    arm = priority_decision.PriorityDecisionArm(observer)
    arm.want(_B)
    assert arm.serve() == 0 and _B in arm.pending, "no leg yet: pending"
    with psycopg.connect(configured_db) as conn:
        conn.execute("INSERT INTO interaction_payloads (content_hash, content, byte_size) VALUES ('h-b', '{}'::jsonb, 2)")
        conn.commit()
    _insert_leg(configured_db, interaction_id=_B, leg_type="request", payload_hash="h-b")
    assert arm.serve() == 0 and _B in arm.pending, "not classified: pending"
    _classify(configured_db, "h-b")
    assert arm.serve() == 1
    assert _risk_versions(configured_db, _B) == [(1, ["request"])]


def test_a_named_id_expires(configured_db: str) -> None:
    arm = priority_decision.PriorityDecisionArm(make_risk_observer(_FakeOpaClient()))
    arm.want(_B, now=100.0)
    arm.serve(now=100.0 + priority_decision.TTL_SECONDS + 0.1)
    assert arm.pending == {}


def test_not_an_interaction_id_is_ignored(configured_db: str) -> None:
    arm = priority_decision.PriorityDecisionArm(make_risk_observer(_FakeOpaClient()))
    arm.want("'; DROP TABLE spans; --")
    arm.want("")
    assert arm.pending == {}


def test_an_opa_outage_keeps_the_id_pending(configured_db: str) -> None:
    _unready_prefix_then_ready_leg(configured_db)
    opa = _FakeOpaClient()
    opa.fail_with = OpaRequestError("OPA unreachable (injected)")
    arm = priority_decision.PriorityDecisionArm(make_risk_observer(opa))
    arm.want(_B)
    assert arm.serve() == 0
    assert _B in arm.pending
    assert _risk_versions(configured_db, _B) == []
    opa.fail_with = None
    assert arm.serve() == 1
    assert _risk_versions(configured_db, _B) == [(1, ["request"])]


def test_the_write_is_fenced_on_the_cursor(configured_db: str) -> None:
    """Someone moved the cursor while this arm computed: the write rolls back, the
    id stays pending, and a computation on fresh evidence lands."""
    _unready_prefix_then_ready_leg(configured_db)
    real = make_risk_observer(_FakeOpaClient())

    moved: list[bool] = []

    def observer(leg):
        write = real(leg)
        if moved:
            return write
        moved.append(True)
        with psycopg.connect(configured_db) as conn:
            conn.execute(
                "INSERT INTO processor_state (processor_name, last_processed_seq, updated_at) "
                "VALUES ('leg_ready', 99, now()) ON CONFLICT (processor_name) "
                "DO UPDATE SET last_processed_seq = 99"
            )
            conn.commit()
        return write

    arm = priority_decision.PriorityDecisionArm(observer)
    arm.want(_B)
    assert arm.serve() == 0
    assert _risk_versions(configured_db, _B) == []
    assert _B in arm.pending
    assert arm.serve() == 1
    assert _risk_versions(configured_db, _B) == [(1, ["request"])]


def test_the_write_is_fenced_on_a_replacement_that_left_the_cursor_in_place(configured_db: str) -> None:
    """A replacement consumer's recompute or priority decision stamps the cursor row
    without moving it; the stalled arm's older computation must not land after it."""
    _unready_prefix_then_ready_leg(configured_db)
    real = make_risk_observer(_FakeOpaClient())

    def observer(leg):
        write = real(leg)
        with psycopg.connect(configured_db) as conn:
            conn.execute("UPDATE processor_state SET updated_at = clock_timestamp() WHERE processor_name = 'leg_ready'")
            conn.commit()
        return write

    arm = priority_decision.PriorityDecisionArm(observer)
    arm.want(_B)
    assert arm.serve() == 0
    assert _risk_versions(configured_db, _B) == []
    assert _B in arm.pending


def test_the_cursor_arm_is_fenced_on_a_write_that_left_the_cursor_in_place(configured_db: str) -> None:
    """A stalled process's older computation must not land after a replacement's priority decision."""
    _insert_leg(configured_db, interaction_id=_B, leg_type="request", payload_hash=None)
    real = make_risk_observer(_FakeOpaClient())

    def observer(leg):
        write = real(leg)
        with psycopg.connect(configured_db) as conn:
            conn.execute("UPDATE processor_state SET updated_at = clock_timestamp() WHERE processor_name = 'leg_ready'")
            conn.commit()
        return write

    with pytest.raises(db.LockLost):
        driver.drain(0, observer=observer)
    assert _risk_versions(configured_db, _B) == []
    assert _cursor(configured_db) == 0


def _stamp(dsn: str):
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT updated_at FROM processor_state WHERE processor_name = 'leg_ready'").fetchone()
    return row[0] if row else None


def test_every_arm_computes_in_a_turn_and_stamps_the_row(configured_db: str) -> None:
    """The process stays the tables' one serial writer, and the fence above depends on the stamp."""
    _unready_prefix_then_ready_leg(configured_db)
    real = make_risk_observer(_FakeOpaClient())
    busy: list[bool] = []

    def observer(leg):
        busy.append(driver.TURNS._busy)
        return real(leg)

    arm = priority_decision.PriorityDecisionArm(observer)
    arm.want(_B)
    assert arm.serve() == 1
    decision_stamp = _stamp(configured_db)
    assert decision_stamp is not None

    _classify(configured_db, "h-a")
    cursor = driver.drain(0, observer=observer)
    assert cursor > 0
    cursor_stamp = _stamp(configured_db)
    assert cursor_stamp > decision_stamp

    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO leg_inherited_labels (interaction_id, leg_type, trace_id, payload_hash, seq, matcher, "
            "classified) VALUES (%s, 'request', 't', 'h-a', 0, 'exact', true)", (_A,))
        conn.execute("INSERT INTO risk_recompute_requests (interaction_id, reason) VALUES (%s, 'test')", (_A,))
        conn.commit()
    assert driver._drain_recompute_requests(cursor, observer, None, 10) == 1
    assert _stamp(configured_db) > cursor_stamp
    assert len(busy) >= 3 and all(busy), busy


def test_a_lost_lock_stops_the_arm_before_it_computes(configured_db: str) -> None:
    _unready_prefix_then_ready_leg(configured_db)
    calls: list[str] = []

    def guard() -> None:
        raise db.LockLost("lost")

    arm = priority_decision.PriorityDecisionArm(lambda leg: calls.append(leg.interaction_id), guard=guard)
    arm.want(_B)
    with pytest.raises(db.LockLost):
        arm.serve()
    assert calls == []

    stop = threading.Event()
    thread = threading.Thread(target=arm.run, args=(stop, configured_db), daemon=True)
    thread.start()
    time.sleep(0.5)
    with psycopg.connect(configured_db, autocommit=True) as conn:
        conn.execute("SELECT pg_notify(%s, %s)", (priority_decision.PRIORITY_DECISION_CHANNEL, _B))
    thread.join(timeout=10)
    stop.set()
    assert not thread.is_alive(), "a lost lock ends the arm"
    assert calls == []


def test_a_fault_on_one_id_does_not_starve_the_next(configured_db: str) -> None:
    _unready_prefix_then_ready_leg(configured_db)
    _classify(configured_db, "h-a")
    real = make_risk_observer(_FakeOpaClient())

    def observer(leg):
        if leg.interaction_id == _A:
            raise RuntimeError("this one interaction is broken")
        return real(leg)

    arm = priority_decision.PriorityDecisionArm(observer)
    arm.want(_A)
    arm.want(_B)
    assert arm.serve() == 1
    assert _risk_versions(configured_db, _B) == [(1, ["request"])]
    assert _A in arm.pending, "kept until its TTL"


def test_with_inherited_labels_on_the_arm_waits_for_label_lineage(configured_db: str, monkeypatch) -> None:
    monkeypatch.setattr(driver.risk_config, "inherited_labels_mode", lambda: "on")
    _unready_prefix_then_ready_leg(configured_db)
    arm = priority_decision.PriorityDecisionArm(make_risk_observer(_FakeOpaClient()))
    arm.want(_B)
    assert arm.serve() == 0 and _B in arm.pending, "classified, but label lineage has not derived it"
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO leg_inherited_labels (interaction_id, leg_type, trace_id, payload_hash, seq, matcher, "
            "classified) VALUES (%s, 'request', 't', 'h-b', 0, 'exact', true)", (_B,))
        conn.commit()
    assert arm.serve() == 1


def test_the_notify_drives_the_arm(configured_db: str, monkeypatch) -> None:
    """The listener thread, the real channel: a notify naming the interaction is
    answered with its record while the cursor arm is not running at all."""
    monkeypatch.setattr(priority_decision, "IDLE_SECONDS", 0.5)
    _unready_prefix_then_ready_leg(configured_db)
    stop = threading.Event()
    thread = priority_decision.start(make_risk_observer(_FakeOpaClient()), stop, configured_db)
    try:
        time.sleep(0.5)  # the LISTEN is registered
        with psycopg.connect(configured_db, autocommit=True) as conn:
            conn.execute("SELECT pg_notify(%s, %s)", (priority_decision.PRIORITY_DECISION_CHANNEL, _B))
        deadline = time.monotonic() + 5.0
        while not _risk_versions(configured_db, _B) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _risk_versions(configured_db, _B) == [(1, ["request"])]
        assert _cursor(configured_db) == 0
    finally:
        stop.set()
        thread.join(timeout=10)


def test_a_priority_turn_goes_first() -> None:
    turns = driver.Turns()
    order: list[str] = []
    holding = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with turns.cursor():
            holding.set()
            release.wait(5)

    def cursor_turn() -> None:
        with turns.cursor():
            order.append("cursor")

    def priority_turn() -> None:
        with turns.priority_decision():
            order.append("priority")

    h = threading.Thread(target=holder)
    h.start()
    assert holding.wait(5)
    c = threading.Thread(target=cursor_turn)
    c.start()
    time.sleep(0.1)
    w = threading.Thread(target=priority_turn)
    w.start()
    time.sleep(0.1)
    release.set()
    for t in (h, c, w):
        t.join(5)
    assert order == ["priority", "cursor"]
