"""P-label-lineage entry point — ``python -m data_governance.processors.label_lineage``.

Reads ``DATABASE_URL``, validates ``LABEL_MATCHER`` and
``LABEL_CROSS_TRACE_WINDOW_HOURS`` before any DB work (an unknown finder or a window
that is not a number of hours exits 2 rather than deriving under a configuration
the operator did not mean), runs the schema-version check
(ADR-0002: exit 3 on a mismatch, 4 if it cannot compare), then drives the
readiness-gated drain (:func:`driver.run`) until SIGINT/SIGTERM. Shaped like the
data-lineage entry point: no metrics surface, no model load.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from data_governance import db
from data_governance.db.schema_version import SchemaVersionMismatch, check_schema_version
from data_governance.label_lineage import find

from . import driver


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("data_governance.processors.label_lineage")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write(
            "DATABASE_URL must be set to a libpq URL (e.g. postgres://user:pass@host:5432/dbname)\n"
        )
        return 2

    try:
        config = driver.configured()
    except (find.UnknownFinder, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    log.info("label lineage with LABEL_MATCHER=%s %s=%s", config.matcher, driver.WINDOW_ENV, config.window_hours)

    db.configure(dsn)
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

    try:
        driver.run(stop_event, dsn, config)
    finally:
        db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
