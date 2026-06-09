"""Graph-builder backfill (issue #56 / ADR-0007).

A Layer-2 unit that reads ``spans`` and idempotently upserts the derived
``entities`` and ``edges`` graph. Per PROJECT.md §6 the bulk scan goes through
the Layer-1 ``db`` module directly (not the 500-capped ``get_spans``), keyset-
paged on ``arrival_seq`` so memory is bounded.

The whole backfill runs in **one transaction** (ADR-0005's "one transaction per
unit"): either the derived graph is consistent with the scanned spans or the
transaction rolls back. Re-running is a no-op — entities upsert on ``entity_id``
and edges upsert on ``(trace_id, span_id)`` with ``edge_seq`` preserved.

Algorithm (two passes, single scan):

1. **Scan + classify.** Page through ``spans``; for each span compute its
   entity and remember ``(trace_id, span_id) -> (entity_id, semantic_kind)``.
   Accumulate distinct entities (widening first/last-seen in memory) and, for
   every span with a ``parent_id``, an edge candidate.
2. **Resolve edges.** For each candidate, look up the parent's entity in the
   in-memory map. Parent absent -> orphan boundary (``from_entity = NULL``,
   ``edge_kind = UNKNOWN_*``). ``from_entity == to_entity`` -> same-entity
   (internal) pair, suppressed. Otherwise emit the edge.

Entities are upserted before edges so the ``edges -> entities`` FK is satisfied
within the transaction.

Still out of scope here, by design: incremental (``seq``-watermark) derivation
and orphan re-resolution on a tail scan. That's issue #62. This slice always
does a full backfill.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from data_governance import db

from .classify_kind import (
    display_name,
    edge_kind,
    entity_id,
    semantic_kind,
    sub_kind,
)


__all__ = ["BackfillResult", "run_backfill"]


_DEFAULT_BATCH = 2000

_SCAN_SQL = """
    SELECT trace_id, span_id, parent_id, service_name, kind,
           started_at, attributes, arrival_seq
    FROM spans
    WHERE arrival_seq > %s
    ORDER BY arrival_seq
    LIMIT %s
"""

_UPSERT_ENTITY_SQL = """
    INSERT INTO entities (
        entity_id, service_name, semantic_kind, sub_kind,
        display_name, first_seen_at, last_seen_at
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (entity_id) DO UPDATE SET
        first_seen_at = LEAST(entities.first_seen_at, EXCLUDED.first_seen_at),
        last_seen_at  = GREATEST(entities.last_seen_at, EXCLUDED.last_seen_at),
        display_name  = EXCLUDED.display_name
"""

# edge_seq and parent_id are deliberately omitted from the SET clause so they
# keep their existing values on re-derivation: edge_seq stays stable (the
# read-path cursor never skews), and a late-arriving parent flips from_entity
# NULL->real without disturbing the row's position.
_UPSERT_EDGE_SQL = """
    INSERT INTO edges (
        trace_id, span_id, parent_id, to_entity, from_entity, edge_kind
    )
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (trace_id, span_id) DO UPDATE SET
        to_entity   = EXCLUDED.to_entity,
        from_entity = EXCLUDED.from_entity,
        edge_kind   = EXCLUDED.edge_kind
"""


@dataclass(frozen=True)
class BackfillResult:
    """Counts from one backfill run.

    Attributes:
        spans_scanned: rows read from ``spans``.
        entities_upserted: distinct entities written.
        edges_upserted: edges written (after self-loop suppression).
        orphan_edges: edges emitted with ``from_entity IS NULL``.
    """

    spans_scanned: int
    entities_upserted: int
    edges_upserted: int
    orphan_edges: int


def _scan_spans(tx: db.Transaction, batch_size: int) -> Iterator[tuple[Any, ...]]:
    """Yield span rows keyset-paged on ``arrival_seq`` (stable, never reused)."""
    cursor = 0
    while True:
        rows = tx.fetch_all(_SCAN_SQL, (cursor, batch_size))
        if not rows:
            return
        for row in rows:
            yield row
        cursor = rows[-1][-1]  # last arrival_seq in the batch


def run_backfill(*, batch_size: int = _DEFAULT_BATCH) -> BackfillResult:
    """Derive ``entities`` + ``edges`` from all ``spans`` in one transaction.

    Idempotent: running it twice produces identical rows (and identical
    ``edge_seq`` values). Returns a :class:`BackfillResult` for the smoke path
    and tests.
    """
    # entity_id -> [service_name, semantic_kind, sub_kind, first_seen, last_seen]
    entities: dict[str, list[Any]] = {}
    # (trace_id, span_id) -> (entity_id, semantic_kind)
    node: dict[tuple[str, str], tuple[str, str]] = {}
    # (trace_id, span_id, parent_id, to_entity, to_kind)
    edge_candidates: list[tuple[str, str, str, str, str]] = []
    scanned = 0

    with db.transaction() as tx:
        for trace_id, span_id, parent_id, service_name, kind, started_at, attrs, _seq in _scan_spans(
            tx, batch_size
        ):
            scanned += 1
            attributes = attrs or {}
            sk = semantic_kind(attributes, kind)
            sub = sub_kind(sk, attributes)
            eid = entity_id(service_name, sk, sub)
            node[(trace_id, span_id)] = (eid, sk)

            existing = entities.get(eid)
            if existing is None:
                entities[eid] = [service_name, sk, sub, started_at, started_at]
            else:
                if started_at < existing[3]:
                    existing[3] = started_at
                if started_at > existing[4]:
                    existing[4] = started_at

            if parent_id is not None:
                edge_candidates.append((trace_id, span_id, parent_id, eid, sk))

        # Pass 1 write: distinct entities, before edges (FK ordering).
        entity_params = [
            (
                eid,
                svc,
                sk,
                sub,
                display_name(svc, sk, sub),
                first_seen,
                last_seen,
            )
            for eid, (svc, sk, sub, first_seen, last_seen) in entities.items()
        ]
        if entity_params:
            tx.execute_many(_UPSERT_ENTITY_SQL, entity_params)

        # Pass 2: resolve edges against the in-memory node map.
        edge_params: list[tuple[Any, ...]] = []
        orphan_edges = 0
        for trace_id, span_id, parent_id, to_entity, to_kind in edge_candidates:
            parent = node.get((trace_id, parent_id))
            if parent is None:
                from_entity, from_kind = None, None
                orphan_edges += 1
            else:
                from_entity, from_kind = parent
            if from_entity == to_entity:
                # Same-entity (internal) boundary — not an edge. (to_entity is
                # never NULL, so an orphan with from_entity=NULL never matches
                # here and is correctly emitted as a boundary.)
                continue
            edge_params.append(
                (
                    trace_id,
                    span_id,
                    parent_id,
                    to_entity,
                    from_entity,
                    edge_kind(from_kind, to_kind),
                )
            )
        if edge_params:
            tx.execute_many(_UPSERT_EDGE_SQL, edge_params)

    return BackfillResult(
        spans_scanned=scanned,
        entities_upserted=len(entity_params),
        edges_upserted=len(edge_params),
        orphan_edges=orphan_edges,
    )
