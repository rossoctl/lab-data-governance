"""lineage_metadata table for intra-trace data lineage (issue #117, ADR-0027).

Adds ``lineage_metadata`` — the store for **data lineage**: where each interaction
leg's payload originated and what it passed through. It is the table the
data-lineage processor writes (one row per payload-bearing leg of every trace it
derives) and the read surface a future lineage API (#118) will serve, so "what are
the data sources of this payload" is a **read** rather than a recompute (ADR-0027
D7 — matching runs at ingest; a query-time matcher call per payload pair over a
trace's whole history is exactly what persisting avoids).

Shape, per the spec's "Lineage metadata" triple (``docs/data_lineage_alg.md``):

  - ``(interaction_id, leg_type)``   PK. **The leg is the key, not the payload
                                    hash** (ADR-0027 D5): lineage is
                                    position-dependent while a content hash is
                                    not. Payloads are content-addressed and
                                    deduped, so identical bytes can appear at
                                    different positions — across traces, or within
                                    one trace when an entity echoes an input
                                    verbatim — with completely different lineage.
                                    Keying on the hash would collide those distinct
                                    facts into one row. The hash says *what the
                                    content is*; lineage is about *where it came
                                    from*.
  - ``data_sources``                TEXT[] — the set of origins (spec rule 1). Each
                                    element is an **Entity** natural key ("the data
                                    source is assigned the entity name").
  - ``source_transformations``      JSONB — the ``data_source -> set<transformation>``
                                    map (spec rule 2). JSONB because no array type
                                    expresses a map-to-set; the sets serialize as
                                    JSON arrays whose order is insignificant ("order
                                    doesn't matter").
  - ``entity_path``                TEXT[] — the ORDERED list of entities the data
                                    passed through (spec rule 3). Array, not JSONB,
                                    because order is the whole point.
  - ``payload_hash``               the hash of the payload this row describes, kept
                                    as a **secondary index only** (D5) for the
                                    deferred reverse lookup ("where did this content
                                    come from / go"). Non-unique: the same content
                                    legitimately appears on many legs.
  - ``seq``                        the row's own sequence, so a future consumer can
                                    cursor this table the way every other stream is
                                    cursored (ADR-0007). Sourced from the
                                    ``interaction_legs`` row the metadata describes,
                                    so the lineage of a re-derived leg re-announces
                                    itself past a consumer's cursor exactly as the
                                    leg did.

Every metadata column is ``NOT NULL``: an origin has an *empty* entity path and a
one-key map — real empty values, never NULL. NULL would be indistinguishable from
"not yet derived", and the absence of the row already carries that meaning.

Idempotent re-derive (not write-once): the processor re-derives a whole trace on
every arriving leg and upserts, because ``interaction_legs`` rows are themselves
rewritten in place when P-interactions re-derives a trace (the reason migration
0010's NOTIFY trigger covers UPDATE as well as INSERT). So unlike
``payload_classifications`` (write-once over immutable content-addressed payloads,
migration 0008), a conflicting write here must take the DO UPDATE path or stale
lineage would outlive the legs it was derived from. Operational recovery is the
established one: truncate this table, reset the ``data_lineage``
``processor_state`` cursor to 0, re-drain.

``leg_type`` reuses the ``leg_type`` ENUM from 0009 (ADR-0014's structural enums),
so this key cannot hold a leg type ``interaction_legs`` could not.

Per ADR-0002/0005 the DDL is hand-written ``op.execute(...)`` — no SQLAlchemy ORM
models. FK-free, matching the derived schema from 0004: the processor writes
idempotently and every derived table is rebuildable by cursor replay, so a strict
FK to ``interaction_legs`` buys nothing and would only order the truncates.

What this revision deliberately does NOT add:

- **No trace-level ``partial``/``complete`` status table.** ADR-0027 D6 requires
  one to exist eventually, but *where* it lives (a ``lineage_trace_status`` table vs
  derived on read) was open when this revision landed, and absent-payload handling
  was its own ticket (#120). Shipping a column for it here would have fixed the open
  choice by accident. (Since resolved: ADR-0027 **D8** chose the dedicated table,
  added by ``0012_lineage_trace_status``.)
- **No reverse ``payload -> persisting entity`` map.** Deferred (ADR-0027's
  "Outputs"); the ``payload_hash`` index is the seam it will use.
- **No matcher-version column.** Matcher versioning and backfill after a matcher
  change are explicitly deferred (ADR-0027 D7).

Revision ID: 0011_lineage_metadata
Revises: 0010_legs_notify_trigger
Create Date: 2026-07-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0011_lineage_metadata"
down_revision: Union[str, Sequence[str], None] = "0010_legs_notify_trigger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One row per payload-bearing interaction leg. PK is the LEG (D5).
    # Defaults make the empty triple the natural construction for an origin.
    op.execute(
        """
        CREATE TABLE lineage_metadata (
            interaction_id         TEXT     NOT NULL,
            leg_type               leg_type NOT NULL,
            data_sources           TEXT[]   NOT NULL DEFAULT '{}',
            source_transformations JSONB    NOT NULL DEFAULT '{}'::jsonb,
            entity_path            TEXT[]   NOT NULL DEFAULT '{}',
            payload_hash           TEXT,
            seq                    BIGINT   NOT NULL,
            PRIMARY KEY (interaction_id, leg_type)
        )
        """
    )
    # Secondary index on the content hash (D5) — the seam for the deferred reverse
    # lookup. NOT unique: identical content on many legs is normal and expected.
    op.execute(
        "CREATE INDEX lineage_metadata_payload_hash_idx "
        "ON lineage_metadata (payload_hash)"
    )
    # Cursor pagination over this table's own seq (ADR-0007's shape), so the lineage
    # read path / a future downstream consumer can drain it.
    op.execute("CREATE INDEX lineage_metadata_seq_idx ON lineage_metadata (seq)")


def downgrade() -> None:
    # The indexes go with the table. 0009's leg_type ENUM is shared with
    # interaction_legs and is not this revision's to remove.
    op.execute("DROP TABLE IF EXISTS lineage_metadata")
