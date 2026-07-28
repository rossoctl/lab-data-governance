"""In-process tests for the ``payloads`` retrieval seam.

Drives ``retrieval.get_payload`` directly against a migrated DB. This module
owns the content-addressed **Payload** read and the inline **Classification**
verdict (ADR-0024): ``None`` classification while the payload exists but
P-classification has not run, the verdict object once its row lands, and
``None`` for the whole read when the payload is absent.
"""

from __future__ import annotations

import psycopg
import pytest

from data_governance import retrieval


def _insert_payload(conn: psycopg.Connection, *, content_hash: str) -> None:
    conn.execute(
        "INSERT INTO interaction_payloads "
        "(content_hash, content_kind, content, byte_size) "
        "VALUES (%s, 'unknown', '{}'::jsonb, 2)",
        (content_hash,),
    )


def _insert_classification(conn: psycopg.Connection, *, content_hash: str) -> None:
    conn.execute(
        "INSERT INTO payload_classifications "
        "(content_hash, sensitivity_level, regulatory_tags, "
        " contains_identity_bundle, is_personalized, primary_domain, "
        " findings, model_version) "
        "VALUES (%s, 'RESTRICTED', '{PII,GDPR}', TRUE, TRUE, 'person', "
        " '[{\"entity_type\": \"SSN\", \"start\": 0, \"end\": 11}]'::jsonb, 2)",
        (content_hash,),
    )


def test_absent_payload_returns_none(configured_db: str) -> None:
    assert retrieval.get_payload("nope") is None


def test_payload_without_classification_reports_null(configured_db: str) -> None:
    """The eventual-consistency window: payload present, verdict not yet written
    → classification is None (ADR-0024)."""
    with psycopg.connect(configured_db) as conn:
        _insert_payload(conn, content_hash="unclassified")
        conn.commit()
    view = retrieval.get_payload("unclassified")
    assert view is not None
    assert view.content_hash == "unclassified"
    assert view.content_kind == "unknown"
    assert view.byte_size == 2
    assert view.classification is None


def test_payload_carries_verdict_once_written(configured_db: str) -> None:
    """Once the payload_classifications row exists, the verdict is returned
    verbatim (document-level fields + Findings + model_version)."""
    with psycopg.connect(configured_db) as conn:
        _insert_payload(conn, content_hash="classified")
        _insert_classification(conn, content_hash="classified")
        conn.commit()
    view = retrieval.get_payload("classified")
    assert view is not None
    c = view.classification
    assert c is not None
    assert c.sensitivity_level == "RESTRICTED"
    assert c.regulatory_tags == ["PII", "GDPR"]
    assert c.contains_identity_bundle is True
    assert c.is_personalized is True
    assert c.primary_domain == "person"
    assert c.model_version == 2
    assert c.findings == [{"entity_type": "SSN", "start": 0, "end": 11}]
