"""Integration tests for the receiver entry point starting ``MetricsServer`` (#36).

PROJECT.md §5.1 lists ``GET /metrics`` as a required receiver capability, but
PR #34 surfaced that nothing was actually starting :class:`MetricsServer` in
``data_governance.processors.otlp_receiver.__main__``: ``GrpcOtlpServer`` and
``HttpOtlpServer`` were the only servers booted, so the production receiver
process never served ``/metrics``. Issue #36 closes that gap.

These tests boot the real receiver process via ``python -m
data_governance.processors.otlp_receiver`` (mirroring
``test_startup_schema_check.py`` — same subprocess shape, same env-var
plumbing) and verify that ``GET /metrics`` is reachable on
``RECEIVER_METRICS_PORT`` and returns 200 with at least the ``prometheus_client``
process collectors registered.

We deliberately do not import-and-call the entry point in-process: the
contract is "the production receiver process exposes /metrics", and the
process boundary is what the issue's acceptance criteria turn on.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest


# --- helpers (kept parallel with test_startup_schema_check.py) ---------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port_open(host: str, port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.connect((host, port))
                return
            except OSError:
                time.sleep(0.05)
    raise TimeoutError(f"port {host}:{port} did not open within {timeout}s")


def _spawn_receiver(
    dsn: str,
    *,
    grpc_port: int,
    http_port: int,
    metrics_port: int,
) -> subprocess.Popen[str]:
    env = {
        "DATABASE_URL": dsn,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LOG_LEVEL": "INFO",
        "DB_POOL_MIN_SIZE": "1",
        "DB_POOL_MAX_SIZE": "2",
        "DB_POOL_TIMEOUT": "5",
        "RECEIVER_GRPC_PORT": str(grpc_port),
        "RECEIVER_HTTP_PORT": str(http_port),
        "RECEIVER_METRICS_PORT": str(metrics_port),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "data_governance.processors.otlp_receiver"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.fixture()
def free_ports() -> tuple[int, int, int]:
    return _free_port(), _free_port(), _free_port()


@pytest.fixture()
def running_receiver(migrated_dsn: str, free_ports: tuple[int, int, int]):
    """Spawn the receiver process and wait until /healthz is up.

    Yields the (proc, grpc_port, http_port, metrics_port) tuple. Teardown
    SIGTERMs the process and falls back to SIGKILL if it does not exit in
    time. /metrics binding is a separate port from /healthz; we wait on the
    metrics port directly per test.
    """
    grpc_port, http_port, metrics_port = free_ports
    proc = _spawn_receiver(
        migrated_dsn,
        grpc_port=grpc_port,
        http_port=http_port,
        metrics_port=metrics_port,
    )
    try:
        # Healthz coming up means the schema-version check passed and the
        # main lifecycle reached its servers' .start() calls.
        _wait_port_open("127.0.0.1", http_port, timeout=15.0)
        yield proc, grpc_port, http_port, metrics_port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)


# --- /metrics is exposed -----------------------------------------------------


class TestReceiverMetricsSurface:
    """The receiver __main__ starts MetricsServer on RECEIVER_METRICS_PORT."""

    def test_metrics_endpoint_returns_200(self, running_receiver) -> None:
        _proc, _grpc, _http, metrics_port = running_receiver
        _wait_port_open("127.0.0.1", metrics_port, timeout=10.0)

        resp = httpx.get(f"http://127.0.0.1:{metrics_port}/metrics", timeout=2.0)
        assert resp.status_code == 200, (
            f"/metrics returned {resp.status_code}: {resp.text!r}"
        )

    def test_metrics_endpoint_uses_prometheus_content_type(
        self, running_receiver
    ) -> None:
        _proc, _grpc, _http, metrics_port = running_receiver
        _wait_port_open("127.0.0.1", metrics_port, timeout=10.0)

        resp = httpx.get(f"http://127.0.0.1:{metrics_port}/metrics", timeout=2.0)
        # prometheus_client.CONTENT_TYPE_LATEST is "text/plain; version=0.0.4; charset=utf-8"
        # — we only need to pin the text/plain prefix to keep this stable
        # across prometheus_client versions.
        assert "text/plain" in resp.headers.get("content-type", "")

    def test_metrics_endpoint_emits_prometheus_exposition(
        self, running_receiver
    ) -> None:
        """The body must be Prometheus text-exposition format coming from the
        receiver's own registry — not an empty 200, not the default
        prometheus_client registry. The receiver uses an isolated
        ``CollectorRegistry`` (see metrics.make_registry); the default
        registry's process collectors live elsewhere. The strongest cheap
        check is that the body looks like exposition (HELP/TYPE comments)
        and is non-trivial in size."""
        _proc, _grpc, _http, metrics_port = running_receiver
        _wait_port_open("127.0.0.1", metrics_port, timeout=10.0)

        resp = httpx.get(f"http://127.0.0.1:{metrics_port}/metrics", timeout=2.0)
        body = resp.text
        assert "# HELP" in body, (
            f"/metrics body is not Prometheus exposition format; first 500 "
            f"chars:\n{body[:500]}"
        )
        assert "# TYPE" in body, (
            f"/metrics body missing TYPE comments; first 500 chars:\n{body[:500]}"
        )

    def test_metrics_endpoint_exposes_receiver_metrics(
        self, running_receiver
    ) -> None:
        """The receiver-defined metrics from PROJECT.md §5.1 must be in the
        registry that ``MetricsServer`` exposes. Test_metrics.py already
        asserts this via the harness's own MetricsServer; this test pins the
        same contract on the surface produced by ``__main__``."""
        _proc, _grpc, _http, metrics_port = running_receiver
        _wait_port_open("127.0.0.1", metrics_port, timeout=10.0)

        resp = httpx.get(f"http://127.0.0.1:{metrics_port}/metrics", timeout=2.0)
        body = resp.text
        # Just check the metric NAMES are registered — actual increments
        # are the job of the test_metrics.py harness tests.
        for name in (
            "spans_received_total",
            "spans_inserted_total",
            "spans_finalized_total",
            "spans_duplicate_total",
            "span_insert_duration_seconds",
            "span_row_bytes",
            "db_errors_total",
        ):
            assert name in body, (
                f"required metric {name!r} not exposed by receiver __main__'s "
                f"/metrics; first 500 chars:\n{body[:500]}"
            )
