"""Sidecar-algorithm driver over the shared cursor loop (ADR-0028).

The two-span AuthBridge sidecar derivation (:mod:`.sidecar`), selected by
``INTERACTIONS_ALGORITHM=sidecar``. It writes the same production tables as the
streaming and graph algorithms, through its own trace-reconcile write path
(:func:`sidecar._write` — see the module docstring for why it does not share
:func:`state.flush`).

**Re-derive-per-span.** Like the graph driver, the derivation needs a whole
trace but the shared loop (:mod:`.._driver`) is span-cursor-driven: on each
arriving span we reconcile its ENTIRE trace in the loop's one transaction, then
the loop advances the cursor to that span's seq. Idempotent — deterministic
``uuid5`` ids collapse re-derives via ``ON CONFLICT``, and the trace-scoped
reconcile deletes rows the new span set no longer justifies.

It shares the streaming driver's NOTIFY channel (``dg_spans_inserted``) and the
``interactions`` ``processor_state`` cursor: only ONE algorithm runs at a time
(flag-selected at :func:`__main__.main`), so switching algorithms simply
continues the same cursor. Recovery is the shared loop's "one item, one
transaction, cursor advances with the write" contract (ADR-0007).
"""

from __future__ import annotations

import logging
import threading

from data_governance import db
from data_governance.processors import _driver
from data_governance.retrieval import Span
from data_governance.retrieval.spans import _COLUMNS, _row_to_span

from . import sidecar, state
from .driver import NOTIFY_CHANNEL, POLL_SECONDS, _DRAIN_BATCH

log = logging.getLogger(__name__)

_SELECT_COLS = ", ".join(_COLUMNS)


def process_span(tx: db.Transaction, span: Span) -> None:
    """Reconcile *span*'s whole trace within *tx* (fetch + derive + write).

    The caller (the shared loop) owns the transaction and advances the cursor in
    the same *tx*, so the derived writes and the cursor advance commit atomically
    (ADR-0007)."""
    sidecar.derive_trace(tx, span.trace_id)


def _fetch_batch(tx: db.Transaction, cursor: int, limit: int) -> list[Span]:
    rows = tx.fetch_all(
        f"SELECT {_SELECT_COLS} FROM spans WHERE seq > %s ORDER BY seq ASC LIMIT %s",
        (cursor, limit),
    )
    return [_row_to_span(r, in_time_window=True) for r in rows]


def _spec() -> _driver.StreamSpec[Span]:
    """Sidecar-algorithm stream spec for the shared loop. Rebuilt per call so a
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
    """Read the durable cursor (shared with the other algorithms)."""
    return _driver.read_cursor(tx, state.PROCESSOR_NAME)


def drain(cursor: int) -> int:
    """Process every span past *cursor*, one transaction per span. Returns the
    new cursor (the seq of the last span processed, or *cursor* if none)."""
    return _driver.drain(_spec(), cursor)


def run(stop_event: threading.Event, dsn: str) -> None:
    """Wake-driven drain loop over the ``spans`` stream, deriving interactions
    via the two-span sidecar reconcile. Returns when *stop_event* is set. See
    :func:`.._driver.run`."""
    _driver.run(_spec(), stop_event, dsn)
