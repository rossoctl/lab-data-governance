"""Asserts the current head revision of the migration chain.

Lives in its own file (rather than inside whichever migration test happened
to be the last one written) so that adding a new revision touches exactly one
line in exactly one file, instead of forcing an edit inside an unrelated
migration's test module.
"""

from __future__ import annotations

import psycopg


def test_head_is_0019(migrated_dsn: str) -> None:
    """Applying the chain to head lands on the current head revision (0019 —
    the interaction-risk seq cursor column, issue #164, chained after 0018).

    The chain is LINEAR. Merging `main`'s lineage chain (0012-0015) into this
    branch produced two heads, since this branch's own 0012/0013 (DAS risk
    tables, interaction policy decisions) had numbered from the same
    0011_drop_leg_original_seq parent. Resolved by re-parenting this branch's
    two revisions onto `main`'s new head and renumbering them 0016-0017 (see
    ``0016_das_risk_tables``'s docstring) — the same fix `main` applied to its
    own lineage-chain collision when it renumbered 0012-0015.

    0019 is the second application of that same fix. #164's cursor column and
    #102's trace aggregation-mode columns were written concurrently on
    separate branches, both numbering 0018 off the 0017_policy_decisions
    parent. ``0018_trace_aggregation_modes`` reached `risk` first, so #164's
    revision was renumbered 0018 -> 0019 and re-parented onto it. The two
    migrations touch disjoint tables (``interaction_risk_records`` vs
    ``trace_risk_records``), so the order between them carries no meaning —
    only linearity does.

    This is the one canonical head-revision assertion (mirrors
    ``test_classifications_migration.py::test_head_is_0015`` on `main` before
    this merge); every other migration's test asserts mere chain-reachability
    instead, so a new revision landing on top only has to edit this file."""
    with psycopg.connect(migrated_dsn) as conn:
        (version,) = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert version == "0019_interaction_risk_seq"
