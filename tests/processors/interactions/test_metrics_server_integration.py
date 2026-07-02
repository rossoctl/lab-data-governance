"""Integration test: the interactions processor exposes ``GET /metrics`` (#73).

The tripwire counter (exposition name ``finalization_observed_total`` —
prometheus_client appends ``_total`` to counters) must be genuinely scrapeable,
not just incremented in-process. This boots the real processor via ``python -m
data_governance.processors.interactions`` (same subprocess shape as
``test_driver.py``'s entrypoint tests) and verifies ``/metrics`` is reachable on
``INTERACTIONS_METRICS_PORT`` and that the counter appears in the exposition.

We deliberately drive the process boundary rather than calling ``main()``
in-process: the contract the issue turns on is "the production processor
exposes the finalization tripwire on its metrics surface".
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port_open(host: str, port: int, timeout: float = 15.0) -> None:
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


@pytest.fixture()
def running_processor(migrated_dsn: str):
    """Spawn the processor process and wait until /metrics is up.

    Yields ``(proc, metrics_port)``. Teardown SIGTERMs the process, falling back
    to SIGKILL if it does not exit in time.
    """
    metrics_port = _free_port()
    env = {
        "DATABASE_URL": migrated_dsn,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LOG_LEVEL": "INFO",
        "DB_POOL_MIN_SIZE": "1",
        "DB_POOL_MAX_SIZE": "2",
        "DB_POOL_TIMEOUT": "5",
        "INTERACTIONS_METRICS_PORT": str(metrics_port),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "data_governance.processors.interactions"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_port_open("127.0.0.1", metrics_port, timeout=15.0)
        yield proc, metrics_port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)


def test_metrics_endpoint_exposes_finalization_observed_total(running_processor) -> None:
    """/metrics returns 200 Prometheus exposition including the tripwire counter."""
    _proc, metrics_port = running_processor

    resp = httpx.get(f"http://127.0.0.1:{metrics_port}/metrics", timeout=2.0)
    assert resp.status_code == 200, (
        f"/metrics returned {resp.status_code}: {resp.text!r}"
    )
    assert "text/plain" in resp.headers.get("content-type", "")
    body = resp.text
    assert "# HELP" in body, f"not Prometheus exposition: {body[:500]!r}"
    # The tripwire counter must be present under its real exposition name
    # (registered at import, value 0). prometheus_client appends ``_total``.
    assert "finalization_observed_total" in body, (
        f"finalization_observed_total not in /metrics body: {body[:1000]!r}"
    )
