"""Hash cache keys without retaining matched text or changing decision history."""

from __future__ import annotations

import hashlib
import json

import psycopg
from alembic import command

from data_governance.db.migrate import _alembic_config


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _insert(conn, interaction_id, version, fingerprint):
    conn.execute(
        "INSERT INTO interaction_policy_decisions (interaction_id, version, evaluated_at, "
        "risk_level, enforcement_type, allowed_actions, explanation, triggered_rules, "
        "confidence, policy_version, evidence_fingerprint) "
        "VALUES (%s, %s, '2026-01-01T00:00:00Z', 'high', 'block', '{redact}', "
        "'retained decision', '{DG-001}', 0.900, 'v1', %s)",
        (interaction_id, version, fingerprint),
    )


def _rows(dsn):
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT decision_id, interaction_id, version, evaluated_at, risk_level, "
            "enforcement_type, allowed_actions, explanation, triggered_rules, confidence, "
            "policy_version, evidence_fingerprint FROM interaction_policy_decisions "
            "ORDER BY interaction_id, version"
        ).fetchall()


def test_existing_versions_are_hashed_once_without_changing_decision_metadata(pg_dsn, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "0028_correction_replay")
    legacy = [
        json.dumps({"opa_input": {"text": "REVIEW_SYNTHETIC_DETECTED_VALUE"}}, sort_keys=True),
        json.dumps({"opa_input": {"matched_text": "REVIEW_SYNTHETIC_INHERITED_VALUE_✓"}},
                   ensure_ascii=False, sort_keys=True),
        '{"classification_summary": {}}',
    ]
    already_hashed = _digest("already-hashed")
    with psycopg.connect(pg_dsn) as conn:
        for version, value in enumerate(legacy, 1):
            _insert(conn, "historical", version, value)
        _insert(conn, "current", 1, already_hashed)
    before = _rows(pg_dsn)
    command.upgrade(cfg, "head")
    after = _rows(pg_dsn)
    assert [row[:-1] for row in after] == [row[:-1] for row in before]
    assert [row[-1] for row in after] == [already_hashed, *map(_digest, legacy)]
    # Downgrading cannot recover the plaintext. Upgrading again must not
    # rehash keys or allocate replacement decision versions.
    command.downgrade(cfg, "0028_correction_replay")
    command.upgrade(cfg, "head")
    assert _rows(pg_dsn) == after


def test_legacy_writes_are_hashed_and_existing_digests_remain_unchanged(migrated_dsn):
    legacy = '{"text": "REVIEW_SYNTHETIC_LEGACY_WRITER"}'
    changed = '{"text": "REVIEW_SYNTHETIC_UPDATED_WRITER"}'
    already_hashed = _digest("new-writer")
    with psycopg.connect(migrated_dsn) as conn:
        _insert(conn, "legacy", 1, legacy)
        _insert(conn, "new", 1, already_hashed)
    assert [row[-1] for row in _rows(migrated_dsn)] == [_digest(legacy), already_hashed]
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute("UPDATE interaction_policy_decisions SET evidence_fingerprint = %s "
                     "WHERE interaction_id = 'legacy'", (changed,))
    assert [row[-1] for row in _rows(migrated_dsn)] == [_digest(changed), already_hashed]
