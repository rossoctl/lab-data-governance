"""ADR-0003 SQLSTATE→OTLP error classification (issue #8).

:func:`classify_error` maps a psycopg exception to one of four ADR-0003
error classes:

``connection``
    Connection-class SQLSTATE (``08*``), pool exhaustion, or any pre-session
    libpq failure.  The whole batch fails retryable (gRPC UNAVAILABLE / HTTP
    503).

``duplicate``
    ``23505`` on the ``(trace_id, span_id)`` PK — the span is silently
    dropped and the batch succeeds.  Handled as a non-error return value
    from :func:`~write_span.write_span` (``WriteOutcome.DUPLICATE``) rather
    than an exception; this branch is a safety net for any path that catches
    raw psycopg exceptions.

``integrity``
    Other ``23xxx`` integrity violations, plus ``54000`` (program_limit_exceeded),
    ``22001`` (string_data_right_truncation), and ``22023`` (invalid_parameter_value).
    The span is written to ``rejected_spans`` and the batch returns
    OTLP partial-success.

``other``
    Anything else — treated conservatively as retryable.
"""

from __future__ import annotations

from typing import Literal

import psycopg
import psycopg.errors

from data_governance import db

__all__ = ["classify_error"]

# Too-large / malformed SQLSTATE codes that map to integrity (partial-success).
_INTEGRITY_SQLSTATES = frozenset({"54000", "22001", "22023"})

ErrorKind = Literal["connection", "duplicate", "integrity", "other"]


def classify_error(exc: BaseException) -> ErrorKind:
    """Return the ADR-0003 error kind for *exc*.

    Does not raise.  All exceptions — including non-psycopg ones — are
    classified as ``"other"`` unless they match a recognised pattern.
    """
    if db.is_connection_error(exc):
        return "connection"

    if isinstance(exc, psycopg.Error):
        sqlstate: str | None = getattr(exc, "sqlstate", None)

        if sqlstate == "23505":
            return "duplicate"

        if sqlstate is not None:
            if sqlstate.startswith("23"):
                return "integrity"
            if sqlstate in _INTEGRITY_SQLSTATES:
                return "integrity"

    return "other"
