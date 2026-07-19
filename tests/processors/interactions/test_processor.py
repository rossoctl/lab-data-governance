"""The P-interactions processor against the captured 281-span trace.

Loads trace ``2b7b88e0`` into ``spans`` in seq order, drains it through the
production driver, and asserts the derived graph reproduces the prototype's
verified output. The seq-horizon carries the ``--scramble`` order-independence;
the cursor-reset re-run is the production analogue of the recovery path.
"""

from __future__ import annotations

import psycopg

from .conftest import (
    EXPECTED_ENTITIES,
    EXPECTED_INTERACTIONS,
    EXPECTED_PAYLOADS,
    TRACE_2B7B88E0,
    drain_all as _drain_all,
    snapshot as _snapshot,
)


def _counts(dsn: str) -> dict[str, int]:
    with psycopg.connect(dsn) as conn:
        def n(table: str) -> int:
            return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

        return {
            "entities": n("entities"),
            "interactions": n("interactions"),
            "payloads": n("interaction_payloads"),
            "entity_spans": n("entity_spans"),
            "interaction_spans": n("interaction_spans"),
        }


def test_reproduces_verified_counts(loaded_trace: str) -> None:
    """31 interactions, 15 entities, 50 payloads — the verified gate output."""
    _drain_all(loaded_trace)
    counts = _counts(loaded_trace)
    assert counts["interactions"] == EXPECTED_INTERACTIONS, counts
    assert counts["entities"] == EXPECTED_ENTITIES, counts
    assert counts["payloads"] == EXPECTED_PAYLOADS, counts


def test_psp_mock_is_the_only_service_entity(loaded_trace: str) -> None:
    """The one true external-http target is psp-mock; nothing else is a
    `service:` entity (the create_booking→payment-agent A2A call must be
    cross-service, not external-http)."""
    _drain_all(loaded_trace)
    with psycopg.connect(loaded_trace) as conn:
        rows = conn.execute(
            "SELECT natural_key FROM entities WHERE kind = 'service' ORDER BY natural_key"
        ).fetchall()
    service_keys = [r[0] for r in rows]
    assert service_keys == ["service:psp-mock"], service_keys


def test_cursor_advances_to_max_seq(loaded_trace: str) -> None:
    _drain_all(loaded_trace)
    with psycopg.connect(loaded_trace) as conn:
        cursor = conn.execute(
            "SELECT last_processed_seq FROM processor_state WHERE processor_name = 'interactions'"
        ).fetchone()[0]
        max_seq = conn.execute(
            "SELECT max(seq) FROM spans WHERE trace_id = %s", (TRACE_2B7B88E0,)
        ).fetchone()[0]
    assert cursor == max_seq


def test_cursor_reset_rerun_is_byte_identical(loaded_trace: str) -> None:
    """The production analogue of the --scramble gate: after a full drain,
    reset the cursor and re-run — deterministic ids make the derived tables
    byte-identical, proving idempotent re-derive (ADR-0007 recovery)."""
    _drain_all(loaded_trace)
    first = _snapshot(loaded_trace)

    # Reset the cursor and re-drain — re-processes every span from scratch.
    with psycopg.connect(loaded_trace) as conn:
        conn.execute(
            "UPDATE processor_state SET last_processed_seq = 0 "
            "WHERE processor_name = 'interactions'"
        )
        conn.commit()
    _drain_all(loaded_trace)
    second = _snapshot(loaded_trace)

    assert first == second
