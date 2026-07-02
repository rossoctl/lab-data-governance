"""Prometheus metrics for the P-interactions processor.

Mirrors :mod:`data_governance.processors.otlp_receiver.metrics`: all metric
objects are module-level singletons bound to a shared registry, and tests call
:func:`make_registry` to replace the module globals with fresh objects backed by
a new ``CollectorRegistry`` (preventing cross-test counter accumulation).

Production code never calls :func:`make_registry` — it uses the ``_registry``
created at import time, served by the processor's :class:`MetricsServer`.

This slice (#73) exposes a single counter, ``finalization_observed_total``: the
finalization tripwire. The processor's lineage horizon currently uses ``seq``
for both the cursor and the horizon (slice #70), which is faithful only while
every span satisfies ``seq == arrival_seq`` (ADR-0004: ``seq`` advances on
out-of-arrival-order finalization while ``arrival_seq`` is stable). The first
span observed with ``seq != arrival_seq`` is the signal that the deferred
two-column horizon split (cursor = ``seq``, horizon = ``arrival_seq``) must be
implemented; the driver increments this counter on every such span.
"""

from __future__ import annotations

import prometheus_client as prom

__all__ = [
    "make_registry",
    "finalization_observed_total",
]

# Module-level registry; replaced by make_registry() in tests.
_registry = prom.CollectorRegistry(auto_describe=True)


def _make_metrics(
    registry: prom.CollectorRegistry,
) -> tuple[prom.Counter]:
    # Named with the explicit ``_total`` suffix so the registered name equals
    # the scrape/exposition name (prometheus_client appends ``_total`` to
    # counters), matching the receiver's convention (spans_received_total, …).
    finalization_observed_total = prom.Counter(
        "finalization_observed_total",
        "Spans observed with seq != arrival_seq (finalization out of arrival "
        "order; signals the deferred two-column horizon work, issue #73)",
        registry=registry,
    )
    return (finalization_observed_total,)


(finalization_observed_total,) = _make_metrics(_registry)


def make_registry() -> prom.CollectorRegistry:
    """Replace the module-global metric objects with fresh ones and return the registry.

    Call once per test (or test class) to get an isolated registry. After the
    call all module-level names in this module point at the new objects, so any
    code that does ``from metrics import finalization_observed_total`` will need
    to re-import or use ``metrics.finalization_observed_total`` to pick up the
    new object (the driver does the latter). Server code that accepts the
    registry as a dependency receives the new registry via the kwarg.
    """
    global _registry  # noqa: PLW0603
    global finalization_observed_total  # noqa: PLW0603

    new_registry = prom.CollectorRegistry(auto_describe=True)
    _registry = new_registry
    (finalization_observed_total,) = _make_metrics(new_registry)
    return new_registry
