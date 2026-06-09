"""Run the graph-builder backfill from the command line.

    DATABASE_URL=... python -m data_governance.processors.graph_builder

The DSN is read from ``DATABASE_URL`` (matching the receiver's convention) and
handed to :func:`data_governance.db.configure`. The backfill runs once in a
single transaction and prints its :class:`~.build.BackfillResult` counts. This
is the manual smoke path — point it at a database loaded with a real span dump
and eyeball the entity/edge counts.

Out of scope: a long-running / scheduled mode. v1 invokes the backfill
explicitly; incremental derivation is issue #62.
"""

from __future__ import annotations

import logging
import os
import sys

from data_governance import db

from .build import run_backfill


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("data_governance.processors.graph_builder")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write(
            "DATABASE_URL must be set to a libpq URL "
            "(e.g. postgres://user:pass@host:5432/dbname)\n"
        )
        return 2

    db.configure(dsn)
    try:
        result = run_backfill()
    finally:
        db.close_pool()

    log.info(
        "backfill complete: scanned=%d entities=%d edges=%d orphan_edges=%d",
        result.spans_scanned,
        result.entities_upserted,
        result.edges_upserted,
        result.orphan_edges,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
