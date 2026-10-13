"""P-leg-ready consumer entry point — ``python -m data_governance.processors.leg_ready``.

Reads ``DATABASE_URL`` (matching the migrate CLI / receiver / P-interactions /
P-classification / P-entity-ready convention), runs the defence-in-depth
schema-version check, then drives the wake-driven readiness drain loop
(:func:`driver.run` — a ``LISTEN`` notification on ``dg_interaction_leg_ready`` or
the poll backstop wakes each drain) until SIGINT/SIGTERM. Pool sizing comes from
``DB_POOL_*`` consumed by :func:`data_governance.db.configure`.

Mirrors the P-entity-ready entry point (``processors/entity_ready/__main__.py``),
differing in the module names, the logger, the metrics-port env override — and the
downstream observer: since issue #158 this consumer IS the risk pipeline's driver.
At startup it builds the interaction risk engine's OPA client (RISK_OPA_* config)
and injects :func:`data_governance.risk.engine.observer.make_risk_observer`, so
every ready leg automatically recomputes its interaction's risk record (FR-DAS-012
conditions 1 and 2), with the engine's write committing atomically with the
leg-ready cursor advance. ``RISK_ENGINE_OBSERVER=off`` restores the bare
metric-increment delivery (an operational escape hatch — e.g. running the lineage
pipeline with no OPA deployed; with the observer on and OPA unreachable the stream
holds, by design, rather than skipping legs). It drains only while holding the
``consumer:leg_ready`` session lock (``db.session_lock``), so a second instance —
an evicted or still-exiting pod overlapping its replacement — waits instead of
draining beside it, and it re-checks the lock before every drain, exiting if the
lock's connection died.

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

    # The downstream governance consumer (issue #158): the interaction risk
    # engine, injected as the two-phase observer. Import here (not at module
    # top) so the bare consumer never pays the risk-engine import cost when
    # switched off.
    observer = None
    if os.environ.get("RISK_ENGINE_OBSERVER", "on").lower() not in ("off", "0", "false"):
        from data_governance.risk.engine.observer import make_risk_observer
        from data_governance.risk.engine.opa import create_opa_client

        observer = make_risk_observer(create_opa_client())
        log.info("risk engine observer enabled (set RISK_ENGINE_OBSERVER=off to disable)")
    else:
        log.info("risk engine observer DISABLED via RISK_ENGINE_OBSERVER")

    try:
        # `dsn` drives the dedicated LISTEN connection for low-latency wake (issue
        # #71); the drain itself still uses the pool db.configure() set up.
        # One drainer at a time. The risk engine computes a record from the
        # evidence it read BEFORE its write transaction, so two drainers can
        # land an older computation after a newer one, and the trace trigger's
        # seq watermark assumes this is the tables' one serial writer.
        # maxSurge: 0 does not stop an evicted or still-exiting pod from
        # overlapping its replacement; the replacement waits here until the
        # old process's connection closes.
        # The lock's session is re-checked at every wake and before every
        # leg's write: if it died (a Postgres restart, a terminated backend)
        # the lock went with it, and run() raises db.LockLost rather than
        # write unprotected — the process exits 5 and its restart waits for
        # the lock like any other.
        log.info("waiting for the sole-consumer lock %r", driver.PROCESSOR_NAME)
        try:
            with db.session_lock(
                f"consumer:{driver.PROCESSOR_NAME}", dsn, stop=stop_event
            ) as lock:
                log.info("sole-consumer lock held")
                driver.run(stop_event, dsn, observer=observer, guard=lock.check)
        except db.LockWaitStopped:
            log.info("stopped while waiting for the sole-consumer lock")
        except db.LockLost as exc:
            sys.stderr.write(f"sole-consumer lock lost (LockLost: {exc}); exiting\n")
            log.error("sole-consumer lock lost: %s; exiting", exc)
            return 5
    finally:
        # Stop the metrics surface (no in-flight writes) before closing the pool.
        metrics_server.stop(grace=1.0)
        db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
