"""OTLP receiver entry point — ``python -m data_governance.processors.otlp_receiver``.

Boots the gRPC server on 4317, the HTTP/protobuf + ``/healthz`` server
on 4318, and the Prometheus ``/metrics`` server on 9090, and waits for
SIGINT/SIGTERM. The DSN is read from ``DATABASE_URL`` (matching the
migrate CLI's convention in :mod:`data_governance.db.migrate`); pool
sizing comes from ``DB_POOL_*`` environment variables consumed by
:func:`data_governance.db.configure`.

Before binding any sockets the entry point runs the defence-in-depth
schema-version check (issue #10, ADR-0002): it reads
``alembic_version.version_num`` from Postgres and refuses to start if
that does not match the head revision compiled into the image. This
catches "wrong image deployed against this DB" and "init container
forgotten in a non-k8s deployment" — the migrate init container is the
primary ordering mechanism but the receiver still verifies on startup.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from data_governance import db
from data_governance.db.schema_version import (
    SchemaVersionMismatch,
    check_schema_version,
)

from .server import (
    DEFAULT_GRPC_PORT,
    DEFAULT_HTTP_PORT,
    DEFAULT_METRICS_PORT,
    GrpcOtlpServer,
    HttpOtlpServer,
    MetricsServer,
)


def _int_env(name: str, default: int) -> int:
    """Parse an integer port from the environment, falling back to *default*.

    Used to override the receiver's bind ports from outside the process so
    integration tests can run multiple receivers in parallel without
    colliding on 4317/4318. Production never sets these.
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        sys.stderr.write(f"{name} is not a valid integer: {raw!r}\n")
        return default


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

    # Defence-in-depth schema-version check (issue #10, ADR-0002).
    #
    # The migrate init container is the primary gate, but operators can
    # still get here against the wrong DB ("rolled the receiver image
    # forward without updating the migrate job", "ran the wrong image at
    # the wrong DB", "non-k8s deployment skipped the manual migrate").
    # On mismatch we log an actionable error to stderr and exit non-zero
    # so k8s surfaces it as CrashLoopBackOff in `kubectl get pods`. The
    # receiver does NOT itself invoke alembic — `check_schema_version`
    # only reads `alembic_version`.
    try:
        check_schema_version()
    except SchemaVersionMismatch as exc:
        sys.stderr.write(f"{exc}\n")
        log.error("schema version check failed: %s", exc)
        db.close_pool()
        return 3
    except Exception as exc:  # noqa: BLE001 — surface clearly, exit non-zero
        sys.stderr.write(
            f"schema version check failed before comparison: {exc}\n"
        )
        log.exception("schema version check raised before comparison")
        db.close_pool()
        return 4

    grpc_server = GrpcOtlpServer(
        port=_int_env("RECEIVER_GRPC_PORT", DEFAULT_GRPC_PORT),
    )
    http_server = HttpOtlpServer(
        port=_int_env("RECEIVER_HTTP_PORT", DEFAULT_HTTP_PORT),
    )
    metrics_server = MetricsServer(
        port=_int_env("RECEIVER_METRICS_PORT", DEFAULT_METRICS_PORT),
    )
    grpc_server.start()
    http_server.start()
    metrics_server.start()
    log.info(
        "OTLP receiver listening: gRPC on %s:%d, "
        "HTTP/protobuf + /healthz on %s:%d, "
        "Prometheus /metrics on %s:%d",
        grpc_server.host,
        grpc_server.port,
        http_server.host,
        http_server.port,
        metrics_server.host,
        metrics_server.port,
    )

    stop_event = threading.Event()

    def _shutdown(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    stop_event.wait()
    # Reverse start order on shutdown: stop the metrics surface first (it
    # has no in-flight ingest writes), then the OTLP transports, then close
    # the pool.
    metrics_server.stop(grace=1.0)
    grpc_server.stop(grace=2.0)
    http_server.stop(grace=2.0)
    db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
