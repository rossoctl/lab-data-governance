"""P-interactions adapter over the shared cursor-driven driver.

The generic drain/poll/LISTEN-wake loop lives in
:mod:`data_governance.processors._driver` (issue #75); this module supplies the
P-interactions *stream spec* — the ``spans`` batch-fetch, the per-span procedure,
and the ``dg_spans_inserted`` channel / ``interactions`` cursor name — and keeps
the two concerns that are genuinely interactions-only:

  * the per-span rehydrate → run → flush procedure (:func:`process_span`), and
  * the finalization tripwire (:func:`_check_finalization`, #73) — the deferred
    two-column horizon detector, which is meaningful only for a stream whose
    items are ``spans`` carrying ``seq``/``arrival_seq``. It stays OUT of the
    shared loop.

Each span is still handled in ONE transaction that rehydrates the span's lineage
region, runs the verbatim per-span procedure, flushes the re-derived region, and
advances the durable cursor (``processor_state``) — all together, so a crash
mid-span commits nothing and the restart re-processes from the same cursor
(ADR-0007 recovery; the re-derive is idempotent). The atomic cursor advance is
now performed by the shared loop (:func:`_driver.advance_cursor`), in the same
per-span transaction as the flush.

The loop wakes on two signals (issue #71): a Postgres ``LISTEN`` notification
fired by the ``dg_spans_inserted`` trigger (migration 0005) when spans land, and
a periodic poll timeout as the backstop. Both lead to the same action — drain
whatever is past the cursor — so the notification carries no payload and its
count is irrelevant. Correctness depends only on the poll backstop; the
``LISTEN`` wake just removes latency. If the listen connection cannot be opened
or drops mid-run, the shared loop falls back to pure polling and still makes
progress.
"""

from __future__ import annotations

import logging
import threading

from data_governance import db
from data_governance.processors import _driver
from data_governance.retrieval import Span, _COLUMNS, _row_to_span

from . import metrics, procedure, state

log = logging.getLogger(__name__)

_SELECT_COLS = ", ".join(_COLUMNS)

# How long the loop sleeps between drains when idle (poll backstop). Also the
# max time a LISTEN wait blocks before re-draining on the poll path. Exposed at
# module level (not just on the spec) because tests monkeypatch it to shrink the
# backstop; the spec below reads it at build time.
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
    change. This stays an interactions-only concern (issue #75): it is
    meaningful only for a ``spans`` stream, so it lives here, not in the shared
    loop.
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

    The caller (the shared loop) owns the transaction boundary and advances the
    cursor in the same *tx*, so the cursor advance commits atomically with the
    derived writes (ADR-0007).
    """
    _check_finalization(span)
    proc = procedure.Processor()
    state.rehydrate(tx, proc, span)
    proc.process(span)
    state.flush(tx, proc, span)


def _fetch_batch(tx: db.Transaction, cursor: int, limit: int) -> list[Span]:
    rows = tx.fetch_all(
        f"SELECT {_SELECT_COLS} FROM spans WHERE seq > %s ORDER BY seq ASC LIMIT %s",
        (cursor, limit),
    )
    return [_row_to_span(r, in_time_window=True) for r in rows]


def _spec() -> _driver.StreamSpec[Span]:
    """Build the P-interactions stream spec for the shared loop.

    Rebuilt per call so ``POLL_SECONDS`` monkeypatched by a test is picked up.
    """
    return _driver.StreamSpec(
        notify_channel=NOTIFY_CHANNEL,
        processor_name=state.PROCESSOR_NAME,
        fetch_batch=_fetch_batch,
        process_item=process_span,
        item_seq=lambda span: span.seq,
        poll_seconds=POLL_SECONDS,
        batch_size=_DRAIN_BATCH,
    )


def read_cursor(tx: db.Transaction) -> int:
    """Read the P-interactions durable cursor (delegates to the shared loop)."""
    return _driver.read_cursor(tx, state.PROCESSOR_NAME)


def drain(cursor: int) -> int:
    """Process every span past *cursor*, one transaction per span. Returns the
    new cursor (the seq of the last span processed, or *cursor* if none)."""
    return _driver.drain(_spec(), cursor)


def run(stop_event: threading.Event, dsn: str) -> None:
    """Wake-driven drain loop over the ``spans`` stream. Returns when
    *stop_event* is set. See :func:`_driver.run`."""
    _driver.run(_spec(), stop_event, dsn)
