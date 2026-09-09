"""Tests for migration 0020 (numbered past the risk branch; see its docstring) — ``entities.namespace``.

The column carries a pod entity's Kubernetes namespace (wire contract v1.7,
``lineage.self.namespace``) so it can be read without parsing the natural key;
the identity split itself lives in the natural key (``kind:namespace/self.id``),
which is why the column is nullable, unindexed and outside every constraint.

Like the sibling migration tests these run the real migration chain against a
real Postgres (testcontainers) and assert what it produces, not how.
"""

from __future__ import annotations

import psycopg


def _columns(dsn: str, table: str) -> dict[str, dict[str, object]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
    return {name: {"data_type": dt, "is_nullable": nn} for name, dt, nn in rows}


def test_entities_has_a_nullable_namespace_column(migrated_dsn: str) -> None:
    cols = _columns(migrated_dsn, "entities")
    assert cols["namespace"] == {"data_type": "text", "is_nullable": "YES"}
    # The identity contract is untouched: natural_key stays the UNIQUE column.
    with psycopg.connect(migrated_dsn) as conn:
        uniques = {
            r[0]
            for r in conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'entities'"
            ).fetchall()
            if "UNIQUE" in r[0]
        }
    assert any("(natural_key)" in u for u in uniques)
    assert not any("namespace" in u for u in uniques)


def test_namespace_is_absent_for_non_pod_rows(migrated_dsn: str) -> None:
    """A row inserted the way the pre-0016 writers insert (no namespace) lands
    with NULL — absence is a fact, not a default placeholder."""
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, seq, original_seq) "
            "VALUES ('e-user', 'user', 'user:alice', 'alice', 'test', 1, 1)"
        )
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, namespace, "
            "detected_from, seq, original_seq) "
            "VALUES ('e-pod', 'agent', 'agent:team2/weather-service', 'weather-service', "
            "'team2', 'test', 2, 2)"
        )
        rows = dict(
            conn.execute("SELECT natural_key, namespace FROM entities ORDER BY seq").fetchall()
        )
        conn.rollback()
    assert rows == {"user:alice": None, "agent:team2/weather-service": "team2"}


def test_0020_is_applied_in_the_chain() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    walked = {rev.revision for rev in script.walk_revisions()}
    assert "0020_entity_namespace" in walked


def test_downgrade_then_upgrade_round_trips(pg_dsn: str, monkeypatch) -> None:
    """upgrade -> downgrade(0015) -> upgrade adds, drops and re-adds the column;
    the entities table itself (0004) survives the downgrade."""
    from alembic import command

    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    assert "namespace" in _columns(pg_dsn, "entities")

    command.downgrade(cfg, "0019_interaction_risk_seq")
    cols = _columns(pg_dsn, "entities")
    assert cols, "the entities table itself must survive the downgrade"
    assert "namespace" not in cols

    command.upgrade(cfg, "head")
    assert "namespace" in _columns(pg_dsn, "entities")
