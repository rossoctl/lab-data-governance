"""Add ``entities.namespace`` — the Kubernetes namespace of a pod entity.

**Why a column, and why the column alone is not the fix.** Two workloads with
the same name in two namespaces (``team1/weather-service``, ``team2/weather-service``)
derived as ONE entity: the sidecar's ``lineage.self.id`` is the last segment of
the SPIFFE ID, and ``entities.natural_key`` — the UNIQUE column, and the input to
the uuid5 row id — was ``kind:self.id``. Wire contract v1.7 adds the fact
``lineage.self.namespace`` (required at the producer), and the ``sidecar``
interactions algorithm now composes the natural key as
``kind:namespace/self.id``. THAT split is what makes two rows. This column
carries the same value a second time so it can be read without parsing the key:
"all entities in team2", the UI's entity card, a future per-namespace filter.

**Nullable, no backfill, and a cutover the migration cannot make.** Only entities
that ARE a pod have a namespace — a ``user:``, an anonymous ``client:``, an
``llm:`` endpoint, or an un-sidecared callee named by ``peer.host`` have none.
Spans a pre-v1.7 producer emitted carry no namespace, and re-deriving them
reproduces their un-namespaced keys — the consumer never guesses one (ADR-0030
"no mechanism may guess") — so a replay migrates nothing: history keeps its old
identity. From the cutover on, the same pod's spans carry the fact and mint a
NEW row with a new id. A pod therefore has two rows, one for its history and one
going forward, both live, unless the operator drops the pre-v1.7 spans before
the reset in ``docs/CUTOVER-two-span.md`` (truncate the derived tables, reset
the cursor, replay). That is an operator decision about history, stated here so
nobody expects a migration or a replay to unify the two.

**Numbered 0020, on 0015.** Main's chain ends at 0015; the ``risk`` branch, whose
revisions are stamped in a live ``alembic_version``, already carries its own
0016–0019. Two revisions off one parent are two heads and refuse ``upgrade head``
whichever number this one carries, so the merge must re-parent one of them — and
it must be this one, unshipped, not the ones a live database points at. Naming it
0020 now makes that merge a one-line ``down_revision`` edit with no file, test or
pin churn (the deploy branch already runs it that way, on 0019).

**Not part of the UNIQUE constraint.** Identity stays a single string
(``natural_key``) hashed by one formula (``procedure._entity_id``); a composite
``(namespace, natural_key)`` uniqueness would need the id to hash both and
gains nothing once the namespace is inside the key.

Per ADR-0002/0005 the DDL is hand-written ``op.execute(...)``. Reversible: the
downgrade drops the column; the namespaced keys survive it, so a rollback to
0015 leaves a schema 0015's code can read (it never selects the column).

Revision ID: 0020_entity_namespace
Revises: 0015_lineage_entities_rename
Create Date: 2026-09-09
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0020_entity_namespace"
down_revision: Union[str, Sequence[str], None] = "0015_lineage_entities_rename"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable TEXT, no default: absence is a fact (not a pod, or a pre-v1.7
    # producer), never a placeholder.
    op.execute("ALTER TABLE entities ADD COLUMN namespace TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE entities DROP COLUMN namespace")
