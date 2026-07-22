"""Health/readiness probe egresses are not external-http interactions.

An OI TOOL that makes a real business HTTP call to an uninstrumented host
(e.g. ``POST /charge`` to psp-mock) also often fires a liveness probe first
(``GET /healthz`` to the same host). The probe is not a business interaction —
it is infrastructure noise — but the ``external-http`` anchor rule fires once
per qualifying CLIENT egress, so without a filter the probe surfaces as a
SECOND ``tool → service`` interaction with the same caller/callee as the real
call.

Gate 1 of ``_external_http_host`` already excludes well-known non-business
egress paths (``/mcp`` transport, ``/.well-known/*`` discovery) by their OWN
URL — a positive, CLIENT-local, order-independent signal. Probe paths
(``/healthz``, ``/readyz``, ``/livez``, ``/healthcheck``, ``/health``,
``/ping``) join that set. Same shape, same guarantee: decided from the egress's
own path, never from the absence of a (late-arriving) sibling.

These are pure per-span unit tests over ``_external_http_host`` — no trace
fixture, no DB — so they pin the gate directly.
"""

from __future__ import annotations

import datetime as dt

from data_governance.retrieval import Span
from data_governance.processors.interactions.procedure import Processor

_T = "trace-probe"
_NOW = dt.datetime(2026, 7, 21, 12, 0, 0, tzinfo=dt.timezone.utc)


def _client(span_id: str, url: str, method: str, parent_id: str | None = None) -> Span:
    return Span(
        seq=1,
        trace_id=_T,
        span_id=span_id,
        parent_id=parent_id,
        name=method,
        started_at=_NOW,
        attributes={"http.url": url, "http.method": method},
        observed_at=_NOW,
        arrival_seq=1,
        service_name="charge_card",
        kind="CLIENT",
        ended_at=_NOW,
    )


def _host_for(url: str, method: str = "GET") -> str | None:
    """Run the gate for a lone CLIENT egress (no SERVER child, so gate 2 is a
    no-op) and return the host it resolves to, or None if excluded."""
    proc = Processor()
    client = _client("c1", url, method)
    proc.spans_by_id[(_T, "c1")] = client
    proc._span_by_id_index["c1"] = client
    proc.children.setdefault(None, []).append(client)
    return proc._external_http_host(client)


class TestProbePathsExcluded:
    def test_healthz_get_is_not_external_http(self) -> None:
        assert _host_for("http://psp-mock:9091/healthz", "GET") is None

    def test_readyz_excluded(self) -> None:
        assert _host_for("http://svc:8080/readyz", "GET") is None

    def test_livez_excluded(self) -> None:
        assert _host_for("http://svc:8080/livez", "GET") is None

    def test_healthcheck_excluded(self) -> None:
        assert _host_for("http://svc:8080/healthcheck", "GET") is None

    def test_health_excluded(self) -> None:
        assert _host_for("http://svc:8080/health", "GET") is None

    def test_ping_excluded(self) -> None:
        assert _host_for("http://svc:8080/ping", "GET") is None

    def test_trailing_slash_still_excluded(self) -> None:
        assert _host_for("http://svc:8080/healthz/", "GET") is None


class TestBusinessEgressStillEmits:
    def test_real_business_post_still_resolves_host(self) -> None:
        """The gate must NOT over-reach: a real business call to the same host
        (POST /charge) still resolves as external-http."""
        assert _host_for("http://psp-mock:9091/charge", "POST") == "psp-mock"

    def test_business_get_with_query_still_resolves(self) -> None:
        assert (
            _host_for("http://weather-service:9000/weather?location=Tokyo", "GET")
            == "weather-service"
        )

    def test_path_containing_health_substring_not_excluded(self) -> None:
        """Only the exact final path segment matches — a business path that
        merely contains 'health' (e.g. /healthcheckup-report) is NOT a probe."""
        assert (
            _host_for("http://svc:9000/healthcheckups", "GET") == "svc"
        )
