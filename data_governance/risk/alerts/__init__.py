"""Alert generation for the DAS component (issue #104).

Layout follows this package's existing shape (``risk/engine``,
``risk/metrics``, ``risk/rules``) rather than the issue text's
``risk/das/alerts`` — there is no ``das/`` directory in the repo.

- :mod:`generate` — one trace risk record in, one alert outcome out:
  create / count a duplicate / supersede, per FR-DAS-040..043.

The consumer that drives :func:`generate.generate_for_trace_risk_record` is
:mod:`data_governance.processors.risk.alerts` — a durable-cursor drain over
``trace_risk_records`` (migration 0023, issue #175), so an alert is only
ever considered after the trace risk write it depends on is durable
(NFR-DAS-003).
"""
