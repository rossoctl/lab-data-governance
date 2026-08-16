"""Tests for ``data_governance.risk.config`` (issue #98, PRD §10).

Covers the configuration parameters relevant to the backbone: alerting,
storage retention, the trace-risk NOTIFY-trigger channel/poll fallback, the
fan-out batch size, and the metrics refresh interval. The ``api.*`` pagination
defaults from §10 belong to the REST API issues (#109/#111/#113) and are out
of scope here — this module owns no HTTP route.

The leg-trigger and classification-trigger config was removed: those two
triggers are being replaced by a single new trigger, not yet designed.

Every parameter is a module-level constant read from its own env var at
import time (plain ``os.environ.get``, matching the rest of the repo — no
pydantic-settings), so tests reload the module after patching the environment
to observe a changed value.
"""

from __future__ import annotations

import importlib

import pytest

from data_governance.risk import config as risk_config


def _reload() -> object:
    return importlib.reload(risk_config)


@pytest.fixture(autouse=True)
def _restore_module():
    """Reload back to defaults after each test regardless of outcome."""
    yield
    _reload()


# --- defaults ------------------------------------------------------------------


def test_alert_defaults():
    cfg = _reload()
    assert cfg.ALERT_MIN_RISK_LEVEL == "low"
    assert cfg.ALERT_DEDUP_STRATEGY == ["time_window", "risk_threshold"]
    assert cfg.ALERT_DEDUP_TIME_WINDOW_MINUTES == 5
    assert cfg.ALERT_MAX_DAILY_ALERTS == 1000
    assert cfg.ALERT_VOLUME_WARNING_PCT == 90


def test_storage_defaults():
    cfg = _reload()
    assert cfg.STORAGE_RETENTION_DAYS == 365


def test_trace_trigger_defaults():
    cfg = _reload()
    assert cfg.INTERACTION_RISK_WRITTEN_CHANNEL_NAME == "dg_interaction_risk_written"
    assert cfg.TRACE_TRIGGER_POLL_FALLBACK_INTERVAL_SECONDS == 10


def test_fanout_and_metrics_defaults():
    cfg = _reload()
    assert cfg.FANOUT_BATCH_SIZE == 100
    assert cfg.METRICS_REFRESH_INTERVAL_SECONDS == 300


def test_trace_aggregation_mode_defaults():
    cfg = _reload()
    assert cfg.TRACE_AGGREGATION_RISK_LEVEL_MODE == "severity_max"
    assert cfg.TRACE_AGGREGATION_ENFORCEMENT_TYPE_MODE == "severity_max"


# --- overridability --------------------------------------------------------


def test_int_params_overridable_via_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RISK_ALERT_MAX_DAILY_ALERTS", "42")
    monkeypatch.setenv("RISK_STORAGE_RETENTION_DAYS", "7")
    cfg = _reload()
    assert cfg.ALERT_MAX_DAILY_ALERTS == 42
    assert cfg.STORAGE_RETENTION_DAYS == 7


def test_str_params_overridable_via_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RISK_ALERT_MIN_RISK_LEVEL", "high")
    monkeypatch.setenv("RISK_TRACE_TRIGGER_CHANNEL_NAME", "custom_channel")
    cfg = _reload()
    assert cfg.ALERT_MIN_RISK_LEVEL == "high"
    assert cfg.INTERACTION_RISK_WRITTEN_CHANNEL_NAME == "custom_channel"


def test_list_param_overridable_via_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RISK_ALERT_DEDUP_STRATEGY", "risk_threshold,new_entity")
    cfg = _reload()
    assert cfg.ALERT_DEDUP_STRATEGY == ["risk_threshold", "new_entity"]


# --- corner cases ------------------------------------------------------------


def test_malformed_int_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("RISK_ALERT_MAX_DAILY_ALERTS", "not-a-number")
    cfg = _reload()
    assert cfg.ALERT_MAX_DAILY_ALERTS == 1000
    assert "RISK_ALERT_MAX_DAILY_ALERTS" in capsys.readouterr().err


def test_empty_string_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RISK_ALERT_MIN_RISK_LEVEL", "")
    cfg = _reload()
    assert cfg.ALERT_MIN_RISK_LEVEL == "low"


def test_empty_list_env_yields_empty_list_not_default(
    monkeypatch: pytest.MonkeyPatch,
):
    """An explicit empty override means "no dedup strategies," not "use the
    default list" — distinguishing unset (falls back) from set-to-empty
    (means empty) matters for a strategy list."""
    monkeypatch.setenv("RISK_ALERT_DEDUP_STRATEGY", "")
    cfg = _reload()
    assert cfg.ALERT_DEDUP_STRATEGY == []


# --- reserved processor_state names -----------------------------------------


def test_reserved_trace_trigger_processor_name():
    cfg = _reload()
    assert cfg.PROCESSOR_NAME_TRACE_TRIGGER == "risk_trace_trigger"


# --- opa.* (issue #101) -------------------------------------------------------


def test_opa_defaults():
    cfg = _reload()
    assert cfg.OPA_BASE_URL == "http://opa:8181"
    assert cfg.OPA_DECISION_PATH == "/v1/data/data_governance/policy_decision"
    assert cfg.OPA_TIMEOUT_SECONDS == 5
    assert cfg.OPA_MAX_RETRIES == 2


def test_opa_params_overridable_via_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RISK_OPA_BASE_URL", "http://opa.internal:9999")
    monkeypatch.setenv("RISK_OPA_DECISION_PATH", "/v1/data/custom/decision")
    monkeypatch.setenv("RISK_OPA_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("RISK_OPA_MAX_RETRIES", "5")
    cfg = _reload()
    assert cfg.OPA_BASE_URL == "http://opa.internal:9999"
    assert cfg.OPA_DECISION_PATH == "/v1/data/custom/decision"
    assert cfg.OPA_TIMEOUT_SECONDS == 15
    assert cfg.OPA_MAX_RETRIES == 5


def test_opa_malformed_int_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("RISK_OPA_TIMEOUT_SECONDS", "not-a-number")
    cfg = _reload()
    assert cfg.OPA_TIMEOUT_SECONDS == 5
    assert "RISK_OPA_TIMEOUT_SECONDS" in capsys.readouterr().err


# --- risk.evidence.* (issue #163 / #178) --------------------------------------
# The whitelist itself is #178's; these assert the config plumbing around it,
# which #178 shipped untested at this layer.


def test_internal_url_whitelist_patterns_default_is_empty():
    """Empty by default — #178's deliberate "everything is external unless
    named" posture. A real deployment must set it (see
    deploy/k8s/85-leg-ready.yaml)."""
    cfg = _reload()
    assert cfg.INTERNAL_URL_WHITELIST_PATTERNS == []


def test_internal_url_whitelist_patterns_override(monkeypatch):
    monkeypatch.setenv(
        "RISK_INTERNAL_URL_WHITELIST_PATTERNS", " *.corp.example.com , localhost "
    )
    cfg = _reload()
    assert cfg.INTERNAL_URL_WHITELIST_PATTERNS == ["*.corp.example.com", "localhost"]


def test_internal_url_whitelist_patterns_explicit_empty_means_no_whitelist(monkeypatch):
    monkeypatch.setenv("RISK_INTERNAL_URL_WHITELIST_PATTERNS", "")
    cfg = _reload()
    assert cfg.INTERNAL_URL_WHITELIST_PATTERNS == []
