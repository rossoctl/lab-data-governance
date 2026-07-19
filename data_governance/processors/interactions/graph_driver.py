"""Graph-algorithm driver over the shared cursor loop (ADR-0025).

The alternative to the streaming driver (:mod:`.driver`), selected by
``INTERACTIONS_ALGORITHM=graph``. It writes the SAME production tables through the
SAME write path (:func:`state.flush`); only the derivation differs — the batch
graph algorithm (:func:`p_interactions_proto.extractor.extract`) replaces the
per-span streaming procedure.

**Re-run-per-span.** The batch algorithm needs a whole trace, but the shared loop
(:mod:`.._driver`) is span-cursor-driven. So on each arriving span we re-derive its
ENTIRE trace: read every span of the trace, run ``extract`` → ``graph_adapter.adapt``
→ ``state.flush``, all in the one transaction the loop owns, then advance the cursor
to that span's seq. This fits the shared loop with no new machinery and is
idempotent — the adapter's deterministic ``uuid5`` ids collapse the re-derived rows
onto the same production rows via ``ON CONFLICT`` (so re-running a trace as its later
spans arrive converges rather than duplicating).

It shares the streaming driver's NOTIFY channel (``dg_spans_inserted``) and the
``interactions`` ``processor_state`` cursor: only ONE algorithm runs at a time
(flag-selected at :func:`__main__.main`), so switching algorithms simply continues
the same cursor. Recovery is the shared loop's "one item, one transaction, cursor
advances with the write" contract (ADR-0007).

Trade-off (accepted for the first cut): re-deriving a trace once per newly-arrived
span is more CPU than the streaming algorithm's incremental derivation. It is always
consistent and simple; a debounce/trace-idle batching layer is a later optimisation.
"""

from __future__ import annotations

import logging
import threading

from data_governance import db
from data_governance.processors import _driver
from data_governance.retrieval import Span, _COLUMNS, _row_to_span

from . import graph_adapter, state
from .driver import NOTIFY_CHANNEL, POLL_SECONDS, _DRAIN_BATCH
from ..p_interactions_proto.extractor import extract

log = logging.getLogger(__name__)

_SELECT_COLS = ", ".join(_COLUMNS)


def _fetch_trace_spans(tx: db.Transaction, trace_id: str) -> list[Span]:
    """Every span of *trace_id*, read within the caller's transaction so the
    re-derivation sees a consistent snapshot with the cursor advance."""
    rows = tx.fetch_all(
        f"SELECT {_SELECT_COLS} FROM spans WHERE trace_id = %s ORDER BY seq ASC",
        (trace_id,),
    )
    return [_row_to_span(r, in_time_window=True) for r in rows]


def process_span(tx: db.Transaction, span: Span) -> None:
    """Re-derive *span*'s whole trace and flush it, within *tx*.

    The caller (the shared loop) owns the transaction and advances the cursor in
    the same *tx*, so the derived writes and the cursor advance commit atomically
    (ADR-0007). Idempotent: deterministic ids collapse re-derives on ``ON CONFLICT``.
    """
    trace_spans = _fetch_trace_spans(tx, span.trace_id)
    if not trace_spans:
        return
    result = extract(trace_spans)
    rows = graph_adapter.adapt(result, trace_spans)
    # The flush aggregate-recompute horizon is `s.seq <= sentinel.seq`; the arriving
    # span carries the trace's current max seq (it is the newest span past the
    # cursor for this trace), so it is the correct horizon sentinel.
    sentinel = max(trace_spans, key=lambda s: s.seq)
    state.flush(tx, rows, sentinel)


def _fetch_batch(tx: db.Transaction, cursor: int, limit: int) -> list[Span]:
    rows = tx.fetch_all(
        f"SELECT {_SELECT_COLS} FROM spans WHERE seq > %s ORDER BY seq ASC LIMIT %s",
        (cursor, limit),
    )
    return [_row_to_span(r, in_time_window=True) for r in rows]


def _spec() -> _driver.StreamSpec[Span]:
    """Graph-algorithm stream spec for the shared loop. Rebuilt per call so a
    test's monkeypatched ``POLL_SECONDS`` is picked up (mirrors ``driver._spec``)."""
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
    """Read the durable cursor (shared with the streaming algorithm)."""
    return _driver.read_cursor(tx, state.PROCESSOR_NAME)


def drain(cursor: int) -> int:
    """Process every span past *cursor*, one transaction per span. Returns the
    new cursor (the seq of the last span processed, or *cursor* if none)."""
    return _driver.drain(_spec(), cursor)


def run(stop_event: threading.Event, dsn: str) -> None:
    """Wake-driven drain loop over the ``spans`` stream, deriving interactions via
    the batch graph algorithm. Returns when *stop_event* is set. See
    :func:`.._driver.run`."""
    _driver.run(_spec(), stop_event, dsn)
