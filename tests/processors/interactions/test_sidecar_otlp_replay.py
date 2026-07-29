"""Acceptance: the golden two-span trace replayed over the real OTLP wire.

Where ``test_sidecar_write_integration.py`` inserts the golden spans as
``spans`` rows directly, this test drives the *full production ingest path*:
the committed OTLP fixture (``tests/fixtures/golden_two_span.json``) is
replayed by the real ``tools/load_trace.py`` tool → OTLP gRPC → the receiver's
``translate.request_to_span_rows`` → ``write_span`` → the ``spans`` table →
the sidecar interactions algorithm (``drain`` == one pass) → the derived
tables. It then asserts the exact golden tables via the shared
``_assert_golden`` helper. This is the regression guard that the whole
pipeline — not just the derivation — reproduces the 4 / 4 / 8-legs / 10 / 7
golden shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import psycopg

from data_governance.processors.interactions.sidecar_driver import drain

from tools import load_trace

from . import sidecar_golden as golden
# Reuse the exact golden assertions (and the snapshot reader) from the
# DB-insert integration test — same expected tables, one source of truth.
from .test_sidecar_write_integration import _assert_golden, _snapshot

_FIXTURE = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "golden_two_span.json"
)

# Derived tables the reconcile owns. The harness truncates ``spans`` per test but
# not these (they are empty for receiver-only harness tests); truncate them here
# so the exact-count golden asserts hold regardless of harness DB reuse/order.
_DERIVED = (
    "interaction_payloads",
    "interaction_legs",
    "interaction_spans",
    "entity_spans",
    "interactions",
    "entities",
)


def _reset_derived(dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            f"TRUNCATE TABLE {', '.join(_DERIVED)} RESTART IDENTITY CASCADE"
        )
        conn.execute(
            "DELETE FROM processor_state WHERE processor_name = 'interactions'"
        )
        conn.commit()


def test_committed_fixture_matches_golden_source():
    """Drift guard: the committed OTLP fixture is exactly ``golden.to_span_rows()``.

    The fixture is a build artefact of ``sidecar_golden.py`` — if the golden
    trace changes, regenerate the JSON. Keeps the replayable file from silently
    diverging from the fixture every other surface is derived from.
    """
    committed = json.loads(_FIXTURE.read_text())
    assert committed == golden.to_span_rows()


def test_golden_otlp_replay_via_load_trace(otlp_harness):
    """Replay the committed fixture through ``tools/load_trace.py`` into the real
    receiver, derive, and assert the exact golden tables."""
    _reset_derived(otlp_harness.dsn)

    # Drive the actual ops tool end to end (builds the OTLP request, sends gRPC
    # to the harness).
    rc = load_trace.main(
        [str(_FIXTURE), "--transport", "grpc", "--endpoint", otlp_harness.grpc_endpoint]
    )
    assert rc == 0

    # All 10 spans landed via the wire path.
    assert otlp_harness.rows.row_count() == 10

    # Processor one pass (drain from seq 0) → derived tables.
    drain(0)

    _assert_golden(_snapshot(otlp_harness.dsn))
