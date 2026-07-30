"""Prometheus metrics for the P-leg-ready consumer (issue #123, ADR-0027).

Mirrors :mod:`data_governance.processors.entity_ready.metrics`: the metric object
is a module-level singleton bound to a shared registry, and tests call
:func:`make_registry` to replace the module globals with fresh objects backed by a
new ``CollectorRegistry`` (preventing cross-test counter accumulation).

Production code never calls :func:`make_registry` — it uses the ``_registry``
created at import time, served by the consumer's :class:`MetricsServer` on port
9094 (``LEG_READY_METRICS_PORT``), distinct from the receiver's 9090, the
interactions processor's 9091, the classification processor's 9092, and the
entity-ready processor's 9093 so a co-located deployment doesn't collide.

One counter (issue #123):

- ``legs_observed_total`` — one increment per ready **Interaction leg** the drain
  loop delivers to the downstream governance consumer. This is the delivery signal
  the exactly-once acceptance criterion is asserted against: after a full drain it
  equals the number of ready legs past the cursor, and a restart re-draining from
  the durable cursor does not advance it (no re-delivery).
"""

from __future__ import annotations

import prometheus_client as prom

__all__ = [
    "make_registry",
    "legs_observed_total",
]

# Module-level registry; replaced by make_registry() in tests.
_registry = prom.CollectorRegistry(auto_describe=True)


def _make_metrics(registry: prom.CollectorRegistry) -> prom.Counter:
    # Named with the explicit ``_total`` suffix so the registered name equals the
    # scrape/exposition name (prometheus_client appends ``_total`` to counters),
    # matching the sibling consumers' convention.
    legs_observed_total = prom.Counter(
        "legs_observed_total",
        "Ready interaction legs the leg-ready consumer delivered to the downstream "
        "governance consumer",
        registry=registry,
    )
    return legs_observed_total


legs_observed_total = _make_metrics(_registry)


def make_registry() -> prom.CollectorRegistry:
    """Replace the module-global metric objects with fresh ones and return the registry.

    Call once per test to get an isolated registry. After the call all module-level
    names in this module point at the new objects, so code should reference
    ``metrics.legs_observed_total`` (not a bound import) to pick up the rebind. Server
    code that accepts the registry as a dependency receives the new registry via the
    kwarg.
    """
    global _registry  # noqa: PLW0603
    global legs_observed_total  # noqa: PLW0603

    new_registry = prom.CollectorRegistry(auto_describe=True)
    _registry = new_registry
    legs_observed_total = _make_metrics(new_registry)
    return new_registry
