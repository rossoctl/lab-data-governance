"""Leg-ready's priority decision arm: compute an interaction a Sentry gate waits on, out of
``seq`` order.

The cursor arm delivers legs as a contiguous prefix in ``seq`` order, so a ready
egress leg waits behind any earlier leg still being classified, in any trace. A
gate holding that egress cannot wait for the prefix. The verdict service
(:mod:`data_governance.risk.api.verdict_routes`) names the interaction it waits on
with ``pg_notify('dg_priority_decision', '<interaction_id>')``; this arm hears it and
computes that interaction as soon as its request leg is ready by the cursor arm's
own predicate (:func:`.driver.one_leg`), through the same observer, so leg-ready
stays the only OPA client and the only risk writer.

- **Idempotent with the cursor arm.** The engine writes a version only when the
  record differs from the latest (FR-DAS-014), under the per-interaction
  advisory lock, and reuses the cached decision when the evidence is unchanged;
  the cursor arm reaching the same leg later computes the same record and writes
  nothing. Both arms take a turn of :data:`.driver.TURNS`, a priority decision first, so
  the process stays one serial writer.
- **Fenced on the cursor row.** Every leg-ready write stamps the
  ``processor_state`` row (``updated_at``), whether or not it moves the cursor.
  The arm's write transaction updates that row only if it still holds the seq
  and the stamp read inside the turn (:func:`.driver.advance_from`, the fence of
  every leg-ready write): if anyone wrote since — a leg, a recompute, a priority
  decision — the write rolls back and the id stays pending, to be computed
  again on fresh evidence. A real replacement ends this arm at the lock check
  that comes first (:class:`~data_governance.db.LockLost`).
- **Latency only.** A lost notify, a listener that is down, a named id that
  expires: the cursor arm still delivers the leg in order. Nothing here moves the
  cursor or claims anything durable.

A named id whose request leg is not ready yet (the span is still in flight, the
payload is not classified, label lineage has not derived it under this trace, or the
trace has a label correction pending; the stream's own rule, :func:`.driver.one_leg`)
stays pending for
:data:`TTL_SECONDS`, re-checked on every ``dg_interaction_leg_ready`` notify
(classification, interactions and label lineage all fire it) and at least every
:data:`RECHECK_SECONDS`.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict

from data_governance import db

from . import driver

log = logging.getLogger(__name__)

#: The channel the verdict service names a waited interaction on. The payload is
#: the interaction id.
PRIORITY_DECISION_CHANNEL = "dg_priority_decision"

#: How long a named id is kept while its request leg is not ready. A gate waits
#: at most 3 s; past this nobody is waiting and the cursor arm delivers the leg in
#: order.
TTL_SECONDS = 10.0

#: How often pending ids are re-checked with no notify.
RECHECK_SECONDS = 0.25

#: Idle wait when nothing is pending (only a notify matters then).
IDLE_SECONDS = 5.0

#: Wait before re-opening a failed listener session.
RECONNECT_SECONDS = 2.0

#: Most ids held pending at once; the oldest is dropped beyond it (anyone with a
#: DB session can notify, so the set is bounded).
MAX_PENDING = 1024


class _RowMoved(Exception):
    """Someone wrote the cursor row during this computation: drop it, keep the id."""


def _valid_id(payload: str) -> bool:
    try:
        uuid.UUID(payload)
    except ValueError:
        return False
    return True


class PriorityDecisionArm:
    """The arm's state and loop. :meth:`run` is the thread body; :meth:`serve`
    is one pass over the pending ids (the unit the tests drive)."""

    def __init__(self, observer: driver.Observer, *, guard=None) -> None:
        self._observer = observer
        self._guard = guard
        #: interaction id -> monotonic deadline, oldest first.
        self.pending: OrderedDict[str, float] = OrderedDict()

    def want(self, interaction_id: str, now: float | None = None) -> None:
        if not _valid_id(interaction_id):
            log.debug("ignoring priority decision payload %r: not an interaction id", interaction_id)
            return
        now = time.monotonic() if now is None else now
        self.pending[interaction_id] = now + TTL_SECONDS
        self.pending.move_to_end(interaction_id)
        while len(self.pending) > MAX_PENDING:
            self.pending.popitem(last=False)

    def serve(self, now: float | None = None) -> int:
        """Compute every pending interaction whose request leg is ready; drop the
        expired ones. Returns how many were computed. Stops the pass at an
        observer deferral (OPA unreachable): the ids stay pending. Any other fault
        keeps its one id pending until its TTL and the pass goes on; only a lost
        lock ends it."""
        now = time.monotonic() if now is None else now
        done = 0
        for interaction_id, deadline in list(self.pending.items()):
            if now > deadline:
                del self.pending[interaction_id]
                log.info("priority decision interaction_id=%s expired before its request leg was ready", interaction_id)
                continue
            try:
                with db.transaction() as tx:
                    leg = driver.one_leg(tx, interaction_id, "request")
                if leg is None or not leg.ready:
                    continue
                self._compute(leg)
            except driver.LegDeferred as exc:
                log.warning("priority decision interaction_id=%s deferred by observer (%s); retrying",
                            interaction_id, exc.__cause__ or exc)
                break
            except db.LockLost:
                raise
            except _RowMoved:
                log.warning("priority decision interaction_id=%s: the cursor row moved during its computation; retrying",
                            interaction_id)
                continue
            except Exception as exc:  # noqa: BLE001 — one id's fault must not starve the ids behind it
                log.warning("priority decision interaction_id=%s failed (%s); retrying until it expires", interaction_id, exc)
                continue
            del self.pending[interaction_id]
            done += 1
        return done

    def _compute(self, leg: driver.Leg) -> None:
        started = time.monotonic()
        with driver.TURNS.priority_decision():
            if self._guard is not None:
                self._guard()
            with db.transaction() as tx:
                seq, stamp = driver.read_stamp(tx, driver.PROCESSOR_NAME)
            write = self._observer(leg)
            if write is not None:
                try:
                    with db.transaction() as tx:
                        driver.advance_from(tx, driver.PROCESSOR_NAME, seq, seq, stamp)
                        write(tx)
                except db.LockLost as exc:
                    raise _RowMoved(str(exc)) from exc
        log.info("priority decision interaction_id=%s computed (leg seq=%d) in %.0f ms",
                 leg.interaction_id, leg.seq, (time.monotonic() - started) * 1000)

    def run(self, stop_event: threading.Event, dsn: str) -> None:
        """Listen on the priority decision channel and the leg-ready channel until
        *stop_event*; serve the pending ids on every wake. A listener failure
        re-opens after :data:`RECONNECT_SECONDS`; a lost sole-consumer lock ends
        the arm (the cursor arm's own guard ends the process)."""
        while not stop_event.is_set():
            try:
                # The priority decision channel last: the session's last statement names it,
                # which tells it apart from the cursor arm's LISTEN session.
                with db.listen_payloads((driver.NOTIFY_CHANNEL, PRIORITY_DECISION_CHANNEL), dsn) as listener:
                    log.info("priority decision arm listening on %r", PRIORITY_DECISION_CHANNEL)
                    while not stop_event.is_set():
                        notes = listener.wait(RECHECK_SECONDS if self.pending else IDLE_SECONDS)
                        now = time.monotonic()
                        for channel, payload in notes:
                            if channel == PRIORITY_DECISION_CHANNEL:
                                self.want(payload, now)
                        if self.pending:
                            self.serve()
            except db.LockLost as exc:
                log.error("priority decision arm stopping: %s", exc)
                return
            except Exception as exc:  # noqa: BLE001 — latency-only arm: log, re-open
                if stop_event.is_set():
                    return
                log.warning("priority decision arm failed (%s); re-opening in %.0f s — the cursor arm still delivers",
                            exc, RECONNECT_SECONDS)
                stop_event.wait(RECONNECT_SECONDS)


def start(observer: driver.Observer, stop_event: threading.Event, dsn: str, *, guard=None) -> threading.Thread:
    """Start the arm on a daemon thread and return it."""
    arm = PriorityDecisionArm(observer, guard=guard)
    thread = threading.Thread(target=arm.run, args=(stop_event, dsn), name="leg-ready-priority-decision", daemon=True)
    thread.start()
    return thread
