"""Tests for ``GET /healthz`` (PROJECT.md §5.1, issue #3 acceptance).

Returns 200 iff the OTLP HTTP/protobuf transport is open AND Postgres is
reachable; 503 otherwise. Reachability is probed by issuing a trivial
``SELECT 1`` through the Layer 1 pool — if the pool can hand out a
connection and the round trip succeeds, Postgres is reachable.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator

import httpx
import pytest

from data_governance import db
from data_governance.processors.otlp_receiver.server import HttpOtlpServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port_open(host: str, port: int, timeout: float = 5.0) -> None:
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
def http_server(configured_db: str) -> Iterator[HttpOtlpServer]:
    port = _free_port()
    server = HttpOtlpServer(host="127.0.0.1", port=port)
    server.start()
    try:
        _wait_port_open("127.0.0.1", port)
        yield server
    finally:
        server.stop(grace=1.0)


class TestHealthzGreen:
    def test_returns_200_when_otlp_open_and_db_reachable(
        self, http_server: HttpOtlpServer
    ) -> None:
        r = httpx.get(f"http://127.0.0.1:{http_server.port}/healthz", timeout=2.0)
        assert r.status_code == 200
        assert r.text == "ok"


class TestHealthzRed:
    def test_returns_503_when_db_pool_is_closed(
        self, configured_db: str, http_server: HttpOtlpServer
    ) -> None:
        # Close the Layer 1 pool out from under the running HTTP server.
        # The /healthz handler probes the pool with a trivial SELECT, so
        # losing the pool must drop healthz to 503.
        db.close_pool()
        try:
            r = httpx.get(
                f"http://127.0.0.1:{http_server.port}/healthz", timeout=2.0
            )
            assert r.status_code == 503
        finally:
            # Restore the pool so subsequent fixture teardown ``close_pool``
            # is a no-op rather than a double-close. The migrated DSN is
            # the value yielded by ``configured_db``.
            db.configure(configured_db)

    def test_returns_503_when_db_unreachable(
        self, http_server: HttpOtlpServer
    ) -> None:
        # Repoint the pool at a port that nothing is listening on. The
        # SELECT 1 probe must fail and healthz must report 503.
        original_dsn = None  # pool is currently configured by the http_server
        # Reconfigure to a guaranteed-bad host:port. Use a short pool
        # timeout so the test doesn't hang on the default 30 s.
        bad_dsn = "postgresql://nobody@127.0.0.1:1/nodb"
        try:
            db.configure(bad_dsn, timeout=0.5)
            r = httpx.get(
                f"http://127.0.0.1:{http_server.port}/healthz", timeout=5.0
            )
            assert r.status_code == 503
        finally:
            # Leave teardown a safe pool to close.
            db.close_pool()
            # Suppress unused-warning by referencing the placeholder.
            assert original_dsn is None
