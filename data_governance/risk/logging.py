"""Shared logging setup for DAS risk processors (issue #98).

Factors out the ``logging.basicConfig(...)`` call already copy-pasted into
every existing ``__main__.py`` (receiver, P-interactions, P-classification) so
the DAS processors (#99-#102) call one shared function instead of re-pasting
it. Plain text, matching repo convention — no JSON/structured logging despite
what the (stale) implementation notes describe.
"""

from __future__ import annotations

import logging

__all__ = ["configure_logging"]

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger with the repo's standard plain-text format.

    Idempotent under repeat calls (``force=True``) so re-configuring at a new
    level does not accumulate duplicate handlers.
    """
    logging.basicConfig(level=level, format=_FORMAT, force=True)
