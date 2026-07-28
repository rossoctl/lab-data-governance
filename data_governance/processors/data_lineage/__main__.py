"""P-data-lineage entry point — ``python -m data_governance.processors.data_lineage``.

Reads ``DATABASE_URL`` (matching the migrate CLI / receiver / P-interactions /
P-classification convention), runs the defence-in-depth schema-version check, then
drives the shared wake-driven drain loop (:func:`driver.run` — a ``LISTEN``
notification on ``dg_legs_inserted`` or the poll backstop wakes each drain) until
SIGINT/SIGTERM. Pool sizing comes from ``DB_POOL_*`` consumed by
:func:`data_governance.db.configure`.

``SEMANTIC_MATCHER`` selects the semantic matcher lineage is computed with; it is
read by :func:`data_governance.matching.get_matcher` (issue #116), not here, so this
entry point never names a matcher. An unrecognised name raises ``UnknownMatcher``
rather than silently falling back — a governance tool must not emit
maybe-everywhere lineage while the operator believes a real matcher is running.

Mirrors the P-classification entry point (``processors/classification/__main__.py``)
in shape, with two deliberate omissions:

- **No Prometheus /metrics surface.** Both sibling processors expose one; this
  processor ships without counters (issue #117 specifies none), and a registry with
  nothing in it would be a port to allocate and a deployment to wire for no signal.
  When lineage gets its first counter, the metrics server goes in here in the
  sibling's shape (next free port after the receiver's 9090 / P-interactions' 9091 /
  P-classification's 9092).
- **No model/detector load.** Matching is pluggable and the default is trivial
  (ADR-0027); any real matcher's setup cost belongs behind ``get_matcher``, not here.

Before processing any legs the entry point runs the schema-version check (issue #10,
ADR-0002): it reads ``alembic_version.version_num`` and refuses to start if that does
not match the head revision compiled into the image. This catches "wrong image
deployed against this DB" and "init container forgotten" — the migrate init container
is the primary ordering mechanism but the processor still verifies on startup,
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
from data_governance.matching import UnknownMatcher, get_matcher

from . import driver


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("data_governance.processors.data_lineage")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write(
            "DATABASE_URL must be set to a libpq URL "
            "(e.g. postgres://user:pass@host:5432/dbname)\n"
        )
        return 2

    # Validate the matcher configuration BEFORE any DB work, so a typo'd
    # SEMANTIC_MATCHER fails fast and visibly (mirrors how P-interactions validates
    # INTERACTIONS_ALGORITHM before dialing the database).
    try:
        get_matcher()
    except UnknownMatcher as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    db.configure(dsn)

    # Defence-in-depth schema-version check (issue #10, ADR-0002). The processor
    # does NOT invoke alembic — `check_schema_version` only reads `alembic_version`
    # and compares it to the head compiled into the image.
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
        # `dsn` drives the dedicated LISTEN connection for low-latency wake (issue
        # #71); the drain itself still uses the pool db.configure() set up.
        driver.run(stop_event, dsn)
    finally:
        db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
