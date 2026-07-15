"""payload_classifications table for the P-classification verdict (issue #77).

Adds ``payload_classifications`` — the one-row-per-**Payload** store for the
data-governance **Classification** (CONTEXT.md, ADR-0024). It is the table
P-classification writes (one row per payload it drains) and the payload read
surface (``GET /api/payloads/{hash}``) joins to expose the nullable
``classification`` field.

Shape, per CONTEXT.md **Classification**:

  - ``content_hash``            PK — one verdict per content-addressed payload,
                                so dedup is inherited from the payload's content
                                addressing (a body referenced by many
                                interactions/traces is classified once).
  - the document-level verdict  ``sensitivity_level`` (the aggregated verdict),
                                ``regulatory_tags`` (the tags it carries),
                                ``contains_identity_bundle`` (does it hold an
                                identity bundle), ``is_personalized``, and
                                ``primary_domain``.
  - ``findings``                the **Findings** inline as JSONB — zero or more
                                sensitive items the NER model detected (a
                                ``(start, end)`` region + NER-tag type + derived
                                sensitivity attributes). Empty ``[]`` when the
                                verdict is clean.
  - ``model_version``           monotonic INTEGER (starts at 1) so verdicts from
                                different model/config generations are
                                comparable — the future re-classification hook
                                (ADR-0024); automatic re-classification on a
                                version change is NOT implemented here.

Write-once (ADR-0024): a **Payload** is immutable (content-addressed), so its
classification never mutates. There is deliberately NO ``seq`` /
``arrival_seq`` / finalization column — unlike ``interactions`` / ``entities``
which mutate in place (ADR-0012). P-classification writes with
``ON CONFLICT (content_hash) DO NOTHING``; a model upgrade is handled
operationally (truncate this table, reset the ``classification``
``processor_state`` cursor to 0, re-drain).

Per ADR-0002/0005 the DDL is hand-written ``op.execute(...)`` — no SQLAlchemy
ORM models. There are deliberately no foreign keys to ``interaction_payloads``:
P-classification drains the payload stream and writes idempotently; a strict FK
buys nothing here and matches the FK-free derived schema (migration 0004).

``sensitivity_level`` and ``primary_domain`` stay TEXT rather than Postgres
ENUM types: unlike the P-interactions structural enums (ADR-0014), the
classification taxonomy is model-derived and churns as the NER model / mapping
matures (issue #78), so the closed set is enforced in code, not the schema —
the same reasoning that keeps ``interaction_payloads.content_kind`` TEXT.

Revision ID: 0008_payload_classifications
Revises: 0007_payloads_cursorable_stream
Create Date: 2026-07-14
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0008_payload_classifications"
down_revision: Union[str, Sequence[str], None] = "0007_payloads_cursorable_stream"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One row per content-addressed Payload; the document-level verdict columns,
    # the Findings inline as JSONB, and the model_version hook (ADR-0024).
    #
    # regulatory_tags is a TEXT[] (the set of tags the payload carries);
    # findings defaults to an empty JSON array so a clean verdict is a real
    # zero-Findings row, never NULL (a NULL classification means "not yet
    # processed", and that distinction lives on the payload read surface, not
    # in this table — ADR-0024).
    op.execute(
        """
        CREATE TABLE payload_classifications (
            content_hash             TEXT    PRIMARY KEY,
            sensitivity_level        TEXT    NOT NULL,
            regulatory_tags          TEXT[]  NOT NULL DEFAULT '{}',
            contains_identity_bundle BOOLEAN NOT NULL DEFAULT FALSE,
            is_personalized          BOOLEAN NOT NULL DEFAULT FALSE,
            primary_domain           TEXT,
            findings                 JSONB   NOT NULL DEFAULT '[]'::jsonb,
            model_version            INTEGER NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS payload_classifications")
