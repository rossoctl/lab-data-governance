"""Tests for ``data_governance.risk.logging`` (issue #98).

Factors out the ``logging.basicConfig(...)`` call already copy-pasted into
every existing ``__main__.py`` (plain-text format, no JSON) so the DAS
processors (#99-#102) call one shared function instead of re-pasting it.
"""

from __future__ import annotations

import logging

from data_governance.risk.logging import configure_logging


def test_configure_logging_installs_expected_format(caplog: object) -> None:
    configure_logging("INFO")
    root = logging.getLogger()
    assert root.handlers, "expected at least one handler installed"
    fmt = root.handlers[0].formatter._fmt  # type: ignore[union-attr]
    assert fmt == "%(asctime)s %(levelname)s %(name)s %(message)s"


def test_configure_logging_respects_level() -> None:
    configure_logging("WARNING")
    assert logging.getLogger().level == logging.WARNING
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_is_idempotent_under_repeat_calls() -> None:
    configure_logging("INFO")
    n_before = len(logging.getLogger().handlers)
    configure_logging("INFO")
    n_after = len(logging.getLogger().handlers)
    assert n_after == n_before
