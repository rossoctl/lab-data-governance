"""DAS configuration — PRD-SENTRY-001 v8 §10 parameters as env-backed constants.

Plain module-level constants read via ``os.environ.get`` at import time,
matching the rest of the repo (no pydantic/pydantic-settings anywhere in this
codebase). Each PRD dotted name (e.g. ``alert.min_risk_level``) maps to a
``RISK_``-prefixed upper-snake env var (``RISK_ALERT_MIN_RISK_LEVEL``).

Only the backbone-relevant §10 parameters are defined here: alerting,
storage retention, the trace-risk NOTIFY-trigger channel/poll-fallback
interval, the fan-out batch size, and the metrics refresh interval. The
remaining ``api.*.default_limit`` parameters belong to whichever issue
implements the endpoint that consumes them; issue #113 (rule catalog) owns
``API_RISK_RULES_DEFAULT_LIMIT``/``API_RISK_RULES_MAX_LIMIT`` below.

The leg-trigger and classification-trigger config (channel names, poll
fallbacks, reserved processor names for #99/#100) has been removed: those two
triggers are being replaced by a single new trigger, not yet designed, so
there is nothing to configure yet.
"""

from __future__ import annotations

import os
import sys

__all__ = [
    "ALERT_MIN_RISK_LEVEL",
    "ALERT_DEDUP_STRATEGY",
    "ALERT_DEDUP_TIME_WINDOW_MINUTES",
    "ALERT_MAX_DAILY_ALERTS",
    "ALERT_VOLUME_WARNING_PCT",
    "STORAGE_RETENTION_DAYS",
    "INTERACTION_RISK_WRITTEN_CHANNEL_NAME",
    "TRACE_TRIGGER_POLL_FALLBACK_INTERVAL_SECONDS",
    "FANOUT_BATCH_SIZE",
    "METRICS_REFRESH_INTERVAL_SECONDS",
    "PROCESSOR_NAME_TRACE_TRIGGER",
    "TRACE_AGGREGATION_RISK_LEVEL_MODE",
    "TRACE_AGGREGATION_ENFORCEMENT_TYPE_MODE",
    "OPA_BASE_URL",
    "OPA_DECISION_PATH",
    "OPA_TIMEOUT_SECONDS",
    "OPA_MAX_RETRIES",
    "API_RISK_RULES_DEFAULT_LIMIT",
    "API_RISK_RULES_MAX_LIMIT",
]


def _str_env(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw if raw else default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        sys.stderr.write(f"{name} is not a valid integer: {raw!r}\n")
        return default


def _list_env(name: str, default: list[str]) -> list[str]:
    """Comma-separated list override.

    Unset -> *default*. Explicitly set to an empty string -> ``[]`` — an
    operator writing ``RISK_ALERT_DEDUP_STRATEGY=`` means "no dedup
    strategies," not "use the default list."
    """
    raw = os.environ.get(name)
    if raw is None:
        return list(default)
    if raw == "":
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- alert.* -------------------------------------------------------------

ALERT_MIN_RISK_LEVEL = _str_env("RISK_ALERT_MIN_RISK_LEVEL", "low")
ALERT_DEDUP_STRATEGY = _list_env(
    "RISK_ALERT_DEDUP_STRATEGY", ["time_window", "risk_threshold"]
)
ALERT_DEDUP_TIME_WINDOW_MINUTES = _int_env("RISK_ALERT_DEDUP_TIME_WINDOW_MINUTES", 5)
ALERT_MAX_DAILY_ALERTS = _int_env("RISK_ALERT_MAX_DAILY_ALERTS", 1000)
ALERT_VOLUME_WARNING_PCT = _int_env("RISK_ALERT_VOLUME_WARNING_PCT", 90)

# --- storage.* -------------------------------------------------------------

STORAGE_RETENTION_DAYS = _int_env("RISK_STORAGE_RETENTION_DAYS", 365)

# --- risk.trace_trigger.* ----------------------------------------------------

# Channel the dg_interaction_risk_written trigger (migration 0010) notifies
# on; the future Trace Risk Processor (#102) listens here.
INTERACTION_RISK_WRITTEN_CHANNEL_NAME = _str_env(
    "RISK_TRACE_TRIGGER_CHANNEL_NAME", "dg_interaction_risk_written"
)
TRACE_TRIGGER_POLL_FALLBACK_INTERVAL_SECONDS = _int_env(
    "RISK_TRACE_TRIGGER_POLL_FALLBACK_INTERVAL_SECONDS", 10
)

# --- risk.fanout.* -----------------------------------------------------------

FANOUT_BATCH_SIZE = _int_env("RISK_FANOUT_BATCH_SIZE", 100)

# --- metrics.* -------------------------------------------------------------

METRICS_REFRESH_INTERVAL_SECONDS = _int_env("RISK_METRICS_REFRESH_INTERVAL_SECONDS", 300)

# --- reserved processor_state.processor_name values (issue #98) -------------
# Carved out now so #102 cannot collide on a processor_name.

PROCESSOR_NAME_TRACE_TRIGGER = "risk_trace_trigger"

# --- risk.trace_aggregation.* (issue #102) -----------------------------------
# How interaction-level risk/enforcement values are rolled up into a trace
# risk record (FR-DAS-021). Named "trace_aggregation" (not just "aggregation")
# because this is specifically the trace-level rollup — other aggregations
# (e.g. entity-level, in a future ARC issue) would get their own
# ``*_AGGREGATION_*`` pair rather than sharing these. "severity_max" (highest
# risk_level / strictest enforcement_type across the trace's current
# interaction risk records) is the only mode implemented today; additional
# modes (e.g. weighted compounding) are a documented future extension, not
# built here.

TRACE_AGGREGATION_RISK_LEVEL_MODE = _str_env(
    "RISK_TRACE_AGGREGATION_RISK_LEVEL_MODE", "severity_max"
)
TRACE_AGGREGATION_ENFORCEMENT_TYPE_MODE = _str_env(
    "RISK_TRACE_AGGREGATION_ENFORCEMENT_TYPE_MODE", "severity_max"
)

# --- opa.* (issue #101) -------------------------------------------------------
# No OPA deploy manifest exists in deploy/k8s/ yet, so OPA_BASE_URL's default
# is a placeholder host, not a verified deployment address.

OPA_BASE_URL = _str_env("RISK_OPA_BASE_URL", "http://opa:8181")
OPA_DECISION_PATH = _str_env(
    "RISK_OPA_DECISION_PATH", "/v1/data/data_governance/policy_decision"
)
OPA_TIMEOUT_SECONDS = _int_env("RISK_OPA_TIMEOUT_SECONDS", 5)
OPA_MAX_RETRIES = _int_env("RISK_OPA_MAX_RETRIES", 2)

# --- api.risk_rules.* (issue #113) ------------------------------------------

API_RISK_RULES_DEFAULT_LIMIT = _int_env("RISK_API_RISK_RULES_DEFAULT_LIMIT", 50)
API_RISK_RULES_MAX_LIMIT = _int_env("RISK_API_RISK_RULES_MAX_LIMIT", 200)
