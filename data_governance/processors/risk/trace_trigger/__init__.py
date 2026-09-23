"""Trace risk trigger processor (issues #102/#164).

A Layer-2 DAS consumer that drains the ``interaction_risk_records`` stream by
``seq`` (migration 0018) and recomputes the trace risk rollup for each new
interaction risk version's ``trace_id`` — the wiring PR #172 deferred until
the #164 cursoring landed.

The wake path is the established stream shape: migration 0016's
statement-level trigger fires a payload-less
``pg_notify('dg_interaction_risk_written', '')`` after every insert; this
consumer holds a durable ``risk_trace_trigger`` cursor in ``processor_state``
and wakes on the notification plus a periodic poll backstop
(``risk.trace_trigger.poll_fallback_interval_seconds``, AC-DAS-018). A missed
notification costs latency only, never correctness.

This processor is the ONLY caller of
:func:`data_governance.risk.engine.trace_compute.compute_trace_risk` in the
pipeline — the interaction risk write path never invokes it inline
(AC-DAS-008a); the two communicate exclusively through the table + channel,
mirroring how ``processors/interactions`` and ``processors/classification``
are separate processes over the same Postgres instance.

Run as ``python -m data_governance.processors.risk.trace_trigger``.
"""
