"""End-to-end blocklist tests (issue #9 acceptance criteria).

Covers regex patterns (literal + wildcard), the blocked_span_counts upsert,
the spans_blocked_total Prometheus counter, silent-success OTLP responses, and
downstream invisibility via get_spans.
"""

from __future__ import annotations

import psycopg
import pytest

from data_governance.processors.otlp_receiver import metrics as _metrics
from data_governance.processors.otlp_receiver.blocklist import match as blocklist_match
from tests.harness.fixtures import OtlpHarness


# ---------------------------------------------------------------------------
# Unit tests: blocklist.match()
# ---------------------------------------------------------------------------


class TestBlocklistMatch:
    def test_literal_pattern_matches(self) -> None:
        assert blocklist_match("/metrics") == "/metrics"

    def test_pattern_case_sensitive(self) -> None:
        assert blocklist_match("/Metrics") is None

    def test_regex_wildcard_matches(self) -> None:
        assert blocklist_match("otlp_receiver/export") == r"otlp_receiver/.*"

    def test_regex_wildcard_bare_prefix_matches(self) -> None:
        assert blocklist_match("otlp_receiver/") == r"otlp_receiver/.*"

    def test_regex_is_fully_anchored(self) -> None:
        # A literal pattern must not match a name that merely contains it;
        # match() uses re.fullmatch, so partial matches are rejected.
        assert blocklist_match("x/metrics") is None
        assert blocklist_match("/metrics/extra") is None

    def test_regex_no_match_for_unrelated_name(self) -> None:
        assert blocklist_match("my-service/handler") is None

    def test_non_blocked_span_returns_none(self) -> None:
        assert blocklist_match("user.checkout") is None

    def test_kubernetes_liveness_probe_blocked(self) -> None:
        assert blocklist_match("/healthz") == "/healthz"

    def test_kubernetes_readiness_probe_blocked(self) -> None:
        assert blocklist_match("/readyz") == "/readyz"

    def test_metrics_get_blocked(self) -> None:
        assert blocklist_match("GET /metrics") == "GET /metrics"

    def test_healthz_get_blocked(self) -> None:
        assert blocklist_match("GET /healthz") == r"(GET|POST) /healthz.*"

    def test_healthz_get_with_asgi_suffix_blocked(self) -> None:
        # The real-world leak: Starlette OTel emits ' http send'/' http receive'
        # child spans, which the old exact entry missed. (issue: healthz suffix)
        assert blocklist_match("GET /healthz http send") == r"(GET|POST) /healthz.*"
        assert (
            blocklist_match("GET /healthz http receive") == r"(GET|POST) /healthz.*"
        )

    def test_healthz_post_blocked(self) -> None:
        assert blocklist_match("POST /healthz") == r"(GET|POST) /healthz.*"

    def test_healthz_subpath_blocked(self) -> None:
        assert blocklist_match("GET /healthz/ready") == r"(GET|POST) /healthz.*"

    def test_agent_card_dot_is_literal(self) -> None:
        # The '.' in the agent-card path is escaped, so it matches literally and
        # not as the regex any-char wildcard.
        assert (
            blocklist_match("GET /.well-known/agent-card.json")
            == r"GET /\.well-known/agent-card\.json"
        )
        assert blocklist_match("GET /Xwell-knownXagent-cardXjson") is None


# ---------------------------------------------------------------------------
# Unit tests: pattern validation
# ---------------------------------------------------------------------------


def test_invalid_regex_rejected() -> None:
    from data_governance.processors.otlp_receiver.blocklist import (
        _validate,
    )

    # Unbalanced group / bracket — does not compile as a regex.
    with pytest.raises(ValueError, match="invalid"):
        _validate(("(GET",))


def test_valid_patterns_accepted() -> None:
    from data_governance.processors.otlp_receiver.blocklist import _validate

    _validate(("/metrics", r"prefix.*", "exact-name", r"(GET|POST) /healthz.*"))


def test_empty_pattern_rejected() -> None:
    from data_governance.processors.otlp_receiver.blocklist import _validate

    with pytest.raises(ValueError):
        _validate(("",))


# ---------------------------------------------------------------------------
# End-to-end harness tests
# ---------------------------------------------------------------------------


def _blocked_count(dsn: str, pattern: str) -> tuple[int, object]:
    """Return (count, last_seen_at) from blocked_span_counts for *pattern*."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT count, last_seen_at FROM blocked_span_counts WHERE pattern = %s",
            (pattern,),
        ).fetchone()
    if row is None:
        return 0, None
    return int(row[0]), row[1]


def _prometheus_blocked_count(pattern: str) -> float:
    """Return the current spans_blocked_total counter value for *pattern*."""
    try:
        return _metrics.spans_blocked_total.labels(pattern=pattern)._value.get()
    except Exception:
        return 0.0


class TestBlocklistEndToEnd:
    def test_exact_blocked_span_not_in_spans(self, otlp_harness: OtlpHarness) -> None:
        _metrics.make_registry()
        trace_id, span_id = otlp_harness.client.send_grpc_span(name="/metrics")
        row = otlp_harness.rows.get(trace_id, span_id)
        assert row is None, "blocked span must not appear in spans"
        assert _prometheus_blocked_count("/metrics") == 1.0

    def test_prefix_blocked_span_not_in_spans(self, otlp_harness: OtlpHarness) -> None:
        _metrics.make_registry()
        trace_id, span_id = otlp_harness.client.send_grpc_span(
            name="otlp_receiver/export"
        )
        row = otlp_harness.rows.get(trace_id, span_id)
        assert row is None, "prefix-blocked span must not appear in spans"
        assert _prometheus_blocked_count(r"otlp_receiver/.*") == 1.0

    def test_healthz_asgi_suffix_span_not_in_spans(
        self, otlp_harness: OtlpHarness
    ) -> None:
        """The real leak: ' http send' child spans of a /healthz probe."""
        _metrics.make_registry()
        trace_id, span_id = otlp_harness.client.send_http_span(
            name="GET /healthz http send"
        )
        row = otlp_harness.rows.get(trace_id, span_id)
        assert row is None, "healthz ASGI-suffix span must not appear in spans"
        assert _prometheus_blocked_count(r"(GET|POST) /healthz.*") == 1.0

    def test_non_blocked_span_written_normally(self, otlp_harness: OtlpHarness) -> None:
        trace_id, span_id = otlp_harness.client.send_grpc_span(name="user.checkout")
        row = otlp_harness.rows.get(trace_id, span_id)
        assert row is not None, "non-blocked span must be written to spans"
        assert row["name"] == "user.checkout"

    def test_blocked_span_count_increments(self, otlp_harness: OtlpHarness) -> None:
        otlp_harness.client.send_grpc_span(name="/metrics")
        otlp_harness.client.send_grpc_span(name="/metrics")
        count, last_seen = _blocked_count(otlp_harness.dsn, "/metrics")
        assert count == 2
        assert last_seen is not None

    def test_blocked_span_upsert_accumulates_across_calls(
        self, otlp_harness: OtlpHarness
    ) -> None:
        for _ in range(3):
            otlp_harness.client.send_http_span(name="GET /metrics")
        count, _ = _blocked_count(otlp_harness.dsn, "GET /metrics")
        assert count == 3

    def test_otlp_response_success_for_fully_blocked_batch(
        self, otlp_harness: OtlpHarness
    ) -> None:
        """A batch where every span is blocked must return OTLP success.

        We verify this by confirming the client call does not raise (the SDK
        raises on non-2xx / gRPC error status) and no rejected_spans row is
        written.
        """
        otlp_harness.client.send_grpc_span(name="/healthz")
        with psycopg.connect(otlp_harness.dsn) as conn:
            row = conn.execute("SELECT COUNT(*) FROM rejected_spans").fetchone()
        assert row is not None
        assert int(row[0]) == 0, "blocked span must not appear in rejected_spans"

    def test_blocked_span_invisible_to_row_inspector(
        self, otlp_harness: OtlpHarness
    ) -> None:
        """Downstream invisibility: row_count() must not increase for blocked spans."""
        before = otlp_harness.rows.row_count()
        otlp_harness.client.send_grpc_span(name="/readyz")
        otlp_harness.client.send_grpc_span(name="GET /healthz")
        after = otlp_harness.rows.row_count()
        assert after == before, "blocked spans must not increase spans row count"

    def test_mixed_batch_blocks_some_writes_others(
        self, otlp_harness: OtlpHarness
    ) -> None:
        """A batch with blocked and non-blocked spans: only non-blocked rows land."""
        trace_id_ok, span_id_ok = otlp_harness.client.send_grpc_span(
            name="http.server.request"
        )
        _, _ = otlp_harness.client.send_grpc_span(name="/metrics")
        row = otlp_harness.rows.get(trace_id_ok, span_id_ok)
        assert row is not None, "non-blocked span in mixed batch must be written"
        assert otlp_harness.rows.row_count() == 1

    def test_http_transport_also_blocks(self, otlp_harness: OtlpHarness) -> None:
        trace_id, span_id = otlp_harness.client.send_http_span(name="/livez")
        row = otlp_harness.rows.get(trace_id, span_id)
        assert row is None, "HTTP-transport blocked span must not appear in spans"
        count, _ = _blocked_count(otlp_harness.dsn, "/livez")
        assert count >= 1
