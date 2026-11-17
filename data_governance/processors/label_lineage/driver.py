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
- **Correction replay.** An independent dirty-trace queue re-derives corrected
  parents, endpoints, times, trace membership and deleted legs after the cursor
  has passed them. Each derivation takes the shared trace lock before reading;
  its observed queue generation is acknowledged after commit, so a newer
  correction remains queued. Empty traces lose their derived rows and coverage.
- **The stale arm.** The cursor never revisits a leg it passed, so this arm
  re-derives traces holding a leg at or below the cursor whose label row was
  derived from another payload or matcher, was derived before the payload was
  classified, or is missing although the payload is classified. It never moves the
  cursor; the predicate is its durable state.

Every derivation notifies ``dg_interaction_leg_ready`` at commit: with the risk flag
on, the label row is what makes a leg ready for leg-ready, which listens there (this
processor wakes once more and finds nothing new).

Across conversations (``LABEL_CROSS_CONVERSATION=on``, default off): the derivation
of a trace also loads what its persistent senders received in other conversations
(``label_occurrences``, valid in the trace's time window, whose value is in a payload
they send) and writes what its persistent receivers received, replacing the
trace's rows. The switch alone governs it, whatever the risk flag says (as the rest
of this processor runs); the risk engine hands crossed labels to the policy only while
leg-ready has both ``RISK_INHERITED_LABELS`` and this switch on. With it off nothing
crosses and nothing is stored; a derivation that deletes occurrences written while it
was on still queues what rested on them (the correction).

**The correction.** A derivation that adds, removes or changes an occurrence queues,
in its own transaction, every other conversation with a leg the entity sent inside the
occurrence's interval (:func:`.store.affected`, queued by :func:`.store.queue` in
sorted order; the cursor arm queues once, at the end of its transaction); the dirty arm derives them again, looping
until the queue is empty or a batch is spent. So the rows do not depend on which
conversation was derived first. It ends because a match reads only occurrences
strictly earlier in event time (R1): a re-derivation that changes no occurrence queues
nothing, and each round settles the earliest leg still wrong. It needs one worker at a
time (two would let a derivation read the occurrences before another commits its
change): the entry point holds the session lock ``consumer:label_lineage``, checked
before every drain.

When a re-derivation changes a leg's inherited summary — including a leg that
gains its first edges — the leg's interaction is written to
``risk_recompute_requests`` in the same transaction. That is a fact about the
derivation; this processor reads no risk configuration. Leg-ready alone decides
what to do with label-only requests (``RISK_INHERITED_LABELS``): recompute when
on, retain them when off (one row per interaction bounds the table). Structural
correction requests are processed in either mode without losing pending labels.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import threading
from collections.abc import Callable, Mapping

from data_governance import db
from data_governance.label_lineage import cross, find, fold
from data_governance.label_lineage.units import Occurrence, Site, Unit
from data_governance.label_lineage.value import Value
from data_governance.processors.classification.projection import project
from data_governance.processors import _driver
from data_governance.processors import readiness_cursor as rc
from data_governance.processors.data_lineage import memory
from data_governance.processors.data_lineage.traversal import Entity
from data_governance.processors.trace_lock import lock_trace

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
    """What one run derives with: the finder and its name, the memory scope declared
    per entity natural key (``LABEL_MEMORY_SCOPES``), and the cross-conversation
    configuration (``None``: nothing crosses)."""

    finder: find.Finder
    matcher: str
    scopes: Mapping[str, str] = dataclasses.field(default_factory=dict)
    crossing: cross.Crossing | None = None


def configured() -> Config:
    """The configuration from the environment; raises on an unknown finder, an
    invalid scope declaration or an invalid cross-conversation configuration."""
    name = find.configured_name()
    return Config(find.get_finder(name), name, memory.parse_scopes(os.environ.get(memory.SCOPES_ENV_VAR)),
                  cross.configured(os.environ))


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


def load_trace(tx: db.Transaction, trace_id: str, config: Config | None = None) -> fold.TraceInput:
    """Every leg of *trace_id* with its wire time and its parent's caller/callee,
    the entities they name, the payloads they carry and the findings of the
    classified ones — one snapshot, in the caller's transaction. With crossing on,
    also what the trace's persistent senders hold from other conversations
    (:func:`load_priors`)."""
    rows = tx.fetch_all(
        "SELECT l.interaction_id, l.leg_type::text, l.seq, i.caller_entity_id, i.callee_entity_id, "
        "       l.payload_hash, l.occurred_at, i.parent_interaction_id "
        "FROM interaction_legs l JOIN interactions i ON i.id = l.interaction_id "
        "WHERE i.trace_id = %s",
        (trace_id,),
    )
    legs = [fold.WireLeg(r[0], r[1], int(r[2]), r[3], r[4], r[5], occurred_at=r[6],
                         parent_interaction_id=r[7]) for r in rows]
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
    crossing = config.crossing if config else None
    held = held_elsewhere(tx, trace_id, legs, entities, config) if crossing and crossing.holdings_limit else {}
    # a leg over the holdings limit is searched for nothing, so nothing is loaded for it: the count is its cost
    over = {key for key, n in held.items() if n > crossing.holdings_limit} if held else set()
    priors = load_priors(tx, trace_id, [leg for leg in legs if (leg.interaction_id, leg.leg_type) not in over],
                         entities, payloads, config) if crossing else ()
    return fold.TraceInput(trace_id=trace_id, legs=legs, entities=entities, payloads=payloads,
                           findings=findings, priors=priors, held_elsewhere=held)


# K: the most words among the occurrences the load below could return for these sends (the same window), so a
# long value that expired, or one held only in this conversation, does not raise the cost of every later send.
_LONGEST_SQL = """
SELECT max(word_count) FROM label_occurrences
 WHERE entity_id = %s AND trace_id <> %s AND valid_from < %s AND valid_to > %s
"""

_PRIORS_SQL = """
SELECT entity_id, trace_id, interaction_id, leg_type::text, payload_hash, value_text, span_start, span_end,
       record_start, record_end, origin_trace_id, origin_interaction_id, origin_leg_type::text,
       origin_payload_hash, unit_start, unit_end, unit_text, unit_value, entity_type, regulatory_tags,
       sensitivity_level, category, held_from, held_to, evidence, level, valid_from, valid_to
  FROM label_occurrences
 WHERE entity_id = %s AND core_hash = ANY(%s) AND trace_id <> %s
   AND valid_from < %s AND valid_to > %s
"""


def load_priors(tx: db.Transaction, trace_id: str, legs: list[fold.WireLeg], entities: Mapping[str, Entity],
                payloads: Mapping[str, object], config: Config) -> tuple[cross.Holding, ...]:
    """What the trace's persistent senders received in other conversations and send
    again here: per sender, the occurrences valid somewhere in the window of the legs it
    sends whose value is in one of those payloads. A value found there is a run of its
    own number of consecutive words, so the payload's runs of 1..K words are hashed (K
    the most words the sender holds) and the occurrences are loaded by those hashes:
    exactly the held values present, whatever and however much the sender holds. K is
    taken from the other conversations' occurrences valid in that window only, through
    the index on (entity, valid_to). Cost per payload: its words times K hashes and
    index probes; the index on (entity, core_hash, valid_to) keeps expired ones out."""
    sent = [leg for leg in legs if leg.occurred_at is not None and leg.payload_hash is not None
            and fold.is_persistent(entities, fold.ends(leg)[0], config.scopes)]
    rows: list[tuple] = []
    for sender in sorted({fold.ends(leg)[0] for leg in sent}):
        mine = [leg for leg in sent if fold.ends(leg)[0] == sender]
        times = [leg.occurred_at for leg in mine]
        (longest,) = tx.fetch_one(_LONGEST_SQL, (sender, trace_id, max(times), min(times)))
        if not longest:
            continue
        hashes = sorted(set().union(*(cross.window_hashes(project(payloads.get(leg.payload_hash, "")), longest)
                                      for leg in mine)))
        rows += tx.fetch_all(_PRIORS_SQL, (sender, hashes, trace_id, max(times), min(times)))
    return tuple(_holding(r) for r in rows)


_HELD_SQL = """
SELECT count(*) FROM (
    SELECT 1 FROM label_occurrences
     WHERE entity_id = %s AND trace_id <> %s AND valid_to > %s AND valid_from < %s
     LIMIT %s) held
"""


def held_elsewhere(tx: db.Transaction, trace_id: str, legs: list[fold.WireLeg], entities: Mapping[str, Entity],
                   config: Config) -> dict[tuple[str, str], int]:
    """Per leg a persistent entity sends, how many occurrences other conversations hold
    with it valid at the leg's time, counted no further than one past the holdings
    limit (all the limit needs to know)."""
    limit = config.crossing.holdings_limit + 1
    out = {}
    for leg in legs:
        sender = fold.ends(leg)[0]
        if leg.occurred_at is not None and leg.payload_hash is not None and fold.is_persistent(
                entities, sender, config.scopes):
            t = leg.occurred_at
            out[(leg.interaction_id, leg.leg_type)] = tx.fetch_one(_HELD_SQL, (sender, trace_id, t, t, limit))[0]
    return out


def _holding(r: tuple) -> cross.Holding:
    (entity_id, trace_id, ix, leg_type, payload_hash, value_text, span_start, span_end, record_start, record_end,
     o_trace, o_ix, o_leg, o_hash, unit_start, unit_end, unit_text, unit_value, entity_type, tags, level_s,
     category, held_from, held_to, evidence, level, valid_from, valid_to) = r
    unit = Unit(entity_type=entity_type, regulatory_tags=tuple(tags or ()), sensitivity_level=level_s,
                category=category, text=unit_text, value=Value(unit_value, unit_start, unit_end),
                origin=Site(o_trace, o_ix, o_leg, o_hash))
    occ = Occurrence(unit=unit, at=Site(trace_id, ix, leg_type, payload_hash), span=(span_start, span_end),
                     held_from=held_from, held_to=held_to, matched_text=value_text)
    record = None if record_start is None else (record_start, record_end)
    return cross.Holding(entity_id, occ, record, valid_from, valid_to, evidence, level)


def derive_and_persist(tx: db.Transaction, trace_id: str, config: Config,
                       corrections: set[str] | None = None) -> set[str]:
    """Re-derive *trace_id* and write it within *tx*. Returns the interactions whose
    inherited summary changed, and queues their recompute in the same transaction; with
    crossing on, also the conversations a changed occurrence bears on
    (:func:`.store.affected`): queued now, or added to *corrections* for the caller to
    queue at the end of its transaction. The correction runs whatever the switch says: a
    derivation with it off deletes the trace's occurrences, and what rested on them is
    queued too. With the switch always off that is a read of an empty table and a delete
    per derivation."""

    def correct(before: set[tuple]) -> None:
        traces = store.affected(tx, trace_id, before)
        if corrections is None:
            store.queue(tx, traces)
        else:
            corrections.update(traces)

    lock_trace(tx, trace_id)
    before = store.occurrences(tx, trace_id)
    trace = load_trace(tx, trace_id, config)
    if not trace.legs:
        for table in ("payload_label_edges", "leg_inherited_labels", "label_lineage_trace_status",
                      "label_occurrences", "label_crossings", "label_crossed_edges", "label_sends"):
            tx.execute(f"DELETE FROM {table} WHERE trace_id = %s", (trace_id,))
        correct(before)
        tx.execute("SELECT pg_notify(%s, '')", (NOTIFY_CHANNEL,))
        return set()
    changed = store.write(tx, fold.derive(trace, config.finder, config.scopes, config.crossing), config.matcher)
    correct(before)
    # The rows just written are what leg-ready's readiness waits for with the risk
    # flag on: wake it at commit instead of leaving it to its poll backstop.
    tx.execute("SELECT pg_notify(%s, '')", (NOTIFY_CHANNEL,))
    if changed:
        tx.execute_many(
            "INSERT INTO risk_recompute_requests (interaction_id, reason) VALUES (%s, %s) "
            "ON CONFLICT (interaction_id) DO UPDATE SET requested_at = now(), "
            "reason = EXCLUDED.reason, labels_pending = true",
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
        traces = sorted({by_seq[item.seq].trace_id for item in result.delivered})
        with db.transaction() as tx:
            # Take every trace lock before queue/derived-row locks, in sorted
            # order, because this cursor transaction can cover several traces.
            for trace_id in traces:
                lock_trace(tx, trace_id)
            queued = tx.fetch_all(
                "SELECT trace_id, generation FROM label_lineage_dirty_traces WHERE trace_id = ANY(%s)",
                (traces,),
            )
            # In any order: a conversation derived before another of the run registered what it
            # depends on is queued by that derivation and derived again (the correction).
            corrections: set[str] = set()
            for trace_id in traces:
                derive_and_persist(tx, trace_id, config, corrections)
            store.queue(tx, corrections)     # sorted, at the end: queue rows in one order
            _driver.advance_cursor(tx, spec.processor_name, result.cursor)
        _ack_dirty(queued)
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


def _ack_dirty(queued: list[tuple]) -> None:
    # Acknowledgment takes only queue-row locks, after derivation committed.
    # A new correction's generation survives; a crash causes an idempotent replay.
    if not queued:
        return
    with db.transaction() as tx:
        tx.execute_many(
            "DELETE FROM label_lineage_dirty_traces WHERE trace_id = %s AND generation = %s",
            queued,
        )
        tx.execute("SELECT pg_notify(%s, '')", (NOTIFY_CHANNEL,))


def _redrive_dirty(config: Config, limit: int = _DRAIN_BATCH) -> int:
    """The correction arm: derive every queued trace again, oldest generation first,
    reading the queue again until it is empty or *limit* derivations are spent (a
    derivation here may queue another; the rest waits for the next wake)."""
    done = 0
    while done < limit:
        with db.transaction() as tx:
            queued = tx.fetch_all(
                "SELECT trace_id, generation FROM label_lineage_dirty_traces ORDER BY generation LIMIT %s",
                (limit - done,),
            )
        if not queued:
            break
        for trace_id, generation in queued:
            with db.transaction() as tx:
                derive_and_persist(tx, trace_id, config)
            _ack_dirty([(trace_id, generation)])
        done += len(queued)
    if done:
        log.info("label-lineage re-derived %d corrected trace(s)", done)
    return done


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
    """One wake: the cursor arm, correction replay, then the stale arm.
    Returns the new cursor."""
    config = config or configured()
    cursor = _drain_ready(_spec(), cursor, config)
    _redrive_dirty(config)
    _redrive_stale(cursor, config)
    return cursor


def run(stop_event: threading.Event, dsn: str, config: Config | None = None,
        guard: Callable[[], None] | None = None) -> None:
    """Wake-driven drain over ``interaction_legs`` until *stop_event* is set; *guard*,
    when given, runs before every drain (the entry point's sole-consumer lock check)."""
    config = config or configured()

    def drain_once(spec: _driver.StreamSpec[ReadyLeg], cursor: int) -> int:
        if guard is not None:
            guard()
        return drain(cursor, config)

    _driver.run(_spec(), stop_event, dsn, drain_fn=drain_once)
