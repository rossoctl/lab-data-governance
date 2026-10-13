"""Move ``content_kind`` from the content row to the reference (issue #286).

``interaction_payloads`` is content-addressed: one row per distinct bytes,
referenced from every leg that carries them, classified once (ADR-0024). The
**Content kind** is not a property of the bytes — it is the role the bytes
played on one leg (an agent that returns its LLM's completion verbatim makes
one row that is ``llm_completion`` on one leg and ``agent_response`` on the
other). Stored on the content row it could only ever hold the first writer's
kind (``ON CONFLICT (content_hash) DO NOTHING``), so every other reference
read a wrong kind. It now lives on ``interaction_legs``, next to the
``payload_hash`` it qualifies, and the content row is bytes-only.

The classification processor no longer reads a kind: the **Text projection
rule** is a function of the content alone (it recognises the message-list
shape structurally), which is what makes one classification per
``content_hash`` sound by construction rather than by coincidence.

``content_kind`` stays TEXT (ADR-0014: the vocabulary churns with the
classifier, so it is validated in code, not by an ENUM) and is nullable: NULL
for a protocol with no semantic body kind (plain http, in the sidecar
vocabulary) and for a leg no extraction produced.

No backfill, by decision: the derived tables are rebuilt from ``spans`` by
cursor replay (truncate the derived tables, reset the processor cursor), so
legs written before this revision carry NULL until their trace is re-derived.
``payload_classifications`` rows are not touched: for every shape in the
golden and e2e corpora the old projection gave the same text, so those
verdicts stand. One shape projects differently now — a message envelope
none of whose messages carries text (old rule: the empty string; now:
serialised whole); rows classified under the old rule for it keep their
verdict until the operational re-classification (ADR-0024).

Numbered 0022 on 0020. PR #281 adds 0021 on the same parent; when it lands,
this revision re-parents onto it with a one-line ``down_revision`` edit, as
0020 itself did against the ``risk`` chain.

Revision ID: 0022_content_kind_on_legs
Revises: 0020_entity_namespace
Create Date: 2026-10-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0022_content_kind_on_legs"
down_revision: Union[str, Sequence[str], None] = "0020_entity_namespace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE interaction_legs ADD COLUMN content_kind TEXT")
    op.execute("ALTER TABLE interaction_payloads DROP COLUMN content_kind")


def downgrade() -> None:
    # The pre-0022 column was NOT NULL; existing rows cannot recover a kind the
    # content row never truly owned, so they take the vocabulary's open-world
    # value (CONTEXT.md **Content kind**: ``unknown``). The leg column goes.
    op.execute(
        "ALTER TABLE interaction_payloads "
        "ADD COLUMN content_kind TEXT NOT NULL DEFAULT 'unknown'"
    )
    op.execute("ALTER TABLE interaction_payloads ALTER COLUMN content_kind DROP DEFAULT")
    op.execute("ALTER TABLE interaction_legs DROP COLUMN content_kind")
