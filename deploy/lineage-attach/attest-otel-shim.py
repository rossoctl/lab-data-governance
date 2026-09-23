#!/usr/bin/env python3
"""Runtime assertions shared by shim builds and live workload status."""

from __future__ import annotations

import os
import sys


def verify_inert() -> None:
    loaded = sorted(module for module in sys.modules if module.startswith("opentelemetry"))
    if loaded:
        raise SystemExit(f"gate off, yet otel loaded: {loaded}")


def verify_propagates() -> None:
    assert "opentelemetry.instrumentation.auto_instrumentation" in sys.modules, (
        "hook did not run"
    )
    assert os.environ.get("OTEL_TRACES_EXPORTER") is not None, "exporter selection not pinned"
    from opentelemetry.propagate import inject
    from opentelemetry.trace import (
        NonRecordingSpan,
        SpanContext,
        TraceFlags,
        set_span_in_context,
    )

    context = set_span_in_context(
        NonRecordingSpan(
            SpanContext(
                trace_id=1,
                span_id=1,
                is_remote=False,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
        )
    )
    carrier: dict[str, str] = {}
    inject(carrier, context=context)
    assert "traceparent" in carrier, f"propagator injects nothing: {carrier!r}"
    assert "rossoctl_turnspan" in sys.modules, "turn-span shim did not load at startup"
    import rossoctl_turnspan

    assert callable(getattr(rossoctl_turnspan, "install", None)), (
        "turn-span shim has no install()"
    )


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode == "inert":
        verify_inert()
    elif mode == "propagates":
        verify_propagates()
    else:
        raise SystemExit("usage: attest-otel-shim.py inert|propagates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
