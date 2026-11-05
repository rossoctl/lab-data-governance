"""Prometheus metrics for the alerts processor (issue #104).

Same singleton-plus-``make_registry`` pattern as the sibling processors.
Served on port 9096 (``RISK_ALERTS_METRICS_PORT``), distinct from the
receiver's 9090 through the trace trigger's 9095.

One counter, labelled by outcome:

- ``alert_outcomes_total{action}`` — one increment per trace risk record the
  drain delivered, with ``action`` one of ``created`` / ``duplicate`` /
  ``superseded`` / ``skipped`` (see
  :class:`data_governance.risk.alerts.generate.AlertOutcome`).
"""

from __future__ import annotations

import prometheus_client as prom

__all__ = ["make_registry", "alert_outcomes_total"]

_registry = prom.CollectorRegistry(auto_describe=True)


def _make_metrics(registry: prom.CollectorRegistry) -> prom.Counter:
    return prom.Counter(
        "alert_outcomes_total",
        "Trace risk records the alerts processor delivered, by outcome",
        labelnames=("action",),
        registry=registry,
    )


alert_outcomes_total = _make_metrics(_registry)


def make_registry() -> prom.CollectorRegistry:
    """Replace the module-global metric objects with fresh ones and return
    the registry (one per test, so counters never accumulate across tests)."""
    global _registry  # noqa: PLW0603
    global alert_outcomes_total  # noqa: PLW0603

    new_registry = prom.CollectorRegistry(auto_describe=True)
    _registry = new_registry
    alert_outcomes_total = _make_metrics(new_registry)
    return new_registry
