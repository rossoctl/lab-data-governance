"""§3.1 ingest blocklist — span-name patterns dropped at the OTLP socket.

Pattern grammar: exact strings or ``prefix*`` globs — nothing else. Mixed
grammar in a single entry (e.g. ``pre*fix``) is rejected at import time.

Blocked spans are silently dropped before any ``spans`` write; they are
counted in ``blocked_span_counts`` and in the ``spans_blocked_total``
Prometheus counter, but never appear in ``rejected_spans`` or in any
downstream query.
"""

from __future__ import annotations

import re

__all__ = ["match"]

# ---------------------------------------------------------------------------
# Pattern list
# ---------------------------------------------------------------------------

# Each entry is an exact span name OR a ``prefix*`` glob (star only at end).
# Reviewed by PR; not runtime-configurable.
_PATTERNS: tuple[str, ...] = (
    # Kubernetes liveness / readiness probe spans
    "GET /healthz",
    "GET /readyz",
    "GET /livez",
    "/healthz",
    "/readyz",
    "/livez",
    # Prometheus /metrics scrape paths
    "GET /metrics",
    "/metrics",
    # Receiver self-spans (if the receiver instruments itself)
    "otlp_receiver/*",
)

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_VALID_PATTERN = re.compile(r"^[^*]+\*?$")


def _validate(patterns: tuple[str, ...]) -> None:
    """Reject any entry that is not a valid exact string or ``prefix*`` glob."""
    for p in patterns:
        if not _VALID_PATTERN.match(p):
            raise ValueError(
                f"blocklist pattern {p!r} is invalid: "
                "only exact strings and 'prefix*' globs are allowed"
            )


_validate(_PATTERNS)

# ---------------------------------------------------------------------------
# Match function
# ---------------------------------------------------------------------------


def match(span_name: str) -> str | None:
    """Return the first matching pattern if *span_name* is blocklisted, else ``None``.

    Tries exact patterns first, then prefix globs. The returned value is
    the pattern string itself — callers use it as the label for
    ``blocked_span_counts`` and ``spans_blocked_total{pattern}``.
    """
    for pattern in _PATTERNS:
        if pattern.endswith("*"):
            if span_name.startswith(pattern[:-1]):
                return pattern
        else:
            if span_name == pattern:
                return pattern
    return None
