"""Smoke tests for the OTLP test harness (issue #5).

These tests are the harness's own minimal acceptance suite — they assert
that the bundled fixtures bring up a real Postgres + receiver, accept OTLP
requests via gRPC and HTTP/protobuf, and surface the resulting rows back
through the row-inspection helpers.

The acceptance-criterion smoke test for issue #5 ("an OTLP-sent span lands
in `spans`") is the migrated tracer-bullet test from issue #3, which lives
in ``tests/processors/otlp_receiver/test_end_to_end.py`` after the harness
ships. The tests here exercise the *fixture surface* — the harness
contract — which is what subsequent slices will rely on.
"""

from __future__ import annotations

import psycopg

from tests.harness.fixtures import OtlpHarness


def test_grpc_span_lands_in_spans_table(otlp_harness: OtlpHarness) -> None:
    trace_id, span_id = otlp_harness.client.send_grpc_span(name="via-grpc")

    row = otlp_harness.rows.get(trace_id, span_id)

    assert row is not None
    assert row["name"] == "via-grpc"


def test_http_span_lands_in_spans_table(otlp_harness: OtlpHarness) -> None:
    trace_id, span_id = otlp_harness.client.send_http_span(name="via-http")

    row = otlp_harness.rows.get(trace_id, span_id)

    assert row is not None
    assert row["name"] == "via-http"


def test_rows_by_trace_id_returns_all_spans_in_trace(
    otlp_harness: OtlpHarness,
) -> None:
    trace_id, span_id_a = otlp_harness.client.send_grpc_span(name="span-a")
    _, span_id_b = otlp_harness.client.send_grpc_span(
        name="span-b", trace_id=trace_id
    )

    rows = otlp_harness.rows.by_trace_id(trace_id)

    span_ids = {r["span_id"] for r in rows}
    assert span_ids == {span_id_a, span_id_b}


def test_per_test_reset_truncates_with_restart_identity(
    otlp_harness: OtlpHarness,
) -> None:
    """Truncate is per-test and resets ``spans_seq`` (RESTART IDENTITY).

    Self-contained: doesn't depend on which sibling test ran first.
    Asserts seq starts at 1 on entry (proves the fixture's pre-test
    truncate reset identity), advances on subsequent inserts, and resets
    back to 1 after an in-test ``TRUNCATE ... RESTART IDENTITY``.
    """
    trace_id_a, span_id_a = otlp_harness.client.send_grpc_span(name="first")
    row_a = otlp_harness.rows.get(trace_id_a, span_id_a)
    assert row_a is not None
    # RESTART IDENTITY at fixture entry must have reset spans_seq, so the
    # first insert in this test sees seq == 1 regardless of test order.
    assert row_a["seq"] == 1

    trace_id_b, span_id_b = otlp_harness.client.send_grpc_span(name="second")
    row_b = otlp_harness.rows.get(trace_id_b, span_id_b)
    assert row_b is not None
    assert row_b["seq"] == 2

    # In-test truncate: must also reset identity, so the next insert is
    # back to seq == 1. This is the actual truncate-resets-identity assertion.
    with psycopg.connect(otlp_harness.dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE TABLE spans, blocked_span_counts "
                "RESTART IDENTITY CASCADE"
            )
        conn.commit()

    trace_id_c, span_id_c = otlp_harness.client.send_grpc_span(name="post-truncate")
    row_c = otlp_harness.rows.get(trace_id_c, span_id_c)
    assert row_c is not None
    assert row_c["seq"] == 1
