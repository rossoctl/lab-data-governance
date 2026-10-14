"""Alerts adapter over the shared cursor-driven driver (issues #104/#175).

Same shape as the trace-trigger adapter one level down: the generic
drain/poll/LISTEN-wake loop lives in :mod:`data_governance.processors._driver`;
this module supplies the *stream spec* — the ``trace_risk_records`` batch
fetch by ``seq``, the per-record alert procedure, and the
``dg_trace_risk_written`` channel / ``risk_alerts`` cursor name. No readiness
gate (a trace risk record is actionable the moment it is inserted — the
table is insert-only and immutably versioned), so ``_driver`` is reused
verbatim.

Each record is handled in ONE transaction that runs the alert procedure
(:func:`generate.generate_for_trace_risk_record`) AND advances the durable
cursor — so a crash mid-record commits nothing and the restart re-delivers
from the same cursor (ADR-0007): the rolled-back record wrote nothing, so
its second delivery is its first effect. Because this consumer only ever
sees committed trace risk rows, every alert is generated strictly after the
write it depends on is durable (NFR-DAS-003).

The cursor is a ``seq`` watermark, so this stream inherits the repo's
insert-allocation caveat (``docs/PROJECT.md`` §3): a ``seq`` allocated
earlier but committed later than one the drain has already passed is
skipped, and that trace gets no alert until its next new version. It cannot
happen while ``trace_risk_records`` has one writer committing serially: the
single-replica trace-trigger processor. An involuntary overlap of two
trace-trigger pods would reopen it.
"""

from __future__ import annotations

import dataclasses
import logging
import threading

from data_governance import db
from data_governance.processors import _driver
from data_governance.risk import config
from data_governance.risk.alerts import generate

from . import metrics

log = logging.getLogger(__name__)

PROCESSOR_NAME = config.PROCESSOR_NAME_ALERTS
NOTIFY_CHANNEL = config.TRACE_RISK_WRITTEN_CHANNEL_NAME
# Module-level (not just on the spec) so tests can shrink the backstop; the
# spec reads it at build time.
POLL_SECONDS = float(config.ALERTS_POLL_FALLBACK_INTERVAL_SECONDS)

_DRAIN_BATCH = 500


@dataclasses.dataclass(frozen=True)
class TraceRiskWritten:
    """One row of the ``trace_risk_records`` stream: the record to consider
    for an alert and the ``seq`` the loop advances the cursor by."""

    trace_risk_id: str
    seq: int


def process_record(tx: db.Transaction, record: TraceRiskWritten) -> None:
    """Consider *record* for an alert, within *tx*. The shared loop owns the
    transaction and advances the cursor in the same *tx*, so the alert write
    and the advance commit atomically (ADR-0007), so a record is delivered
    again only after its first transaction rolled back."""
    outcome = generate.generate_for_trace_risk_record(tx, record.trace_risk_id)
    # Reference the module globals (not bound imports) so a test's
    # metrics.make_registry() rebind is picked up.
    metrics.alert_outcomes_total.labels(action=outcome.action).inc()
    if outcome.action != "skipped":
        log.info(
            "trace_risk_id=%s seq=%d -> %s alert_id=%s superseded=%s",
            record.trace_risk_id, record.seq, outcome.action,
            outcome.alert_id, outcome.superseded_alert_id,
        )


def _fetch_batch(tx: db.Transaction, cursor: int, limit: int) -> list[TraceRiskWritten]:
    rows = tx.fetch_all(
        "SELECT trace_risk_id, seq FROM trace_risk_records "
        "WHERE seq > %s ORDER BY seq ASC LIMIT %s",
        (cursor, limit),
    )
    return [TraceRiskWritten(trace_risk_id=str(r[0]), seq=int(r[1])) for r in rows]


def _spec() -> _driver.StreamSpec[TraceRiskWritten]:
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
    """Read the alerts durable cursor (delegates to the shared loop)."""
    return _driver.read_cursor(tx, PROCESSOR_NAME)


def drain(cursor: int) -> int:
    """Consider every trace risk record past *cursor*, one transaction per
    record. Returns the new cursor."""
    return _driver.drain(_spec(), cursor)


def run(stop_event: threading.Event, dsn: str) -> None:
    """Wake-driven drain loop over the ``trace_risk_records`` stream: a
    ``LISTEN`` notification on ``dg_trace_risk_written`` (migration 0023) or
    the poll backstop, whichever comes first. Returns when *stop_event* is
    set. See :func:`_driver.run`."""
    _driver.run(_spec(), stop_event, dsn)
