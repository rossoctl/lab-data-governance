"""The tier of evidence that decided each interaction risk record version, and the late finding.

One table beside ``interaction_risk_records`` (0016), which is not altered. The risk
engine asks the policy per tier of evidence on one snapshot — classification (0),
the labels known inside the conversation (1), the crossed labels (2) — and keeps the
strictest decision; each version it writes gets one row here, in the same
transaction, in every mode (``tier`` 0 when inherited labels are off).

``interaction_risk_tiers`` — keyed by the record version it describes
(``(interaction_id, version)``, the records' own unique key). ``tier`` is the tier
that decided the version: the first that blocked, else the highest asked. ``late``
is true when the version is stricter than the latest earlier version that evidenced
the same legs: a finding that came after a decision on the same evidence could have
been answered. It does not reach back; the earlier version stands. A row goes with its
record (``ON DELETE CASCADE``), so a script that deletes records deletes their rows.

Revision ID: 0033_interaction_risk_tiers
Revises: 0032_label_corrections
Create Date: 2026-11-13
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0033_interaction_risk_tiers"
down_revision: Union[str, Sequence[str], None] = "0032_label_corrections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE interaction_risk_tiers (
            interaction_id TEXT     NOT NULL,
            version        INTEGER  NOT NULL,
            tier           SMALLINT NOT NULL CHECK (tier BETWEEN 0 AND 2),
            late           BOOLEAN  NOT NULL,
            PRIMARY KEY (interaction_id, version),
            FOREIGN KEY (interaction_id, version)
                REFERENCES interaction_risk_records (interaction_id, version) ON DELETE CASCADE
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS interaction_risk_tiers")
