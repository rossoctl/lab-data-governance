"""Rename ``lineage_metadata.entity_path`` to ``entities`` — the spec made it a set.

A pure rename of one column, because the **authoritative spec changed its
meaning**. ``docs/data_lineage_alg.md`` (human-owned) previously described the
third element of the lineage metadata triple as a *list* of entities; it now says:

    3. the set of entities - through which entities the data passed through
            Note: this is unordered. In case an order is needed - it will need to
            be derived from the trace using an API.

and the operation rules read "A new **set** of entities which is empty", "create a
copy of the entity **set** and extend it with the entity name", "the **set** of
entities is merged and extended with the entity".

**Why the column could not keep its name.** ``entity_path`` promised a *path* — a
sequence, something a consumer may legitimately read hop-by-hop. Migration 0011
documented it as "the ORDERED list of entities ... Array, not JSONB, because order
is the whole point". That promise was never safely keepable: a ``merge`` unions two
branches that arrived through different entities, and there is no single truthful
interleaving of them to serve — the old implementation could only offer an
arbitrary first-arrival order out of its traversal. The spec has now settled the
question in the honest direction (unordered, with ordering deferred to a future
trace-derived API), so the column name must stop advertising a guarantee the data
does not carry. A field named ``entity_path`` invites exactly the reading the spec
now forbids, and a stale name on a governance claim is worse than a churn-y
migration.

**Rename, not drop-and-add.** The column's contents are still valid — the same
entity natural keys, only re-read as a set instead of a sequence — so existing
rows survive untouched. ``ALTER TABLE ... RENAME COLUMN`` also keeps the indexes
and the ``NOT NULL``/``DEFAULT '{}'`` intact for free.

**The type stays ``TEXT[]``.** Postgres has no set type, so the array remains the
representation; what changed is that its *order is meaningless*. The processor
writes it **sorted** — the same trick ``source_transformations`` already uses for
its sets — purely so a re-derivation of identical lineage produces byte-identical
rows and idempotency stays observable. That sort is serialization, not semantics:
nothing may infer flow order from the array position.

Migration 0011 is deliberately **left as it was**: it recorded the shape that was
correct when it shipped, and rewriting applied history to look like it always knew
better hides the fact that the spec moved. The *why* lives here, in the revision
that made the change (ADR-0027 records the decision).

Per ADR-0002/0005 the DDL is hand-written ``op.execute(...)`` — no SQLAlchemy ORM
models, no autogenerate. Reversible: the downgrade renames back, so a rollback to
0012 leaves a schema 0012's code can read.

The revision id is terse (``0013_lineage_entities_rename``, not
``0013_rename_entity_path_to_entities``) because Alembic's ``alembic_version``
table stores it in a ``VARCHAR(32)``; a longer id fails at write time on the very
migration that would have widened it.

Revision ID: 0013_lineage_entities_rename
Revises: 0012_lineage_trace_status
Create Date: 2026-07-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0013_lineage_entities_rename"
down_revision: Union[str, Sequence[str], None] = "0012_lineage_trace_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename in place: the values are unchanged, only their contract is (a set,
    # not a path). Indexes, NOT NULL and the '{}' default all ride along.
    op.execute("ALTER TABLE lineage_metadata RENAME COLUMN entity_path TO entities")


def downgrade() -> None:
    # Symmetric rename back, so 0012-era code finds the column it expects. Data is
    # untouched in both directions; only the promise the name makes differs.
    op.execute("ALTER TABLE lineage_metadata RENAME COLUMN entities TO entity_path")
