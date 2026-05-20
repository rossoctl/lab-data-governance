"""Fixtures for API tests."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import psycopg
import pytest

from data_governance import db
from data_governance.api import SpansApiServer


@pytest.fixture()
def migrated_dsn(pg_dsn: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "data_governance.db.migrate"],
        env={"DATABASE_URL": pg_dsn, "PATH": "/usr/bin:/bin"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"migrate exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return pg_dsn


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
def api_server(configured_db: str) -> Iterator[SpansApiServer]:
    port = _free_port()
    server = SpansApiServer(host="127.0.0.1", port=port)
    server.start()
    try:
        _wait_port("127.0.0.1", port)
        yield server
    finally:
        server.stop(grace=1.0)
