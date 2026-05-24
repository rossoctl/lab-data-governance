"""UI backend entry point — ``python -m data_governance.api``.

Boots the Spans REST API + UI shell on port 8080 (the k8s manifests in
``deploy/k8s/40-ui.yaml`` rely on this command and port). The DSN is read
from ``DATABASE_URL``, matching the receiver's convention
(:mod:`data_governance.processors.otlp_receiver.__main__`).

The UI backend is a read-only consumer of Postgres (§6 retrieval API): it
neither runs migrations nor checks ``alembic_version`` on startup. The
receiver's startup schema-version check (issue #10) catches that drift on
write; a read-only consumer hitting an out-of-date schema will surface as
``GET /spans`` 5xxs once Postgres returns errors, which is fine for v1.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

import uvicorn

from data_governance import db

from . import DEFAULT_PORT, build_app


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
    log = logging.getLogger("data_governance.api")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write(
            "DATABASE_URL must be set to a libpq URL "
            "(e.g. postgres://user:pass@host:5432/dbname)\n"
        )
        return 2

    db.configure(dsn)

    host = os.environ.get("API_HOST", "0.0.0.0")
    port = _int_env("API_PORT", DEFAULT_PORT)

    config = uvicorn.Config(
        app=build_app(),
        host=host,
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
    server = uvicorn.Server(config)

    # Run uvicorn in a background thread so we can install signal handlers
    # in the main thread the same way the receiver entrypoint does. uvicorn's
    # own signal handling assumes it owns the main thread; running it in a
    # background thread sidesteps that and matches receiver __main__.
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    log.info("UI backend listening on %s:%d", host, port)

    stop_event = threading.Event()

    def _shutdown(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    stop_event.wait()
    server.should_exit = True
    thread.join(timeout=5.0)
    db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
