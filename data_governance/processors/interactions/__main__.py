"""P-interactions processor entry point — ``python -m data_governance.processors.interactions``.

Reads ``DATABASE_URL`` (matching the migrate CLI / receiver convention), runs the
defence-in-depth schema-version check, then drives the wake-driven drain loop
(a ``LISTEN`` notification or the poll backstop wakes each drain, issue #71) until
SIGINT/SIGTERM. Pool sizing comes from ``DB_POOL_*`` consumed by
:func:`data_governance.db.configure`.

``INTERACTIONS_ALGORITHM`` selects which derivation drives the loop (all write
the SAME production tables; only the derivation differs, and only ONE runs at a
time so they share the ``interactions`` cursor):

- ``streaming`` (default) — the per-span, eventually-consistent streaming
  algorithm (:func:`driver.run`, ADR-0007).
- ``graph`` — the batch graph algorithm, re-derived per span (:func:`graph_driver.run`,
  ADR-0026).
- ``sidecar`` — the two-span AuthBridge sidecar derivation, whole-trace
  reconcile per span (:func:`sidecar_driver.run`, ADR-0029).

Before processing any spans the entry point runs the schema-version check
(issue #10, ADR-0002): it reads ``alembic_version.version_num`` and refuses to
start if that does not match the head revision compiled into the image. This
catches "wrong image deployed against this DB" and "init container forgotten" —
the migrate init container is the primary ordering mechanism but the processor
still verifies on startup, exiting non-zero so k8s surfaces it as
CrashLoopBackOff.
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

from . import driver, graph_driver, metrics, sidecar_driver

# Distinct from the receiver's 9090 so a co-located receiver + processor don't
# collide on the metrics port. Overridable via INTERACTIONS_METRICS_PORT.
DEFAULT_METRICS_PORT = 9091

# Which derivation drives the loop. All write the same production tables.
_ALGORITHMS = {
    "streaming": driver.run,
    "graph": graph_driver.run,
    "sidecar": sidecar_driver.run,
}
_DEFAULT_ALGORITHM = "streaming"


def _int_env(name: str, default: int) -> int:
    """Parse an integer port from the environment, falling back to *default*.

    Lets the processor's metrics port be overridden from outside the process
    (e.g. so integration tests can run multiple processors without colliding).
    Production never sets it.
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
    log = logging.getLogger("data_governance.processors.interactions")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write(
            "DATABASE_URL must be set to a libpq URL "
            "(e.g. postgres://user:pass@host:5432/dbname)\n"
        )
        return 2

    algo_name = os.environ.get("INTERACTIONS_ALGORITHM", _DEFAULT_ALGORITHM)
    run_loop = _ALGORITHMS.get(algo_name)
    if run_loop is None:
        sys.stderr.write(
            f"INTERACTIONS_ALGORITHM must be one of {sorted(_ALGORITHMS)}; "
            f"got {algo_name!r}\n"
        )
        return 2

    db.configure(dsn)

    # Defence-in-depth schema-version check (issue #10, ADR-0002). The processor
    # does NOT invoke alembic — `check_schema_version` only reads
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

    # Prometheus /metrics surface (issue #73 tripwire lives here). Reuses the
    # receiver's MetricsServer, pointed at this processor's own registry.
    metrics_server = MetricsServer(
        port=_int_env("INTERACTIONS_METRICS_PORT", DEFAULT_METRICS_PORT),
        registry=metrics._registry,
    )
    metrics_server.start()
    log.info(
        "interactions processor Prometheus /metrics on %s:%d",
        metrics_server.host,
        metrics_server.port,
    )

    log.info("interactions processor using %r algorithm", algo_name)

    try:
        # `dsn` drives the dedicated LISTEN connection for low-latency wake
        # (issue #71); the drain itself still uses the pool db.configure() set up.
        run_loop(stop_event, dsn)
    finally:
        # Stop the metrics surface (no in-flight writes) before closing the pool.
        metrics_server.stop(grace=1.0)
        db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
