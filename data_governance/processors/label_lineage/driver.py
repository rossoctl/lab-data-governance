"""P-label-lineage adapter: the readiness-gated stream consumer that derives label
lineage per trace.

The discipline is leg-ready's and data lineage's, nothing new:

- **Readiness, contiguous prefix.** Legs are drained by ``seq`` from
  ``interaction_legs``; a leg is ready when its parent interaction is visible and
  its payload is absent or classified. :func:`readiness_cursor.contiguous_prefix`
  delivers the leading ready run and holds at the first unready leg, so a leg is
  derived only once the classifier has spoken for it. Woken by
  ``dg_interaction_leg_ready`` (classification and interactions fire it), with
  the poll backstop for correctness.
- **Whole-trace re-derivation.** A delivered run of ready legs re-derives each
  trace it touches once, in the one transaction that advances the
  ``label_lineage`` cursor to the run's last leg: edges upserted, leg rows
  upserted, each trace's rows the derivation did not produce deleted (set
  membership), the status row upserted (:mod:`.store`). A crash re-does the run;
  the derivation is idempotent and order-independent, a function of committed
  state.
- **The stale arm.** The cursor never revisits a leg it passed, so a second arm
  re-derives traces holding a leg at or below the cursor whose label row was
  derived from another payload or matcher, was derived before the payload was
  classified, or is missing although the payload is classified. It never moves the
  cursor; the predicate is its durable state.

Every derivation notifies ``dg_interaction_leg_ready`` at commit: with the risk flag
on, the label row is what makes a leg ready for leg-ready, which listens there (this
processor wakes once more and finds nothing new).

When a re-derivation changes a leg's inherited summary — including a leg that
gains its first edges — the leg's interaction is written to
``risk_recompute_requests`` in the same transaction. That is a fact about the
derivation; this processor reads no risk configuration. Leg-ready alone decides
what to do with the requests (``RISK_INHERITED_LABELS``): drain and recompute when
on, leave them when off (one row per interaction bounds the table).
"""

from __future__ import annotations

import dataclasses
import logging
import threading

from data_governance import db
from data_governance.label_lineage import find, fold
from data_governance.processors import _driver
from data_governance.processors import readiness_cursor as rc
from data_governance.processors.data_lineage.traversal import Entity

from . import store

log = logging.getLogger(__name__)

PROCESSOR_NAME = "label_lineage"
NOTIFY_CHANNEL = "dg_interaction_leg_ready"
POLL_SECONDS = 5.0
_DRAIN_BATCH = 500


@dataclasses.dataclass(frozen=True)
class ReadyLeg:
    trace_id: str | None
    seq: int
    leg_type: str
    ready: bool


@dataclasses.dataclass(frozen=True)
class Config:
    """What one run derives with: the finder and its name."""

    finder: find.Finder
    matcher: str


def configured() -> Config:
    """The configuration from the environment; raises on an unknown finder."""
    name = find.configured_name()
    return Config(find.get_finder(name), name)


_FETCH_SQL = """
SELECT i.trace_id, l.seq, l.leg_type::text,
       (i.trace_id IS NOT NULL AND (l.payload_hash IS NULL OR pc.content_hash IS NOT NULL)) AS ready
  FROM interaction_legs l
  LEFT JOIN interactions i ON i.id = l.interaction_id
  LEFT JOIN payload_classifications pc ON pc.content_hash = l.payload_hash
 WHERE l.seq > %s
 ORDER BY l.seq ASC
 LIMIT %s
"""


def _fetch_batch(tx: db.Transaction, cursor: int, limit: int) -> list[ReadyLeg]:
    return [ReadyLeg(r[0], int(r[1]), r[2], bool(r[3])) for r in tx.fetch_all(_FETCH_SQL, (cursor, limit))]


def load_trace(tx: db.Transaction, trace_id: str) -> fold.TraceInput:
    """Every leg of *trace_id* with its wire time and its parent's caller/callee,
    the entities they name, the payloads they carry and the findings of the
    classified ones — one snapshot, in the caller's transaction."""
    rows = tx.fetch_all(
        "SELECT l.interaction_id, l.leg_type::text, l.seq, i.caller_entity_id, i.callee_entity_id, "
        "       l.payload_hash, l.occurred_at "
        "FROM interaction_legs l JOIN interactions i ON i.id = l.interaction_id "
        "WHERE i.trace_id = %s",
        (trace_id,),
    )
    legs = [fold.WireLeg(r[0], r[1], int(r[2]), r[3], r[4], r[5], occurred_at=r[6]) for r in rows]
    entity_ids = sorted({leg.caller_entity_id for leg in legs} | {leg.callee_entity_id for leg in legs})
    hashes = sorted({leg.payload_hash for leg in legs if leg.payload_hash is not None})
    entities = {
        r[0]: Entity(id=r[0], natural_key=r[1], kind=r[2])
        for r in tx.fetch_all("SELECT id, natural_key, kind::text FROM entities WHERE id = ANY(%s)",
                              (entity_ids,))
    }
    payloads = dict(tx.fetch_all(
        "SELECT content_hash, content FROM interaction_payloads WHERE content_hash = ANY(%s)", (hashes,)))
    findings = {
        r[0]: list(r[1] or [])
        for r in tx.fetch_all(
            "SELECT content_hash, findings FROM payload_classifications WHERE content_hash = ANY(%s)",
            (hashes,))
    }
    return fold.TraceInput(trace_id=trace_id, legs=legs, entities=entities, payloads=payloads,
                           findings=findings)


def derive_and_persist(tx: db.Transaction, trace_id: str, config: Config) -> set[str]:
    """Re-derive *trace_id* and write it within *tx*. Returns the interactions whose
    inherited summary changed, and queues their recompute in the same transaction."""
    trace = load_trace(tx, trace_id)
    if not trace.legs:
        return set()
    changed = store.write(tx, fold.derive(trace, config.finder), config.matcher)
    # The rows just written are what leg-ready's readiness waits for with the risk
    # flag on: wake it at commit instead of leaving it to its poll backstop.
    tx.execute("SELECT pg_notify(%s, '')", (NOTIFY_CHANNEL,))
    if changed:
        tx.execute_many(
            "INSERT INTO risk_recompute_requests (interaction_id, reason) VALUES (%s, %s) "
            "ON CONFLICT (interaction_id) DO UPDATE SET requested_at = now(), reason = EXCLUDED.reason",
            [(interaction_id, "inherited labels changed") for interaction_id in sorted(changed)],
        )
    return changed


def _drain_ready(spec: _driver.StreamSpec[ReadyLeg], cursor: int, config: Config) -> int:
    """The cursor arm: take the leading ready run past *cursor* (holding at the first
    unready leg), derive each trace it touches once, and advance the cursor to the
    run's last leg — one transaction per run."""
    while True:
        with db.transaction() as tx:
            batch = spec.fetch_batch(tx, cursor, spec.batch_size)
        if not batch:
            return cursor
        result = rc.contiguous_prefix(
            [rc.ReadinessItem(seq=leg.seq, leg_type=leg.leg_type, ready=leg.ready) for leg in batch], cursor)
        if not result.delivered:
            return cursor
        by_seq = {leg.seq: leg for leg in batch}
        traces = dict.fromkeys(by_seq[item.seq].trace_id for item in result.delivered)
        with db.transaction() as tx:
            for trace_id in traces:
                derive_and_persist(tx, trace_id, config)
            _driver.advance_cursor(tx, spec.processor_name, result.cursor)
        cursor = result.cursor
        if len(result.delivered) < len(batch) or len(batch) < spec.batch_size:
            return cursor


_STALE_SQL = """
SELECT i.trace_id
  FROM interaction_legs l
  JOIN interactions i ON i.id = l.interaction_id
  LEFT JOIN leg_inherited_labels m ON m.interaction_id = l.interaction_id AND m.leg_type = l.leg_type
  LEFT JOIN payload_classifications pc ON pc.content_hash = l.payload_hash
 WHERE l.seq <= %s
   AND ((m.interaction_id IS NULL AND l.payload_hash IS NOT NULL AND pc.content_hash IS NOT NULL)
        OR (m.interaction_id IS NOT NULL
            AND (m.payload_hash IS DISTINCT FROM l.payload_hash
                 OR m.matcher <> %s
                 OR (NOT m.classified AND pc.content_hash IS NOT NULL))))
 GROUP BY i.trace_id
 ORDER BY min(l.seq)
 LIMIT %s
"""


def _redrive_stale(cursor: int, config: Config, limit: int = _DRAIN_BATCH) -> int:
    """The stale arm: re-derive every trace holding a leg at or below *cursor*
    whose label row no longer matches it. Never moves the cursor (the oldest
    offending leg first, so a sticky trace cannot starve the rest)."""
    with db.transaction() as tx:
        traces = [r[0] for r in tx.fetch_all(_STALE_SQL, (cursor, config.matcher, limit))]
    for trace_id in traces:
        with db.transaction() as tx:
            derive_and_persist(tx, trace_id, config)
    if traces:
        log.info("label-lineage re-derived %d trace(s) whose legs changed under the cursor", len(traces))
    return len(traces)


def _spec() -> _driver.StreamSpec[ReadyLeg]:
    """The stream spec for the shared wake loop. ``process_item`` is never driven
    by ``_driver.drain`` here (the drain is the readiness-gated one above)."""
    return _driver.StreamSpec(
        notify_channel=NOTIFY_CHANNEL,
        processor_name=PROCESSOR_NAME,
        fetch_batch=_fetch_batch,
        process_item=lambda tx, leg: None,
        item_seq=lambda leg: leg.seq,
        poll_seconds=POLL_SECONDS,
        batch_size=_DRAIN_BATCH,
    )


def read_cursor(tx: db.Transaction) -> int:
    return _driver.read_cursor(tx, PROCESSOR_NAME)


def drain(cursor: int, config: Config | None = None) -> int:
    """One wake: the cursor arm, then the stale arm over the advanced cursor.
    Returns the new cursor."""
    config = config or configured()
    cursor = _drain_ready(_spec(), cursor, config)
    _redrive_stale(cursor, config)
    return cursor


def run(stop_event: threading.Event, dsn: str, config: Config | None = None) -> None:
    """Wake-driven drain over ``interaction_legs`` until *stop_event* is set."""
    config = config or configured()
    _driver.run(_spec(), stop_event, dsn, drain_fn=lambda spec, cursor: drain(cursor, config))
