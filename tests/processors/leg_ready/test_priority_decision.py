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
- with inherited labels on, the arm waits for label lineage, except for a block on the
  classification alone (the early block): released at once, asked once, never an allow,
  never over a record that exists, fenced and retried like any write;
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


def test_the_recompute_arm_is_fenced_on_a_write_that_left_the_cursor_in_place(configured_db: str) -> None:
    """The recompute arm's write is fenced on the stamp too, not the seq alone: a replacement's
    priority decision or recompute leaves the seq where it was."""
    _insert_leg(configured_db, interaction_id=_B, leg_type="request", payload_hash=None)
    real = make_risk_observer(_FakeOpaClient())
    cursor = driver.drain(0, observer=real)
    assert cursor > 0 and _risk_versions(configured_db, _B) == [(1, ["request"])]
    with psycopg.connect(configured_db) as conn:
        conn.execute("INSERT INTO risk_recompute_requests (interaction_id, reason, base_recompute) "
                     "VALUES (%s, 'test', true)", (_B,))
        conn.commit()

    def observer(leg):
        write = real(leg)
        with psycopg.connect(configured_db) as conn:
            conn.execute("UPDATE processor_state SET updated_at = clock_timestamp() WHERE processor_name = 'leg_ready'")
            conn.commit()
        return write

    with pytest.raises(db.LockLost):
        driver._drain_recompute_requests(cursor, observer, None, 10)
    assert _risk_versions(configured_db, _B) == [(1, ["request"])], "the stalled computation did not land"
    with psycopg.connect(configured_db) as conn:
        left = conn.execute("SELECT count(*) FROM risk_recompute_requests WHERE interaction_id = %s", (_B,)).fetchone()[0]
    assert left == 1, "the request stays for the next wake"


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
            "classified) VALUES (%s, 'request', 't-158', 'h-a', 0, 'exact', true)", (_A,))
        # A structural request (base_recompute): the recompute arm serves it whatever
        # RISK_INHERITED_LABELS says; a label-only one waits for the flag.
        conn.execute("INSERT INTO risk_recompute_requests (interaction_id, reason, base_recompute) "
                     "VALUES (%s, 'test', true)", (_A,))
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
            "classified) VALUES (%s, 'request', 't-other', 'h-b', 0, 'exact', true)", (_B,))
        conn.commit()
    assert arm.serve() == 0, "a row derived under another trace is not this leg's: the stream's rule"
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE leg_inherited_labels SET trace_id = 't-158' WHERE interaction_id = %s", (_B,))
        conn.execute("INSERT INTO label_lineage_dirty_traces (trace_id) VALUES ('t-158')")
        conn.commit()
    assert arm.serve() == 0, "the trace has a label correction pending: the stream's rule"
    with psycopg.connect(configured_db) as conn:
        conn.execute("DELETE FROM label_lineage_dirty_traces WHERE trace_id = 't-158'")
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


# --- the early block ---------------------------------------------------------------------------------
# A decision is released when it is a block, or when the evidence is complete.

from data_governance.processors.leg_ready import metrics  # noqa: E402
from data_governance.risk.engine.observer import make_early_block_observer  # noqa: E402

from .test_risk_observer import _decision  # noqa: E402


class _Opa(_FakeOpaClient):
    """A policy that answers *enforcement* on whatever it is asked."""

    def __init__(self, enforcement: str = "allow") -> None:
        super().__init__()
        self.enforcement = enforcement

    def evaluate(self, **kwargs):
        super().evaluate(**kwargs)
        return _decision(enforcement_type=self.enforcement)


def _early_arm(opa, monkeypatch) -> priority_decision.PriorityDecisionArm:
    """The arm as leg-ready starts it with inherited labels on: both observers on one policy."""
    monkeypatch.setattr(driver.risk_config, "inherited_labels_mode", lambda: "on")
    return priority_decision.PriorityDecisionArm(make_risk_observer(opa), early=make_early_block_observer(opa))


def _derive(dsn: str, interaction_id: str = _B, payload_hash: str = "h-b") -> None:
    """Label lineage derived the leg (no labels to inherit)."""
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO leg_inherited_labels (interaction_id, leg_type, trace_id, payload_hash, seq, matcher, "
            "classified) VALUES (%s, 'request', 't-158', %s, 0, 'exact', true)", (interaction_id, payload_hash))
        conn.commit()


def _records(dsn: str, interaction_id: str = _B) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT r.version, r.enforcement_type, t.tier, t.late FROM interaction_risk_records r "
            "LEFT JOIN interaction_risk_tiers t USING (interaction_id, version) "
            "WHERE r.interaction_id = %s ORDER BY r.version", (interaction_id,)).fetchall()


def test_a_classifier_block_is_released_before_label_lineage(configured_db: str, monkeypatch) -> None:
    metrics.make_registry()
    _unready_prefix_then_ready_leg(configured_db)
    opa = _Opa("block")
    arm = _early_arm(opa, monkeypatch)
    arm.want(_B)
    assert arm.serve() == 1, "classified, label lineage has not derived it, and the policy blocks"
    assert _records(configured_db) == [(1, "block", 0, False)]
    assert _B not in arm.pending and arm.asked_early == {} and len(opa.calls) == 1
    assert metrics.early_blocks_total._value.get() == 1


def test_an_early_allow_is_not_released_and_is_asked_once(configured_db: str, monkeypatch) -> None:
    _unready_prefix_then_ready_leg(configured_db)
    opa = _Opa("allow")
    arm = _early_arm(opa, monkeypatch)
    arm.want(_B)
    for _ in range(3):
        assert arm.serve() == 0
    assert _records(configured_db) == [] and _B in arm.pending
    assert len(opa.calls) == 1, "asked once on this payload, however many re-checks pass"
    with psycopg.connect(configured_db) as conn:
        assert conn.execute("SELECT count(*) FROM interaction_policy_decisions").fetchone()[0] == 0
    _derive(configured_db)
    assert arm.serve() == 1
    assert _records(configured_db) == [(1, "allow", 1, False)]
    assert len(opa.calls) == 2 and arm.asked_early == {}


def test_with_everything_ready_there_is_no_early_question(configured_db: str, monkeypatch) -> None:
    _unready_prefix_then_ready_leg(configured_db)
    _derive(configured_db)
    opa = _Opa("block")
    asked: list[str] = []
    monkeypatch.setattr(driver.risk_config, "inherited_labels_mode", lambda: "on")
    early = make_early_block_observer(opa)
    arm = priority_decision.PriorityDecisionArm(
        make_risk_observer(opa), early=lambda leg: asked.append(leg.interaction_id) or early(leg))
    arm.want(_B)
    assert arm.serve() == 1 and asked == []
    assert _records(configured_db) == [(1, "block", 0, False)] and len(opa.calls) == 1


def test_readiness_is_read_again_inside_the_turn(configured_db: str, monkeypatch) -> None:
    """The look that takes the turn saw "classified only"; by the time the turn is held, label lineage has
    derived the leg and the full record is written. The arm runs the full computation on what is there now, and
    the fuller record stays the latest."""
    _unready_prefix_then_ready_leg(configured_db)
    opa = _Opa("allow")
    arm = _early_arm(opa, monkeypatch)
    with db.transaction() as tx:
        stale = driver.one_leg(tx, _B, "request")
    assert stale.classified and not stale.ready
    _derive(configured_db)
    with db.transaction() as tx:
        make_risk_observer(opa)(driver.one_leg(tx, _B, "request"))(tx)        # the cursor arm got there first
    assert _records(configured_db) == [(1, "allow", 1, False)]
    opa.enforcement = "block"          # what an early question would answer, were it asked
    arm.want(_B)
    assert arm._compute(stale) is True
    assert _records(configured_db) == [(1, "allow", 1, False)], "the full record was not pushed aside"
    assert len(opa.calls) == 1, "no early question: the full computation ran, on its cached decision"


def test_a_fenced_out_early_block_is_asked_again(configured_db: str, monkeypatch) -> None:
    _unready_prefix_then_ready_leg(configured_db)
    opa = _Opa("block")
    monkeypatch.setattr(driver.risk_config, "inherited_labels_mode", lambda: "on")
    real = make_early_block_observer(opa)
    moved: list[bool] = []

    def early(leg):
        write = real(leg)
        if not moved:
            moved.append(True)
            with psycopg.connect(configured_db) as conn:
                conn.execute(
                    "INSERT INTO processor_state (processor_name, last_processed_seq, updated_at) "
                    "VALUES ('leg_ready', 99, now()) ON CONFLICT (processor_name) "
                    "DO UPDATE SET last_processed_seq = 99")
                conn.commit()
        return write

    arm = priority_decision.PriorityDecisionArm(make_risk_observer(opa), early=early)
    arm.want(_B)
    assert arm.serve() == 0 and _records(configured_db) == []
    assert _B in arm.pending and _B not in arm.asked_early, "not remembered: the write did not land"
    assert arm.serve() == 1 and _records(configured_db) == [(1, "block", 0, False)]


def test_an_opa_outage_at_the_early_question_keeps_the_id_unasked(configured_db: str, monkeypatch) -> None:
    _unready_prefix_then_ready_leg(configured_db)
    opa = _Opa("block")
    opa.fail_with = OpaRequestError("OPA unreachable (injected)")
    arm = _early_arm(opa, monkeypatch)
    arm.want(_B)
    assert arm.serve() == 0 and _B in arm.pending and arm.asked_early == {}
    opa.fail_with = None
    assert arm.serve() == 1


def test_what_was_asked_early_goes_with_its_pending_entry(configured_db: str, monkeypatch) -> None:
    _unready_prefix_then_ready_leg(configured_db)
    opa = _Opa("allow")
    arm = _early_arm(opa, monkeypatch)
    arm.want(_B, now=0.0)
    assert arm.serve(now=1.0) == 0 and arm.asked_early == {_B: "h-b"}
    arm.asked_early[_B] = "another-payload"            # the request leg's payload changed since it was asked
    assert arm.serve(now=2.0) == 0 and len(opa.calls) == 2 and arm.asked_early == {_B: "h-b"}
    assert arm.serve(now=priority_decision.TTL_SECONDS + 1.0) == 0
    assert arm.pending == {} and arm.asked_early == {}, "expired together"
    monkeypatch.setattr(priority_decision, "MAX_PENDING", 1)
    arm.want(_B)
    arm.serve()
    assert _B in arm.asked_early
    arm.want(_A)
    assert list(arm.pending) == [_A] and arm.asked_early == {}, "evicted together"


def test_without_the_early_observer_the_arm_waits_as_before(configured_db: str, monkeypatch) -> None:
    """``LEG_READY_EARLY_BLOCK=off``: no early observer is handed to the arm."""
    monkeypatch.setattr(driver.risk_config, "inherited_labels_mode", lambda: "on")
    _unready_prefix_then_ready_leg(configured_db)
    opa = _Opa("block")
    arm = priority_decision.PriorityDecisionArm(make_risk_observer(opa))
    arm.want(_B)
    assert arm.serve() == 0 and opa.calls == [] and _records(configured_db) == []


def test_with_inherited_labels_off_a_classified_leg_is_a_ready_leg(configured_db: str) -> None:
    _unready_prefix_then_ready_leg(configured_db)
    opa = _Opa("allow")
    asked: list[str] = []
    arm = priority_decision.PriorityDecisionArm(make_risk_observer(opa), early=lambda leg: asked.append("early"))
    arm.want(_B)
    assert arm.serve() == 1 and asked == [] and _records(configured_db) == [(1, "allow", 0, False)]


def test_a_classifier_block_is_released_while_a_correction_is_pending(configured_db: str, monkeypatch) -> None:
    """Label lineage derived the leg, and the trace has a correction pending: not ready, still classified."""
    _unready_prefix_then_ready_leg(configured_db)
    _derive(configured_db)
    with psycopg.connect(configured_db) as conn:
        conn.execute("INSERT INTO label_lineage_dirty_traces (trace_id) VALUES ('t-158')")
        conn.commit()
    arm = _early_arm(_Opa("block"), monkeypatch)
    arm.want(_B)
    assert arm.serve() == 1 and _records(configured_db) == [(1, "block", 0, False)]


def test_an_early_block_leaves_the_recompute_queue_alone(configured_db: str, monkeypatch) -> None:
    _unready_prefix_then_ready_leg(configured_db)
    with psycopg.connect(configured_db) as conn:
        conn.execute("INSERT INTO risk_recompute_requests (interaction_id, reason, labels_pending) "
                     "VALUES (%s, 'inherited labels changed', true)", (_B,))
        conn.commit()
        before = conn.execute("SELECT to_jsonb(r)::text FROM risk_recompute_requests r").fetchall()
    arm = _early_arm(_Opa("block"), monkeypatch)
    arm.want(_B)
    assert arm.serve() == 1
    with psycopg.connect(configured_db) as conn:
        assert conn.execute("SELECT to_jsonb(r)::text FROM risk_recompute_requests r").fetchall() == before


def test_an_unclassified_leg_is_not_asked_early(configured_db: str, monkeypatch) -> None:
    """The early question needs the classification: a leg whose payload is not classified yet is neither asked
    nor remembered as asked, so the early block still fires when the classification lands."""
    _unready_prefix_then_ready_leg(configured_db)        # _A's payload h-a is not classified
    opa = _Opa("block")
    arm = _early_arm(opa, monkeypatch)
    with db.transaction() as tx:
        leg = driver.one_leg(tx, _A, "request")
    assert not leg.classified and not leg.ready
    arm.want(_A)
    assert arm.serve() == 0 and opa.calls == [] and arm.asked_early == {} and _records(configured_db, _A) == []
    assert arm._compute(leg) is False and opa.calls == [], "nor when a stale look takes the turn"
    _classify(configured_db, "h-a")
    assert arm.serve() == 1 and _records(configured_db, _A) == [(1, "block", 0, False)]


def test_a_second_ask_after_an_early_block_writes_nothing_and_says_so(configured_db: str, monkeypatch, caplog) -> None:
    """The verdict service names a waited exchange more than once. The second early block finds the record of the
    first and writes nothing; the log does not say it blocked early again, and the counter does not move."""
    import logging
    metrics.make_registry()
    _unready_prefix_then_ready_leg(configured_db)
    arm = _early_arm(_Opa("block"), monkeypatch)
    caplog.set_level(logging.INFO, logger=priority_decision.log.name)
    for _ in range(2):
        arm.want(_B)
        assert arm.serve() == 1
    assert _records(configured_db) == [(1, "block", 0, False)] and metrics.early_blocks_total._value.get() == 1
    lines = [r.getMessage() for r in caplog.records if _B in r.getMessage()]
    assert sum("blocked early" in m for m in lines) == 1 and sum("wrote nothing" in m for m in lines) == 1
