"""Alerts processor entry point — ``python -m data_governance.processors.risk.alerts``.

Reads ``DATABASE_URL`` (matching the migrate CLI / receiver / sibling
processors convention), runs the defence-in-depth schema-version check, then
drives the wake-driven drain loop (:func:`driver.run` — a ``LISTEN``
notification on ``dg_trace_risk_written`` or the poll backstop wakes
each drain) until SIGINT/SIGTERM. Pool sizing comes from ``DB_POOL_*``
consumed by :func:`data_governance.db.configure`.

Mirrors the trace-trigger entry point, differing in the module names, the
logger, the metrics-port env override, and one check before the database is
touched: an alert floor the generator would misread exits 5. Nothing
external is constructed at startup; the rule catalog is read per alert from
``RISK_RULES_SOURCE`` (the ``opa-policy`` ConfigMap, see 92-risk-alerts.yaml).

Before processing any records the entry point runs the schema-version check
(issue #10, ADR-0002): it reads ``alembic_version.version_num`` and refuses
to start if that does not match the head revision compiled into the image,
exiting non-zero so k8s surfaces it as CrashLoopBackOff.
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
from data_governance.processors.otlp_receiver.server import MetricsServer
from data_governance.risk.alerts import generate

from . import driver, metrics

# Distinct from the receiver's 9090, the interactions processor's 9091, the
# classification processor's 9092, the entity-ready consumer's 9093, and the
# leg-ready consumer's 9094, and the trace trigger's 9095 so a co-located
# deployment doesn't collide on the metrics port. Overridable via
# RISK_ALERTS_METRICS_PORT (0 =
# ephemeral, used by tests).
DEFAULT_METRICS_PORT = 9096


def _int_env(name: str, default: int) -> int:
    """Parse an integer port from the environment, falling back to *default*."""
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
    log = logging.getLogger("data_governance.processors.risk.alerts")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write(
            "DATABASE_URL must be set to a libpq URL "
            "(e.g. postgres://user:pass@host:5432/dbname)\n"
        )
        return 2

    # Refuse an alert configuration the generator would silently misread
    # (an unknown floor disables the floor) before touching the database.
    try:
        generate.validate_config()
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        log.error("alert configuration invalid: %s", exc)
        return 5

    db.configure(dsn)

    # Defence-in-depth schema-version check (issue #10, ADR-0002). The
    # processor does NOT invoke alembic — `check_schema_version` only reads
    # `alembic_version` and compares it to the head compiled into the image.
    try:
        check_schema_version()
    except SchemaVersionMismatch as exc:
        sys.stderr.write(f"{exc}\n")
        log.error("schema version check failed: %s", exc)
        db.close_pool()
        return 3
    except Exception as exc:  # noqa: BLE001 — surface clearly, exit non-zero
        sys.stderr.write(f"schema version check failed before comparison: {exc}\n")
        log.exception("schema version check raised before comparison")
        db.close_pool()
        return 4

    stop_event = threading.Event()

    def _shutdown(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Prometheus /metrics surface. Reuses the receiver's MetricsServer,
    # pointed at this processor's own registry.
    metrics_server = MetricsServer(
        port=_int_env("RISK_ALERTS_METRICS_PORT", DEFAULT_METRICS_PORT),
        registry=metrics._registry,
    )
    metrics_server.start()
    log.info(
        "alerts processor Prometheus /metrics on %s:%d",
        metrics_server.host,
        metrics_server.port,
    )

    try:
        # `dsn` drives the dedicated LISTEN connection for low-latency wake
        # (issue #71); the drain itself still uses the pool db.configure()
        # set up.
        driver.run(stop_event, dsn)
    finally:
        # Stop the metrics surface (no in-flight writes) before closing the pool.
        metrics_server.stop(grace=1.0)
        db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
