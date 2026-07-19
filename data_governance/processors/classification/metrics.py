"""Prometheus metrics for the P-classification processor.

Mirrors :mod:`data_governance.processors.interactions.metrics`: all metric
objects are module-level singletons bound to a shared registry, and tests call
:func:`make_registry` to replace the module globals with fresh objects backed by
a new ``CollectorRegistry`` (preventing cross-test counter accumulation).

Production code never calls :func:`make_registry` — it uses the ``_registry``
created at import time, served by the processor's :class:`MetricsServer` on port
9092 (``CLASSIFICATION_METRICS_PORT``), distinct from the receiver's 9090 and
the interactions processor's 9091 so a co-located deployment doesn't collide.

Two counters (issues #77, #81):

- ``payloads_classified_total`` (#77) — one increment per **Payload** the drain
  loop writes a **Classification** for.
- ``projection_fallbacks_total`` (#81) — one increment per **Payload** whose
  **Text projection rule** had no branch for its **Content kind** and fell back
  to serializing the whole ``content`` JSONB (``unknown`` and any unhandled
  kind; CONTEXT.md **Text projection rule**). This is the projection-coverage
  signal: divided by ``payloads_classified_total`` it is the fraction of
  payloads the classifier saw only as best-effort serialized JSONB rather than
  real **Classifiable text**. The real per-kind projection lands in issue #78
  behind the same counter, so this metric is deliberately additive — #78
  refines *when* the fallback fires without renaming or dropping the counter.

The real model slice (issue #78) adds finding/verdict-shaped metrics behind the
same registry.
"""

from __future__ import annotations

import prometheus_client as prom

__all__ = [
    "make_registry",
    "payloads_classified_total",
    "projection_fallbacks_total",
]

# Module-level registry; replaced by make_registry() in tests.
_registry = prom.CollectorRegistry(auto_describe=True)


def _make_metrics(
    registry: prom.CollectorRegistry,
) -> tuple[prom.Counter, prom.Counter]:
    # Named with the explicit ``_total`` suffix so the registered name equals
    # the scrape/exposition name (prometheus_client appends ``_total`` to
    # counters), matching the receiver's convention (spans_received_total, …).
    payloads_classified_total = prom.Counter(
        "payloads_classified_total",
        "Payloads for which P-classification wrote a Classification row",
        registry=registry,
    )
    projection_fallbacks_total = prom.Counter(
        "projection_fallbacks_total",
        "Payloads whose Text projection rule fell back to whole-JSONB "
        "serialization (Content kind with no projection branch — unknown or "
        "unhandled); the projection-coverage gap",
        registry=registry,
    )
    return (payloads_classified_total, projection_fallbacks_total)


(payloads_classified_total, projection_fallbacks_total) = _make_metrics(_registry)


def make_registry() -> prom.CollectorRegistry:
    """Replace the module-global metric objects with fresh ones and return the registry.

    Call once per test to get an isolated registry. After the call all
    module-level names in this module point at the new objects, so code should
    reference ``metrics.payloads_classified_total`` (not a bound import) to pick
    up the rebind. Server code that accepts the registry as a dependency
    receives the new registry via the kwarg.
    """
    global _registry  # noqa: PLW0603
    global payloads_classified_total  # noqa: PLW0603
    global projection_fallbacks_total  # noqa: PLW0603

    new_registry = prom.CollectorRegistry(auto_describe=True)
    _registry = new_registry
    (payloads_classified_total, projection_fallbacks_total) = _make_metrics(
        new_registry
    )
    return new_registry
