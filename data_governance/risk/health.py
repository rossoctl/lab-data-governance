"""DAS health payload — PRD-SENTRY-001 v8 §7.8 shape (issue #98).

Provides the payload-shaping function only, ``risk_health()`` — not an HTTP
route. The repo already has ``GET /healthz`` (``data_governance/api``) doing
the DB-connectivity probe this issue's acceptance criteria describe; adding a
second, differently-shaped route here would duplicate it. Wiring a
DAS-specific ``GET /health`` route that calls this function, and resolving
the ``/health`` (PRD) vs ``/healthz`` (repo) naming discrepancy, is #113's
job — it owns the API surface.

``trigger_channel_connected``, ``trigger_lag_seconds``, and
``daily_alert_count`` are stubbed: no risk processor or alert generator exists
yet (#98 is the backbone only), so there is nothing real to report. They will
be wired up as #99-#104 land.
"""

from __future__ import annotations

from data_governance import db

__all__ = ["risk_health"]


def _postgres_reachable() -> bool:
    try:
        with db.transaction() as tx:
            tx.execute("SELECT 1")
    except Exception:  # noqa: BLE001 — any failure means "not reachable"
        return False
    return True


def risk_health() -> dict[str, object]:
    """Return the PRD §7.8 health payload shape.

    ``status`` is ``"ok"`` only when Postgres is reachable; stubbed fields do
    not currently affect it (there is no live trigger/alert state to degrade
    on yet).
    """
    db_connected = _postgres_reachable()
    return {
        "status": "ok" if db_connected else "degraded",
        "db_connected": db_connected,
        "trigger_channel_connected": False,
        "trigger_lag_seconds": None,
        "daily_alert_count": 0,
    }
