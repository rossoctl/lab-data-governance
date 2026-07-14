"""P-classification adapter over the shared cursor-driven driver.

The generic drain/poll/LISTEN-wake loop lives in
:mod:`data_governance.processors._driver` (issue #75); this module supplies the
P-classification *stream spec* — the ``interaction_payloads`` batch-fetch, the
per-payload procedure, and the ``dg_payloads_inserted`` channel / ``classification``
cursor name — mirroring the P-interactions adapter (``processors/interactions/
driver.py``) exactly, minus the interactions-only concerns (no lineage
rehydration, no finalization tripwire — payloads are content-addressed and never
finalize; ADR-0024).

Each payload is handled in ONE transaction that runs the per-payload procedure
(:func:`process_payload`) AND advances the durable ``classification`` cursor —
together, so a crash mid-payload commits nothing and the restart re-processes
from the same cursor (ADR-0007 recovery). The per-payload write is idempotent:
it inserts one **Classification** row ``ON CONFLICT (content_hash) DO NOTHING``,
so re-processing the same payload never duplicates a row and never mutates an
existing verdict (write-once; ADR-0024). The atomic cursor advance is performed
by the shared loop (:func:`_driver.advance_cursor`) in the same transaction.

The loop wakes on two signals (issue #71): a Postgres ``LISTEN`` notification
fired by the ``dg_payloads_inserted`` trigger (migration 0007) when payloads
land, and a periodic poll timeout as the backstop. Correctness depends only on
the poll backstop; the ``LISTEN`` wake just removes latency. If the listen
connection cannot be opened or drops mid-run, the shared loop falls back to pure
polling and still makes progress.
"""

from __future__ import annotations

import dataclasses
import json
import threading

from data_governance import db
from data_governance.processors import _driver

from . import metrics, verdict

# The durable ``processor_state`` cursor row for this stream. Distinct from
# "interactions" so the two Layer-2 processors keep independent cursors.
PROCESSOR_NAME = "classification"

# Channel the payloads-insert trigger (migration 0007) notifies on. Must match
# the ``pg_notify('dg_payloads_inserted', ...)`` in dg_notify_payloads().
NOTIFY_CHANNEL = "dg_payloads_inserted"

# How long the loop sleeps between drains when idle (poll backstop). Also the
# max time a LISTEN wait blocks before re-draining. Exposed at module level (not
# just on the spec) because tests monkeypatch it to shrink the backstop; the
# spec reads it at build time. Mirrors the interactions driver.
POLL_SECONDS = 5.0

# How many payloads to pull per drain batch (each is still its own transaction).
_DRAIN_BATCH = 500

# Content kinds the Text projection rule has (or will have) a dedicated branch
# for. Any kind NOT in this set — ``unknown`` and any unhandled kind — projects
# via the whole-JSONB serialization fallback, which is the projection-coverage
# gap the ``projection_fallbacks_total`` counter tracks (CONTEXT.md **Text
# projection rule**, issue #81).
#
# HOOK for issue #78: the real per-kind projection lands in #78. Until then the
# verdict stub ignores the payload bytes entirely, so this set is only consulted
# to emit the coverage signal — no text is actually projected yet. When #78
# implements the projection rule, this set should become the authoritative list
# of kinds with a real branch (kept in sync with the rule's ``match``), and the
# increment below moves to fire on the rule's actual fallback path. The counter
# name and semantics stay put so the metric is additive across the two issues.
# Mirrors the Content kind enum (CONTEXT.md) minus ``unknown``.
_PROJECTABLE_CONTENT_KINDS = frozenset(
    {
        "llm_chat_prompt",
        "llm_completion",
        "tool_call_arguments",
        "tool_call_result",
        "http_request_body",
        "http_response_body",
        "agent_message",
    }
)


@dataclasses.dataclass(frozen=True)
class Payload:
    """One row of the ``interaction_payloads`` stream, as P-classification reads
    it: the content-addressed key, its **Content kind**, the JSONB ``content``
    the classifier projects into **Classifiable text**, and the ``seq`` the loop
    advances the cursor by."""

    content_hash: str
    content_kind: str
    content: object
    seq: int


def process_payload(tx: db.Transaction, payload: Payload) -> None:
    """Derive and persist one **Payload**'s **Classification**, within *tx*.

    Runs the classifier (:func:`verdict.classify` — projects the payload's
    ``content`` into its **Classifiable text**, detects **Findings** through the
    detector seam, aggregates the verdict; issue #78) and writes exactly one row
    into ``payload_classifications`` with ``ON CONFLICT (content_hash) DO NOTHING`` —
    write-once and idempotent (ADR-0024): re-processing the same payload after a
    crash is a no-op, never a duplicate row or an in-place mutation.

    The caller (the shared loop) owns the transaction boundary and advances the
    cursor in the same *tx*, so the classification write and the cursor advance
    commit atomically (ADR-0007).
    """
    # Projection-coverage signal (issue #81). The **Text projection rule** has no
    # branch for this payload's **Content kind** (``unknown`` or an unhandled
    # kind), so projection would fall back to whole-JSONB serialization — count
    # it. HOOK for #78: today the verdict stub ignores the bytes, so this only
    # emits the coverage metric; when #78 implements the projection rule this
    # increment moves onto the rule's real fallback path (same counter, same
    # semantics — the metric is additive across the two issues). Referenced as a
    # module global so a test's metrics.make_registry() rebind is picked up.
    if payload.content_kind not in _PROJECTABLE_CONTENT_KINDS:
        metrics.projection_fallbacks_total.inc()

    v = verdict.classify(payload.content_hash, payload.content_kind, payload.content)
    tx.execute(
        "INSERT INTO payload_classifications "
        "(content_hash, sensitivity_level, regulatory_tags, "
        " contains_identity_bundle, is_personalized, primary_domain, "
        " findings, model_version) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s) "
        "ON CONFLICT (content_hash) DO NOTHING",
        (
            payload.content_hash,
            v.sensitivity_level,
            v.regulatory_tags,
            v.contains_identity_bundle,
            v.is_personalized,
            v.primary_domain,
            json.dumps(v.findings, default=str),
            v.model_version,
        ),
    )
    # One increment per payload the drain processed. Reference the module global
    # (not a bound import) so a test's metrics.make_registry() rebind is picked
    # up. Follows the interactions driver's in-procedure increment convention:
    # on the rare crash-mid-payload rollback the counter can lead the committed
    # rows by one (the re-drain then re-counts), i.e. at-least-once — acceptable
    # for a progress gauge.
    metrics.payloads_classified_total.inc()


def _fetch_batch(tx: db.Transaction, cursor: int, limit: int) -> list[Payload]:
    rows = tx.fetch_all(
        "SELECT content_hash, content_kind, content, seq "
        "FROM interaction_payloads WHERE seq > %s ORDER BY seq ASC LIMIT %s",
        (cursor, limit),
    )
    return [
        Payload(content_hash=r[0], content_kind=r[1], content=r[2], seq=int(r[3]))
        for r in rows
    ]


def _spec() -> _driver.StreamSpec[Payload]:
    """Build the P-classification stream spec for the shared loop.

    Rebuilt per call so ``POLL_SECONDS`` monkeypatched by a test is picked up.
    """
    return _driver.StreamSpec(
        notify_channel=NOTIFY_CHANNEL,
        processor_name=PROCESSOR_NAME,
        fetch_batch=_fetch_batch,
        process_item=process_payload,
        item_seq=lambda payload: payload.seq,
        poll_seconds=POLL_SECONDS,
        batch_size=_DRAIN_BATCH,
    )


def read_cursor(tx: db.Transaction) -> int:
    """Read the P-classification durable cursor (delegates to the shared loop)."""
    return _driver.read_cursor(tx, PROCESSOR_NAME)


def drain(cursor: int) -> int:
    """Process every payload past *cursor*, one transaction per payload. Returns
    the new cursor (the seq of the last payload processed, or *cursor* if
    none)."""
    return _driver.drain(_spec(), cursor)


def run(stop_event: threading.Event, dsn: str) -> None:
    """Wake-driven drain loop over the ``interaction_payloads`` stream. Returns
    when *stop_event* is set. See :func:`_driver.run`."""
    _driver.run(_spec(), stop_event, dsn)
