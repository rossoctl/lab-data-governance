"""P-leg-ready consumer.

A Layer-2 consumer (sibling of ``interactions``, ``classification``, and
``entity_ready``) that drains the ``interaction_legs`` stream by ``seq`` and
delivers each **Interaction leg** to a downstream governance consumer — the risk
processor, the data-lineage processor, and eventually a Policy Decision Point (PDP)
— at the moment it reaches **Leg readiness** (written **and** its payload, if any,
classified; ADR-0027, issue #123).

This is the readiness-gated half of the ADR-0027 leg-readiness work (the
``entity_ready`` consumer, #121, is the ungated half). Unlike ``entity_ready``,
which reuses the shared driver verbatim, this consumer needs a bespoke
contiguous-prefix drain: the readiness predicate is non-monotonic in ``seq`` (a
low-``seq`` leg with an unclassified payload can sit behind a high-``seq`` ready
leg), so it advances only across the leading unbroken run of ready legs and stops at
the first unready one (head-of-line blocking; :mod:`..readiness_cursor`). It still
reuses ``_driver``'s cursor helpers and LISTEN/poll wake machinery.

Since the ADR-0027 reversal (issue #123) each leg carries its own distinct DB-owned
``seq`` (request leg inserted first → lower seq), so a plain single-BIGINT cursor
totally orders the legs and persists directly in ``processor_state`` — request is
delivered before its response purely by seq order.

The wake path is the established stream shape: P-classification (after each
classify) and P-interactions (after a flush that wrote legs) fire a payload-less
``pg_notify('dg_interaction_leg_ready', '')``; the consumer holds a durable
``leg_ready`` cursor in ``processor_state`` and wakes on the notification plus a
periodic poll backstop. A missed or lost notification costs latency only, never
correctness — the poll backstop still delivers, and readiness is re-derived from the
durable tables (``interaction_legs`` ⋈ ``payload_classifications``) on every drain
(ADR-0015).

There is no real downstream consumer yet (risk/lineage/PDP are future work), so this
consumer's "processing" is a thin, real delivery: it hands each ready leg to an
injected observer (the downstream seam) and increments a Prometheus counter. The
drain semantics — readiness gating, exactly-once delivery past a durable cursor,
correct resume across a restart, no stranding — are what this ticket validates.

Run as ``python -m data_governance.processors.leg_ready``.
"""
