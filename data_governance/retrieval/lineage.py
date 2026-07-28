"""Retrieval submodule — the trace-scoped **Data lineage** read (issue #118).

Part of the :mod:`data_governance.retrieval` package. Sibling to
:mod:`.interactions` (the derived flow forest) and :mod:`.payloads` (the
content-addressed body + its inline verdict); this one reads what
**P-data-lineage** has materialised into ``lineage_metadata``.

**Pure lookup** (ADR-0027 D7). Matching runs at ingest and the metadata is
persisted precisely so a read is a ``SELECT`` — no matcher is resolved here, no
traversal is re-run, and nothing on this path can call an LLM/NER matcher per
payload pair. If lineage is missing, the answer is "not yet computed", never
"compute it now".

**Keyed per leg, not per payload** (ADR-0027 D5). The unit is the
**Interaction leg** ``(interaction_id, leg_type)``: identical payload bytes at
different positions carry completely different lineage, so ``payload_hash``
rides along as a *fact about the row* (the seam for the deferred reverse lookup)
and never as the key.

**Trace scoping goes through ``interactions``.** ``lineage_metadata`` has no
``trace_id`` of its own (same as ``interaction_legs``, ADR-0025 puts identity on
the parent), so the read joins legs → interactions and LEFT JOINs the metadata.
Getting that join wrong would pull unrelated traces into one answer — a false
cross-trace data-flow claim, the one thing a governance tool must not make
(inter-trace lineage is Step II, deferred).

Two absences are **graceful shapes, not errors**:

- **Eventual consistency** — lineage is derived asynchronously, so a leg exists
  before its lineage row does. The leg is still listed, with ``lineage=None``
  meaning *exactly* "not yet derived". This is the ``get_payload`` nullable
  **Classification** precedent (ADR-0024) applied to the same window: because
  every metadata column is ``NOT NULL``, an origin's *empty* triple (empty
  ``entity_path``, one-key map with an empty set) is a real value that stays
  distinguishable from the absent row.
- **Not-yet-migrated DB** — ``lineage_metadata`` (or the interactions schema the
  scoping joins through) may not exist yet; the read returns an empty typed
  result rather than raising, mirroring :func:`.interactions._derived_tables_exist`.

Trace-level ``complete``/``partial`` status (ADR-0027 D6) is deliberately absent
here — its storage location is an open ADR item and it ships with the
absent-payload ticket (#120).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from data_governance import db

__all__ = [
    "DataLineageLegView",
    "DataLineageView",
    "GetDataLineageResult",
    "get_data_lineage",
]


# ---------------------------------------------------------------------------
# Return types — field names serialize verbatim to the wire shape the UI reads
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataLineageView:
    """The **Data lineage metadata** triple for one **Interaction leg**, as
    persisted (spec ``docs/data_lineage_alg.md`` "Lineage metadata"):

    1. ``data_sources`` — the origins, each an **Entity**'s natural key.
    2. ``source_transformations`` — ``data_source -> list<transformation>``.
       Stored as JSONB because no array type expresses a map-to-set; a *list* on
       the wire since JSON has no set, and its order is insignificant (the
       processor writes it sorted, so a re-derivation is byte-identical).
    3. ``entity_path`` — the **ordered** entities the data passed through.
       Order is the whole point; an origin's path is legitimately empty.

    ``seq`` is the row's own cursor value (sourced from the leg it describes), so
    a consumer can tell a re-derivation apart from the original.
    """

    data_sources: list[str]
    source_transformations: dict[str, list[str]]
    entity_path: list[str]
    seq: int


@dataclass(frozen=True)
class DataLineageLegView:
    """One **Interaction leg** of a trace with its nullable lineage.

    The leg key is ``(interaction_id, leg_type)`` (ADR-0027 D5). ``payload_hash``
    is the leg's payload (``None`` when the leg carries no body) — a fact about
    the row, not its key. ``lineage`` is ``None`` in the eventual-consistency
    window before P-data-lineage has derived this leg, which is distinct from a
    derived-but-empty triple (see :class:`DataLineageView`).
    """

    interaction_id: str
    leg_type: str
    payload_hash: str | None
    lineage: DataLineageView | None


@dataclass(frozen=True)
class GetDataLineageResult:
    legs: list[DataLineageLegView] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Not-yet-migrated guard
# ---------------------------------------------------------------------------


def _lineage_tables_exist(tx: db.Transaction) -> bool:
    """Whether every table this read needs exists on this DB.

    ``lineage_metadata`` is migration 0011 while ``interactions`` /
    ``interaction_legs`` (which the trace scoping joins through) are 0004 —
    separate revisions, so all three are probed rather than one standing in for
    the rest as in :func:`.interactions._derived_tables_exist` (there the derived
    tables all land in the same migration, so probing one is sufficient). A
    deployment mid-upgrade serves an empty lineage result rather than a 500.

    ``COUNT(DISTINCT table_name)``, not ``COUNT(*)``: the same table name can
    legitimately appear in more than one schema on the search path, and a plain
    row count would then overshoot the expected 3 and read as *not* migrated.
    """
    row = tx.fetch_one(
        "SELECT COUNT(DISTINCT table_name) FROM information_schema.tables "
        "WHERE table_name IN ('lineage_metadata', 'interactions', "
        "'interaction_legs')"
    )
    return row is not None and row[0] == 3


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def get_data_lineage(trace_id: str) -> GetDataLineageResult:
    """Read the persisted **Data lineage metadata** for every **Interaction
    leg** of *trace_id*, in leg ``seq`` order.

    A pure lookup (ADR-0027 D7). Legs are driven off ``interaction_legs`` and
    ``lineage_metadata`` is LEFT JOINed on, so a leg whose lineage has not been
    derived yet is still returned with ``lineage=None`` ("not yet computed")
    rather than dropped or raised on. Empty when the trace has no interactions,
    and empty — never an error — when the lineage or interactions migration has
    not run.
    """
    with db.transaction() as tx:
        if not _lineage_tables_exist(tx):
            return GetDataLineageResult()

        rows = tx.fetch_all(
            "SELECT l.interaction_id::text, l.leg_type::text, l.payload_hash, "
            "       m.data_sources, m.source_transformations, m.entity_path, "
            "       m.seq "
            "FROM interaction_legs l "
            "JOIN interactions i ON i.id = l.interaction_id "
            "LEFT JOIN lineage_metadata m "
            "  ON m.interaction_id = l.interaction_id "
            " AND m.leg_type = l.leg_type "
            "WHERE i.trace_id = %s "
            # Leg seq is the only execution order the schema offers (the parent
            # interactions row has no seq) — ADR-0025/ADR-0027 D6 both reason in
            # it, so the lineage list reads in the order it was derived.
            "ORDER BY l.seq ASC",
            (trace_id,),
        )
        return GetDataLineageResult(
            legs=[
                DataLineageLegView(
                    interaction_id=r[0],
                    leg_type=r[1],
                    payload_hash=r[2],
                    lineage=_lineage_view(r[3:]),
                )
                for r in rows
            ]
        )


def _lineage_view(row: tuple) -> DataLineageView | None:
    """Shape a ``lineage_metadata`` LEFT JOIN slice into the nullable triple.

    The join columns are all-NULL exactly when no lineage row exists for the leg,
    because every metadata column in migration 0011 is ``NOT NULL`` — so probing
    ``seq`` (also NOT NULL, and never defaulted) unambiguously separates "not yet
    derived" from a derived origin whose triple is genuinely empty.
    """
    data_sources, source_transformations, entity_path, seq = row
    if seq is None:  # no lineage_metadata row (LEFT JOIN miss)
        return None
    return DataLineageView(
        data_sources=list(data_sources),
        source_transformations={
            source: list(transformations)
            for source, transformations in (source_transformations or {}).items()
        },
        entity_path=list(entity_path),
        seq=int(seq),
    )
