"""Join the two alembic heads: the lineage chain and ``main``'s leg-seq cleanup.

**A no-op merge revision. It has no ``upgrade`` body and no schema effect.**
Its whole purpose is to give alembic a single head again.

Two branches independently numbered their migrations from 0010, so merging them
produced a fork rather than a chain:

    0009 ─┬─ 0010_legs_notify_trigger ─ 0011_lineage_metadata ─ 0012_lineage_trace_status ─ 0013_lineage_entities_rename
          └─ 0010_entity_ready_notify ─ 0011_drop_leg_original_seq

Both tips were heads. Alembic refuses ``upgrade head`` with more than one, and in
this repo *every* pod migrates to head on startup (ADR-0002), so two heads is not a
tidiness problem — it is every deployment failing to boot. This revision names both
tips as its ``down_revision`` and is therefore the only head again.

**Why a merge revision rather than renumbering.** The obvious alternative is to
re-parent the lineage chain onto ``main``'s 0011 and renumber it 0012-0014. That
rewrites migrations that have already **run** — and the live cluster's
``alembic_version`` is stamped ``0013_lineage_entities_rename``. Renumbering erases
the revision id the running database points at, leaving alembic unable to locate its
position in the chain: the next pod start fails, and recovering means hand-stamping
production. A merge revision costs one inert file and touches neither shipped
migration nor the deployed stamp.

It also keeps both histories legible. Each branch's revisions stay exactly as they
shipped, in the order they shipped, so ``walk_revisions`` still explains how the
schema actually got here — which for a governance tool's audit trail is worth more
than a linear-looking sequence that was edited after the fact.

**Ordering between the two branches is unconstrained, and that is correct.** The
branches touch disjoint objects: the lineage side creates ``lineage_metadata`` /
``lineage_trace_status`` and renames a column inside the former, while ``main``'s
side drops ``interaction_legs.original_seq`` and adds an entity-ready NOTIFY. No
revision on either side reads or writes anything the other creates, so alembic may
apply them in either interleaving and reach the same schema. There is nothing for
this revision to reconcile — hence the empty body rather than a fix-up.

**Nothing to undo, hence a no-op ``downgrade``.** Downgrading *past* a merge point
is alembic re-splitting the chain, which it does by walking back into each branch;
this revision itself removed nothing, so it restores nothing. The two branches'
own downgrades remain the real ones.

Follow-up, deliberately NOT done here: the duplicate ``0010``/``0011`` numbering is
now permanent history. Future revisions should continue from ``0014`` so the numbers
stay monotonic from this point on, even though two earlier numbers appear twice.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

# revision identifiers, used by Alembic.
revision: str = "0014_merge_lineage_and_leg_seq"
# BOTH former heads. This tuple is the entire point of the file: it is what turns
# two heads into one. The order of the pair is not meaningful to alembic.
down_revision: Union[str, Sequence[str], None] = (
    "0013_lineage_entities_rename",
    "0011_drop_leg_original_seq",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No schema change — see the module docstring. The two merged branches touch
    disjoint objects, so joining them requires no reconciliation."""


def downgrade() -> None:
    """No schema change to reverse. Downgrading past this point re-splits the
    chain; each branch's own ``downgrade`` does the real work."""
