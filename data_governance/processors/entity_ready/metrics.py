"""Prometheus metrics for the P-entity-ready consumer.

Mirrors :mod:`data_governance.processors.classification.metrics`: the metric
object is a module-level singleton bound to a shared registry, and tests call
:func:`make_registry` to replace the module globals with fresh objects backed by
a new ``CollectorRegistry`` (preventing cross-test counter accumulation).

Production code never calls :func:`make_registry` — it uses the ``_registry``
created at import time, served by the consumer's :class:`MetricsServer` on port
9093 (``ENTITY_READY_METRICS_PORT``), distinct from the receiver's 9090, the
interactions processor's 9091, and the classification processor's 9092 so a
co-located deployment doesn't collide.

One counter (issue #121):

- ``entities_observed_total`` — one increment per **Entity** the drain loop
  delivers to the downstream governance consumer. This is the delivery signal
  the exactly-once acceptance criterion is asserted against: after a full drain
  it equals the number of distinct entities created, and a restart re-draining
  from the durable cursor does not advance it (no re-delivery).
"""

from __future__ import annotations

import prometheus_client as prom

__all__ = [
    "make_registry",
    "entities_observed_total",
]

# Module-level registry; replaced by make_registry() in tests.
_registry = prom.CollectorRegistry(auto_describe=True)


def _make_metrics(registry: prom.CollectorRegistry) -> prom.Counter:
    # Named with the explicit ``_total`` suffix so the registered name equals the
    # scrape/exposition name (prometheus_client appends ``_total`` to counters),
    # matching the receiver's convention (spans_received_total, …).
    entities_observed_total = prom.Counter(
        "entities_observed_total",
        "Entities the entity-ready consumer delivered to the downstream "
        "governance consumer",
        registry=registry,
    )
    return entities_observed_total


entities_observed_total = _make_metrics(_registry)


def make_registry() -> prom.CollectorRegistry:
    """Replace the module-global metric objects with fresh ones and return the registry.

    Call once per test to get an isolated registry. After the call all
    module-level names in this module point at the new objects, so code should
    reference ``metrics.entities_observed_total`` (not a bound import) to pick up
    the rebind. Server code that accepts the registry as a dependency receives
    the new registry via the kwarg.
    """
    global _registry  # noqa: PLW0603
    global entities_observed_total  # noqa: PLW0603

    new_registry = prom.CollectorRegistry(auto_describe=True)
    _registry = new_registry
    entities_observed_total = _make_metrics(new_registry)
    return new_registry
