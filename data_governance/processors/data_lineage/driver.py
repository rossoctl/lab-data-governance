"""P-data-lineage adapter over the shared cursor-driven driver (issue #117).

The generic drain/poll/LISTEN-wake loop lives in
:mod:`data_governance.processors._driver`; this module supplies the data-lineage
*stream spec* — the ``interaction_legs`` batch-fetch, the per-leg procedure, and
the ``dg_legs_inserted`` channel (migration 0010) / ``data_lineage`` cursor name —
mirroring the P-classification adapter (``processors/classification/driver.py``).

**Re-derive-per-leg.** The shared loop's grain is one item, one transaction; data
lineage's grain is a whole trace (``inbound(i)`` spans every earlier leg of the
trace — ADR-0027 D1). So on each arriving leg we re-derive its ENTIRE trace: read
every leg of the trace with its parent's caller/callee, run the pure traversal
(:func:`traversal.derive_trace_lineage`), upsert every resulting row, all in the one
transaction the loop owns, then let the loop advance the cursor to that leg's seq.
This is the ``interactions/graph_driver.py`` precedent verbatim, and it needs no new
machinery: the derivation is idempotent because the key is deterministic
(``(interaction_id, leg_type)``) and the write is ``ON CONFLICT ... DO UPDATE``, so
re-deriving a trace as its later legs arrive converges rather than duplicating.

The upsert must take the DO UPDATE path (not DO NOTHING): re-derivation genuinely
changes a leg's lineage as its trace fills in, and ``interaction_legs`` rows are
themselves rewritten in place when P-interactions re-derives a trace — which is why
migration 0010's NOTIFY trigger covers UPDATE as well as INSERT. Insert-if-absent
would freeze the first, most partial answer.

**Upserting alone is not enough once a derivation can get SHORTER** (ADR-0027 D6's
absent-payload cutoff, issue #120). A trace derived complete and later truncated at
a gap would keep the rows past the new cutoff, contradicting its own ``partial``
status — so ``process_leg`` also deletes the trace's rows at and after the stop
position, and writes the trace-level status into ``lineage_trace_status`` (migration
0012). All three writes share the loop's one transaction, so a trace's metadata and
its coverage claim can never disagree.

**Trace scoping needs a join.** ``interaction_legs`` has no ``trace_id`` (ADR-0025
puts identity on the parent), so both the arriving leg's trace and the trace's legs
are reached through ``interactions``. Getting that wrong would pull unrelated traces
into one lineage graph — a false cross-trace data-flow claim, which is the one thing
a governance tool must not make (inter-trace lineage is Step II, deferred).

**The matcher is resolved through the public contract only** — one
:func:`data_governance.matching.get_matcher` call per drain, never an import of a
matcher implementation (ADR-0027: lineage does not know how matching decides).

Trade-off, accepted as in the graph driver: re-deriving a trace once per newly
arrived leg is more CPU than an incremental derivation. It is always consistent and
simple; debounce/trace-idle batching is a later optimisation.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading

from data_governance import db
from data_governance.matching import Matcher, get_matcher
from data_governance.processors import _driver

from . import traversal
from .traversal import Entity, Leg

log = logging.getLogger(__name__)

# The durable ``processor_state`` cursor row for this stream. Distinct from
# "interactions" and "classification" so all three Layer-2 processors keep
# independent cursors.
PROCESSOR_NAME = "data_lineage"

# Channel the legs-write trigger (migration 0010) notifies on. Must match the
# ``pg_notify('dg_legs_inserted', '')`` in dg_notify_legs().
NOTIFY_CHANNEL = "dg_legs_inserted"

# Idle sleep between drains / max LISTEN wait (poll backstop). Module-level so a
# test can shrink it; the spec reads it at build time.
POLL_SECONDS = 5.0

# How many legs to pull per drain batch (each is still its own transaction).
_DRAIN_BATCH = 500


@dataclasses.dataclass(frozen=True)
class ArrivingLeg:
    """One row of the ``interaction_legs`` stream as this processor reads it: the
    ``trace_id`` (joined in from the parent — the legs table has none) that
    identifies the trace to re-derive, and the ``seq`` the loop advances by."""

    trace_id: str
    seq: int


def load_trace(tx: db.Transaction, trace_id: str) -> tuple[list[Leg], dict[str, Entity]]:
    """Every leg of *trace_id* (with its parent's caller/callee) plus the entities
    they reference, read within the caller's transaction so the re-derivation sees a
    consistent snapshot with the cursor advance.

    The join through ``interactions`` is what scopes the read to one trace and what
    supplies the caller/callee identity the traversal routes on (ADR-0025 keeps both
    on the parent). Ordering is by leg ``seq`` — the only execution order the schema
    offers, since the parent row has no ``seq``.

    Payload *content* is loaded separately by :func:`load_payloads` — it is needed
    only to feed the matcher, and the default matcher reads neither argument, so
    keeping it out of this read makes the structural half of the derivation
    independently readable (and independently testable).
    """
    rows = tx.fetch_all(
        "SELECT l.interaction_id, l.leg_type::text, l.seq, "
        "       i.caller_entity_id, i.callee_entity_id, l.payload_hash "
        "FROM interaction_legs l "
        "JOIN interactions i ON i.id = l.interaction_id "
        "WHERE i.trace_id = %s ORDER BY l.seq ASC",
        (trace_id,),
    )
    legs = [
        Leg(
            interaction_id=r[0],
            leg_type=r[1],
            seq=int(r[2]),
            caller_entity_id=r[3],
            callee_entity_id=r[4],
            payload_hash=r[5],
        )
        for r in rows
    ]
    entity_ids = {leg.caller_entity_id for leg in legs} | {
        leg.callee_entity_id for leg in legs
    }
    entities: dict[str, Entity] = {}
    if entity_ids:
        placeholders = ", ".join(["%s"] * len(entity_ids))
        erows = tx.fetch_all(
            f"SELECT id, natural_key, kind::text FROM entities WHERE id IN ({placeholders})",
            list(entity_ids),
        )
        entities = {
            r[0]: Entity(id=r[0], natural_key=r[1], kind=r[2]) for r in erows
        }
    return legs, entities


def load_payloads(tx: db.Transaction, legs: list[Leg]) -> dict[str, object]:
    """The stored ``content`` of every payload *legs* reference, by ``content_hash``.

    This is what the matcher actually compares (the spec's ``match(payload_a,
    payload_b)`` is over payload content, not over hashes). The default matcher
    reads neither argument, so this read buys nothing today — it exists so that
    swapping in a real, content-reading matcher stays a pure configuration change
    (ADR-0027), rather than requiring a new read here at that point.

    A hash with no ``interaction_payloads`` row simply does not appear in the map;
    the traversal then falls back to the hash itself, which the matching contract
    permits (a payload is opaque to it).
    """
    hashes = {leg.payload_hash for leg in legs if leg.payload_hash is not None}
    if not hashes:
        return {}
    placeholders = ", ".join(["%s"] * len(hashes))
    rows = tx.fetch_all(
        f"SELECT content_hash, content FROM interaction_payloads "
        f"WHERE content_hash IN ({placeholders})",
        list(hashes),
    )
    return {r[0]: r[1] for r in rows}


def process_leg(tx: db.Transaction, leg: ArrivingLeg, matcher: Matcher) -> None:
    """Re-derive *leg*'s whole trace's data lineage and persist it, within *tx*.

    The caller (the shared loop) owns the transaction and advances the cursor in the
    same *tx*, so the derived writes and the cursor advance commit atomically
    (ADR-0007 recovery). Idempotent: the ``(interaction_id, leg_type)`` key is
    deterministic and the write upserts, so re-deriving converges.

    Three writes, all in *tx* so a trace's metadata and its coverage status can
    never disagree: upsert the derived rows, **delete** any of this trace's rows the
    derivation no longer covers, and upsert the trace's ``complete``/``partial``
    status (ADR-0027 D6, issue #120). Together they make the persisted lineage of a
    trace exactly the derivation's output — no more, so a shrinking derivation
    genuinely shrinks the answer.
    """
    legs, entities = load_trace(tx, leg.trace_id)
    if not legs:
        # Nothing to derive, and therefore nothing to claim: no status row either.
        # Absence of the row is "not yet derived"; writing `complete` here would
        # assert full coverage of a trace whose legs have not landed.
        return
    result = traversal.derive_trace_lineage(
        legs,
        entities,
        matcher=matcher,
        payloads=load_payloads(tx, legs),
    )
    seq_by_key = {(item.interaction_id, item.leg_type): item.seq for item in legs}
    hash_by_key = {
        (item.interaction_id, item.leg_type): item.payload_hash for item in legs
    }
    for (interaction_id, leg_type), derived in result.legs.items():
        _upsert(
            tx,
            interaction_id=interaction_id,
            leg_type=leg_type,
            lineage=derived.lineage,
            payload_hash=hash_by_key[(interaction_id, leg_type)],
            seq=seq_by_key[(interaction_id, leg_type)],
        )
    _delete_stale(tx, leg.trace_id, set(result.legs))
    _upsert_status(tx, leg.trace_id, result)


def _delete_stale(
    tx: db.Transaction, trace_id: str, derived_keys: set[traversal.LegKey]
) -> None:
    """Drop *trace_id*'s **stale** lineage rows — those this derivation did not
    produce, left behind by an earlier and longer one (ADR-0027 D9).

    Named for what it deletes, not for the condition it tests: every row it removes
    is a lineage fact that some previous derivation of this same trace asserted and
    this one no longer does. Nothing it deletes is current.

    **Why upserting is not enough.** This driver re-derives a whole trace per
    arriving leg and upserts without deleting, so rows survive from earlier
    derivations. Once a derivation can get *shorter* — ADR-0027 D6's absent-payload
    cutoff (#120), triggered when a leg's payload is rewritten to NULL or a gap
    appears as P-interactions re-derives legs in place (migration 0010's trigger
    covers UPDATE for exactly this reason) — the rows past the new cutoff are stale.
    Left behind, the read would serve lineage for legs *after* the gap while the
    status says ``partial``: not merely stale but self-contradictory, and a positive
    claim about data whose provenance is no longer visible.

    **The condition is "not in the derived set", not "seq >= stop".** ``seq`` is a
    re-allocated cursor value, not a stable position: rewriting a leg draws a fresh
    ``seq`` from the sequence, so the gap leg's new ``seq`` sits *above* the stale
    rows written under its old one and a ``seq``-threshold delete would spare
    exactly the rows it must remove. The derivation is the sole authority on which
    of a trace's legs have lineage, so anything else under this trace goes — which
    also cleans up rows whose leg has disappeared entirely.

    Scoped through ``interactions`` because ``lineage_metadata`` has no ``trace_id``
    (ADR-0025 keeps identity on the parent). Getting that wrong would delete another
    trace's evidence.
    """
    # The covered set is passed as two parallel TEXT[] arrays rather than an
    # expanded `NOT IN ((%s, %s), ...)`: one placeholder pair regardless of trace
    # size (a long trace would otherwise generate hundreds of parameters), and it
    # degenerates correctly to "delete everything" for an empty prefix — which is a
    # real case, the gap landing on the trace's first leg.
    kept_interaction_ids = [key[0] for key in derived_keys]
    kept_leg_types = [key[1] for key in derived_keys]
    tx.execute(
        "DELETE FROM lineage_metadata m USING interactions i "
        "WHERE i.id = m.interaction_id AND i.trace_id = %s "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM unnest(%s::text[], %s::text[]) AS kept(interaction_id, leg_type)"
        "  WHERE kept.interaction_id = m.interaction_id"
        "    AND kept.leg_type = m.leg_type::text"
        ")",
        (trace_id, kept_interaction_ids, kept_leg_types),
    )


def _upsert_status(
    tx: db.Transaction, trace_id: str, result: traversal.TraceLineage
) -> None:
    """Record whether *trace_id*'s lineage covers the whole trace (ADR-0027 D6).

    One row per trace (PK ``trace_id``), so this upsert is the entire idempotency
    story — and the reason the status lives in its own table rather than being
    derived on read (see migration 0012). The partial→complete transition, when a
    late payload arrives, is a plain overwrite of that single row: there is no
    earlier, longer answer left behind to shadow it.

    ``stopped_at_seq`` is written as NULL for a complete trace, which the table's
    CHECK constraint pairs with the status so a half-written claim ("partial, but I
    won't say from where") cannot be stored.
    """
    tx.execute(
        "INSERT INTO lineage_trace_status (trace_id, status, stopped_at_seq) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (trace_id) DO UPDATE SET "
        "status = EXCLUDED.status, stopped_at_seq = EXCLUDED.stopped_at_seq",
        (trace_id, str(result.status), result.stopped_at_seq),
    )


def _upsert(
    tx: db.Transaction,
    *,
    interaction_id: str,
    leg_type: str,
    lineage: traversal.DataLineage,
    payload_hash: str | None,
    seq: int,
) -> None:
    """Write one ``lineage_metadata`` row, overwriting any previous derivation.

    DO UPDATE rather than DO NOTHING: a re-derivation is the *point* (a trace's
    lineage sharpens as its later legs arrive, and P-interactions rewrites legs in
    place), so an insert-if-absent write would pin the first, most partial answer
    forever.

    The triple is serialized in the metadata's own terms: the source set and the
    entity set as TEXT[], and the ``data_source -> set<transformation>`` map as
    JSONB.

    **Every set is written SORTED, and the sort is serialization only — never
    meaning.** All three of ``data_sources``, ``entities`` and each
    transformation set are unordered per the spec ("the set of entities ... Note:
    this is unordered. In case an order is needed - it will need to be derived from
    the trace using an API"). They are sorted on the way out purely so a
    re-derivation of identical lineage produces byte-identical rows, which is what
    makes idempotency observable; order is insignificant, so pinning it costs
    nothing. A reader must not infer flow order from the array position — the
    derivation cannot supply one, and the array would be lying if it implied one.
    """
    tx.execute(
        "INSERT INTO lineage_metadata (interaction_id, leg_type, data_sources, "
        "source_transformations, entities, payload_hash, seq) "
        "VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s) "
        "ON CONFLICT (interaction_id, leg_type) DO UPDATE SET "
        "data_sources = EXCLUDED.data_sources, "
        "source_transformations = EXCLUDED.source_transformations, "
        "entities = EXCLUDED.entities, "
        "payload_hash = EXCLUDED.payload_hash, "
        "seq = EXCLUDED.seq",
        (
            interaction_id,
            leg_type,
            sorted(lineage.data_sources),
            json.dumps(
                {
                    source: sorted(str(t) for t in transformations)
                    for source, transformations in sorted(
                        lineage.source_transformations.items()
                    )
                }
            ),
            sorted(lineage.entities),
            payload_hash,
            seq,
        ),
    )


def _fetch_batch(tx: db.Transaction, cursor: int, limit: int) -> list[ArrivingLeg]:
    """Legs past *cursor* in ``seq`` order, each carrying its trace id.

    The ``trace_id`` comes from the parent ``interactions`` row (the legs table has
    none), so a leg whose parent is not yet visible is not fetched — it will be
    picked up when the parent lands and the leg is re-announced, or by the poll
    backstop after the parent's own write. That is the correct behaviour: without a
    parent there is no caller/callee and therefore no lineage to derive.
    """
    rows = tx.fetch_all(
        "SELECT i.trace_id, l.seq FROM interaction_legs l "
        "JOIN interactions i ON i.id = l.interaction_id "
        "WHERE l.seq > %s ORDER BY l.seq ASC LIMIT %s",
        (cursor, limit),
    )
    return [ArrivingLeg(trace_id=r[0], seq=int(r[1])) for r in rows]


def _spec(matcher: Matcher | None = None) -> _driver.StreamSpec[ArrivingLeg]:
    """Build the data-lineage stream spec for the shared loop.

    Rebuilt per call so ``POLL_SECONDS`` monkeypatched by a test is picked up.

    *matcher* is resolved ONCE per drain/run through the public
    :func:`~data_governance.matching.get_matcher` and closed over here, so every leg
    of every trace in one run is derived by the same matcher (a mid-drain
    reconfiguration cannot produce a half-and-half trace). Passing an explicit
    matcher is for tests and a future CLI override.
    """
    resolved = matcher if matcher is not None else get_matcher()
    return _driver.StreamSpec(
        notify_channel=NOTIFY_CHANNEL,
        processor_name=PROCESSOR_NAME,
        fetch_batch=_fetch_batch,
        process_item=lambda tx, leg: process_leg(tx, leg, resolved),
        item_seq=lambda leg: leg.seq,
        poll_seconds=POLL_SECONDS,
        batch_size=_DRAIN_BATCH,
    )


def read_cursor(tx: db.Transaction) -> int:
    """Read the data-lineage durable cursor (delegates to the shared loop)."""
    return _driver.read_cursor(tx, PROCESSOR_NAME)


def drain(cursor: int, matcher: Matcher | None = None) -> int:
    """Process every leg past *cursor*, one transaction per leg (each re-deriving
    that leg's whole trace). Returns the new cursor (the seq of the last leg
    processed, or *cursor* if none)."""
    return _driver.drain(_spec(matcher), cursor)


def run(stop_event: threading.Event, dsn: str, matcher: Matcher | None = None) -> None:
    """Wake-driven drain loop over the ``interaction_legs`` stream. Returns when
    *stop_event* is set. See :func:`_driver.run`."""
    _driver.run(_spec(matcher), stop_event, dsn)
