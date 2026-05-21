"""Prometheus metrics for the OTLP receiver (issue #8 / PROJECT.md §5.1).

All metric objects are module-level singletons bound to a shared registry.
Tests call :func:`make_registry` to replace the module globals with fresh
objects backed by a new ``CollectorRegistry``, preventing cross-test
counter accumulation.

Production code never calls :func:`make_registry` — it uses the
``_registry`` created at import time.
"""

from __future__ import annotations

import prometheus_client as prom

__all__ = [
    "make_registry",
    "db_errors_total",
    "span_insert_duration_seconds",
    "span_row_bytes",
    "spans_blocked_total",
    "spans_duplicate_total",
    "spans_finalized_total",
    "spans_inserted_total",
    "spans_received_total",
]

# Module-level registry; replaced by make_registry() in tests.
_registry = prom.CollectorRegistry(auto_describe=True)


def _make_metrics(
    registry: prom.CollectorRegistry,
) -> tuple[
    prom.Counter,
    prom.Counter,
    prom.Counter,
    prom.Counter,
    prom.Counter,
    prom.Histogram,
    prom.Histogram,
    prom.Counter,
]:
    spans_received = prom.Counter(
        "spans_received_total",
        "OTLP spans received per transport (grpc / http)",
        labelnames=["transport"],
        registry=registry,
    )
    spans_blocked = prom.Counter(
        "spans_blocked_total",
        "Spans dropped by the §3.1 blocklist",
        labelnames=["pattern"],
        registry=registry,
    )
    spans_inserted = prom.Counter(
        "spans_inserted_total",
        "Spans successfully written via the INSERT path",
        registry=registry,
    )
    spans_finalized = prom.Counter(
        "spans_finalized_total",
        "Spans updated via the finalization path",
        registry=registry,
    )
    spans_duplicate = prom.Counter(
        "spans_duplicate_total",
        "ON CONFLICT DO NOTHING hits (PK duplicate)",
        registry=registry,
    )
    insert_duration = prom.Histogram(
        "span_insert_duration_seconds",
        "Per-span write latency (INSERT and UPDATE)",
        registry=registry,
    )
    row_bytes = prom.Histogram(
        "span_row_bytes",
        "Serialised row size on write",
        buckets=(256, 512, 1024, 4096, 16384, 65536, 262144, 1048576),
        registry=registry,
    )
    db_errors = prom.Counter(
        "db_errors_total",
        "DB errors by class (connection / integrity / other); PK conflicts excluded",
        labelnames=["kind"],
        registry=registry,
    )
    return (
        spans_received,
        spans_blocked,
        spans_inserted,
        spans_finalized,
        spans_duplicate,
        insert_duration,
        row_bytes,
        db_errors,
    )


(
    spans_received_total,
    spans_blocked_total,
    spans_inserted_total,
    spans_finalized_total,
    spans_duplicate_total,
    span_insert_duration_seconds,
    span_row_bytes,
    db_errors_total,
) = _make_metrics(_registry)


def make_registry() -> prom.CollectorRegistry:
    """Replace the module-global metric objects with fresh ones and return the registry.

    Call once per test (or test class) to get an isolated registry.  After
    the call all module-level names in this module point at the new objects,
    so any code that does ``from metrics import spans_inserted_total`` will
    need to re-import or use ``metrics.spans_inserted_total`` to pick up the
    new object.  Server code that accepts the registry as a dependency will
    receive the new registry via the kwarg.
    """
    global _registry  # noqa: PLW0603
    global spans_received_total  # noqa: PLW0603
    global spans_blocked_total  # noqa: PLW0603
    global spans_inserted_total  # noqa: PLW0603
    global spans_finalized_total  # noqa: PLW0603
    global spans_duplicate_total  # noqa: PLW0603
    global span_insert_duration_seconds  # noqa: PLW0603
    global span_row_bytes  # noqa: PLW0603
    global db_errors_total  # noqa: PLW0603

    new_registry = prom.CollectorRegistry(auto_describe=True)
    _registry = new_registry
    (
        spans_received_total,
        spans_blocked_total,
        spans_inserted_total,
        spans_finalized_total,
        spans_duplicate_total,
        span_insert_duration_seconds,
        span_row_bytes,
        db_errors_total,
    ) = _make_metrics(new_registry)
    return new_registry
