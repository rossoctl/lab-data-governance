"""Prometheus metrics for the P-leg-ready consumer (issue #123, ADR-0027).

The observability surface is one counter, registry-isolated per test
(``make_registry``, mirroring the sibling consumers' metrics tests):

- ``legs_observed_total`` — one increment per ready **Interaction leg** the drain
  loop delivers to the downstream governance consumer. A second, independent check
  on the exactly-once + readiness-gate acceptance criteria: after a full drain it
  equals the number of ready legs past the cursor, an unready leg is not counted,
  and a restart re-draining from the durable cursor does not advance it.
"""

from __future__ import annotations

import psycopg

from data_governance.processors.leg_ready import driver, metrics


def _mk_interaction(conn: psycopg.Connection, ix_id: str) -> None:
    for eid in (f"{ix_id}-caller", f"{ix_id}-callee"):
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, original_seq) VALUES (%s, 'agent', %s, 'n', 'd', 1) "
            "ON CONFLICT (natural_key) DO NOTHING",
            (eid, eid),
        )
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, "
        "callee_entity_id, summary) VALUES (%s, 't', %s, %s, 's') "
        "ON CONFLICT (id) DO NOTHING",
        (ix_id, f"{ix_id}-caller", f"{ix_id}-callee"),
    )


def _insert_leg(dsn: str, *, ix: str, leg_type: str, payload_hash: str | None) -> None:
    with psycopg.connect(dsn) as conn:
        _mk_interaction(conn, ix)
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error, original_seq) VALUES (%s, %s, now(), %s, false, 0)",
            (ix, leg_type, payload_hash),
        )
        conn.commit()


def test_counter_is_registered_at_zero() -> None:
    """The delivery counter exists in the registry from import (value 0), so it is
    scrapeable before any leg is delivered."""
    registry = metrics.make_registry()
    assert registry.get_sample_value("legs_observed_total") == 0.0


def test_counter_increments_once_per_delivered_ready_leg(configured_db: str) -> None:
    """Draining two ready (no-payload) legs increments ``legs_observed_total`` by
    two."""
    registry = metrics.make_registry()

    _insert_leg(configured_db, ix="m0", leg_type="request", payload_hash=None)
    _insert_leg(configured_db, ix="m1", leg_type="request", payload_hash=None)

    driver.drain(0)

    assert registry.get_sample_value("legs_observed_total") == 2.0


def test_counter_not_incremented_for_an_unready_leg(configured_db: str) -> None:
    """A payload-bearing leg whose payload is not classified is withheld, so the
    delivery counter does not advance — the readiness gate is observable."""
    registry = metrics.make_registry()

    _insert_leg(configured_db, ix="u0", leg_type="request", payload_hash="h0")
    driver.drain(0)

    assert registry.get_sample_value("legs_observed_total") == 0.0


def test_counter_does_not_advance_on_a_no_new_leg_restart(configured_db: str) -> None:
    """After a full drain, a restart that re-reads the durable cursor and finds no
    new ready legs does not increment the counter — exactly-once delivery, no
    re-delivery on replay (ADR-0007)."""
    registry = metrics.make_registry()

    _insert_leg(configured_db, ix="only", leg_type="request", payload_hash=None)
    cursor = driver.drain(0)
    assert registry.get_sample_value("legs_observed_total") == 1.0

    driver.drain(cursor)
    assert registry.get_sample_value("legs_observed_total") == 1.0
