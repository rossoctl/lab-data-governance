"""Tests for ``data_governance.risk.health`` (issue #98, PRD §7.8).

This module provides the payload-shaping function only — ``risk_health()`` —
not an HTTP route. The repo already has ``GET /healthz`` (DB-connectivity
probe) serving the UI backend; wiring a DAS-specific ``GET /health`` route
that calls this function is #113's job, which owns the API surface and the
``/health`` vs ``/healthz`` naming discrepancy between the PRD and this repo.

The trigger-channel fields (``trigger_channel_connected``, ``trigger_lag_seconds``)
and ``daily_alert_count`` are stubbed here — no processor exists yet to report
real values — and are documented as such.
"""

from __future__ import annotations

from data_governance import db
from data_governance.risk.health import risk_health


def test_payload_shape_matches_prd(configured_db: str) -> None:
    payload = risk_health()
    for key in (
        "status",
        "db_connected",
        "trigger_channel_connected",
        "trigger_lag_seconds",
        "daily_alert_count",
    ):
        assert key in payload, f"missing key {key!r}"


def test_db_connected_true_against_live_db(configured_db: str) -> None:
    payload = risk_health()
    assert payload["db_connected"] is True
    assert payload["status"] == "ok"


def test_db_connected_false_when_pool_closed(configured_db: str) -> None:
    db.close_pool()
    try:
        payload = risk_health()
        assert payload["db_connected"] is False
        assert payload["status"] != "ok"
    finally:
        db.configure(configured_db)


def test_stubbed_fields_are_documented_placeholders(configured_db: str) -> None:
    """No processor exists yet (issue #98 is the backbone only), so the
    trigger/alert fields are stubbed rather than backed by real state."""
    payload = risk_health()
    assert payload["trigger_channel_connected"] is False
    assert payload["trigger_lag_seconds"] is None
    assert payload["daily_alert_count"] == 0
