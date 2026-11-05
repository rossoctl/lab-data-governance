"""Alerts processor (issue #104): a durable-cursor consumer over
``trace_risk_records`` (migration 0023, issue #175) that runs
:func:`data_governance.risk.alerts.generate.generate_for_trace_risk_record`
once per trace risk record, inside the per-item transaction the shared
driver owns. Entry point: ``python -m data_governance.processors.risk.alerts``.
"""
