"""§3.1 ingest blocklist — span-name patterns dropped at the OTLP socket.

Pattern grammar: each entry is a Python regular expression, matched against
the *whole* span name with :func:`re.fullmatch` (so patterns are fully
anchored — a literal entry does not match a name that merely contains it).
Entries that do not compile as a regex are rejected at import time.

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

# Each entry is a regex matched with re.fullmatch. Literal paths need no
# wildcard; use ``.*`` for "any suffix" and escape regex metacharacters (e.g.
# ``\.``) that should match literally. Reviewed by PR; not runtime-configurable.
_PATTERNS: tuple[str, ...] = (
    # Kubernetes liveness / readiness probe spans. The healthz entry also
    # covers the ' http send'/' http receive' ASGI child spans the Starlette
    # OTel instrumentation emits (and POST probes), via the trailing ``.*``.
    r"(GET|POST) /healthz.*",
    r"GET /readyz",
    r"GET /livez",
    r"/healthz",
    r"/readyz",
    r"/livez",
    # Prometheus /metrics scrape paths
    r"GET /metrics",
    r"/metrics",
    # A2A agent-card discovery polling ('.' escaped so it matches literally)
    r"GET /\.well-known/agent-card\.json",
    r"GET /\.well-known/agent-card\.json http send",
    r"GET /\.well-known/agent-card\.json http receive",
    # Receiver self-spans (if the receiver instruments itself)
    r"otlp_receiver/.*",
)

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    """Compile each entry, rejecting any that is empty or not a valid regex.

    Returns the compiled patterns (parallel to *patterns*) so the module can
    match against pre-compiled objects while still reporting the source string.
    """
    compiled: list[re.Pattern[str]] = []
    for p in patterns:
        if p == "":
            raise ValueError("blocklist pattern is invalid: empty string")
        try:
            compiled.append(re.compile(p))
        except re.error as exc:
            raise ValueError(
                f"blocklist pattern {p!r} is invalid: {exc}"
            ) from exc
    return tuple(compiled)


_COMPILED: tuple[re.Pattern[str], ...] = _validate(_PATTERNS)

# ---------------------------------------------------------------------------
# Match function
# ---------------------------------------------------------------------------


def match(span_name: str) -> str | None:
    """Return the first matching pattern if *span_name* is blocklisted, else ``None``.

    Patterns are tried in list order and matched with :func:`re.fullmatch`.
    The returned value is the pattern *source* string — callers use it as the
    label for ``blocked_span_counts`` and ``spans_blocked_total{pattern}``.
    """
    for pattern, compiled in zip(_PATTERNS, _COMPILED):
        if compiled.fullmatch(span_name):
            return pattern
    return None
