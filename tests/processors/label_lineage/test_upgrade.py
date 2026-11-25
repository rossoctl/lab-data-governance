"""Label lineage across the upgrade to 0032: a conversation derived before it has no send rows, so no correction
could find it, until it is derived again."""

from __future__ import annotations

import psycopg

from data_governance import db
from data_governance.label_lineage import cross, find
from data_governance.processors.label_lineage import driver
from tests.label_lineage.test_cross import _first, _second
from tests.processors.label_lineage.test_driver import _crossings, _q, _seed_input

CROSSED = driver.Config(find.exact, "exact", crossing=cross.Crossing())


def test_a_conversation_derived_before_0032_follows_a_correction_after_the_upgrade(pg_dsn: str, monkeypatch) -> None:
    """T2 crosses T1's SSN and birthdate; back at 0031 the send rows are gone, as on a database that 0031's code
    derived. After the upgrade and one wake, deleting T1's receiving request takes T2's crossings with it."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    db.configure(pg_dsn)
    try:
        _seed_input(pg_dsn, _first())
        _seed_input(pg_dsn, _second())
        cursor = driver.drain(0, CROSSED)
        assert _crossings(pg_dsn) == [("DOB", "T1", "corroborated"), ("SSN", "T1", "corroborated")]
        db.close_pool()
        command.downgrade(cfg, "0031_label_crossings")
        command.upgrade(cfg, "head")
        assert _q(pg_dsn, "SELECT count(*) FROM label_sends") == [(0,)]
        db.configure(pg_dsn)
        driver.drain(cursor, CROSSED)
        with psycopg.connect(pg_dsn) as conn:
            conn.execute("DELETE FROM interaction_legs WHERE interaction_id = '1' AND leg_type = 'request'")
        driver.drain(cursor, CROSSED)
        assert _crossings(pg_dsn) == []
        assert _q(pg_dsn, "SELECT count(*) FROM label_lineage_dirty_traces") == [(0,)]
    finally:
        db.close_pool()
