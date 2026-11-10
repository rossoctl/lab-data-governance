"""The verdict service — ``python -m data_governance.risk.api.verdict_app``.

Serves the Sentry gate's verdict (:mod:`.verdict_routes`), ``/healthz`` (liveness)
and ``/readyz`` (readiness), and nothing else. The gate calls it from workload
namespaces; the full API (traces, payloads, the UI) admits the platform
namespaces only (deploy/k8s/50-networkpolicy.yaml), and widening that would hand
every workload namespace every payload DG holds. So the verdict runs as its own
process with its own Service and ingress policy (deploy/k8s/45-verdict.yaml).

It runs from the classification image: with ``VERDICT_PRIORITY_CLASSIFICATION=on`` (the default) it
loads the classifier's model at start, classifies a fixed sample, and only then
reports ready; a model that does not load ends the process, as it does the
classifier's, rather than leave a pod that never turns ready. With ``VERDICT_PRIORITY_CLASSIFICATION=off`` it loads no model and is ready at once,
so it also runs from the receiver image.

``main`` refuses to start without ``VERDICT_SECRET`` (the port would otherwise serve
every caller) and on a schema other than the one compiled into the image (priority
classification writes a derived table).

It holds its own change hub (one ``LISTEN`` session) and a pool sized to its
bounded workers (:attr:`.verdict_routes.Settings.pool_size`, unless
``DB_POOL_MAX_SIZE`` says otherwise). It never calls OPA and never writes a risk
record: leg-ready is the only OPA client and the only risk writer.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from data_governance import db
from data_governance.change_hub import ChangeHub
from data_governance.db.schema_version import SchemaVersionMismatch, check_schema_version
from data_governance.risk.api import verdict_routes
from data_governance.risk.api.verdict_routes import PriorityClassifier, Settings, VerdictService

DEFAULT_PORT = 8080

log = logging.getLogger("data_governance.risk.api.verdict_app")


def _default_priority_classifier(settings: Settings) -> PriorityClassifier:
    # The classifier's own detector factory and model generation, so a row the
    # priority classification writes is the row the cursor loop would have written.
    from data_governance.processors.classification.__main__ import MODEL_VERSION, _build_detector

    return PriorityClassifier(_build_detector, MODEL_VERSION, settings.priority_classification_concurrency)


def _exit_on_failed_load(task: asyncio.Task) -> None:
    if task.cancelled() or task.exception() is None:
        return
    log.critical("verdict priority classification: the model did not load: %s; exiting", task.exception())
    _exit(1)


#: How a failed model load ends the process (a test replaces it).
_exit = os._exit


@asynccontextmanager
async def _lifespan(app: Starlette) -> AsyncIterator[None]:
    hub: ChangeHub = app.state.change_hub
    settings: Settings = app.state.verdict_settings
    hub.start(asyncio.get_running_loop())
    priority_classifier = app.state.priority_classifier_factory(settings) if settings.priority_classification else None
    service = VerdictService(hub, settings, priority_classifier)
    app.state.verdict = service
    service.start()
    warm = None
    if priority_classifier is not None:
        warm = asyncio.create_task(priority_classifier.load_and_warm(), name="verdict-warm")
        warm.add_done_callback(_exit_on_failed_load)
    log.info("verdict service: priority_classification=%s priority_decision=%s max_waiters=%d read_workers=%d priority_classification_concurrency=%d",
             "on" if settings.priority_classification else "off", "on" if settings.priority_decision else "off",
             settings.max_waiters, settings.read_workers, settings.priority_classification_concurrency)
    try:
        yield
    finally:
        if warm is not None:
            warm.cancel()
        await service.stop()
        hub.stop()


async def _healthz(_request: Request) -> Response:
    return PlainTextResponse("ok")


async def _readyz(request: Request) -> Response:
    service: VerdictService | None = getattr(request.app.state, "verdict", None)
    if service is None or not service.ready:
        return PlainTextResponse("warming", status_code=503)
    return PlainTextResponse("ok")


def build_verdict_app(
    *,
    listen_dsn: str | None,
    settings: Settings | None = None,
    priority_classifier_factory=_default_priority_classifier,
) -> Starlette:
    app = Starlette(
        routes=[
            *verdict_routes.routes(),
            Route("/healthz", endpoint=_healthz, methods=["GET"]),
            Route("/readyz", endpoint=_readyz, methods=["GET"]),
        ],
        lifespan=_lifespan,
    )
    app.state.change_hub = ChangeHub(listen_dsn, channels=verdict_routes.HUB_CHANNELS)
    app.state.verdict_settings = settings if settings is not None else Settings.from_env()
    app.state.priority_classifier_factory = priority_classifier_factory
    return app


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write("DATABASE_URL must be set to a libpq URL\n")
        return 2
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    if not settings.secret:
        sys.stderr.write("VERDICT_SECRET must be set: without it the port would serve every caller "
                         "(deploy/k8s/45-verdict.yaml reads it from Secret dg-verdict-secret)\n")
        return 2
    db.configure(dsn, max_size=None if os.environ.get("DB_POOL_MAX_SIZE") else settings.pool_size)
    try:
        check_schema_version()
    except SchemaVersionMismatch as exc:
        sys.stderr.write(f"{exc}\n")
        db.close_pool()
        return 3
    except Exception as exc:  # noqa: BLE001 — surface clearly, exit non-zero
        sys.stderr.write(f"schema version check failed before comparison: {exc}\n")
        db.close_pool()
        return 4
    app = build_verdict_app(listen_dsn=dsn, settings=settings)
    uvicorn.run(
        app,
        host=os.environ.get("VERDICT_HOST", "0.0.0.0"),
        port=int(os.environ.get("VERDICT_PORT", DEFAULT_PORT)),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
    db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
