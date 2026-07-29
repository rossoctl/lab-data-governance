"""P-entity-ready adapter over the shared cursor-driven driver (issue #121, ADR-0027).

The generic drain/poll/LISTEN-wake loop lives in
:mod:`data_governance.processors._driver` (issue #75); this module supplies the
entity-ready *stream spec* — the ``entities`` batch-fetch, the per-entity
delivery procedure, and the ``dg_entity_ready`` channel / ``entity_ready`` cursor
name — mirroring the P-classification adapter (``processors/classification/
driver.py``) exactly. This is the clean, independent half of ADR-0027: the
``entities`` stream has **no readiness gate** (an entity is ready the moment it is
created — first-detection only), so the shared ``_driver`` is reused **verbatim**,
with none of the readiness-cursor logic the ``dg_interaction_leg_ready`` path will
need.

Each entity is handled in ONE transaction that runs the per-entity procedure
(:func:`process_entity`) AND advances the durable ``entity_ready`` cursor —
together, so a crash mid-entity commits nothing and the restart re-delivers from
the same cursor (ADR-0007 recovery). The delivery itself is idempotent from the
stream's point of view: the cursor is the complete record of what has been
delivered, so re-draining from the durable cursor never re-delivers an entity
already past it (the exactly-once property this ticket validates).

The loop wakes on two signals (issue #71): a Postgres ``LISTEN`` notification
fired by the ``dg_entities_notify`` trigger (migration 0010) when a new entity
lands, and a periodic poll timeout as the backstop. Correctness depends only on
the poll backstop; the ``LISTEN`` wake just removes latency. If the listen
connection cannot be opened or drops mid-run, the shared loop falls back to pure
polling and still makes progress (ADR-0015/ADR-0027).

There is no real downstream consumer yet (the risk/lineage/PDP processors are
future work), so delivery is a thin, real seam: each entity is handed to an
injected ``observer`` callback (the downstream stand-in) and counted. When no
observer is injected, delivery is just the metric increment — the drain still
demonstrates exactly-once delivery past the durable cursor, which is what is
under test.
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Callable

from data_governance import db
from data_governance.processors import _driver

from . import metrics

# The durable ``processor_state`` cursor row for this stream. Distinct from
# "interactions" and "classification" so the Layer-2 consumers keep independent
# cursors.
PROCESSOR_NAME = "entity_ready"

# Channel the entities-insert trigger (migration 0010) notifies on. Must match
# the ``pg_notify('dg_entity_ready', ...)`` in dg_notify_entities().
NOTIFY_CHANNEL = "dg_entity_ready"

# How long the loop sleeps between drains when idle (poll backstop). Also the max
# time a LISTEN wait blocks before re-draining. Exposed at module level (not just
# on the spec) because tests monkeypatch it to shrink the backstop; the spec
# reads it at build time. Mirrors the classification/interactions drivers.
POLL_SECONDS = 5.0

# How many entities to pull per drain batch (each is still its own transaction).
_DRAIN_BATCH = 500

# A downstream governance consumer seam: ``(Entity) -> None``. The risk/lineage/
# PDP consumers are future work (ADR-0027), so this is where they will plug in;
# ``None`` (the default) means delivery is just the metric increment.
Observer = Callable[["Entity"], None]


@dataclasses.dataclass(frozen=True)
class Entity:
    """One row of the ``entities`` stream, as the entity-ready consumer reads it.

    Carries the entity identity a downstream governance consumer needs (``id``,
    ``kind``, ``natural_key``) plus the ``seq`` the loop advances the cursor by.
    Cross-trace stable (ADR-0013): no ``trace_id``. This is a driver-local read
    shape (like P-classification's :class:`~..classification.driver.Payload`),
    kept separate from the retrieval ``EntityView`` so the consumer's cursor field
    is not coupled to the trace-scoped read model.
    """

    id: str
    kind: str
    natural_key: str
    seq: int


def process_entity(
    tx: db.Transaction,
    entity: Entity,
    observer: Observer | None = None,
) -> None:
    """Deliver one newly-created **Entity** to the downstream governance consumer,
    within *tx*.

    For this ticket there is no real downstream (ADR-0027 — risk/lineage/PDP are
    future work), so delivery is: hand the entity to the injected *observer* (the
    downstream seam) if one is present, and increment the delivery counter. The
    delivery must be idempotent from the stream's point of view — a crash
    mid-entity re-delivers it from the same cursor — which holds because the
    cursor is the complete record of what has been delivered.

    The caller (the shared loop) owns the transaction boundary and advances the
    cursor in the same *tx*, so delivery and the cursor advance commit atomically
    (ADR-0007): a crash before commit rolls back the cursor advance, so the
    restart re-delivers this exact entity rather than skipping it.
    """
    if observer is not None:
        observer(entity)
    # One increment per entity the drain delivered. Reference the module global
    # (not a bound import) so a test's metrics.make_registry() rebind is picked
    # up. Follows the classification/interactions convention: on the rare
    # crash-mid-item rollback the counter can lead the committed cursor by one
    # (the re-drain then re-counts), i.e. at-least-once — acceptable for a
    # progress gauge; the durable cursor is the exactly-once record.
    metrics.entities_observed_total.inc()


def _fetch_batch(tx: db.Transaction, cursor: int, limit: int) -> list[Entity]:
    rows = tx.fetch_all(
        "SELECT id, kind, natural_key, seq "
        "FROM entities WHERE seq > %s ORDER BY seq ASC LIMIT %s",
        (cursor, limit),
    )
    return [
        Entity(id=r[0], kind=r[1], natural_key=r[2], seq=int(r[3])) for r in rows
    ]


def _spec(observer: Observer | None = None) -> _driver.StreamSpec[Entity]:
    """Build the entity-ready stream spec for the shared loop.

    Rebuilt per call so ``POLL_SECONDS`` monkeypatched by a test is picked up.

    *observer* is bound into the per-item procedure so the shared loop's
    ``(tx, entity) -> None`` contract is unchanged: the downstream seam is
    provided once at startup and closed over here.
    """
    return _driver.StreamSpec(
        notify_channel=NOTIFY_CHANNEL,
        processor_name=PROCESSOR_NAME,
        fetch_batch=_fetch_batch,
        process_item=lambda tx, entity: process_entity(tx, entity, observer=observer),
        item_seq=lambda entity: entity.seq,
        poll_seconds=POLL_SECONDS,
        batch_size=_DRAIN_BATCH,
    )


def read_cursor(tx: db.Transaction) -> int:
    """Read the entity-ready durable cursor (delegates to the shared loop)."""
    return _driver.read_cursor(tx, PROCESSOR_NAME)


def drain(cursor: int, observer: Observer | None = None) -> int:
    """Deliver every entity past *cursor*, one transaction per entity. Returns the
    new cursor (the seq of the last entity delivered, or *cursor* if none).

    *observer* is the downstream governance seam (ADR-0027); ``None`` keeps
    delivery to the metric increment.
    """
    return _driver.drain(_spec(observer=observer), cursor)


def run(
    stop_event: threading.Event,
    dsn: str,
    observer: Observer | None = None,
) -> None:
    """Wake-driven drain loop over the ``entities`` stream. Returns when
    *stop_event* is set. See :func:`_driver.run`.

    Wakes on a ``LISTEN`` notification on ``dg_entity_ready`` (low latency; fired
    by the migration-0010 trigger) or the poll backstop, whichever comes first;
    both lead to the same drain. Falls back to poll-only if the LISTEN connection
    is unavailable — a missing/severed notification only costs latency (ADR-0015).

    *observer* is the downstream governance consumer seam (risk/lineage/PDP —
    future work), injected into every delivered entity; ``None`` keeps delivery to
    the metric increment.
    """
    _driver.run(_spec(observer=observer), stop_event, dsn)
