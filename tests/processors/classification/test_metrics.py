"""Prometheus metrics for the P-classification processor (issue #77).

The stub's minimal observability surface is a single counter,
``payloads_classified_total``, incremented once per **Payload** the drain loop
writes a **Classification** for. This mirrors the interactions metrics test's
registry-isolation pattern (``make_registry`` per test).
"""

from __future__ import annotations

import psycopg

from data_governance.processors.classification import driver, metrics


def _insert_payload(dsn: str, *, content_hash: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO interaction_payloads "
            "(content_hash, content_kind, content, byte_size) "
            "VALUES (%s, 'unknown', '{}'::jsonb, 2)",
            (content_hash,),
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
