"""Fixtures for API tests."""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator

import psycopg
import pytest

from data_governance import db
from data_governance.api import SpansApiServer


@pytest.fixture()
def configured_db(migrated_dsn: str) -> Iterator[str]:
    db.close_pool()
    db.configure(migrated_dsn)
    try:
        yield migrated_dsn
    finally:
        db.close_pool()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(host: str, port: int, timeout: float = 5.0) -> None:
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
def insert_span(configured_db: str):
    """Return a helper that inserts a minimal span row directly."""
    def _insert(conn, *, trace_id: str, span_id: str, name: str):
        conn.execute(
            """
            INSERT INTO spans (
                trace_id, span_id, parent_id, kind, name,
                started_at, attributes, seq, arrival_seq, observed_at
            ) VALUES (
                %s, %s, NULL, 'INTERNAL', %s,
                now(), '{}'::jsonb, nextval('spans_seq'), currval('spans_seq'), now()
            )
            """,
            (trace_id, span_id, name),
        )
        conn.commit()
    return _insert


@pytest.fixture()
def api_server(configured_db: str) -> Iterator[SpansApiServer]:
    port = _free_port()
    server = SpansApiServer(host="127.0.0.1", port=port)
    server.start()
    try:
        _wait_port("127.0.0.1", port)
        yield server
    finally:
        server.stop(grace=1.0)
