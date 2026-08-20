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

Atomicity
---------
Each delivered leg is handled in ONE transaction that runs the per-leg delivery
AND advances the durable ``leg_ready`` cursor — together, so a crash mid-leg
commits nothing and the restart re-delivers from the same cursor (ADR-0007). Since
delivery is a thin observer (no real downstream yet — risk/lineage/PDP are future
work), the durable cursor is the complete exactly-once record.

Run as ``python -m data_governance.processors.leg_ready``.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from collections.abc import Callable

from data_governance import db
from data_governance.processors import _driver
from data_governance.processors import readiness_cursor as rc

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


def _fetch_batch(tx: db.Transaction, cursor: int, limit: int) -> list[Leg]:
    rows = tx.fetch_all(_FETCH_SQL, (cursor, limit))
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
    """
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
            return cursor

        by_seq = {leg.seq: leg for leg in batch}
        for item in result.delivered:
            leg = by_seq[item.seq]
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
            # Phase 2 — the observer's write + the cursor advance, one txn.
            with db.transaction() as tx:
                process_leg(tx, leg, write)
                _driver.advance_cursor(tx, spec.processor_name, leg.seq)
            cursor = leg.seq

        # If the ready prefix stopped short of the whole batch, a leg is held —
        # do not fetch further (the block holds). Only continue when every fetched
        # leg was delivered AND the batch was full (there may be more ready legs).
        if len(result.delivered) < len(batch):
            return cursor
        if len(batch) < spec.batch_size:
            return cursor


def read_cursor(tx: db.Transaction) -> int:
    """Read the leg-ready durable cursor (delegates to the shared loop)."""
    return _driver.read_cursor(tx, PROCESSOR_NAME)


def drain(cursor: int, observer: Observer | None = None) -> int:
    """Deliver every ready leg in the leading contiguous-prefix run past *cursor*,
    one transaction per leg. Returns the new cursor (the seq of the last delivered
    leg, or *cursor* if none / the head is unready / the observer deferred).

    *observer* is the downstream governance seam (ADR-0027, two-phase since issue
    #158 — see :data:`Observer`); ``None`` keeps delivery to the metric
    increment."""
    return _drain_spec(_spec(), cursor, observer=observer)


def run(
    stop_event: threading.Event,
    dsn: str,
    observer: Observer | None = None,
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
    increment."""
    def _drain_with_observer(spec: _driver.StreamSpec[Leg], cursor: int) -> int:
        return _drain_spec(spec, cursor, observer=observer)

    _driver.run(_spec(), stop_event, dsn, drain_fn=_drain_with_observer)
