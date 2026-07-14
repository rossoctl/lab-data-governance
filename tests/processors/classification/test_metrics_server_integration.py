"""Integration test: the P-classification processor exposes ``GET /metrics`` (#81).

Both counters — ``payloads_classified_total`` (#77) and the projection-coverage
``projection_fallbacks_total`` (#81) — must be genuinely scrapeable, not just
incremented in-process. This boots the real processor via ``python -m
data_governance.processors.classification`` (same subprocess shape as the
entrypoint tests and the interactions ``test_metrics_server_integration.py``) and
verifies ``/metrics`` is reachable on ``CLASSIFICATION_METRICS_PORT`` and that
both counters appear in the exposition.

We deliberately drive the process boundary rather than calling ``main()``
in-process: the contract issue #81 turns on is "the production processor exposes
its metrics surface on port 9092 (overridable via CLASSIFICATION_METRICS_PORT),
with no collision against the receiver's 9090 / the interactions 9091".
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from data_governance.processors.classification.__main__ import DEFAULT_METRICS_PORT
from data_governance.processors.interactions.__main__ import (
    DEFAULT_METRICS_PORT as INTERACTIONS_DEFAULT_PORT,
)
from data_governance.processors.otlp_receiver.server import (
    DEFAULT_METRICS_PORT as RECEIVER_DEFAULT_PORT,
)


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


def test_default_metrics_port_is_9092_and_collision_free() -> None:
    """The default port is 9092, distinct from the receiver's 9090 and the
    interactions processor's 9091 — a co-located deployment cannot collide."""
    assert DEFAULT_METRICS_PORT == 9092
    assert DEFAULT_METRICS_PORT != INTERACTIONS_DEFAULT_PORT
    assert DEFAULT_METRICS_PORT != RECEIVER_DEFAULT_PORT
    # And the three defaults are pairwise distinct (9090 / 9091 / 9092).
    assert len({RECEIVER_DEFAULT_PORT, INTERACTIONS_DEFAULT_PORT, DEFAULT_METRICS_PORT}) == 3


@pytest.fixture()
def running_processor(migrated_dsn: str):
    """Spawn the processor process and wait until /metrics is up.

    Yields ``(proc, metrics_port)``. The port is passed via
    ``CLASSIFICATION_METRICS_PORT`` — exercising the override — and picked free
    so a parallel receiver/interactions run cannot collide. Teardown SIGTERMs the
    process, falling back to SIGKILL if it does not exit in time.
    """
    metrics_port = _free_port()
    env = {
        "DATABASE_URL": migrated_dsn,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LOG_LEVEL": "INFO",
        "DB_POOL_MIN_SIZE": "1",
        "DB_POOL_MAX_SIZE": "2",
        "DB_POOL_TIMEOUT": "5",
        "CLASSIFICATION_METRICS_PORT": str(metrics_port),
        # Skip the in-process NER-model load (issue #79): this test exercises the
        # /metrics surface, orthogonal to the model, and the dev/test environment
        # installs neither torch/transformers (classification-image-only) nor the
        # ~500 MB weights. Production loads the model — see __main__._build_detector.
        "CLASSIFICATION_SKIP_MODEL": "1",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "data_governance.processors.classification"],
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


def test_metrics_endpoint_exposes_both_counters(running_processor) -> None:
    """/metrics returns 200 Prometheus exposition including both counters at
    their real exposition names (prometheus_client appends ``_total``), value 0
    at import — proving they are registered and scrapeable before any payload."""
    _proc, metrics_port = running_processor

    resp = httpx.get(f"http://127.0.0.1:{metrics_port}/metrics", timeout=2.0)
    assert resp.status_code == 200, (
        f"/metrics returned {resp.status_code}: {resp.text!r}"
    )
    assert "text/plain" in resp.headers.get("content-type", "")
    body = resp.text
    assert "# HELP" in body, f"not Prometheus exposition: {body[:500]!r}"
    assert "payloads_classified_total" in body, (
        f"payloads_classified_total not in /metrics body: {body[:1000]!r}"
    )
    assert "projection_fallbacks_total" in body, (
        f"projection_fallbacks_total not in /metrics body: {body[:1000]!r}"
    )
