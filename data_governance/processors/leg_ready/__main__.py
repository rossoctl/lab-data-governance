"""P-leg-ready consumer entry point — ``python -m data_governance.processors.leg_ready``.

Reads ``DATABASE_URL`` (matching the migrate CLI / receiver / P-interactions /
P-classification / P-entity-ready convention), runs the defence-in-depth
schema-version check, then drives the wake-driven readiness drain loop
(:func:`driver.run` — a ``LISTEN`` notification on ``dg_interaction_leg_ready`` or
the poll backstop wakes each drain) until SIGINT/SIGTERM. Pool sizing comes from
``DB_POOL_*`` consumed by :func:`data_governance.db.configure`.

Mirrors the P-entity-ready entry point (``processors/entity_ready/__main__.py``)
exactly, differing only in the module names, the logger, and the metrics-port env
override — like entity-ready there is no per-item model to construct at startup. The
downstream governance consumer (risk/lineage/PDP) is future work; until it lands,
delivery is the drain's metric increment, so ``run`` is driven with no injected
observer.

Before processing any legs the entry point runs the schema-version check (issue #10,
ADR-0002): it reads ``alembic_version.version_num`` and refuses to start if that does
not match the head revision compiled into the image, exiting non-zero so k8s surfaces
it as CrashLoopBackOff.
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

from . import driver, metrics

# Distinct from the receiver's 9090, the interactions processor's 9091, the
# classification processor's 9092, and the entity-ready processor's 9093 so a
# co-located deployment doesn't collide on the metrics port. Overridable via
# LEG_READY_METRICS_PORT (0 = ephemeral, used by tests).
DEFAULT_METRICS_PORT = 9094


def _int_env(name: str, default: int) -> int:
    """Parse an integer port from the environment, falling back to *default*.

    Lets the consumer's metrics port be overridden from outside the process (e.g. so
    integration tests can run multiple processors without colliding). Production never
    sets it.
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
    log = logging.getLogger("data_governance.processors.leg_ready")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write(
            "DATABASE_URL must be set to a libpq URL "
            "(e.g. postgres://user:pass@host:5432/dbname)\n"
        )
        return 2

    db.configure(dsn)

    # Defence-in-depth schema-version check (issue #10, ADR-0002). The consumer does
    # NOT invoke alembic — `check_schema_version` only reads `alembic_version` and
    # compares it to the head compiled into the image.
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

    # Prometheus /metrics surface. Reuses the receiver's MetricsServer, pointed at
    # this consumer's own registry.
    metrics_server = MetricsServer(
        port=_int_env("LEG_READY_METRICS_PORT", DEFAULT_METRICS_PORT),
        registry=metrics._registry,
    )
    metrics_server.start()
    log.info(
        "leg-ready consumer Prometheus /metrics on %s:%d",
        metrics_server.host,
        metrics_server.port,
    )

    try:
        # `dsn` drives the dedicated LISTEN connection for low-latency wake (issue
        # #71); the drain itself still uses the pool db.configure() set up. No
        # downstream observer is injected yet (risk/lineage/PDP are future work,
        # ADR-0027) — delivery is the drain's metric increment.
        driver.run(stop_event, dsn)
    finally:
        # Stop the metrics surface (no in-flight writes) before closing the pool.
        metrics_server.stop(grace=1.0)
        db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
