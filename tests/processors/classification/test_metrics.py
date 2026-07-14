"""Prometheus metrics for the P-classification processor (issues #77, #81).

The observability surface is two counters, both registry-isolated per test
(``make_registry``, mirroring the interactions metrics test):

- ``payloads_classified_total`` (#77) — one increment per **Payload** the drain
  loop writes a **Classification** for.
- ``projection_fallbacks_total`` (#81) — one increment per **Payload** whose
  **Text projection rule** fell back to the whole-JSONB serialization because
  its **Content kind** has no projection branch (``unknown`` and any unhandled
  kind). The projection-coverage signal: ``projection_fallbacks_total /
  payloads_classified_total`` is the fraction of payloads the classifier saw
  only as best-effort serialized JSONB rather than real **Classifiable text**.
"""

from __future__ import annotations

import psycopg

from data_governance.processors.classification import driver, metrics


def _insert_payload(
    dsn: str, *, content_hash: str, content_kind: str = "unknown"
) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO interaction_payloads "
            "(content_hash, content_kind, content, byte_size) "
            "VALUES (%s, %s, '{}'::jsonb, 2)",
            (content_hash, content_kind),
        )
        conn.commit()


def test_counter_increments_once_per_classified_payload(
    configured_db: str, monkeypatch
) -> None:
    """Draining two payloads increments ``payloads_classified_total`` by two."""
    registry = metrics.make_registry()

    _insert_payload(configured_db, content_hash="m0")
    _insert_payload(configured_db, content_hash="m1")

    driver.drain(0)

    value = registry.get_sample_value("payloads_classified_total")
    assert value == 2.0


def test_projection_fallback_counter_is_registered() -> None:
    """The projection-coverage counter exists in the registry from import (value
    0), so it is scrapeable before any payload is classified (#81)."""
    registry = metrics.make_registry()
    value = registry.get_sample_value("projection_fallbacks_total")
    assert value == 0.0


def test_unknown_kind_payload_increments_projection_fallback(
    configured_db: str,
) -> None:
    """A payload whose **Content kind** is ``unknown`` has no **Text projection
    rule** branch, so projection falls back to whole-JSONB serialization and the
    projection-coverage counter increments (#81, CONTEXT.md Text projection
    rule)."""
    registry = metrics.make_registry()

    _insert_payload(configured_db, content_hash="u0", content_kind="unknown")

    driver.drain(0)

    assert registry.get_sample_value("projection_fallbacks_total") == 1.0
    # Every payload is still classified — the fallback is a coverage gap, not a
    # skip (ADR-0024: every payload gets a real verdict).
    assert registry.get_sample_value("payloads_classified_total") == 1.0


def test_projected_kind_payload_does_not_increment_fallback(
    configured_db: str,
) -> None:
    """A payload whose **Content kind** has a projection branch is projected
    directly, so the projection-coverage counter stays at 0 while the payload is
    still classified (#81)."""
    registry = metrics.make_registry()

    _insert_payload(
        configured_db, content_hash="p0", content_kind="llm_chat_prompt"
    )

    driver.drain(0)

    assert registry.get_sample_value("projection_fallbacks_total") == 0.0
    assert registry.get_sample_value("payloads_classified_total") == 1.0


def test_unbranched_kind_payload_increments_projection_fallback(
    configured_db: str,
) -> None:
    """A payload whose **Content kind** exists but has no **Text projection rule**
    branch (e.g. ``http_request_body`` — recognised by P-interactions but not yet
    projected by P-classification) takes the whole-JSONB fallback and so DOES
    increment the projection-coverage counter (#81, #78). This pins the counter to
    the rule's *actual* fallback set (``projection.is_projectable``) rather than a
    hand-maintained kind list — the earlier hardcoded set treated ``http_*`` /
    ``agent_message`` as projectable and would have undercounted them."""
    registry = metrics.make_registry()

    _insert_payload(
        configured_db, content_hash="h0", content_kind="http_request_body"
    )

    driver.drain(0)

    assert registry.get_sample_value("projection_fallbacks_total") == 1.0
    assert registry.get_sample_value("payloads_classified_total") == 1.0
