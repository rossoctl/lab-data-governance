"""Asserts the current head revision of the migration chain.

Lives in its own file (rather than inside whichever migration test happened
to be the last one written) so that adding a new revision touches exactly one
line in exactly one file, instead of forcing an edit inside an unrelated
migration's test module.
"""

from __future__ import annotations

import psycopg


def test_head_is_0013(migrated_dsn: str) -> None:
    """Applying the chain to head lands on the current head revision (0013 —
    the interaction policy-decision table, issue #101, chained after 0012)."""
    with psycopg.connect(migrated_dsn) as conn:
        (version,) = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert version == "0013_policy_decisions"
