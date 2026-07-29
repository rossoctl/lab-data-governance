"""DAS configuration — PRD-SENTRY-001 v8 §10 parameters as env-backed constants.

Plain module-level constants read via ``os.environ.get`` at import time,
matching the rest of the repo (no pydantic/pydantic-settings anywhere in this
codebase). Each PRD dotted name (e.g. ``alert.min_risk_level``) maps to a
``RISK_``-prefixed upper-snake env var (``RISK_ALERT_MIN_RISK_LEVEL``).

Only the backbone-relevant §10 parameters are defined here: alerting,
storage retention, the three risk NOTIFY-trigger channels/poll-fallback
intervals, the fan-out batch size, and the metrics refresh interval. The
``api.*.default_page_size``/``default_limit`` parameters belong to the REST
API issues (#109/#111/#113), which own the endpoints that consume them.

Also reserves the three ``processor_state.processor_name`` values issue #98
carves out for the future trigger processors (#99-#102), so those issues
cannot collide on a name.
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
    "LEG_TRIGGER_CHANNEL_NAME",
    "LEG_TRIGGER_POLL_FALLBACK_INTERVAL_SECONDS",
    "CLASSIFICATION_TRIGGER_CHANNEL_NAME",
    "CLASSIFICATION_TRIGGER_POLL_FALLBACK_INTERVAL_SECONDS",
    "TRACE_TRIGGER_CHANNEL_NAME",
    "TRACE_TRIGGER_POLL_FALLBACK_INTERVAL_SECONDS",
    "FANOUT_BATCH_SIZE",
    "METRICS_REFRESH_INTERVAL_SECONDS",
    "PROCESSOR_NAME_LEG_TRIGGER",
    "PROCESSOR_NAME_CLASSIFICATION_TRIGGER",
    "PROCESSOR_NAME_TRACE_TRIGGER",
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

# --- risk.leg_trigger.* ------------------------------------------------------

LEG_TRIGGER_CHANNEL_NAME = _str_env(
    "RISK_LEG_TRIGGER_CHANNEL_NAME", "dg_interaction_legs_inserted"
)
LEG_TRIGGER_POLL_FALLBACK_INTERVAL_SECONDS = _int_env(
    "RISK_LEG_TRIGGER_POLL_FALLBACK_INTERVAL_SECONDS", 10
)

# --- risk.classification_trigger.* -------------------------------------------

CLASSIFICATION_TRIGGER_CHANNEL_NAME = _str_env(
    "RISK_CLASSIFICATION_TRIGGER_CHANNEL_NAME", "dg_classifications_inserted"
)
CLASSIFICATION_TRIGGER_POLL_FALLBACK_INTERVAL_SECONDS = _int_env(
    "RISK_CLASSIFICATION_TRIGGER_POLL_FALLBACK_INTERVAL_SECONDS", 60
)

# --- risk.trace_trigger.* ----------------------------------------------------

TRACE_TRIGGER_CHANNEL_NAME = _str_env(
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
# Carved out now so #99-#102 cannot collide on a processor_name.

PROCESSOR_NAME_LEG_TRIGGER = "risk_interaction_leg_trigger"
PROCESSOR_NAME_CLASSIFICATION_TRIGGER = "risk_classification_trigger"
PROCESSOR_NAME_TRACE_TRIGGER = "risk_trace_trigger"
