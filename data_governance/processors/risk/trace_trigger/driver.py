"""Trace-risk-trigger adapter over the shared cursor-driven driver (#102/#164).

The generic drain/poll/LISTEN-wake loop lives in
:mod:`data_governance.processors._driver` (issue #75); this module supplies
the trace-trigger *stream spec* — the ``interaction_risk_records`` batch
fetch, the per-record recompute procedure, and the
``dg_interaction_risk_written`` channel / ``risk_trace_trigger`` cursor name —
mirroring the P-entity-ready adapter exactly. Like ``entities``, the
``interaction_risk_records`` stream has **no readiness gate** (a risk record
is actionable the moment it is inserted — the table is insert-only and
immutably versioned), so the shared ``_driver`` is reused **verbatim**.

Each record is handled in ONE transaction that runs the trace recompute
(:func:`data_governance.risk.engine.trace_compute.compute_trace_risk`, passed
this transaction) AND advances the durable ``risk_trace_trigger`` cursor —
together, so a crash or recompute failure mid-record commits nothing and the
restart re-delivers from the same cursor (ADR-0007 recovery). The recompute
is idempotent (identical rollup ⇒ no write), so a re-delivered record never
double-writes a trace risk version.

One recompute per delivered record (AC-DAS-008: every interaction risk
create/update triggers exactly one trace recomputation for its trace). A
burst of records for the same trace coalesces at the NOTIFY layer but NOT at
the drain layer — each record past the cursor still drives its own recompute;
all but the last are typically idempotent no-ops once the rollup reflects the
latest state.
"""

from __future__ import annotations

import dataclasses
import threading

from data_governance import db
from data_governance.processors import _driver
from data_governance.risk import config
from data_governance.risk.engine import trace_compute

from . import metrics

# The durable ``processor_state`` cursor row for this stream — the name #98
# reserved in risk/config.py so no sibling issue could collide on it.
PROCESSOR_NAME = config.PROCESSOR_NAME_TRACE_TRIGGER

# Channel migration 0016's statement-level dg_interaction_risk_notify trigger
# fires on. Config-backed (RISK_TRACE_TRIGGER_CHANNEL_NAME).
NOTIFY_CHANNEL = config.INTERACTION_RISK_WRITTEN_CHANNEL_NAME

# Poll backstop (risk.trace_trigger.poll_fallback_interval_seconds, PRD §10 /
# AC-DAS-018: missed-NOTIFY recovery within this interval). Exposed at module
# level (not just on the spec) because tests monkeypatch it to shrink the
# backstop; the spec reads it at build time. Mirrors the sibling drivers.
POLL_SECONDS = float(config.TRACE_TRIGGER_POLL_FALLBACK_INTERVAL_SECONDS)

# How many records to pull per drain batch (each is still its own transaction).
_DRAIN_BATCH = 500


@dataclasses.dataclass(frozen=True)
class RiskWritten:
    """One row of the ``interaction_risk_records`` stream, as this consumer
    reads it: the ``trace_id`` to recompute and the ``seq`` the loop advances
    the cursor by. A driver-local read shape — the recompute re-reads the
    trace's full current record set itself (FR-DAS-020), so nothing else from
    the row is carried."""

    trace_id: str
    seq: int


def process_record(tx: db.Transaction, record: RiskWritten) -> None:
    """Recompute the trace risk rollup for *record*'s trace, within *tx*.

    The caller (the shared loop) owns the transaction boundary and advances
    the cursor in the same *tx*, so the rollup write and the cursor advance
    commit atomically (ADR-0007): a recompute failure rolls back the cursor
    advance and the record is re-delivered on a later wake, never silently
    skipped. ``compute_trace_risk`` is idempotent, so the re-delivery (and
    the same-trace burst case) never double-writes a version.
    """
    trace_compute.compute_trace_risk(record.trace_id, tx=tx)
    # Reference the module global (not a bound import) so a test's
    # metrics.make_registry() rebind is picked up.
    metrics.trace_recomputes_total.inc()


def _fetch_batch(tx: db.Transaction, cursor: int, limit: int) -> list[RiskWritten]:
    rows = tx.fetch_all(
        "SELECT trace_id, seq "
        "FROM interaction_risk_records WHERE seq > %s ORDER BY seq ASC LIMIT %s",
        (cursor, limit),
    )
    return [RiskWritten(trace_id=r[0], seq=int(r[1])) for r in rows]


def _spec() -> _driver.StreamSpec[RiskWritten]:
    """Build the trace-trigger stream spec for the shared loop. Rebuilt per
    call so ``POLL_SECONDS`` monkeypatched by a test is picked up."""
    return _driver.StreamSpec(
        notify_channel=NOTIFY_CHANNEL,
        processor_name=PROCESSOR_NAME,
        fetch_batch=_fetch_batch,
        process_item=process_record,
        item_seq=lambda record: record.seq,
        poll_seconds=POLL_SECONDS,
        batch_size=_DRAIN_BATCH,
    )


def read_cursor(tx: db.Transaction) -> int:
    """Read the trace-trigger durable cursor (delegates to the shared loop)."""
    return _driver.read_cursor(tx, PROCESSOR_NAME)


def drain(cursor: int) -> int:
    """Recompute for every record past *cursor*, one transaction per record.
    Returns the new cursor (the seq of the last record processed, or *cursor*
    if none)."""
    return _driver.drain(_spec(), cursor)


def run(stop_event: threading.Event, dsn: str) -> None:
    """Wake-driven drain loop over the ``interaction_risk_records`` stream.
    Returns when *stop_event* is set. See :func:`_driver.run`.

    Wakes on a ``LISTEN`` notification on ``dg_interaction_risk_written`` (low
    latency; fired by migration 0016's trigger) or the poll backstop,
    whichever comes first; both lead to the same drain. Falls back to
    poll-only if the LISTEN connection is unavailable — a missing/severed
    notification only costs latency (ADR-0015, AC-DAS-018).
    """
    _driver.run(_spec(), stop_event, dsn)
