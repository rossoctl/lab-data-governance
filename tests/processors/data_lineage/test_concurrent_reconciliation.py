"""Sidecar reconciliation and data-lineage derivation of ONE trace, run concurrently.

The sidecar reconciler (``interactions/sidecar.py``) deletes a trace's stale
interactions and legs; the data-lineage processor re-derives the whole trace in one
transaction. Two things must hold for every interleaving of the two:

- **Neither transaction dies with** ``DeadlockDetected``. Neither processor loop
  catches 40P01, so a deadlock victim crash-loops its pod.
- **No** ``lineage_metadata`` **row outlives its leg.** Migration 0026's delete
  triggers remove a deleted leg's lineage row, and ``_delete_stale`` reaches rows only
  through ``interactions``, so a lineage row written after the trigger ran is orphaned
  for good (ADR-0035).

Each case pauses one side mid-transaction: a third session holds a row lock that side
needs. The other side starts only once ``pg_stat_activity`` shows the first waiting
on that session; the holder is released only once the second side is waiting too (or
has finished). Sequencing is by observed lock waits, never by sleeping. The real
functions run on both sides — ``sidecar.derive_trace`` and ``driver.process_leg`` —
each in its own connection so the test knows the backend it is watching.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from collections.abc import Callable
from typing import Any

import psycopg
import pytest

from data_governance import db
from data_governance.matching import get_matcher
from data_governance.processors.data_lineage import driver as lineage_driver
from data_governance.processors.interactions import sidecar, sidecar_driver
from data_governance.processors.interactions.procedure import _interaction_id

from tests.processors.interactions import sidecar_golden as golden

TRACE = golden.TRACE
# Interaction A: the golden entry exchange. Its spans exist, so reconciliation keeps it.
KEPT = _interaction_id(TRACE, golden.A1)
# Interaction X: a child of A that no stored span justifies (the reconcile's shrink
# case, e.g. a demoted entry). Reconciliation deletes it and both its legs. Its id
# sorts after A's, so a key-ordered reader reaches A's rows first.
STALE = _interaction_id(TRACE, "e0e0e0e0e0e0e0e0")
_STALE_TOOL = "e_stale_tool"
_T0 = dt.datetime(2026, 7, 21, 12, 0, 0, tzinfo=dt.timezone.utc)

# Bound on every wait. Generous: a pass never comes near it, and a hang must fail
# rather than stall the run.
_TIMEOUT = 15.0
# Poll cadence while waiting for a backend to show a lock wait. Only the cadence:
# what the test does next depends on the observed state, never on elapsed time.
_POLL_INTERVAL = 0.005


# --- seed ---------------------------------------------------------------------


def _lineage_keys(dsn: str) -> set[tuple[str, str]]:
    with psycopg.connect(dsn) as conn:
        return {
            (r[0], r[1])
            for r in conn.execute(
                "SELECT interaction_id, leg_type::text FROM lineage_metadata"
            ).fetchall()
        }


def _seed(dsn: str) -> int:
    """Derive the golden entry exchange A, add the stale child X, derive lineage for
    the whole trace, and return the data-lineage cursor.

    X's legs are inserted response first, so a physical-order scan (the reconciler's
    multi-row DELETE) reaches X's response before its request. A key-ordered reader
    reaches X's request first. That opposite order is the lock cycle PR #281's
    review reproduced.
    """
    golden.insert_subset(dsn, [golden.A1, golden.A2], 1)
    sidecar_driver.drain(0)
    with psycopg.connect(dsn) as conn:
        (agent,) = conn.execute(
            "SELECT callee_entity_id FROM interactions WHERE id = %s", (KEPT,)
        ).fetchone()
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, detected_from, "
            "seq, original_seq) VALUES (%s, 'tool', 'tool:team1/stale-tool', "
            "'stale-tool', 'observed', 1, 1)",
            (_STALE_TOOL,),
        )
        conn.execute(
            "INSERT INTO interactions (id, trace_id, parent_interaction_id, "
            "caller_entity_id, callee_entity_id, summary) VALUES (%s, %s, %s, %s, %s, %s)",
            (STALE, TRACE, KEPT, agent, _STALE_TOOL, "weather-service → stale-tool"),
        )
        for leg_type, body, offset in (
            ("response", "x-response-body", 2),
            ("request", "x-request-body", 1),
        ):
            conn.execute(
                "INSERT INTO interaction_payloads (content_hash, content, byte_size) "
                "VALUES (%s, %s::jsonb, 1)",
                (body, json.dumps(body)),
            )
            conn.execute(
                "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
                "payload_hash) VALUES (%s, %s, %s, %s)",
                (STALE, leg_type, _T0 + dt.timedelta(seconds=offset), body),
            )
        conn.commit()
    cursor = lineage_driver.drain(0)
    assert _lineage_keys(dsn) == {
        (KEPT, "request"), (KEPT, "response"), (STALE, "request"), (STALE, "response"),
    }
    return cursor


# --- the two sides ------------------------------------------------------------


def _reconcile(tx: db.Transaction) -> None:
    """P-interactions' per-span work under the sidecar algorithm."""
    sidecar.derive_trace(tx, TRACE)


def _derive_lineage(tx: db.Transaction) -> None:
    """P-data-lineage's per-item work; every arm (cursor, dirty, stale) runs this."""
    lineage_driver.process_leg(
        tx, lineage_driver.ArrivingLeg(trace_id=TRACE, seq=0), get_matcher()
    )


_WORK: dict[str, Callable[[db.Transaction], None]] = {
    "reconcile": _reconcile,
    "lineage": _derive_lineage,
}


class _Side:
    """One processor transaction on its own connection and thread."""

    def __init__(self, dsn: str, name: str) -> None:
        self.name = name
        self.conn = psycopg.connect(dsn)
        self.pid = self.conn.info.backend_pid
        self.error: BaseException | None = None
        self.committed = False
        self._work = _WORK[name]
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def _run(self) -> None:
        try:
            self._work(db.Transaction(self.conn))
            self.conn.commit()
            self.committed = True
        except BaseException as exc:  # noqa: BLE001 — reported by the test
            self.error = exc
            try:
                self.conn.rollback()
            except psycopg.Error:
                pass

    def start(self) -> None:
        self._thread.start()

    def done(self) -> bool:
        return self._thread.ident is not None and not self._thread.is_alive()

    def join(self) -> bool:
        self._thread.join(_TIMEOUT)
        return not self._thread.is_alive()


def _lock_wait(probe: psycopg.Connection, pid: int) -> tuple[str, list[int]] | None:
    """``(wait_event, blocking pids)`` while *pid* waits on a heavyweight lock."""
    row = probe.execute(
        "SELECT wait_event_type, wait_event, pg_blocking_pids(pid) "
        "FROM pg_stat_activity WHERE pid = %s",
        (pid,),
    ).fetchone()
    if row is None or row[0] != "Lock":
        return None
    return row[1], list(row[2])


def _await_lock_wait(
    probe: psycopg.Connection, side: _Side, *, blocked_by: int | None = None,
) -> tuple[str, list[int]] | None:
    """Poll until *side* waits on a lock (held by *blocked_by*, when given).

    Returns the observed wait, or ``None`` when *side* finished without one. A side
    that is expected to pause (``blocked_by`` given) must not finish first.
    """
    deadline = time.monotonic() + _TIMEOUT
    while time.monotonic() < deadline:
        wait = _lock_wait(probe, side.pid)
        if wait is not None and (blocked_by is None or blocked_by in wait[1]):
            return wait
        if side.done():
            if blocked_by is not None:
                pytest.fail(
                    f"{side.name} finished before pausing on pid {blocked_by}: "
                    f"{side.error!r}"
                )
            return None
        time.sleep(_POLL_INTERVAL)
    pytest.fail(f"{side.name} (pid {side.pid}) never waited on a lock")


# --- interleavings ------------------------------------------------------------

# Each case names the side that starts first and the row locks a third session holds
# to pause it part-way through its transaction.
_INTERLEAVINGS = [
    # Reconciliation deletes X's response leg, then waits for X's request leg. A
    # key-ordered reader would lock X's request and wait for X's response.
    pytest.param(
        "reconcile",
        [(
            "SELECT 1 FROM interaction_legs WHERE interaction_id = %s "
            "AND leg_type = 'request' FOR KEY SHARE",
            (STALE,),
        )],
        id="reconcile-paused-between-the-stale-legs",
    ),
    # Reconciliation has deleted both legs, and their delete triggers have already
    # removed X's lineage rows; it waits to delete interaction X. A lineage write
    # landing now would be orphaned.
    pytest.param(
        "reconcile",
        [("SELECT 1 FROM interactions WHERE id = %s FOR KEY SHARE", (STALE,))],
        id="reconcile-paused-after-the-leg-delete-triggers",
    ),
    # The lineage derivation is paused with X in its read and nothing written. A
    # row-locking reader stops at interaction X after locking X's request leg; a
    # reader that takes no row locks stops at its first lineage write instead.
    pytest.param(
        "lineage",
        [
            ("SELECT 1 FROM interactions WHERE id = %s FOR UPDATE", (STALE,)),
            (
                "SELECT 1 FROM lineage_metadata WHERE interaction_id = %s FOR UPDATE",
                (KEPT,),
            ),
        ],
        id="lineage-paused-after-reading-the-stale-rows",
    ),
]


@pytest.mark.parametrize(("first_name", "hold"), _INTERLEAVINGS)
def test_reconciliation_and_lineage_of_one_trace_serialize(
    configured_db: str, first_name: str, hold: list[tuple[str, tuple[Any, ...]]],
) -> None:
    dsn = configured_db
    cursor = _seed(dsn)
    second_name = "lineage" if first_name == "reconcile" else "reconcile"

    probe = psycopg.connect(dsn, autocommit=True)
    holder = psycopg.connect(dsn)
    first = _Side(dsn, first_name)
    second = _Side(dsn, second_name)
    try:
        for sql, params in hold:
            assert holder.execute(sql, params).fetchall(), sql
        holder_pid = holder.info.backend_pid

        first.start()
        _await_lock_wait(probe, first, blocked_by=holder_pid)
        second.start()
        second_wait = _await_lock_wait(probe, second)
        holder.rollback()
        assert first.join() and second.join(), "a side is still running after release"
    finally:
        holder.rollback()
        for side in (first, second):
            if not side.done() and side._thread.ident is not None:
                probe.execute("SELECT pg_terminate_backend(%s)", (side.pid,))
                side.join()
            side.conn.close()
        holder.close()
        probe.close()

    errors = {s.name: repr(s.error) for s in (first, second) if s.error is not None}
    assert errors == {}, f"a side failed: {errors}"
    assert first.committed and second.committed

    # Reconciliation committed its shrink: X and both its legs are gone.
    with psycopg.connect(dsn) as conn:
        assert conn.execute(
            "SELECT count(*) FROM interactions WHERE id = %s", (STALE,)
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM interaction_legs WHERE interaction_id = %s", (STALE,)
        ).fetchone() == (0,)

    # One drain replays the trace queued by the delete triggers. Afterwards every
    # lineage row still has its leg, and the kept interaction is fully derived.
    lineage_driver.drain(cursor)
    with psycopg.connect(dsn) as conn:
        orphans = conn.execute(
            "SELECT m.interaction_id, m.leg_type::text FROM lineage_metadata m "
            "WHERE NOT EXISTS (SELECT 1 FROM interaction_legs l "
            "  WHERE l.interaction_id = m.interaction_id AND l.leg_type = m.leg_type)"
        ).fetchall()
        status = conn.execute(
            "SELECT status::text FROM lineage_trace_status WHERE trace_id = %s", (TRACE,)
        ).fetchone()
    assert orphans == []
    assert _lineage_keys(dsn) == {(KEPT, "request"), (KEPT, "response")}
    assert status == ("complete",)

    # The second side waited for the first to finish with the trace — on the
    # per-trace advisory lock, before touching any row.
    assert second_wait is not None, f"{second_name} never waited for {first_name}"
    event, blockers = second_wait
    assert (event, first.pid in blockers) == ("advisory", True), second_wait


def test_lineage_derivation_leaves_the_traces_rows_unlocked(configured_db: str) -> None:
    """Re-deriving a trace writes no tuple locks into its legs or interactions.

    A row lock per row per re-derivation is a tuple-header write (and WAL, and a
    multixact once two lockers overlap) for every row of the trace each time a leg
    arrives — quadratic in the trace's length. While the derivation's transaction is
    still open, another session can take an exclusive lock on every row of the trace
    without waiting."""
    dsn = configured_db
    _seed(dsn)
    with psycopg.connect(dsn) as lineage, psycopg.connect(dsn) as probe:
        _derive_lineage(db.Transaction(lineage))
        locked = probe.execute(
            "SELECT l.interaction_id, l.leg_type::text FROM interaction_legs l "
            "JOIN interactions i ON i.id = l.interaction_id "
            "WHERE i.trace_id = %s FOR UPDATE OF l, i NOWAIT",
            (TRACE,),
        ).fetchall()
        probe.rollback()
        lineage.rollback()
    assert len(locked) == 4
