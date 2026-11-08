"""Upgrade the schemas actually produced by each branch, preserving evidence.

The historical fixtures execute the shipped migration DDL with the branch's
original ancestry. They do not simulate an old database by stamping a fresh
schema. In particular, risk's old 0020 follows 0019, whereas main's follows 0015.
"""

from __future__ import annotations

import ast
import hashlib
import shutil
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from data_governance.db.migrate import _alembic_config

_RISK_REVISIONS = {16, 17, 18, 19, 23, 24}
_TABLES = {
    "interaction_risk_records", "interaction_policy_decisions", "trace_risk_records", "alerts",
    "payload_label_edges", "leg_inherited_labels", "label_lineage_trace_status",
    "data_lineage_dirty_traces", "label_lineage_dirty_traces", "risk_recompute_requests",
    "trace_risk_recompute_requests",
}
_TRIGGERS = {
    "dg_interaction_risk_notify", "dg_trace_risk_notify", "dg_lineage_interaction_dirty",
    "dg_governance_interaction_dirty", "dg_governance_interaction_deleted",
    "dg_governance_leg_dirty", "dg_governance_leg_deleted", "dg_governance_legs_inserted",
    "dg_risk_request_generation",
}


def historical_config(tmp_path: Path, branch: str) -> Config:
    """Freeze the main/risk graphs that existed before PR #338 was resolved."""
    cfg = _alembic_config()
    source = Path(cfg.get_main_option("script_location"))
    scripts = tmp_path / branch
    shutil.copytree(source, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    for path in (scripts / "versions").glob("*.py"):
        text = path.read_text()
        assignments = {}
        for node in ast.parse(text).body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assignments[node.target.id] = node
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                assignments[node.targets[0].id] = node
        if "revision" not in assignments:
            continue
        revision = ast.literal_eval(assignments["revision"].value)
        number = int(revision.split("_", 1)[0])
        if number > (26 if branch == "main" else 24) or (branch == "main" and number in _RISK_REVISIONS):
            path.unlink()
            continue
        if branch == "risk" and number in (20, 23):
            parent = "0019_interaction_risk_seq" if number == 20 else "0022_content_kind_on_legs"
            node = assignments["down_revision"]
            lines = text.splitlines(keepends=True)
            lines[node.lineno - 1:node.end_lineno] = [f"down_revision = {parent!r}\n"]
            path.write_text("".join(lines))
    cfg.set_main_option("script_location", str(scripts))
    return cfg


def seed_evidence(dsn: str, *, risk: bool, payload_kind: bool = False) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, detected_from, original_seq, namespace) "
            "VALUES ('upgrade-agent', 'agent', 'agent:kept', 'Kept agent', 'observed', 1, 'kept-namespace')"
        )
        conn.execute(
            "INSERT INTO spans (trace_id, span_id, name, started_at, attributes, arrival_seq) "
            "VALUES ('upgrade-trace', 'kept-span', 'kept', '2026-01-01T00:00:00Z', '{\"kept\":true}', 1)"
        )
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, summary) "
            "VALUES ('upgrade-ix', 'upgrade-trace', 'upgrade-agent', 'upgrade-agent', 'kept summary')"
        )
        if payload_kind:
            conn.execute(
                "INSERT INTO interaction_payloads (content_hash, content_kind, content, byte_size) "
                "VALUES ('kept-payload', 'agent_request', '{\"kept\":true}', 13)"
            )
        else:
            conn.execute(
                "INSERT INTO interaction_payloads (content_hash, content, byte_size) "
                "VALUES ('kept-payload', '{\"kept\":true}', 13)"
            )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, payload_hash) "
            "VALUES ('upgrade-ix', 'request', '2026-01-01T00:00:01Z', 'kept-payload')"
        )
        if risk:
            conn.execute(
                "INSERT INTO interaction_risk_records (interaction_id, trace_id, caller_entity_id, "
                "callee_entity_id, version, computed_at, risk_level, policy_event_count) "
                "VALUES ('upgrade-ix', 'upgrade-trace', 'upgrade-agent', 'upgrade-agent', "
                "1, '2026-01-01T00:00:02Z', 'high', 1)"
            )
            conn.execute(
                "INSERT INTO interaction_policy_decisions (interaction_id, version, evaluated_at, "
                "risk_level, evidence_fingerprint) "
                "VALUES ('upgrade-ix', 1, '2026-01-01T00:00:02Z', 'high', %s)",
                # Merge preservation is independent of 0029's legacy-key
                # backfill, which has its own historical-row regression.
                (hashlib.sha256(b"kept-fingerprint").hexdigest(),),
            )
            conn.execute(
                "INSERT INTO trace_risk_records (trace_id, version, computed_at, trace_risk_level, "
                "interaction_count, policy_event_count) "
                "VALUES ('upgrade-trace', 1, '2026-01-01T00:00:03Z', 'high', 1, 1)"
            )
            conn.execute(
                "INSERT INTO alerts (timestamp, trace_id, risk_level, title, description) "
                "VALUES ('2026-01-01T00:00:04Z', 'upgrade-trace', 'high', 'Kept alert', 'Kept description')"
            )


def evidence_snapshot(dsn: str, *, risk: bool) -> list[list[tuple]]:
    queries = [
        "SELECT id, natural_key, namespace FROM entities ORDER BY id",
        "SELECT trace_id, span_id, name, attributes, seq FROM spans ORDER BY seq",
        "SELECT id, trace_id, summary FROM interactions ORDER BY id",
        "SELECT interaction_id, leg_type, payload_hash, occurred_at, seq FROM interaction_legs ORDER BY seq",
        "SELECT content_hash, content FROM interaction_payloads ORDER BY content_hash",
    ]
    if risk:
        queries += [
            "SELECT interaction_risk_id, interaction_id, version, risk_level, seq FROM interaction_risk_records",
            "SELECT interaction_id, version, evidence_fingerprint FROM interaction_policy_decisions",
            "SELECT trace_risk_id, trace_id, version, trace_risk_level FROM trace_risk_records",
            "SELECT alert_id, trace_id, title, description, status FROM alerts",
        ]
    with psycopg.connect(dsn) as conn:
        return [conn.execute(query).fetchall() for query in queries]


def assert_combined_schema(dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        triggers = {r[0] for r in conn.execute(
            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgenabled = 'O'")}
        assert _TABLES <= tables
        assert _TRIGGERS <= triggers
        assert conn.execute("SELECT count(*) FROM alembic_version").fetchone() == (1,)
        assert conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'lineage_trace_status' "
            "AND column_name = 'prefix_leg_keys'").fetchone() == ("prefix_leg_keys",)
        assert conn.execute("SELECT pg_get_serial_sequence('interaction_risk_records', 'seq')").fetchone() == (
            "public.interaction_risk_seq",)
        assert conn.execute("SELECT pg_get_serial_sequence('trace_risk_records', 'seq')").fetchone() == (
            "public.trace_risk_seq",)


@pytest.mark.parametrize(("branch", "revision"), [
    ("main", "0022_content_kind_on_legs"),
    ("main", "0026_causal_leg_order"),
    ("risk", "0024_label_lineage"),
])
def test_upgrade_from_each_shipped_branch_preserves_evidence(
    pg_dsn: str, tmp_path: Path, monkeypatch, branch: str, revision: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    command.upgrade(historical_config(tmp_path, branch), revision)
    seed_evidence(pg_dsn, risk=branch == "risk")
    before = evidence_snapshot(pg_dsn, risk=branch == "risk")
    if branch == "main":
        with psycopg.connect(pg_dsn) as conn:
            assert conn.execute("SELECT to_regclass('interaction_risk_records')").fetchone() == (None,)

    command.upgrade(_alembic_config(), "head")
    assert_combined_schema(pg_dsn)
    assert evidence_snapshot(pg_dsn, risk=branch == "risk") == before
    command.upgrade(_alembic_config(), "head")
    assert evidence_snapshot(pg_dsn, risk=branch == "risk") == before


def test_fresh_install_has_both_branches_and_can_be_repeated(pg_dsn: str, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    command.upgrade(_alembic_config(), "head")
    assert_combined_schema(pg_dsn)
    command.upgrade(_alembic_config(), "head")
    assert_combined_schema(pg_dsn)


@pytest.mark.parametrize("revision", ["0020_entity_namespace", "0022_content_kind_on_legs"])
def test_historical_risk_adoption_preserves_evidence_then_upgrades(
    pg_dsn: str, tmp_path: Path, monkeypatch, revision: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    command.upgrade(historical_config(tmp_path, "risk"), revision)
    seed_evidence(pg_dsn, risk=True, payload_kind=revision == "0020_entity_namespace")
    before = evidence_snapshot(pg_dsn, risk=True)
    adoption = Path(__file__).resolve().parents[2] / "tools" / "adopt_historical_risk_history.sql"
    for _ in range(2):
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute(adoption.read_text())
    assert evidence_snapshot(pg_dsn, risk=True) == before
    command.upgrade(_alembic_config(), "head")
    assert_combined_schema(pg_dsn)
    assert evidence_snapshot(pg_dsn, risk=True) == before
    command.upgrade(_alembic_config(), "head")
    assert_combined_schema(pg_dsn)


@pytest.mark.parametrize(("branch", "revision", "damage"), [
    ("main", "0022_content_kind_on_legs", None),
    ("risk", "0022_content_kind_on_legs", "ALTER TABLE interaction_policy_decisions DROP COLUMN evidence_fingerprint"),
    ("risk", "0022_content_kind_on_legs", "DROP TRIGGER dg_interaction_risk_notify ON interaction_risk_records"),
    ("risk", "0022_content_kind_on_legs", "ALTER TABLE interaction_policy_decisions ALTER COLUMN confidence TYPE numeric"),
    ("risk", "0024_label_lineage", None),
])
def test_adoption_refuses_other_or_incomplete_schemas_without_changing_the_stamp(
    pg_dsn: str, tmp_path: Path, monkeypatch, branch: str, revision: str, damage: str | None,
) -> None:
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    command.upgrade(historical_config(tmp_path, branch), revision)
    if damage:
        with psycopg.connect(pg_dsn) as conn:
            conn.execute(damage)
    adoption = Path(__file__).resolve().parents[2] / "tools" / "adopt_historical_risk_history.sql"
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(adoption.read_text())
    with psycopg.connect(pg_dsn) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(revision,)]
