"""Prometheus metrics for the P-entity-ready consumer (issue #121).

The observability surface is one counter, registry-isolated per test
(``make_registry``, mirroring the classification/interactions metrics tests):

- ``entities_observed_total`` — one increment per **Entity** the drain loop
  delivers to the downstream governance consumer. This is a second, independent
  check on the exactly-once acceptance criterion: after a full drain it equals
  the number of distinct entities created, and a restart re-draining from the
  durable cursor does not advance it.
"""

from __future__ import annotations

import psycopg

from data_governance.processors.entity_ready import driver, metrics


def _insert_entity(dsn: str, *, natural_key: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO entities "
            "(id, kind, natural_key, display_name, detected_from, original_seq) "
            "VALUES (%s, 'agent', %s, 'n', 'd', 1)",
            (natural_key, natural_key),
        )
        conn.commit()


def test_counter_is_registered_at_zero() -> None:
    """The delivery counter exists in the registry from import (value 0), so it is
    scrapeable before any entity is delivered."""
    registry = metrics.make_registry()
    assert registry.get_sample_value("entities_observed_total") == 0.0


def test_counter_increments_once_per_delivered_entity(
    configured_db: str,
) -> None:
    """Draining two entities increments ``entities_observed_total`` by two."""
    registry = metrics.make_registry()

    _insert_entity(configured_db, natural_key="m0")
    _insert_entity(configured_db, natural_key="m1")

    driver.drain(0)

    assert registry.get_sample_value("entities_observed_total") == 2.0


def test_counter_does_not_advance_on_a_no_new_entity_restart(
    configured_db: str,
) -> None:
    """After a full drain, a restart that re-reads the durable cursor and finds no
    new entities does not increment the counter — exactly-once delivery, no
    re-delivery on replay (ADR-0007)."""
    registry = metrics.make_registry()

    _insert_entity(configured_db, natural_key="only")
    cursor = driver.drain(0)
    assert registry.get_sample_value("entities_observed_total") == 1.0

    # Restart: re-read the durable cursor, drain again — nothing new.
    driver.drain(cursor)
    assert registry.get_sample_value("entities_observed_total") == 1.0
