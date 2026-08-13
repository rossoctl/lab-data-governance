"""Asserts the current head revision of the migration chain.

Lives in its own file (rather than inside whichever migration test happened
to be the last one written) so that adding a new revision touches exactly one
line in exactly one file, instead of forcing an edit inside an unrelated
migration's test module.
"""

from __future__ import annotations

import psycopg


def test_head_is_0017(migrated_dsn: str) -> None:
    """Applying the chain to head lands on the current head revision (0017 —
    the interaction policy-decision table, issue #101, chained after 0016).

    The chain is LINEAR. Merging `main`'s lineage chain (0012-0015) into this
    branch produced two heads, since this branch's own 0012/0013 (DAS risk
    tables, interaction policy decisions) had numbered from the same
    0011_drop_leg_original_seq parent. Resolved by re-parenting this branch's
    two revisions onto `main`'s new head and renumbering them 0016-0017 (see
    ``0016_das_risk_tables``'s docstring) — the same fix `main` applied to its
    own lineage-chain collision when it renumbered 0012-0015.

    This is the one canonical head-revision assertion (mirrors
    ``test_classifications_migration.py::test_head_is_0015`` on `main` before
    this merge); every other migration's test asserts mere chain-reachability
    instead, so a new revision landing on top only has to edit this file."""
    with psycopg.connect(migrated_dsn) as conn:
        (version,) = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert version == "0017_policy_decisions"
