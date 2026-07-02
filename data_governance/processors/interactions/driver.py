"""Cursor-driven driver for the P-interactions processor.

Drains the ``spans`` table by ``seq`` and processes each span end-to-end. Each
span is handled in ONE transaction that rehydrates the span's lineage region,
runs the verbatim per-span procedure, flushes the re-derived region, and
advances the durable cursor (``processor_state``) — all together, so a crash
mid-span commits nothing and the restart re-processes from the same cursor
(ADR-0007 recovery; the re-derive is idempotent).

The loop wakes on two signals (issue #71): a Postgres ``LISTEN`` notification
fired by the ``dg_spans_inserted`` trigger (migration 0005) when spans land,
and a periodic poll timeout as the backstop. Both lead to the same action —
drain whatever is past the cursor — so the notification carries no payload and
its count is irrelevant. Correctness depends only on the poll backstop; the
``LISTEN`` wake just removes latency. If the listen connection cannot be opened
or drops mid-run, the loop falls back to pure polling and still makes progress.
"""

from __future__ import annotations

import logging
import threading

from data_governance import db
from data_governance.retrieval import Span, _COLUMNS, _row_to_span

from . import metrics, procedure, state

log = logging.getLogger(__name__)

_SELECT_COLS = ", ".join(_COLUMNS)

# How long the loop sleeps between drains when idle (poll backstop). Also the
# max time a LISTEN wait blocks before re-draining on the poll path.
POLL_SECONDS = 5.0

# Channel the spans-insert trigger (migration 0005) notifies on. Must match the
# ``pg_notify('dg_spans_inserted', ...)`` in dg_notify_spans().
NOTIFY_CHANNEL = "dg_spans_inserted"

# How many spans to pull per drain batch (each is still its own transaction).
_DRAIN_BATCH = 500

# Finalization tripwire (#73). Gates the one-time WARNING so the log isn't
# flooded once finalization starts; the metric still increments per span. The
# drain loop is single-threaded, so a plain module flag is sufficient.
_finalization_warned = False


def _check_finalization(span: Span) -> None:
    """Tripwire for the deferred two-column horizon (slice #73).

    The processor uses ``seq`` for BOTH the cursor and the lineage horizon
    (slice #70), which is faithful only while every span has
    ``seq == arrival_seq``. Per ADR-0004 ``seq`` advances when a span is
    finalized out of arrival order while ``arrival_seq`` stays fixed, so the
    first span with ``seq != arrival_seq`` is the signal that the deferred split
    (cursor = ``seq``, horizon = ``arrival_seq``) must now be implemented.

    Increments ``finalization_observed_total`` on every such span and logs a
    single WARNING on the first one. Pure detector — no classification/output
    change.
    """
    global _finalization_warned  # noqa: PLW0603
    if span.seq == span.arrival_seq:
        return
    # Reference the module global (not a bound import) so a test's
    # metrics.make_registry() rebind is picked up.
    metrics.finalization_observed_total.inc()
    if not _finalization_warned:
        _finalization_warned = True
        log.warning(
            "finalization observed: span %s (trace %s) has seq=%d != "
            "arrival_seq=%d. The lineage horizon currently uses seq for both "
            "the cursor and the horizon (slice #70); finalization out of "
            "arrival order means the deferred two-column split "
            "(cursor=seq, horizon=arrival_seq) must now be implemented.",
            span.span_id,
            span.trace_id,
            span.seq,
            span.arrival_seq,
        )


def process_span(tx: db.Transaction, span: Span) -> None:
    """Rehydrate → run the verbatim procedure → flush, within *tx*.

    The caller owns the transaction boundary (so the cursor advance commits
    atomically with the derived writes).
    """
    _check_finalization(span)
    proc = procedure.Processor()
    state.rehydrate(tx, proc, span)
    proc.process(span)
    state.flush(tx, proc, span)


def read_cursor(tx: db.Transaction) -> int:
    row = tx.fetch_one(
        "SELECT last_processed_seq FROM processor_state WHERE processor_name = %s",
        (state.PROCESSOR_NAME,),
    )
    return int(row[0]) if row else 0


def _fetch_batch(tx: db.Transaction, cursor: int, limit: int) -> list[Span]:
    rows = tx.fetch_all(
        f"SELECT {_SELECT_COLS} FROM spans WHERE seq > %s ORDER BY seq ASC LIMIT %s",
        (cursor, limit),
    )
    return [_row_to_span(r, in_time_window=True) for r in rows]


def drain(cursor: int) -> int:
    """Process every span past *cursor*, one transaction per span. Returns the
    new cursor (the seq of the last span processed, or *cursor* if none)."""
    while True:
        # Read the next batch in its own short transaction; each span is then
        # processed (and the cursor advanced) in its own transaction.
        with db.transaction() as tx:
            batch = _fetch_batch(tx, cursor, _DRAIN_BATCH)
        if not batch:
            return cursor
        for span in batch:
            with db.transaction() as tx:
                process_span(tx, span)
            cursor = span.seq
        if len(batch) < _DRAIN_BATCH:
            return cursor


def _poll_loop(stop_event: threading.Event, cursor: int) -> int:
    """Pure poll-drain loop (the backstop). Sleeps up to POLL_SECONDS between
    drains. Used when no LISTEN connection is available. Returns the cursor."""
    while not stop_event.is_set():
        cursor = drain(cursor)
        stop_event.wait(timeout=POLL_SECONDS)
    return cursor


def run(stop_event: threading.Event, dsn: str) -> None:
    """Wake-driven drain loop. Returns when *stop_event* is set.

    Drains once, then waits for either a ``LISTEN`` notification (low latency)
    or the poll timeout (backstop) before each subsequent drain. *dsn* is the
    libpq URL for the dedicated LISTEN connection (the pool's connections are
    the wrong shape — ADR-0015); the drain itself still uses the pool.

    If the LISTEN connection cannot be opened or drops mid-run, falls back to a
    pure poll loop (:func:`_poll_loop`) for the rest of the run — the poll
    backstop guarantees progress, so a missing/severed NOTIFY only costs
    latency (issue #71).
    """
    with db.transaction() as tx:
        cursor = read_cursor(tx)
    log.info("interactions processor starting at cursor seq=%d", cursor)

    # Drain anything already past the cursor before we start waiting, so a span
    # that landed before LISTEN was registered is not stranded until the first
    # poll timeout.
    cursor = drain(cursor)

    # Wake loop, falling back to poll-only if the LISTEN connection is
    # unavailable. Only a LISTEN-connection failure triggers the fallback —
    # drain() errors propagate out of run() unchanged (a drain failure is a
    # pool/DB fault, not a LISTEN-wake fault, and must not be misreported as
    # one or silently downgraded to polling).
    try:
        cursor = _wake_loop(stop_event, cursor, dsn)
    except _ListenUnavailable as exc:
        log.warning(
            "LISTEN wake unavailable (%s); falling back to poll-only drain "
            "(every %.0fs). Correctness is unaffected; only latency rises.",
            exc.__cause__,
            POLL_SECONDS,
        )
        cursor = _poll_loop(stop_event, cursor)

    log.info("interactions processor stopped at cursor seq=%d", cursor)


class _ListenUnavailable(Exception):
    """Internal signal: the LISTEN connection could not be opened, or dropped mid-run.

    Raised only for connection-class failures of the LISTEN side; carries the
    original error as ``__cause__``. Keeps the LISTEN-wake fallback in
    :func:`run` from ever catching a :func:`drain` error (a pool/DB fault),
    which must surface, not be downgraded to poll-only.
    """


def _wake_loop(stop_event: threading.Event, cursor: int, dsn: str) -> int:
    """LISTEN-driven drain loop. Returns the cursor when *stop_event* is set.

    Wraps ONLY the LISTEN-connection operations (open + wait) in connection-error
    handling, re-raising those as :class:`_ListenUnavailable`. :func:`drain` is
    called outside that boundary, so a pool/DB fault during drain propagates
    unchanged and is never misclassified as a LISTEN-wake failure.
    """
    try:
        listen_cm = db.listen(NOTIFY_CHANNEL, dsn)
        listener = listen_cm.__enter__()
    except Exception as exc:  # noqa: BLE001 — open failure → maybe fall back
        if db.is_connection_error(exc):
            raise _ListenUnavailable(str(exc)) from exc
        raise
    try:
        log.info("interactions processor listening on %r", NOTIFY_CHANNEL)
        while not stop_event.is_set():
            # Wake on a notification or the poll timeout; the reason is
            # irrelevant — we always drain. A LISTEN-connection error here
            # becomes _ListenUnavailable so run() can fall back to poll-only.
            try:
                listener.wait(POLL_SECONDS)
            except Exception as exc:  # noqa: BLE001 — wait failure → maybe fall back
                if db.is_connection_error(exc):
                    raise _ListenUnavailable(str(exc)) from exc
                raise
            # Outside the listen-error boundary: drain() exceptions propagate.
            cursor = drain(cursor)
    finally:
        listen_cm.__exit__(None, None, None)
    return cursor
