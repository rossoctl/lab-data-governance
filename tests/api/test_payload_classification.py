"""Contract tests for the nullable ``classification`` field on the payload read
surface (issue #77).

``GET /api/payloads/{hash}`` gains a ``classification`` field that surfaces the
P-classification **Classification** verdict inline (CONTEXT.md, ADR-0024):

- ``null`` while the **Payload** exists but P-classification has not yet written
  its verdict (the eventual-consistency window) — null means *exactly* "not yet
  processed", never "processed but skipped";
- the verdict object (``sensitivity_level``, ``regulatory_tags``,
  ``contains_identity_bundle``, ``is_personalized``, ``primary_domain``,
  ``findings``, ``model_version``) once the ``payload_classifications`` row
  exists.

These exercise the public HTTP surface end to end (a real server + httpx),
mirroring ``tests/api/test_flow_endpoints.py``.
"""

from __future__ import annotations

import psycopg
import pytest

from data_governance.api import SpansApiServer

pytest.importorskip("httpx")
import httpx  # noqa: E402


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


def _insert_payload(conn: psycopg.Connection, *, content_hash: str) -> None:
    conn.execute(
        "INSERT INTO interaction_payloads "
        "(content_hash, content_kind, content, byte_size) "
        "VALUES (%s, 'unknown', '{}'::jsonb, 2)",
        (content_hash,),
    )


def _insert_classification(conn: psycopg.Connection, *, content_hash: str) -> None:
    """Write a stub-shaped Classification row directly (the shape
    P-classification writes)."""
    conn.execute(
        "INSERT INTO payload_classifications "
        "(content_hash, sensitivity_level, regulatory_tags, "
        " contains_identity_bundle, is_personalized, primary_domain, "
        " findings, model_version) "
        "VALUES (%s, 'PUBLIC', '{}', FALSE, FALSE, NULL, '[]'::jsonb, 1)",
        (content_hash,),
    )


def test_payload_classification_is_null_before_processing(api_server, configured_db):
    """A payload with no ``payload_classifications`` row yet reports
    ``classification: null`` — the eventual-consistency window (ADR-0024)."""
    with psycopg.connect(configured_db) as conn:
        _insert_payload(conn, content_hash="unclassified")
        conn.commit()

    resp = httpx.get(f"{_base_url(api_server)}/api/payloads/unclassified")
    assert resp.status_code == 200
    body = resp.json()
    assert "classification" in body, "payload read surface must carry a classification field"
    assert body["classification"] is None


def test_payload_classification_populates_once_the_row_exists(api_server, configured_db):
    """Once P-classification has written the verdict, the field carries the
    Classification object with the document-level verdict + Findings +
    model_version (CONTEXT.md)."""
    with psycopg.connect(configured_db) as conn:
        _insert_payload(conn, content_hash="classified")
        _insert_classification(conn, content_hash="classified")
        conn.commit()

    resp = httpx.get(f"{_base_url(api_server)}/api/payloads/classified")
    assert resp.status_code == 200
    verdict = resp.json()["classification"]
    assert verdict is not None
    assert verdict["sensitivity_level"] == "PUBLIC"
    assert verdict["regulatory_tags"] == []
    assert verdict["contains_identity_bundle"] is False
    assert verdict["is_personalized"] is False
    assert verdict["primary_domain"] is None
    assert verdict["findings"] == []
    assert verdict["model_version"] == 1


def test_served_finding_keys_the_detected_type_under_entity_type(
    api_server, configured_db
):
    """A populated **Finding** is served with its detected type under the
    ``entity_type`` key — the one spelling the classification logic writes, the
    ``findings`` JSONB stores, the API serves verbatim, and the UI's ``Finding``
    wire type reads (CONTEXT.md **Finding**). Guards the cross-stack contract so
    the type key cannot silently drift back to ``tag`` (which would blank the
    UI's Findings "Type" column once a real detector lands in #79)."""
    with psycopg.connect(configured_db) as conn:
        _insert_payload(conn, content_hash="withfinding")
        # A finding exactly as data_governance...classification.logic.build_finding
        # emits it: the detected NER-tag type is keyed ``entity_type``.
        conn.execute(
            "INSERT INTO payload_classifications "
            "(content_hash, sensitivity_level, regulatory_tags, "
            " contains_identity_bundle, is_personalized, primary_domain, "
            " findings, model_version) "
            "VALUES (%s, 'CONFIDENTIAL', '{PII}', FALSE, TRUE, 'person', "
            "'[{\"entity_type\": \"EMAIL\", \"start\": 6, \"end\": 18, "
            "\"text\": \"jane@doe.org\"}]'::jsonb, 1)",
            ("withfinding",),
        )
        conn.commit()

    verdict = httpx.get(f"{_base_url(api_server)}/api/payloads/withfinding").json()[
        "classification"
    ]
    assert verdict is not None
    assert len(verdict["findings"]) == 1
    finding = verdict["findings"][0]
    # The served finding keys the detected type under entity_type (NOT tag).
    assert finding["entity_type"] == "EMAIL"
    assert "tag" not in finding
    assert finding["text"] == "jane@doe.org"
    assert (finding["start"], finding["end"]) == (6, 18)


def test_payload_still_carries_its_own_fields_alongside_classification(
    api_server, configured_db
):
    """The classification field is additive — the payload's own fields
    (content_hash, content_kind, content, byte_size) are unchanged."""
    with psycopg.connect(configured_db) as conn:
        _insert_payload(conn, content_hash="both")
        conn.commit()

    body = httpx.get(f"{_base_url(api_server)}/api/payloads/both").json()
    assert body["content_hash"] == "both"
    assert body["content_kind"] == "unknown"
    assert body["byte_size"] == 2
    assert body["classification"] is None
