"""P-label-lineage entry point — ``python -m data_governance.processors.label_lineage``.

Reads ``DATABASE_URL``, validates ``LABEL_MATCHER``, ``LABEL_MEMORY_SCOPES`` and the
cross-conversation configuration (``LABEL_CROSS_*``) before any DB work (an invalid one exits 2 rather than deriving
under a configuration the operator did not mean), runs the schema-version check
(ADR-0002: exit 3 on a mismatch, 4 if it cannot compare), then drives the
readiness-gated drain (:func:`driver.run`) until SIGINT/SIGTERM. Shaped like the
data-lineage entry point: no metrics surface, no model load.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading

from data_governance import db
from data_governance.db.schema_version import SchemaVersionMismatch, check_schema_version

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
    except ValueError as exc:   # an unknown finder, invalid scopes or an invalid crossing
        sys.stderr.write(f"{exc}\n")
        return 2
    log.info("label lineage with LABEL_MATCHER=%s, LABEL_MEMORY_SCOPES=%s", config.matcher,
             json.dumps(dict(config.scopes), sort_keys=True))
    c = config.crossing
    log.info("across conversations: %s", "off" if c is None else
             f"on, at most {c.edges_per_value} edges per value, retention {int(c.retention.total_seconds())} s, anchors {','.join(sorted(c.anchors))}, "
             f"limits: holdings per entity {c.holdings_limit or 'none'}, payload bytes {c.payload_bytes or 'none'}, "
             f"candidates per payload {c.candidates_limit or 'none'}")

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

    # Sole consumer, as leg-ready: the correction a derivation queues assumes no other
    # worker derives meanwhile (one could read the occurrences before this one commits a
    # change and miss its correction for good). maxSurge: 0 does not stop an evicted or
    # still-exiting pod from overlapping its replacement; the replacement waits here
    # until the old process's connection closes. The lock's session is checked before
    # every drain: if it died, run() raises db.LockLost and the process exits 5.
    log.info("waiting for the sole-consumer lock %r", driver.PROCESSOR_NAME)
    try:
        with db.session_lock(f"consumer:{driver.PROCESSOR_NAME}", dsn, stop=stop_event) as lock:
            log.info("sole-consumer lock held")
            driver.run(stop_event, dsn, config, guard=lock.check)
    except db.LockWaitStopped:
        log.info("stopped while waiting for the sole-consumer lock")
    except db.LockLost as exc:
        sys.stderr.write(f"sole-consumer lock lost (LockLost: {exc}); exiting\n")
        log.error("sole-consumer lock lost: %s; exiting", exc)
        return 5
    finally:
        db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
