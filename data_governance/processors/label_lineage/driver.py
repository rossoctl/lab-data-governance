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

- **Across conversations** (``LABEL_CROSS_TRACE_WINDOW_HOURS`` > 0, #273). Before a
  trace is derived, :func:`load_priors` reads, for each of its entities that keeps
  state (an agent, a declared source — ``memory.accumulates`` /
  ``memory.is_entity_source``), the payload-bearing legs of OTHER traces that
  entity was handed or said inside the window, each with the occurrences it holds
  (its own findings and what reached it there, rebuilt from the stored edges). They
  join the fold as :class:`~data_governance.label_lineage.fold.Prior` inputs. What
  crosses is written as ordinary edges with ``from_trace_id`` / ``origin_trace_id``
  set. The leg summaries count them, so a label that crossed changes the summary
  of every leg it reaches and queues that interaction's recompute.
- **The dirty arm (S7).** A trace derived with priors was derived from another
  trace's edges. When a derivation changes a trace's edge set, every other trace
  that shares a stateful entity with it inside the window (the mirror of the
  priors' bounds) is queued in ``label_lineage_dirty_traces``; after the stale arm,
  the drain re-derives up to a batch of queued traces, deleting each row in the
  transaction that re-derives it. The change gate bounds the rounds: a derivation
  that changes nothing queues nothing, so two traces that read each other settle.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import math
import os
import threading

from data_governance import db
from data_governance.label_lineage import find, fold
from data_governance.label_lineage.units import Occurrence, Site, Unit, at_origin, units_of
from data_governance.label_lineage.value import Value
from data_governance.processors import _driver
from data_governance.processors import readiness_cursor as rc
from data_governance.processors.classification.projection import project
from data_governance.processors.data_lineage import memory
from data_governance.processors.data_lineage.traversal import Entity

from . import store

log = logging.getLogger(__name__)

PROCESSOR_NAME = "label_lineage"
NOTIFY_CHANNEL = "dg_interaction_leg_ready"
POLL_SECONDS = 5.0
_DRAIN_BATCH = 500
#: Rounds of the dirty arm per wake: two traces that read each other settle in
#: two; what is still queued after the last round waits for the next wake.
_DIRTY_ROUNDS = 3
WINDOW_ENV = "LABEL_CROSS_TRACE_WINDOW_HOURS"


@dataclasses.dataclass(frozen=True)
class ReadyLeg:
    trace_id: str | None
    seq: int
    leg_type: str
    ready: bool


@dataclasses.dataclass(frozen=True)
class Config:
    """What one run derives with: the finder and its name, and the
    cross-conversation window in hours (0 = off)."""

    finder: find.Finder
    matcher: str
    window_hours: float = 0.0


def parse_window(raw: str | None) -> float:
    """``LABEL_CROSS_TRACE_WINDOW_HOURS`` as hours: unset or empty is 0 (off);
    anything that is not a number >= 0 is a ``ValueError`` naming the variable."""
    text = (raw or "").strip()
    if not text:
        return 0.0
    try:
        hours = float(text)
    except ValueError:
        raise ValueError(f"{WINDOW_ENV} must be a number of hours (0 switches it off); got {raw!r}") from None
    if not math.isfinite(hours) or hours < 0:
        raise ValueError(f"{WINDOW_ENV} must be a finite number of hours >= 0; got {raw!r}")
    return hours


def configured() -> Config:
    """The configuration from the environment; raises on an unknown finder or a
    window that is not a number of hours."""
    name = find.configured_name()
    return Config(find.get_finder(name), name, parse_window(os.environ.get(WINDOW_ENV)))


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


# --- across conversations ---------------------------------------------------------------------------


def stateful_entity_ids(trace: fold.TraceInput) -> list[str]:
    """The trace's entities that can carry a value between conversations: agents
    (they accumulate, ADR-0028 D2) and declared sources (a store). A memoryless
    LLM shared by every tenant would link two users who sent it the same number."""
    return sorted(e.id for e in trace.entities.values() if memory.accumulates(e) or memory.is_entity_source(e))


def _span(trace: fold.TraceInput) -> tuple[dt.datetime, dt.datetime] | None:
    timed = [leg.occurred_at for leg in trace.legs if leg.occurred_at is not None]
    return (min(timed), max(timed)) if timed else None


_PRIOR_LEGS_SQL = """
SELECT i.trace_id, l.interaction_id, l.leg_type::text, l.occurred_at, l.payload_hash,
       i.caller_entity_id, i.callee_entity_id, p.content, pc.findings
  FROM interaction_legs l
  JOIN interactions i ON i.id = l.interaction_id
  JOIN interaction_payloads p ON p.content_hash = l.payload_hash
  LEFT JOIN payload_classifications pc ON pc.content_hash = l.payload_hash
 WHERE i.trace_id <> %s
   AND (i.caller_entity_id = ANY(%s) OR i.callee_entity_id = ANY(%s))
   AND l.occurred_at >= %s AND l.occurred_at < %s
 ORDER BY l.occurred_at, l.seq
"""

_REACHED_SQL = """
SELECT to_interaction_id, to_leg_type::text, to_payload_hash,
       origin_interaction_id, origin_leg_type::text, origin_payload_hash, COALESCE(origin_trace_id, trace_id),
       unit_start, unit_end, unit_text, unit_value, entity_type, regulatory_tags, sensitivity_level, category,
       matched_text, matched_start, matched_end, match_kind, score, held_from, held_to
  FROM payload_label_edges
 WHERE to_interaction_id = ANY(%s)
"""


def _reached_occurrence(r: tuple, at: Site) -> Occurrence:
    """An occurrence at *at*, rebuilt from a stored edge row into that leg
    (columns as ``_REACHED_SQL`` lists them, the three ``to_*`` first)."""
    unit = Unit(entity_type=r[11], regulatory_tags=tuple(r[12] or ()), sensitivity_level=r[13], category=r[14] or "",
                text=r[9], value=Value(r[10], r[7], r[8]), origin=Site(r[6], r[3], r[4], r[5]))
    span = None if r[16] is None else (r[16], r[17])
    return Occurrence(unit=unit, at=at, span=span, held_from=r[20], held_to=r[21], match_kind=r[18],
                      score=float(r[19]), matched_text=r[15])


def load_priors(tx: db.Transaction, trace: fold.TraceInput, window_hours: float) -> tuple[fold.Prior, ...]:
    """The payloads of OTHER traces that this trace's stateful entities were handed
    or said inside the window — from *window_hours* before the trace's first leg
    up to its last — each as a :class:`~data_governance.label_lineage.fold.Prior`
    per stateful entity it involves, holding the leg's own units (its findings,
    with their own origin) and what reached it there (its stored edges)."""
    span = _span(trace)
    ids = stateful_entity_ids(trace)
    if window_hours <= 0 or span is None or not ids:
        return ()
    first, last = span
    rows = tx.fetch_all(_PRIOR_LEGS_SQL, (trace.trace_id, ids, ids, first - dt.timedelta(hours=window_hours), last))
    if not rows:
        return ()
    reached: dict[tuple[str, str, str], list[tuple]] = {}
    for r in tx.fetch_all(_REACHED_SQL, (sorted({r[1] for r in rows}),)):
        reached.setdefault((r[0], r[1], r[2]), []).append(r)  # edges at the leg's CURRENT payload only
    priors: list[fold.Prior] = []
    for other_trace, ix, leg_type, at, payload_hash, caller, callee, content, findings in rows:
        site = Site(other_trace, ix, leg_type, payload_hash)
        arrived = [_reached_occurrence(r, site) for r in reached.get((ix, leg_type, payload_hash), ())]
        # The leg's own findings, as its own derivation read them: a finding that a
        # unit of its own conversation reached is that unit confirmed (fold clause
        # (a)), not a second origin.
        confirming = [o for o in arrived if o.span is not None and o.unit.origin.trace_id == other_trace]
        own = [at_origin(u) for u in units_of(findings or (), project(content), site)
               if u.anchored and not any(fold._inside(u, o) for o in confirming)]
        occurrences: dict[tuple, Occurrence] = {o.key: o for o in own}
        for o in arrived:
            occurrences.setdefault(o.key, o)
        if not occurrences:
            continue
        for entity_id in sorted({caller, callee} & set(ids)):
            priors.append(fold.Prior(at=site, occurrences=tuple(occurrences.values()), entity_id=entity_id,
                                     occurred_at=at))
    return tuple(priors)


_DEPENDENTS_SQL = """
INSERT INTO label_lineage_dirty_traces (trace_id, reason)
SELECT i.trace_id, %s
  FROM interaction_legs l
  JOIN interactions i ON i.id = l.interaction_id
 WHERE i.trace_id <> %s
   AND (i.caller_entity_id = ANY(%s) OR i.callee_entity_id = ANY(%s))
 GROUP BY i.trace_id
HAVING max(l.occurred_at) > %s AND min(l.occurred_at) <= %s
ON CONFLICT (trace_id) DO UPDATE SET requested_at = now(), reason = EXCLUDED.reason
RETURNING trace_id
"""


def queue_dependents(tx: db.Transaction, trace: fold.TraceInput, window_hours: float) -> int:
    """Queue every OTHER trace whose derivation may have read this one as a prior:
    it shares a stateful entity, it has a leg after this trace's first and a leg no
    later than the window after this trace's last — the mirror of
    :func:`load_priors`' bounds (a superset; an unchanged re-derivation is cheap
    and queues nothing). Returns the number queued."""
    span = _span(trace)
    ids = stateful_entity_ids(trace)
    if window_hours <= 0 or span is None or not ids:
        return 0
    first, last = span
    rows = tx.fetch_all(_DEPENDENTS_SQL, (f"source trace {trace.trace_id} changed", trace.trace_id, ids, ids,
                                          first, last + dt.timedelta(hours=window_hours)))
    return len(rows)


def derive_and_persist(tx: db.Transaction, trace_id: str, config: Config) -> set[str]:
    """Re-derive *trace_id* and write it within *tx*. Returns the interactions whose
    inherited summary changed, and queues their recompute in the same transaction.
    With the window on, the priors are read in the same transaction, and the traces
    that read THIS one are queued when its edges changed."""
    trace = load_trace(tx, trace_id)
    # Whichever arm asked, the trace is derived (or gone) from current state:
    # a queued trace whose legs were re-keyed away must leave the queue too.
    tx.execute("DELETE FROM label_lineage_dirty_traces WHERE trace_id = %s", (trace_id,))
    if not trace.legs:
        return set()
    before = store.exposed(tx, trace_id) if config.window_hours > 0 else None
    trace = dataclasses.replace(trace, priors=load_priors(tx, trace, config.window_hours))
    derivation = fold.derive(trace, config.finder)
    changed = store.write(tx, derivation, config.matcher)
    # What priors read of this trace — its edges, and each leg's payload and
    # classification — changed: the traces that read it are derived from stale
    # input. (The edge set alone misses a leg whose unit travels nowhere inside
    # its own trace, classified late: nothing crosses from it until this fires.)
    if before is not None and store.exposed_by(derivation) != before:
        queued = queue_dependents(tx, trace, config.window_hours)
        if queued:
            log.info("label-lineage queued %d trace(s) that read %s", queued, trace_id)
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


def _redrive_dirty(config: Config, limit: int = _DRAIN_BATCH) -> int:
    """The dirty arm: re-derive up to *limit* traces queued because a trace they
    read changed, oldest request first; :func:`derive_and_persist` deletes the row
    in the transaction that re-derives it (a failed derivation leaves its row for
    the next wake; a trace the cursor or stale arm re-derived meanwhile is already
    off the queue)."""
    with db.transaction() as tx:
        traces = [r[0] for r in tx.fetch_all(
            "SELECT trace_id FROM label_lineage_dirty_traces ORDER BY requested_at, trace_id LIMIT %s", (limit,))]
    for trace_id in traces:
        with db.transaction() as tx:
            derive_and_persist(tx, trace_id, config)
    if traces:
        log.info("label-lineage re-derived %d trace(s) whose source trace changed", len(traces))
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
    """One wake: the cursor arm, the stale arm over the advanced cursor, then the
    dirty arm. Returns the new cursor."""
    config = config or configured()
    cursor = _drain_ready(_spec(), cursor, config)
    _redrive_stale(cursor, config)
    for _ in range(_DIRTY_ROUNDS):
        if not _redrive_dirty(config):
            break
    return cursor


def run(stop_event: threading.Event, dsn: str, config: Config | None = None) -> None:
    """Wake-driven drain over ``interaction_legs`` until *stop_event* is set."""
    config = config or configured()
    _driver.run(_spec(), stop_event, dsn, drain_fn=lambda spec, cursor: drain(cursor, config))
