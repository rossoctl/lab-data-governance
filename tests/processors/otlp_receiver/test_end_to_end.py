"""End-to-end test (issue #3 acceptance), now built on the issue-#5 harness.

Boots both OTLP transports (via the session-scoped harness servers), sends
one span via gRPC and another via HTTP, then asserts both rows landed in
``spans``. This test was originally hand-wired in issue #3; issue #5
migrates it to the reusable :mod:`tests.harness` fixtures so it now
exercises the same harness that subsequent slices (finalization, blocklist,
trace-tree UI) will assert "OTLP request in, correct rows out" against.
"""

from __future__ import annotations

from tests.harness.fixtures import OtlpHarness


def test_both_transports_persist_spans_to_one_database(
    otlp_harness: OtlpHarness,
) -> None:
    grpc_trace_id, grpc_span_id = otlp_harness.client.send_grpc_span(
        name="via-grpc",
        service_name="grpc-side",
    )
    http_trace_id, http_span_id = otlp_harness.client.send_http_span(
        name="via-http",
        service_name="http-side",
    )

    grpc_row = otlp_harness.rows.get(grpc_trace_id, grpc_span_id)
    http_row = otlp_harness.rows.get(http_trace_id, http_span_id)

    assert grpc_row is not None, "gRPC span did not land in spans"
    assert http_row is not None, "HTTP span did not land in spans"
    assert grpc_row["name"] == "via-grpc"
    assert http_row["name"] == "via-http"
