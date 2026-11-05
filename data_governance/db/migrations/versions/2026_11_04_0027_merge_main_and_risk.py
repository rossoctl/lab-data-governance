"""Join main's causal-order migration and risk's label-lineage migration.

The shared 0020/0022 ancestry remains the ancestry deployed from main. Risk's
0016–0019 branch joins it at 0023, so upgrading an existing main database runs
the missing risk DDL; an existing risk database at 0024 already has that DDL.
This revision joins 0024 and 0026 without changing either branch's schema.

Revision ID: 0027_merge_main_and_risk
Revises: 0024_label_lineage, 0026_causal_leg_order
Create Date: 2026-11-04
"""

from __future__ import annotations

revision = "0027_merge_main_and_risk"
down_revision = ("0024_label_lineage", "0026_causal_leg_order")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
