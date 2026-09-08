"""Entry point: python -m data_governance.processors.dgl_decisions

Runs the DGL-decisions cursor loop until SIGTERM / KeyboardInterrupt.

Environment variables
---------------------
DATABASE_URL   libpq connection string (required).

Usage
-----
    DATABASE_URL="postgres://data_governance:change-me@data-governance-postgres:5432/data_governance" \
        python -m data_governance.processors.dgl_decisions
"""

from __future__ import annotations

import logging
import os
import signal
import threading

from data_governance import db

from . import driver

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)


def main() -> None:
    dsn = os.environ["DATABASE_URL"]
    db.configure(dsn)

    stop = threading.Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        log.info("dgl_decisions processor: received signal %d, stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("dgl_decisions processor starting")
    driver.run(stop, dsn)
    log.info("dgl_decisions processor stopped")


if __name__ == "__main__":
    main()
