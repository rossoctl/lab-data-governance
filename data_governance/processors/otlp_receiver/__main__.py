"""OTLP receiver entry point — ``python -m data_governance.processors.otlp_receiver``.

Boots the gRPC server on 4317 and the HTTP/protobuf + ``/healthz`` server
on 4318, and waits for SIGINT/SIGTERM. The DSN is read from
``DATABASE_URL`` (matching the migrate CLI's convention in
:mod:`data_governance.db.migrate`); pool sizing comes from ``DB_POOL_*``
environment variables consumed by :func:`data_governance.db.configure`.

Future slices that grow the receiver (blocklist #9, metrics #8,
SQLSTATE→OTLP mapping #8) extend this entry point; the tracer-bullet
version stays deliberately small.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from data_governance import db

from .server import GrpcOtlpServer, HttpOtlpServer


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("data_governance.processors.otlp_receiver")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write(
            "DATABASE_URL must be set to a libpq URL "
            "(e.g. postgres://user:pass@host:5432/dbname)\n"
        )
        return 2

    db.configure(dsn)

    grpc_server = GrpcOtlpServer()
    http_server = HttpOtlpServer()
    grpc_server.start()
    http_server.start()
    log.info(
        "OTLP receiver listening: gRPC on %s:%d, HTTP/protobuf + /healthz on %s:%d",
        grpc_server.host,
        grpc_server.port,
        http_server.host,
        http_server.port,
    )

    stop_event = threading.Event()

    def _shutdown(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    stop_event.wait()
    grpc_server.stop(grace=2.0)
    http_server.stop(grace=2.0)
    db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
