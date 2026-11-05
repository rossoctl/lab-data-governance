"""Prometheus metrics for the trace-risk-trigger processor (#102/#164).

Mirrors :mod:`data_governance.processors.leg_ready.metrics`: the metric object
is a module-level singleton bound to a shared registry, and tests call
:func:`make_registry` to replace the module globals with fresh objects backed
by a new ``CollectorRegistry`` (preventing cross-test counter accumulation).

Production code never calls :func:`make_registry` — it uses the ``_registry``
created at import time, served by the processor's ``MetricsServer`` on port
9095 (``RISK_TRACE_TRIGGER_METRICS_PORT``), distinct from the receiver's
9090, the interactions processor's 9091, the classification processor's 9092,
the entity-ready consumer's 9093, and the leg-ready consumer's 9094 so a
co-located deployment doesn't collide.

One counter:

- ``trace_recomputes_total`` — one increment per interaction risk record the
  drain delivered to a trace recompute (AC-DAS-008: after a full drain it
  equals the number of records past the cursor; many are idempotent no-op
  recomputes, which still count — the counter tracks deliveries, not writes).
"""

from __future__ import annotations

import prometheus_client as prom

__all__ = [
    "make_registry",
    "trace_recomputes_total",
]

# Module-level registry; replaced by make_registry() in tests.
_registry = prom.CollectorRegistry(auto_describe=True)


def _make_metrics(registry: prom.CollectorRegistry) -> prom.Counter:
    trace_recomputes_total = prom.Counter(
        "trace_recomputes_total",
        "Interaction risk records the trace-trigger processor delivered to a "
        "trace risk recompute",
        registry=registry,
    )
    return trace_recomputes_total


trace_recomputes_total = _make_metrics(_registry)


def make_registry() -> prom.CollectorRegistry:
    """Replace the module-global metric objects with fresh ones and return the
    registry. Call once per test to get an isolated registry; code references
    ``metrics.trace_recomputes_total`` (not a bound import) to pick up the
    rebind."""
    global _registry  # noqa: PLW0603
    global trace_recomputes_total  # noqa: PLW0603

    new_registry = prom.CollectorRegistry(auto_describe=True)
    _registry = new_registry
    trace_recomputes_total = _make_metrics(new_registry)
    return new_registry
