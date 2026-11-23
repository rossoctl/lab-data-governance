"""Asserts the current head revision of the migration chain.

Lives in its own file (rather than inside whichever migration test happened
to be the last one written) so that adding a new revision touches exactly one
line in exactly one file, instead of forcing an edit inside an unrelated
migration's test module.
"""

from __future__ import annotations

import psycopg


def test_head_is_0030(migrated_dsn: str) -> None:
    """Both migration branches converge on one head. Other migration tests assert
    their schema and reachability without duplicating this head assertion."""
    with psycopg.connect(migrated_dsn) as conn:
        (version,) = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert version == "0030_label_edges_holding_index"
