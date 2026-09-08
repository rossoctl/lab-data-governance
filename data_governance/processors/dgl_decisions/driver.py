"""DGL-decisions processor — derives ``dgl_decisions`` rows from decision spans.

Every time a span with ``service_name = 'dgl-governance'`` arrives in the
``spans`` table, this processor extracts the DGL attributes and inserts one row
into ``dgl_decisions``. It follows the standard ADR-0007 cursor pattern used by
the interactions and classification processors:

- Cursors over ``spans.seq`` (the same stream as the sidecar interactions
  processor, different processor_state row).
- Wakes on the ``dg_spans_inserted`` NOTIFY channel (same trigger as the
  sidecar interactions processor — both watch the same ``spans`` table).
- One span → one transaction → cursor advances atomically with the write.
- Idempotent: ``ON CONFLICT (invocation_id, phase) DO NOTHING`` makes
  re-processing the same span on crash-recovery a safe no-op.

Span shape emitted by intent-governance (authbridge plugin):

    service_name  = "dgl-governance"
    span.name     = "dgl <component> <phase>"  e.g. "dgl get_employee_profile tool_request"
    attributes = {
        "dgl.component":        "<tool_name>",
        "dgl.phase":            "tool_request" | "tool_result",
        "dgl.invocation_id":    "INV-<nanos>",
        "dgl.exchange_id":      "<16-char hex span_id of lineage anchor>",
        "dgl.trace_id":         "<32-char hex trace_id>",
        "dgl.decision":         "ALLOW" | "BLOCK" | "MODIFY",
        "dgl.reason":           "<reason_code>",
        "dgl.payload":          "<original JSON payload>",          (always present)
        "dgl.effective_payload":"<modified JSON payload>",          (MODIFY only)
    }

Any span that does NOT have ``service_name = 'dgl-governance'`` is silently
skipped — this processor shares the ``spans`` cursor with the interactions
processor, so it sees every span; filtering is done here, not at ingestion.
"""

from __future__ import annotations

import json
import logging
import threading

from data_governance import db
from data_governance.processors import _driver
from data_governance.retrieval import Span
from data_governance.retrieval.spans import _COLUMNS, _row_to_span

log = logging.getLogger(__name__)

# Shared with the interactions processors — same source stream, different cursor.
NOTIFY_CHANNEL = "dg_spans_inserted"
PROCESSOR_NAME = "dgl_decisions"
POLL_SECONDS   = 5.0
_DRAIN_BATCH   = 500

_DGL_SERVICE_NAME = "dgl-governance"

_SELECT_COLS = ", ".join(_COLUMNS)

_INSERT_SQL = """
INSERT INTO dgl_decisions
    (exchange_id, trace_id, invocation_id, component, phase,
     decision, reason, execution_id, original_payload, modified_payload, occurred_at)
VALUES
    (%s, %s, %s, %s, %s,
     %s, %s, %s, %s::jsonb, %s::jsonb, %s)
ON CONFLICT (invocation_id, phase) DO NOTHING
"""


# ---------------------------------------------------------------------------
# Per-span processing
# ---------------------------------------------------------------------------


def _attr(span: Span, key: str) -> str:
    """Return ``span.attributes[key]`` as a string, or '' when absent."""
    val = (span.attributes or {}).get(key)
    return str(val) if val is not None else ""


def _json_or_none(raw: str) -> str | None:
    """Return the string as-is if it is valid JSON, else None.

    OTel attributes are strings; the receiver stores them verbatim. We need
    to validate before passing to psycopg's ``::jsonb`` cast so a malformed
    value does not abort the transaction.
    """
    if not raw:
        return None
    try:
        json.loads(raw)
        return raw
    except (ValueError, TypeError):
        return None


def process_span(tx: db.Transaction, span: Span) -> None:
    """Insert a dgl_decisions row for *span* if it is a DGL decision span.

    Silently skips spans from any other service. Called by the shared cursor
    loop inside the per-span transaction that also advances the cursor.
    """
    if span.service_name != _DGL_SERVICE_NAME:
        return

    exchange_id      = _attr(span, "dgl.exchange_id")
    trace_id         = _attr(span, "dgl.trace_id")
    invocation_id    = _attr(span, "dgl.invocation_id")
    component        = _attr(span, "dgl.component")
    phase            = _attr(span, "dgl.phase")
    decision         = _attr(span, "dgl.decision")
    reason           = _attr(span, "dgl.reason") or None
    execution_id     = _attr(span, "dgl.execution_id") or None
    original_payload = _json_or_none(_attr(span, "dgl.payload"))
    # effective_payload / modified_payload is only present on MODIFY decisions.
    modified_payload = _json_or_none(_attr(span, "dgl.effective_payload"))
    occurred_at      = span.started_at

    if not exchange_id or not invocation_id or not decision:
        log.warning(
            "dgl_decisions processor: span %s missing required dgl.* attributes, skipping",
            span.span_id,
        )
        return

    tx.execute(
        _INSERT_SQL,
        (
            exchange_id, trace_id, invocation_id, component, phase,
            decision, reason, execution_id, original_payload, modified_payload, occurred_at,
        ),
    )
    log.debug(
        "dgl_decisions: recorded exchange=%s invocation=%s phase=%s decision=%s",
        exchange_id, invocation_id, phase, decision,
    )


# ---------------------------------------------------------------------------
# Cursor-loop plumbing (mirrors sidecar_driver / graph_driver)
# ---------------------------------------------------------------------------


def _fetch_batch(tx: db.Transaction, cursor: int, limit: int) -> list[Span]:
    rows = tx.fetch_all(
        f"SELECT {_SELECT_COLS} FROM spans WHERE seq > %s ORDER BY seq ASC LIMIT %s",
        (cursor, limit),
    )
    return [_row_to_span(r, in_time_window=True) for r in rows]


def _spec() -> _driver.StreamSpec[Span]:
    return _driver.StreamSpec(
        notify_channel=NOTIFY_CHANNEL,
        processor_name=PROCESSOR_NAME,
        fetch_batch=_fetch_batch,
        process_item=process_span,
        item_seq=lambda span: span.seq,
        poll_seconds=POLL_SECONDS,
        batch_size=_DRAIN_BATCH,
    )


def run(stop_event: threading.Event, dsn: str) -> None:
    """Wake-driven drain loop. Returns when *stop_event* is set."""
    _driver.run(_spec(), stop_event, dsn)
