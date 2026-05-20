"""End-to-end OTLP-in / spans-out test harness (issue #5).

The harness wires up, once per test session, a real Postgres + receiver in
a known state and exposes per-test fixtures for sending OTLP requests and
inspecting the resulting rows in ``spans``. Subsequent slices that need to
assert "OTLP request in, correct rows out" reuse this harness rather than
re-implementing the wiring (see PROJECT.md "Testing Decisions" §4).

The harness is registered as a pytest plugin from the top-level
``tests/conftest.py`` (``pytest_plugins = ["tests.harness.plugin",
"tests.harness.fixtures"]``). Tests pull in fixtures by name —
``otlp_harness``, ``otlp_client``, ``span_rows`` — without importing
anything from this package.

To type-annotate fixture parameters, import the public types directly
from :mod:`tests.harness.fixtures`:

    from tests.harness.fixtures import OtlpHarness

This package's ``__init__`` deliberately does **not** re-export those
types. Eagerly importing :mod:`tests.harness.fixtures` here would cause
pytest's assertion-rewriter to warn ("Module already imported so cannot
be rewritten") when the plugin is later loaded.

Layer 1 ``db`` unit tests and Layer 2 ``get_spans`` unit tests deliberately
stay separate and do not depend on this harness.
"""

from __future__ import annotations
