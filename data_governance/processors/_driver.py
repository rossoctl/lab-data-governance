"""Stream-agnostic cursor-driven drain/poll/LISTEN-wake loop (issue #75).

Extracted verbatim (behaviour-preserving) from the P-interactions driver so a
second Layer-2 processor — P-classification (CONTEXT.md) — reuses the proven
loop rather than copying it. The loop knows nothing about spans, interactions,
or payloads: it is parameterized by a :class:`StreamSpec` that supplies the four
stream-specific things — the NOTIFY channel, the ``processor_state`` cursor row
name, a batch-fetch function, and a per-item process function — plus a small
``item_seq`` accessor so the loop can advance the cursor generically.

The loop drains a source table by ``seq`` and processes each item end-to-end.
Each item is handled in ONE transaction that runs the stream's per-item
procedure AND advances the durable cursor (``processor_state``) — together, so a
crash mid-item commits nothing and the restart re-processes from the same cursor
(ADR-0007 recovery; the per-item procedure is expected to be idempotent). This
"one item, one transaction, cursor advances with the write" contract is the
load-bearing invariant every consumer inherits.

That recovery rule assumes the failure is transient. If it is not — if the item
itself is undeliverable — re-processing from the same cursor is an infinite
crash loop, and because the cursor never advances the stream stops for *every*
later item too, not just the bad one. So an item that keeps failing for a
non-connection reason is quarantined after ``max_item_attempts``: logged with
its seq and stepped over. Connection-class failures are never quarantined —
those mean the database is unreachable, not that the item is bad.

The loop wakes on two signals (issue #71): a Postgres ``LISTEN`` notification on
the stream's channel (fired by an insert trigger when new rows land), and a
periodic poll timeout as the backstop. Both lead to the same action — drain
whatever is past the cursor — so the notification carries no payload and its
count is irrelevant. Correctness depends only on the poll backstop; the
``LISTEN`` wake just removes latency. If the listen connection cannot be opened
or drops mid-run, the loop falls back to pure polling and still makes progress.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from collections.abc import Callable
from typing import Generic, TypeVar

from data_governance import db

log = logging.getLogger(__name__)

# The stream's item type. Opaque to the loop: it fetches items via the spec's
# ``fetch_batch``, hands each to ``process_item``, and reads its seq via
# ``item_seq`` to advance the cursor.
T = TypeVar("T")

# Loop defaults, shared by every stream unless the spec overrides them. A drain
# batch is a read-ahead window; each item within it is still its own
# transaction, so batch size trades round-trips against memory, not atomicity.
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_BATCH_SIZE = 500

# How many times :func:`drain` attempts a single item before quarantining it.
# More than one because some non-connection failures are genuinely transient
# (serialization failure, deadlock detected) and clear on an immediate retry;
# a deterministically bad item burns all its attempts and is stepped over.
DEFAULT_MAX_ITEM_ATTEMPTS = 3


@dataclasses.dataclass(frozen=True)
class StreamSpec(Generic[T]):
    """Everything the shared loop needs to drive one processor's stream.

    Attributes:
        notify_channel: the Postgres ``LISTEN`` channel the source table's insert
            trigger notifies on (low-latency wake; the poll backstop is the
            correctness guarantee, so the channel only removes latency).
        processor_name: the ``processor_state.processor_name`` cursor row for this
            stream. The loop reads and advances ``last_processed_seq`` under it.
        fetch_batch: ``(tx, cursor, limit) -> list[item]`` — the items with
            ``seq > cursor`` in ascending ``seq`` order, at most ``limit`` of
            them. Runs in its own short read transaction (not the per-item one).
        process_item: ``(tx, item) -> None`` — the stream's per-item procedure,
            run inside the per-item transaction the loop owns. Must be idempotent
            (a crash re-processes the last item from the same cursor). Must NOT
            advance the cursor itself — the loop does that in the same ``tx``.
        item_seq: ``item -> int`` — the item's ``seq``, used to advance the
            cursor after ``process_item`` succeeds.
        poll_seconds: idle sleep between drains / max LISTEN wait per iteration.
        batch_size: how many items to read per drain batch.
        max_item_attempts: how many times :func:`drain` attempts one item before
            quarantining it (logging it and stepping the cursor past it) so the
            stream keeps moving. ``0`` restores fail-stop: the first failure
            propagates and the processor exits. See :func:`drain`.
    """

    notify_channel: str
    processor_name: str
    fetch_batch: Callable[[db.Transaction, int, int], list[T]]
    process_item: Callable[[db.Transaction, T], None]
    item_seq: Callable[[T], int]
    poll_seconds: float = DEFAULT_POLL_SECONDS
    batch_size: int = DEFAULT_BATCH_SIZE
    max_item_attempts: int = DEFAULT_MAX_ITEM_ATTEMPTS


def read_cursor(tx: db.Transaction, processor_name: str) -> int:
    """Read the durable cursor for *processor_name* (0 if the row is absent)."""
    row = tx.fetch_one(
        "SELECT last_processed_seq FROM processor_state WHERE processor_name = %s",
        (processor_name,),
    )
    return int(row[0]) if row else 0


def advance_cursor(tx: db.Transaction, processor_name: str, seq: int) -> None:
    """Advance the durable cursor to *seq* within *tx*.

    Called by :func:`drain` inside the per-item transaction, so the cursor
    advance commits atomically with the item's derived writes (ADR-0007).
    """
    tx.execute(
        "INSERT INTO processor_state (processor_name, last_processed_seq, updated_at) "
        "VALUES (%s, %s, now()) ON CONFLICT (processor_name) DO UPDATE SET "
        "last_processed_seq = EXCLUDED.last_processed_seq, updated_at = now()",
        (processor_name, seq),
    )


def _process_one(spec: StreamSpec[T], item: T) -> None:
    """Run one item's procedure and cursor advance in a single transaction,
    retrying up to ``spec.max_item_attempts`` times; quarantine on exhaustion.

    Three outcomes, and the distinction between them is the point:

    * **connection-class failure** (``db.is_connection_error``) — re-raised
      immediately, unretried. The stream is not making progress because the
      database is unreachable, not because this item is bad; that must surface
      as it always has, not be mistaken for a poison item and skipped.
    * **other failure, attempts remain** — retried in a fresh transaction. Some
      non-connection failures are transient (serialization failure, deadlock
      detected) and clear on the next attempt.
    * **other failure, attempts exhausted** — quarantined: logged at ERROR with
      the traceback and the item's seq, and the cursor stepped past it in its
      own transaction so the stream resumes.

    Quarantine drops the item's derived output. That is a real loss and the log
    line says so — but the alternative it replaces is worse: before this, one
    undeliverable item halted the processor permanently, because the cursor
    could not advance past it and the restart re-read the very same item. One
    bad item cost every *later* item too, across every trace.
    """
    attempts = max(1, spec.max_item_attempts)
    for attempt in range(1, attempts + 1):
        try:
            with db.transaction() as tx:
                spec.process_item(tx, item)
                advance_cursor(tx, spec.processor_name, spec.item_seq(item))
            return
        except Exception as exc:  # noqa: BLE001 — classified immediately below
            if db.is_connection_error(exc) or spec.max_item_attempts <= 0:
                raise
            if attempt < attempts:
                log.warning(
                    "%s processor: item seq=%d failed (attempt %d/%d), retrying: %s",
                    spec.processor_name,
                    spec.item_seq(item),
                    attempt,
                    attempts,
                    exc,
                )
                continue
            log.error(
                "%s processor: item seq=%d failed %d/%d attempts — QUARANTINED. "
                "Its derived output is dropped and the cursor steps past it so "
                "the stream keeps moving; fix the cause and re-ingest to recover.",
                spec.processor_name,
                spec.item_seq(item),
                attempts,
                attempts,
                exc_info=True,
            )

    # Quarantine: the item's own transaction rolled back, so the cursor advance
    # is a separate commit. Nothing derived from the item is written.
    with db.transaction() as tx:
        advance_cursor(tx, spec.processor_name, spec.item_seq(item))


def drain(spec: StreamSpec[T], cursor: int) -> int:
    """Process every item past *cursor*, one transaction per item. Returns the
    new cursor (the seq of the last item processed, or *cursor* if none).

    Each item is processed AND its cursor advance committed in the same
    transaction, so a crash mid-item commits nothing (ADR-0007).

    An item that fails repeatedly for a non-connection reason is quarantined
    rather than propagated, so one bad item cannot halt the stream forever —
    see :func:`_process_one`. Connection-class failures still propagate
    unchanged. Set ``spec.max_item_attempts = 0`` for the previous fail-stop
    behaviour.
    """
    while True:
        # Read the next batch in its own short transaction; each item is then
        # processed (and the cursor advanced) in its own transaction.
        with db.transaction() as tx:
            batch = spec.fetch_batch(tx, cursor, spec.batch_size)
        if not batch:
            return cursor
        for item in batch:
            _process_one(spec, item)
            cursor = spec.item_seq(item)
        if len(batch) < spec.batch_size:
            return cursor


# A drain function the run machinery calls each wake. Defaults to :func:`drain`
# (the standard monotonic advance-to-max-seq). The leg-readiness consumer passes
# its OWN bespoke contiguous-prefix drain here (issue #123, ADR-0027): it is the
# one stream whose advance is non-monotonic in ``seq``, so it cannot reuse
# :func:`drain`, but it CAN reuse this run/LISTEN/poll wake machinery by supplying
# its drain through this seam. ``(spec, cursor) -> new_cursor``.
DrainFn = Callable[["StreamSpec[T]", int], int]


def _poll_loop(
    spec: StreamSpec[T],
    stop_event: threading.Event,
    cursor: int,
    drain_fn: "DrainFn" = drain,
) -> int:
    """Pure poll-drain loop (the backstop). Sleeps up to ``poll_seconds`` between
    drains. Used when no LISTEN connection is available. Returns the cursor.

    *drain_fn* defaults to :func:`drain`; the leg-readiness consumer passes its
    bespoke contiguous-prefix drain (issue #123)."""
    while not stop_event.is_set():
        cursor = drain_fn(spec, cursor)
        stop_event.wait(timeout=spec.poll_seconds)
    return cursor


class _ListenUnavailable(Exception):
    """Internal signal: the LISTEN connection could not be opened, or dropped mid-run.

    Raised only for connection-class failures of the LISTEN side; carries the
    original error as ``__cause__``. Keeps the LISTEN-wake fallback in
    :func:`run` from ever catching a :func:`drain` error (a pool/DB fault),
    which must surface, not be downgraded to poll-only.
    """


def _wake_loop(
    spec: StreamSpec[T],
    stop_event: threading.Event,
    cursor: int,
    dsn: str,
    drain_fn: "DrainFn" = drain,
) -> int:
    """LISTEN-driven drain loop. Returns the cursor when *stop_event* is set.

    Wraps ONLY the LISTEN-connection operations (open + wait) in connection-error
    handling, re-raising those as :class:`_ListenUnavailable`. The drain is called
    outside that boundary, so a pool/DB fault during drain propagates unchanged and
    is never misclassified as a LISTEN-wake failure.

    *drain_fn* defaults to :func:`drain`; the leg-readiness consumer passes its
    bespoke contiguous-prefix drain (issue #123).
    """
    try:
        listen_cm = db.listen(spec.notify_channel, dsn)
        listener = listen_cm.__enter__()
    except Exception as exc:  # noqa: BLE001 — open failure → maybe fall back
        if db.is_connection_error(exc):
            raise _ListenUnavailable(str(exc)) from exc
        raise
    try:
        log.info("%s processor listening on %r", spec.processor_name, spec.notify_channel)
        while not stop_event.is_set():
            # Wake on a notification or the poll timeout; the reason is
            # irrelevant — we always drain. A LISTEN-connection error here
            # becomes _ListenUnavailable so run() can fall back to poll-only.
            try:
                listener.wait(spec.poll_seconds)
            except Exception as exc:  # noqa: BLE001 — wait failure → maybe fall back
                if db.is_connection_error(exc):
                    raise _ListenUnavailable(str(exc)) from exc
                raise
            # Outside the listen-error boundary: drain exceptions propagate.
            cursor = drain_fn(spec, cursor)
    finally:
        listen_cm.__exit__(None, None, None)
    return cursor


def run(
    spec: StreamSpec[T],
    stop_event: threading.Event,
    dsn: str,
    drain_fn: "DrainFn" = drain,
) -> None:
    """Wake-driven drain loop for *spec*. Returns when *stop_event* is set.

    Drains once, then waits for either a ``LISTEN`` notification (low latency)
    or the poll timeout (backstop) before each subsequent drain. *dsn* is the
    libpq URL for the dedicated LISTEN connection (the pool's connections are
    the wrong shape — ADR-0015); the drain itself still uses the pool.

    If the LISTEN connection cannot be opened or drops mid-run, falls back to a
    pure poll loop (:func:`_poll_loop`) for the rest of the run — the poll
    backstop guarantees progress, so a missing/severed NOTIFY only costs
    latency (issue #71).

    *drain_fn* is the drain the loop invokes on each wake; it defaults to
    :func:`drain` (monotonic advance-to-max-seq). The leg-readiness consumer
    passes its bespoke contiguous-prefix drain here (issue #123, ADR-0027) — the
    one stream whose advance is non-monotonic in ``seq`` — so it reuses this whole
    LISTEN/poll wake machinery without duplicating it.
    """
    with db.transaction() as tx:
        cursor = read_cursor(tx, spec.processor_name)
    log.info("%s processor starting at cursor seq=%d", spec.processor_name, cursor)

    # Drain anything already past the cursor before we start waiting, so an item
    # that landed before LISTEN was registered is not stranded until the first
    # poll timeout.
    cursor = drain_fn(spec, cursor)

    # Wake loop, falling back to poll-only if the LISTEN connection is
    # unavailable. Only a LISTEN-connection failure triggers the fallback —
    # drain errors propagate out of run() unchanged (a drain failure is a
    # pool/DB fault, not a LISTEN-wake fault, and must not be misreported as
    # one or silently downgraded to polling).
    try:
        cursor = _wake_loop(spec, stop_event, cursor, dsn, drain_fn)
    except _ListenUnavailable as exc:
        log.warning(
            "LISTEN wake unavailable (%s); falling back to poll-only drain "
            "(every %.0fs). Correctness is unaffected; only latency rises.",
            exc.__cause__,
            spec.poll_seconds,
        )
        cursor = _poll_loop(spec, stop_event, cursor, drain_fn)

    log.info("%s processor stopped at cursor seq=%d", spec.processor_name, cursor)
