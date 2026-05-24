"""``GET /healthz`` on the UI backend (issue #16).

The k8s manifests in ``deploy/k8s/40-ui.yaml`` wire liveness/readiness probes
to ``/healthz`` on the UI backend; without that route the UI pod would
crashloop after readinessProbe failures.

Like the receiver's healthz (PROJECT.md §5.1), the probe answers 200 when
Postgres is reachable, 503 otherwise. The UI backend's whole job is to
serve ``GET /spans``, which fans out to Postgres — making "Postgres
reachable" the right liveness signal.
"""

from __future__ import annotations

import httpx

from data_governance import db
from data_governance.api import SpansApiServer


def _base_url(server: SpansApiServer) -> str:
    return f"http://127.0.0.1:{server.port}"


def test_healthz_returns_200_when_postgres_reachable(api_server, configured_db):
    resp = httpx.get(f"{_base_url(api_server)}/healthz")
    assert resp.status_code == 200
    assert resp.text.strip() == "ok"


def test_healthz_returns_503_when_postgres_unreachable(api_server, configured_db):
    """Closing the connection pool simulates Postgres being unreachable.

    The UI backend's ``GET /spans`` would already 5xx in this state — the
    probe needs to flip readiness so k8s stops sending traffic.
    """
    db.close_pool()
    try:
        resp = httpx.get(f"{_base_url(api_server)}/healthz")
        assert resp.status_code == 503
    finally:
        # Restore the pool so the api_server fixture's teardown is happy.
        db.configure(configured_db)
