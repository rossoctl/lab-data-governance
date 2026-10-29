"""P-leg-ready adapter: the readiness-gated Layer-2 consumer (issue #123, ADR-0027).

This is the readiness-gated half of ADR-0027 (the entity-ready consumer, #121, is
the ungated half). It drains ``interaction_legs`` by ``seq``, computes each leg's
**Leg readiness** against the DB, feeds the seq-ordered batch through the
contiguous-prefix primitive (:mod:`..readiness_cursor`), delivers the leading
unbroken run of ready legs in seq order, and advances a plain single-BIGINT
``leg_ready`` cursor to the last delivered leg's ``seq``.

The bespoke drain — the one deviation
-------------------------------------
This is the **one place** the shared ``_driver.drain`` cannot be reused verbatim.
``_driver.drain`` advances the cursor to ``item_seq(item)`` for *every* fetched
item unconditionally (monotonic in ``seq``); the leg-readiness predicate is
non-monotonic in ``seq`` — a low-``seq`` unready leg can sit behind a high-``seq``
ready one — so a max-seq advance would strand the low leg forever. :func:`drain`
here instead runs the fetched batch through :func:`readiness_cursor.contiguous_prefix`,
which stops at the first unready leg (head-of-line blocking), and delivers only the
leading ready prefix.

Everything else IS reused:

- ``_driver.read_cursor`` / ``_driver.advance_cursor`` — the plain-BIGINT cursor
  round-trips directly now that legs carry distinct seqs (ADR-0027 reversal), so
  the composite-cursor persistence problem that blocked the original #123 is gone.
- ``_driver.run`` — the LISTEN/poll wake machinery, via its ``drain_fn`` seam:
  ``run`` calls our :func:`drain` on each wake instead of ``_driver.drain``. The
  only ``_driver`` change is that seam (a default-``drain`` parameter), so the
  existing consumers are unaffected.

Readiness predicate
-------------------
A leg is ready iff ``payload_hash IS NULL`` (nothing to classify) OR a
``payload_classifications`` row exists for its ``payload_hash`` (its **Classification**
verdict has landed). Computed in the fetch SQL via a ``LEFT JOIN`` existence check
against ``payload_classifications`` (PK ``content_hash``), so readiness is derived
fresh from the durable tables on every drain — reliability lives in the drain, not
the ``dg_interaction_leg_ready`` notify (which is latency-only; ADR-0015/ADR-0027).

Inherited labels (``RISK_INHERITED_LABELS=on``)
-----------------------------------------------
With the flag on, readiness also waits for label lineage: a payload-bearing leg is
ready only once ``leg_inherited_labels`` holds its row at the leg's current
``payload_hash``, so a decision never runs before label lineage has derived the
leg. The row reflects what was classified at that moment: a leg whose inputs are
classified later (on the lab traces an egress's inputs often have higher seqs)
is decided first with the labels known then, and the labels that land later (a
re-derivation changed an already-decided leg's summary) are queued by label
lineage in ``risk_recompute_requests``; after the cursor arm,
:func:`_drain_recompute_requests` re-delivers those interactions through the
observer — never moving the cursor, fenced on it like the cursor arm — and claims
each request by deleting it in the transaction of its write. The stale-callee
case (#284) may later share this drain.

Atomicity
---------
Each delivered leg is handled in ONE transaction that runs the observer's write
(since issue #158: the interaction's risk record) AND advances the durable
``leg_ready`` cursor — together, so a crash mid-leg commits nothing and the
restart re-delivers from the same cursor (ADR-0007). The observer's compute half,
including the OPA round-trip, runs before that transaction, holding none; see
:data:`Observer`.

Run as ``python -m data_governance.processors.leg_ready``.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections.abc import Callable

from data_governance import db
from data_governance.processors import _driver
from data_governance.processors import readiness_cursor as rc
from data_governance.risk import config as risk_config

from . import metrics

log = logging.getLogger(__name__)

# The durable ``processor_state`` cursor row for this stream. Distinct from
# "interactions" / "classification" / "entity_ready" so the Layer-2 consumers keep
# independent cursors. The row is created on first ``advance_cursor`` upsert.
PROCESSOR_NAME = "leg_ready"

# Channel this consumer LISTENs on. Fired blindly (payload-less) by P-classification
# (after each classify) and P-interactions (after a flush that wrote legs) — both
# taps are latency-only; the cursor drain + poll backstop are authoritative.
NOTIFY_CHANNEL = "dg_interaction_leg_ready"

# How long the loop sleeps between drains when idle (poll backstop). Also the max
# time a LISTEN wait blocks before re-draining. Exposed at module level (not just on
# the spec) because tests monkeypatch it to shrink the backstop; the spec reads it
# at build time. Mirrors the sibling drivers.
POLL_SECONDS = 5.0

# How many legs to pull per drain batch (each delivered leg is still its own
# transaction).
_DRAIN_BATCH = 500

# The downstream governance consumer seam (issue #158). An observer is called
# OUTSIDE any transaction with each ready leg — its compute (for the risk
# consumer: evidence gathering and the OPA HTTP round-trip) must never hold a
# pooled connection open. It returns either ``None`` (nothing to write — the
# leg is delivered and the cursor advances) or a ``LegWrite`` closure, which
# the drain then runs INSIDE the leg's delivery transaction so the observer's
# write commits atomically with the cursor advance (ADR-0007: if the write
# fails, the cursor advance rolls back with it and the leg is re-delivered —
# never silently skipped past).
#
# Failure semantics an observer can choose per leg:
#   - raise :class:`LegDeferred` -> transient hold. The drain stops at this
#     leg with the cursor UNCHANGED and retries on the next wake (poll
#     backstop ≈ POLL_SECONDS). Head-of-line blocking by design: an outage
#     downstream (e.g. OPA unreachable) must delay legs, not lose them.
#   - return ``None`` after logging -> deliberate skip. The cursor advances;
#     the leg will not be re-delivered. For permanently-unprocessable legs
#     only, so a poison leg cannot wedge the stream forever.
#   - raise anything else -> a real fault; it propagates out of the drain
#     (and out of ``run``), surfacing as a crash rather than being absorbed.
LegWrite = Callable[[db.Transaction], None]
Observer = Callable[["Leg"], "LegWrite | None"]


class LegDeferred(Exception):
    """Raised by an observer to hold the stream at the current leg.

    Signals a *transient* per-leg failure (downstream dependency
    unreachable): the drain logs it, leaves the cursor where it is, and ends
    the current drain — the next wake re-delivers the same leg. Chain the
    underlying error as ``__cause__``."""


@dataclasses.dataclass(frozen=True)
class Leg:
    """One ``interaction_legs`` row the leg-ready consumer reads, with its computed
    readiness.

    Carries the leg identity a downstream governance consumer needs
    (``interaction_id``, ``leg_type``, ``payload_hash``) plus the ``seq`` the drain
    orders and cursors on and the ``ready`` flag the contiguous-prefix primitive
    gates on. A driver-local read shape (like the sibling consumers' ``Entity`` /
    ``Payload``)."""

    interaction_id: str
    leg_type: str
    payload_hash: str | None
    seq: int
    ready: bool


def process_leg(tx: db.Transaction, leg: Leg, write: LegWrite | None = None) -> None:
    """Deliver one ready **Interaction leg** within *tx*: run the observer's
    prepared *write* (issue #158 — the write half of the observer's two-phase
    delivery; ``None`` when there is nothing to write) and increment the
    delivery counter.

    The caller (:func:`drain`) owns the transaction boundary and advances the
    cursor in the same *tx*, so the write and the cursor advance commit
    atomically (ADR-0007): a crash before commit rolls back the cursor
    advance, so the restart re-delivers this exact leg rather than skipping
    it. Reference the module-global counter so a test's ``make_registry()``
    rebind is picked up.
    """
    if write is not None:
        write(tx)
    metrics.legs_observed_total.inc()


# The readiness predicate lives in the SQL (derived fresh from the durable tables on
# every drain): a leg is ready iff it has no payload OR its payload has been
# classified. LEFT JOIN payload_classifications (PK content_hash); ``pc.content_hash
# IS NOT NULL`` means a verdict row exists.
_FETCH_SQL = """
SELECT l.interaction_id, l.leg_type::text, l.payload_hash, l.seq,
       (l.payload_hash IS NULL OR pc.content_hash IS NOT NULL) AS ready
  FROM interaction_legs l
  LEFT JOIN payload_classifications pc ON pc.content_hash = l.payload_hash
 WHERE l.seq > %s
 ORDER BY l.seq ASC
 LIMIT %s
"""


# With RISK_INHERITED_LABELS on: classified AND derived by label lineage at the
# leg's current payload.
_FETCH_WITH_LABELS_SQL = """
SELECT l.interaction_id, l.leg_type::text, l.payload_hash, l.seq,
       (l.payload_hash IS NULL
        OR (pc.content_hash IS NOT NULL AND m.payload_hash = l.payload_hash)) AS ready
  FROM interaction_legs l
  LEFT JOIN payload_classifications pc ON pc.content_hash = l.payload_hash
  LEFT JOIN leg_inherited_labels m
         ON m.interaction_id = l.interaction_id AND m.leg_type = l.leg_type
 WHERE l.seq > %s
 ORDER BY l.seq ASC
 LIMIT %s
"""


_HELD_ON_LABELS_SQL = """
SELECT pc.content_hash IS NOT NULL, m.payload_hash
  FROM interaction_legs l
  LEFT JOIN payload_classifications pc ON pc.content_hash = l.payload_hash
  LEFT JOIN leg_inherited_labels m ON m.interaction_id = l.interaction_id AND m.leg_type = l.leg_type
 WHERE l.interaction_id = %s AND l.leg_type = %s
"""


#: How long one head leg may wait for label lineage's row before the hold is
#: logged. A healthy derivation follows its classification within a wake or two
#: (both processors wake on the same notify); a longer wait means label lineage
#: is stopped, crash-looping or far behind.
LABEL_HOLD_WARN_SECONDS = 30.0
# (the held leg, since when) — one head at a time
_label_hold: list = [None, 0.0]


def _warn_if_waiting_for_labels(leg: Leg) -> None:
    """Name the hold when the same head leg has been classified and waiting only
    for label lineage's row for :data:`LABEL_HOLD_WARN_SECONDS` (a stopped or
    crash-looping label-lineage processor holds this stream with the flag on; the
    OPA hold says so per wake, and so does this, once it is not a race)."""
    with db.transaction() as tx:
        row = tx.fetch_one(_HELD_ON_LABELS_SQL, (leg.interaction_id, leg.leg_type))
    waiting = row is not None and row[0] and row[1] != leg.payload_hash
    key = (leg.interaction_id, leg.leg_type, leg.payload_hash)
    if not waiting:
        _label_hold[:] = [None, 0.0]
        return
    if _label_hold[0] != key:
        _label_hold[:] = [key, time.monotonic()]
    since = _label_hold[1]
    if time.monotonic() - since >= LABEL_HOLD_WARN_SECONDS:
        log.warning(
            "leg seq=%d interaction_id=%s is classified and waits for label lineage's row at its "
            "payload (RISK_INHERITED_LABELS=on) for %.0f s; holding cursor — is "
            "data-governance-label-lineage running?",
            leg.seq, leg.interaction_id, time.monotonic() - since,
        )


def _labels_on() -> bool:
    return risk_config.inherited_labels_mode() == "on"


def _fetch_batch(tx: db.Transaction, cursor: int, limit: int) -> list[Leg]:
    rows = tx.fetch_all(_FETCH_WITH_LABELS_SQL if _labels_on() else _FETCH_SQL, (cursor, limit))
    return [
        Leg(
            interaction_id=r[0],
            leg_type=r[1],
            payload_hash=r[2],
            seq=int(r[3]),
            ready=bool(r[4]),
        )
        for r in rows
    ]


def _spec() -> _driver.StreamSpec[Leg]:
    """Build the leg-ready stream spec. Rebuilt per call so a monkeypatched
    ``POLL_SECONDS`` is picked up. ``process_item`` / ``item_seq`` are supplied for
    shape completeness, but this stream's advance is driven by the bespoke
    :func:`drain` (contiguous-prefix), NOT ``_driver.drain`` — see the module
    docstring. The observer is not bound here: the two-phase seam (issue #158)
    needs the observer's compute to run OUTSIDE the per-leg transaction, so the
    bespoke drain drives it directly."""
    return _driver.StreamSpec(
        notify_channel=NOTIFY_CHANNEL,
        processor_name=PROCESSOR_NAME,
        fetch_batch=_fetch_batch,
        process_item=lambda tx, leg: process_leg(tx, leg, None),
        item_seq=lambda leg: leg.seq,
        poll_seconds=POLL_SECONDS,
        batch_size=_DRAIN_BATCH,
    )


def _drain_spec(
    spec: _driver.StreamSpec[Leg],
    cursor: int,
    observer: Observer | None = None,
    guard: Callable[[], None] | None = None,
) -> int:
    """Bespoke contiguous-prefix drain over the leg-readiness stream.

    Fetches legs with ``seq > cursor`` (readiness computed in the fetch SQL), feeds
    the batch through :func:`readiness_cursor.contiguous_prefix`, delivers the
    leading unbroken run of ready legs, and returns the new cursor. Stops as soon as
    a batch does not fully deliver — a held (unready) leg is the head-of-line block,
    so fetching further would only re-see legs behind the block. Loops to the next
    batch only when the whole batch was delivered (a full ready run that may
    continue).

    Per delivered leg, the two-phase observer contract (issue #158): the observer
    runs first, OUTSIDE any transaction — its compute may include a synchronous
    OPA HTTP round-trip, which must not hold a pooled connection — and returns the
    write to perform (or ``None``). Then one transaction runs that write
    (:func:`process_leg`) AND advances the durable cursor, so the observer's write
    commits atomically with the advance. An observer raising :class:`LegDeferred`
    holds the stream: the drain logs it and returns with the cursor unchanged, so
    the same leg is re-delivered on the next wake (transient-failure retry without
    losing the leg). Any other observer error propagates.

    *guard*, when given, runs when the drain starts and before each leg, and
    ends the drain by raising — so a process notices a lost sole-consumer lock
    at its next wake, and a drain that outlives the lock stops at the next leg
    without another OPA call. The guard alone cannot fence a write (it runs on
    another connection), so each leg's transaction also advances the cursor
    only from the value this drain read (:func:`_advance_from`): a leg another
    consumer already delivered rolls back and raises
    :class:`~data_governance.db.LockLost`.
    """
    cursor = _drain_prefix(spec, cursor, observer, guard)
    if observer is not None and _labels_on():
        _drain_recompute_requests(cursor, observer, guard, spec.batch_size)
    return cursor


def _drain_prefix(
    spec: _driver.StreamSpec[Leg],
    cursor: int,
    observer: Observer | None,
    guard: Callable[[], None] | None,
) -> int:
    """The cursor arm of :func:`_drain_spec`: the contiguous ready prefix."""
    if guard is not None:
        guard()
    while True:
        with db.transaction() as tx:
            batch = spec.fetch_batch(tx, cursor, spec.batch_size)
        if not batch:
            return cursor

        result = rc.contiguous_prefix(
            [rc.ReadinessItem(seq=leg.seq, leg_type=leg.leg_type, ready=leg.ready)
             for leg in batch],
            cursor,
        )
        if not result.delivered:
            # Head-of-line block at the very first leg past the cursor: nothing
            # delivered, cursor held. Re-fetching would return the same blocked
            # legs, so stop this drain (the next wake re-evaluates them).
            if _labels_on():
                _warn_if_waiting_for_labels(min(batch, key=lambda leg: leg.seq))
            return cursor
        _label_hold[:] = [None, 0.0]  # the head moved: a later hold starts its own clock

        by_seq = {leg.seq: leg for leg in batch}
        for item in result.delivered:
            leg = by_seq[item.seq]
            if guard is not None:
                guard()
            # Phase 1 — observer compute, no transaction held.
            try:
                write = observer(leg) if observer is not None else None
            except LegDeferred as exc:
                log.warning(
                    "leg seq=%d interaction_id=%s deferred by observer (%s); "
                    "holding cursor at %d, retrying on next wake",
                    leg.seq, leg.interaction_id, exc.__cause__ or exc, cursor,
                )
                return cursor
            # Phase 2 — the cursor advance + the observer's write, one txn. The
            # advance is first and conditional: it is the fence.
            with db.transaction() as tx:
                _advance_from(tx, spec.processor_name, cursor, leg.seq)
                process_leg(tx, leg, write)
            cursor = leg.seq

        # If the ready prefix stopped short of the whole batch, a leg is held —
        # do not fetch further (the block holds). Only continue when every fetched
        # leg was delivered AND the batch was full (there may be more ready legs).
        if len(result.delivered) < len(batch):
            return cursor
        if len(batch) < spec.batch_size:
            return cursor


# Requests whose interaction lies wholly at or below the cursor (one still ahead
# will be delivered by the cursor arm with its labels in place) and whose every
# leg is ready by the cursor arm's own predicate — a leg rewritten in place to a
# payload not yet classified, or not yet derived by label lineage, holds its
# request until it is, so a recompute never decides on a pending verdict.
_RECOMPUTE_SQL = """
SELECT r.interaction_id, r.requested_at, l.leg_type::text, l.payload_hash, l.seq
  FROM risk_recompute_requests r
  JOIN LATERAL (
        SELECT leg_type, payload_hash, seq FROM interaction_legs
         WHERE interaction_id = r.interaction_id ORDER BY seq DESC LIMIT 1
       ) l ON true
 WHERE l.seq <= %s
   AND NOT EXISTS (
        SELECT 1 FROM interaction_legs x
          LEFT JOIN payload_classifications pc ON pc.content_hash = x.payload_hash
          LEFT JOIN leg_inherited_labels m
                 ON m.interaction_id = x.interaction_id AND m.leg_type = x.leg_type
         WHERE x.interaction_id = r.interaction_id
           AND x.payload_hash IS NOT NULL
           AND (pc.content_hash IS NULL OR m.payload_hash IS DISTINCT FROM x.payload_hash))
 ORDER BY r.requested_at, r.interaction_id
 LIMIT %s
"""


_ORPHAN_REQUESTS_SQL = (
    "DELETE FROM risk_recompute_requests r "
    "WHERE NOT EXISTS (SELECT 1 FROM interaction_legs l WHERE l.interaction_id = r.interaction_id)"
)


def _drain_recompute_requests(
    cursor: int,
    observer: Observer,
    guard: Callable[[], None] | None,
    limit: int,
) -> int:
    """Re-deliver the interactions label lineage queued in
    ``risk_recompute_requests`` (their inherited labels changed after this
    consumer passed them). The observer re-gathers evidence — the fingerprint now
    carries the labels — and its write commits with the request's delete; a
    request re-queued meanwhile (a newer ``requested_at``) survives. A deferral
    stops the pass, the request stays for the next wake. A request whose
    interaction has no legs left is dropped. Never moves the cursor. Returns how
    many were recomputed.

    The write is fenced like the cursor arm's: its transaction first holds the
    cursor row at the value this drain read (:func:`_advance_from` from the cursor
    to itself, so a replacement that moved it makes this drain stop with
    :class:`~data_governance.db.LockLost`), then claims the request by deleting it.
    A request another consumer already took, or that was re-queued meanwhile,
    claims nothing and the computation is dropped: an older computation never
    lands after a newer one."""
    with db.transaction() as tx:
        tx.execute(_ORPHAN_REQUESTS_SQL)
        rows = tx.fetch_all(_RECOMPUTE_SQL, (cursor, limit))
    done = 0
    for interaction_id, requested_at, leg_type, payload_hash, seq in rows:
        if guard is not None:
            guard()
        leg = Leg(interaction_id=interaction_id, leg_type=leg_type, payload_hash=payload_hash,
                  seq=int(seq), ready=True)
        try:
            write = observer(leg)
        except LegDeferred as exc:
            log.warning("recompute of interaction_id=%s deferred by observer (%s); retrying on next wake",
                        interaction_id, exc.__cause__ or exc)
            break
        with db.transaction() as tx:
            _advance_from(tx, PROCESSOR_NAME, cursor, cursor)
            claimed = tx.fetch_one(
                "DELETE FROM risk_recompute_requests WHERE interaction_id = %s AND requested_at = %s "
                "RETURNING 1", (interaction_id, requested_at))
            if claimed is None:
                continue
            if write is not None:
                write(tx)
        done += 1
    if done:
        log.info("recomputed %d interaction(s) whose inherited labels changed", done)
    return done


def _advance_from(tx: db.Transaction, processor_name: str, expected: int, seq: int) -> None:
    """Advance the durable cursor from *expected* to *seq*, or raise
    :class:`~data_governance.db.LockLost` if it no longer reads *expected*.

    The sole-consumer lock is checked before each leg, but that check and this
    transaction run on different connections: a consumer that stalls between
    them can lose its lock while a replacement delivers the same legs. The
    conditional update closes that window. It takes the cursor row's lock, so
    the stalled writer waits for the replacement's commit, then finds the
    cursor moved and rolls back its whole leg — record included — instead of
    landing an older computation as the newest version and moving the cursor
    backwards."""
    tx.execute(
        "INSERT INTO processor_state (processor_name, last_processed_seq, updated_at) "
        "VALUES (%s, 0, now()) ON CONFLICT (processor_name) DO NOTHING",
        (processor_name,),
    )
    if tx.fetch_one(
        "UPDATE processor_state SET last_processed_seq = %s, updated_at = now() "
        "WHERE processor_name = %s AND last_processed_seq = %s RETURNING 1",
        (seq, processor_name, expected),
    ) is None:
        raise db.LockLost(
            f"{processor_name} cursor moved past {expected}: another consumer delivered"
        )


def read_cursor(tx: db.Transaction) -> int:
    """Read the leg-ready durable cursor (delegates to the shared loop)."""
    return _driver.read_cursor(tx, PROCESSOR_NAME)


def drain(
    cursor: int,
    observer: Observer | None = None,
    guard: Callable[[], None] | None = None,
) -> int:
    """Deliver every ready leg in the leading contiguous-prefix run past *cursor*,
    one transaction per leg. Returns the new cursor (the seq of the last delivered
    leg, or *cursor* if none / the head is unready / the observer deferred).

    *observer* is the downstream governance seam (ADR-0027, two-phase since issue
    #158 — see :data:`Observer`); ``None`` keeps delivery to the metric
    increment. *guard* runs at the start and before each leg's write (see
    :func:`_drain_spec`)."""
    return _drain_spec(_spec(), cursor, observer=observer, guard=guard)


def run(
    stop_event: threading.Event,
    dsn: str,
    observer: Observer | None = None,
    *,
    guard: Callable[[], None] | None = None,
) -> None:
    """Wake-driven readiness drain over ``interaction_legs``. Returns when
    *stop_event* is set.

    Reuses ``_driver.run``'s LISTEN/poll wake machinery via its ``drain_fn`` seam —
    ``run`` invokes the bespoke contiguous-prefix :func:`_drain_spec` on each wake
    instead of ``_driver.drain``. Wakes on a ``dg_interaction_leg_ready`` LISTEN
    notification (low latency; fired blindly by P-classification / P-interactions) or
    the poll backstop, whichever comes first; both lead to the same drain. Falls back
    to poll-only if the LISTEN connection is unavailable — a missing/severed notify
    only costs latency (ADR-0015).

    *observer* is the downstream governance consumer seam (two-phase since issue
    #158 — see :data:`Observer`); ``None`` keeps delivery to the metric
    increment. *guard*, when given, runs at every wake and before each leg's
    write, and ends the loop by raising — the entry point passes its sole-consumer lock's
    :meth:`~data_governance.db.SessionLock.check`, so a process that lost the
    lock commits nothing more instead of running unprotected."""
    def _drain_with_observer(spec: _driver.StreamSpec[Leg], cursor: int) -> int:
        return _drain_spec(spec, cursor, observer=observer, guard=guard)

    _driver.run(_spec(), stop_event, dsn, drain_fn=_drain_with_observer)
