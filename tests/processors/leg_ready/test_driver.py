"""Readiness-drain loop for the P-leg-ready consumer (issue #123, ADR-0027).

The leg-ready consumer is the readiness-gated half of ADR-0027 (the entity-ready
consumer, #121, is the ungated half). It drains ``interaction_legs`` by ``seq``,
computes each leg's **Leg readiness** (``payload_hash IS NULL`` OR a
``payload_classifications`` row exists for its hash) against the DB, feeds the
seq-ordered batch through the contiguous-prefix primitive (:mod:`..readiness_cursor`),
delivers the leading unbroken run of ready legs in seq order, and advances a plain
single-BIGINT ``leg_ready`` cursor to the last delivered leg's ``seq``.

This is the **one place** the shared ``_driver.drain`` is not reused verbatim: the
readiness predicate is non-monotonic in ``seq`` (a low-``seq`` unready leg can sit
behind a high-``seq`` ready one), so the drain must stop at the first unready leg
rather than advance to the max seq (head-of-line blocking). It DOES reuse
``_driver.read_cursor`` / ``advance_cursor`` (the plain-BIGINT cursor now round-trips
directly, per the reversal) and ``_driver.run``'s LISTEN/poll wake machinery.

Since the reversal (Part 1) each leg has its OWN distinct ``seq`` (request leg
inserted first → lower seq), so ``seq`` alone totally orders the legs: request is
delivered before its response purely because its seq is lower.

These tests prove the acceptance criteria:

- a no-payload leg is ready at write time and delivered;
- a payload-bearing leg is WITHHELD until its payload is classified, then delivered;
- the request leg is delivered before its response leg (lower seq);
- a low-``seq`` unready leg is NOT stranded behind a higher-``seq`` ready leg
  (a two-drain sequence: the ready high leg is held, then both advance once the
  low leg readies);
- each leg is delivered exactly once; a restart re-reading the durable cursor
  resumes with no duplicate and no miss;
- determinism across a cursor-reset re-drain.

The drain tests run in-process against a migrated DB (fast), mirroring
``tests/processors/entity_ready/test_driver.py``.
"""

from __future__ import annotations

import threading
import time

import psycopg
import pytest

from data_governance import db
from data_governance.processors.leg_ready import driver


# --- helpers -----------------------------------------------------------------


def _mk_interaction(conn: psycopg.Connection, ix_id: str) -> None:
    """A parent interaction row the legs hang off (FK-free, but keeps the shape
    realistic). Needs two entities for caller/callee."""
    for eid in (f"{ix_id}-caller", f"{ix_id}-callee"):
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, original_seq) VALUES (%s, 'agent', %s, 'n', 'd', 1) "
            "ON CONFLICT (natural_key) DO NOTHING",
            (eid, eid),
        )
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, "
        "callee_entity_id, summary) VALUES (%s, 't', %s, %s, 's') "
        "ON CONFLICT (id) DO NOTHING",
        (ix_id, f"{ix_id}-caller", f"{ix_id}-callee"),
    )


def _insert_leg(
    dsn: str,
    *,
    interaction_id: str,
    leg_type: str,
    payload_hash: str | None,
) -> int:
    """Insert one leg with a DB-owned (DEFAULT nextval) seq, exactly as the
    streaming ``state.flush`` does. Returns the allocated ``seq``."""
    with psycopg.connect(dsn) as conn:
        _mk_interaction(conn, interaction_id)
        (seq,) = conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, "
            "occurred_at, payload_hash, error, original_seq) "
            "VALUES (%s, %s, now(), %s, false, 0) RETURNING seq",
            (interaction_id, leg_type, payload_hash),
        ).fetchone()
        conn.commit()
    return int(seq)


def _classify(dsn: str, content_hash: str) -> None:
    """Write a payload_classifications row for *content_hash* (a clean PUBLIC
    verdict), making any leg that references it ready."""
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO payload_classifications "
            "(content_hash, sensitivity_level, regulatory_tags, "
            " contains_identity_bundle, is_personalized, primary_domain, "
            " findings, model_version) "
            "VALUES (%s, 'PUBLIC', '{}', false, false, NULL, '[]'::jsonb, 1) "
            "ON CONFLICT (content_hash) DO NOTHING",
            (content_hash,),
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
    """A stand-in downstream governance consumer: records the (seq, leg_type) of
    every leg delivered to it, so a test can assert exactly-once + ordering."""

    def __init__(self) -> None:
        self.delivered: list[tuple[int, str]] = []

    def __call__(self, leg: "driver.Leg") -> None:
        self.delivered.append((leg.seq, leg.leg_type))


# --- readiness gate: no-payload ready now, payload-bearing withheld ----------


def test_no_payload_leg_is_ready_at_write_time(configured_db: str) -> None:
    """A leg with ``payload_hash IS NULL`` has nothing to classify, so it is ready
    the moment it is written and is delivered by the first drain."""
    s0 = _insert_leg(
        configured_db, interaction_id="i0", leg_type="request", payload_hash=None
    )
    sink = _Sink()
    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    new_cursor = driver.drain(cursor, observer=sink)

    assert sink.delivered == [(s0, "request")]
    assert new_cursor == s0
    assert _cursor(configured_db) == s0


def test_payload_bearing_leg_withheld_until_classified_then_delivered(
    configured_db: str,
) -> None:
    """A payload-bearing leg is NOT ready (not delivered, cursor not advanced past
    it) until a ``payload_classifications`` row exists for its hash; once the
    payload is classified the next drain delivers it."""
    s0 = _insert_leg(
        configured_db, interaction_id="i0", leg_type="request", payload_hash="h0"
    )
    sink = _Sink()

    # Drain 1: payload not yet classified → withheld, cursor held at 0.
    c1 = driver.drain(0, observer=sink)
    assert sink.delivered == [], "unclassified payload leg must be withheld"
    assert c1 == 0

    # Classify the payload → the leg becomes ready.
    _classify(configured_db, "h0")

    # Drain 2: re-fetches WHERE seq > 0, now ready → delivered.
    c2 = driver.drain(c1, observer=sink)
    assert sink.delivered == [(s0, "request")]
    assert c2 == s0


# --- request before response (by seq) ----------------------------------------


def test_request_delivered_before_response_by_seq(configured_db: str) -> None:
    """Within one interaction the request leg (lower DB-owned seq — inserted first)
    is delivered before the response leg. Both no-payload (ready at write time)."""
    req = _insert_leg(
        configured_db, interaction_id="i0", leg_type="request", payload_hash=None
    )
    resp = _insert_leg(
        configured_db, interaction_id="i0", leg_type="response", payload_hash=None
    )
    assert req < resp, "request leg must have the lower seq (inserted first)"

    sink = _Sink()
    driver.drain(0, observer=sink)
    assert sink.delivered == [(req, "request"), (resp, "response")]


# --- stranding: low-seq unready must not be jumped ---------------------------


def test_low_seq_unready_leg_not_stranded_behind_high_seq_ready(
    configured_db: str,
) -> None:
    """The headline readiness-cursor acceptance case. A higher-``seq`` ready leg
    must NOT advance the cursor past a lower-``seq`` unready leg; when the low leg
    later becomes ready, a subsequent drain advances across BOTH — never skipping
    the low leg (head-of-line blocking).
    """
    # low seq: payload-bearing, NOT yet classified (unready).
    low = _insert_leg(
        configured_db, interaction_id="lo", leg_type="request", payload_hash="hlo"
    )
    # high seq: no payload (ready now) — but must be HELD behind the unready low.
    high = _insert_leg(
        configured_db, interaction_id="hi", leg_type="request", payload_hash=None
    )
    assert low < high

    sink = _Sink()
    # Drain 1: low is unready and is the first item in seq order → nothing
    # delivered, cursor held at 0 (the ready high leg is NOT jumped to).
    c1 = driver.drain(0, observer=sink)
    assert sink.delivered == [], "a ready high-seq leg must be held behind an unready low one"
    assert c1 == 0

    # The low leg's payload classifies → it becomes ready.
    _classify(configured_db, "hlo")

    # Drain 2: both advance, low before high (seq order).
    c2 = driver.drain(c1, observer=sink)
    assert sink.delivered == [(low, "request"), (high, "request")]
    assert c2 == high


# --- exactly-once + restart --------------------------------------------------


def test_leg_delivered_exactly_once_across_restart(configured_db: str) -> None:
    """A ready leg is delivered exactly once; after a simulated restart (re-reading
    the durable cursor from ``processor_state``) the drain resumes with no
    duplicate and no miss."""
    s0 = _insert_leg(configured_db, interaction_id="a", leg_type="request", payload_hash=None)
    s1 = _insert_leg(configured_db, interaction_id="b", leg_type="request", payload_hash=None)

    sink1 = _Sink()
    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    driver.drain(cursor, observer=sink1)
    assert sink1.delivered == [(s0, "request"), (s1, "request")]

    # A third leg arrives after the first run.
    s2 = _insert_leg(configured_db, interaction_id="c", leg_type="request", payload_hash=None)

    # Simulated restart: re-read the durable cursor, then drain.
    sink2 = _Sink()
    with db.transaction() as tx:
        resumed = driver.read_cursor(tx)
    assert resumed == s1, "restart must resume from the durable cursor"
    driver.drain(resumed, observer=sink2)
    assert sink2.delivered == [(s2, "request")], "no duplicate, no miss"


def test_restart_after_full_drain_delivers_nothing(configured_db: str) -> None:
    """After a full drain, a restart re-reading the durable cursor with no new
    ready legs delivers nothing (the cursor is the complete delivery record)."""
    _insert_leg(configured_db, interaction_id="only", leg_type="request", payload_hash=None)
    driver.drain(0, observer=_Sink())

    sink = _Sink()
    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    driver.drain(cursor, observer=sink)
    assert sink.delivered == []


# --- determinism across a cursor-reset re-drain ------------------------------


def test_deterministic_across_cursor_reset_redrain(configured_db: str) -> None:
    """Resetting the durable cursor to 0 and re-draining the same legs delivers the
    identical sequence — the drain is a pure function of (legs, classifications,
    cursor)."""
    req = _insert_leg(configured_db, interaction_id="i0", leg_type="request", payload_hash="h0")
    resp = _insert_leg(configured_db, interaction_id="i0", leg_type="response", payload_hash=None)
    _classify(configured_db, "h0")

    first = _Sink()
    driver.drain(0, observer=first)

    # Reset the durable cursor and re-drain from 0.
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "UPDATE processor_state SET last_processed_seq = 0 "
            "WHERE processor_name = %s",
            (driver.PROCESSOR_NAME,),
        )
        conn.commit()
    second = _Sink()
    driver.drain(0, observer=second)

    assert second.delivered == first.delivered == [(req, "request"), (resp, "response")]


# --- crash mid-item: cursor advances atomically with delivery ----------------


def test_crash_mid_leg_re_processes_from_same_cursor(configured_db: str) -> None:
    """ADR-0007 atomicity: the cursor advance commits in the SAME transaction as
    the per-leg delivery. A crash mid-leg rolls back the cursor advance, so the
    restart re-delivers that leg from the same cursor — never skipping it."""
    good = _insert_leg(configured_db, interaction_id="good", leg_type="request", payload_hash=None)
    boom = _insert_leg(configured_db, interaction_id="boom", leg_type="request", payload_hash=None)

    def _observer(leg: "driver.Leg") -> None:
        if leg.seq == boom:
            raise RuntimeError("crash mid-leg")

    with pytest.raises(RuntimeError, match="crash mid-leg"):
        driver.drain(0, observer=_observer)

    # "good" committed its cursor advance; "boom" rolled back.
    assert _cursor(configured_db) == good

    sink = _Sink()
    driver.drain(_cursor(configured_db), observer=sink)
    assert sink.delivered == [(boom, "request")], "recovery re-delivers boom, not good"


# --- run(): wakes on both the LISTEN notification and the poll backstop -------


def _drain_via_run(
    dsn: str, *, disable_listen: bool, monkeypatch, insert_after_start: bool
) -> list[int]:
    """Drive the real ``run()`` loop in a thread; return the seqs delivered."""
    delivered: list[int] = []
    lock = threading.Lock()

    def _observer(leg: "driver.Leg") -> None:
        with lock:
            delivered.append(leg.seq)

    monkeypatch.setattr(driver, "POLL_SECONDS", 0.2)

    if disable_listen:
        def _boom(channel, dsn):  # noqa: ANN001 — test stub
            raise db.ConnectionTimeout("listen disabled (injected)")

        monkeypatch.setattr(db, "listen", _boom)

    holder: dict[str, int] = {}
    if not insert_after_start:
        holder["seq"] = _insert_leg(dsn, interaction_id="pre", leg_type="request", payload_hash=None)

    stop = threading.Event()
    t = threading.Thread(
        target=driver.run, args=(stop, dsn), kwargs={"observer": _observer}, daemon=True
    )
    t.start()
    try:
        if insert_after_start:
            time.sleep(0.5)
            holder["seq"] = _insert_leg(dsn, interaction_id="post", leg_type="request", payload_hash=None)
        target = holder["seq"]
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            with lock:
                if target in delivered:
                    break
            time.sleep(0.05)
    finally:
        stop.set()
        t.join(timeout=10)
        assert not t.is_alive(), "run() must return promptly on stop_event"
    with lock:
        return list(delivered)


def test_run_wakes_on_notify_and_delivers(configured_db: str, monkeypatch) -> None:
    """``run()`` with LISTEN active delivers a leg written AFTER the loop started —
    the ``dg_interaction_leg_ready`` notification wakes the drain (low latency)."""
    delivered = _drain_via_run(
        configured_db, disable_listen=False, monkeypatch=monkeypatch, insert_after_start=True
    )
    assert delivered, "run() must deliver a leg written after start (notify wake)"


def test_run_poll_only_still_delivers(configured_db: str, monkeypatch) -> None:
    """With LISTEN disabled, ``run()`` falls back to the poll backstop and still
    delivers — correctness never depends on the notification (latency-only)."""
    delivered = _drain_via_run(
        configured_db, disable_listen=True, monkeypatch=monkeypatch, insert_after_start=True
    )
    assert delivered, "poll backstop must deliver even with LISTEN disabled"


def test_run_drains_backlog_before_waiting(configured_db: str, monkeypatch) -> None:
    """``run()`` drains legs already past the cursor before it starts waiting, so a
    leg that landed before the loop started is not stranded until the first poll."""
    delivered = _drain_via_run(
        configured_db, disable_listen=False, monkeypatch=monkeypatch, insert_after_start=False
    )
    assert delivered, "run() must drain the pre-existing backlog"
