"""P-entity-ready consumer.

A Layer-2 consumer (sibling of ``interactions`` and ``classification``) that
consumes the ``entities`` stream by ``seq`` and delivers each newly-created
**Entity** to a downstream governance consumer — the risk processor, the
data-lineage processor, and eventually a Policy Decision Point (PDP) — so it
reliably learns when a new entity exists (ADR-0027, issue #121).

This is the clean, independent half of the ADR-0027 leg-readiness work. It
validates the notify → cursor-drain → durable-cursor → restart shape before the
harder readiness-gated interaction-leg path (``dg_interaction_leg_ready``) builds
on it. Because ``dg_entity_ready`` is **first-detection only** — an entity's
identity is set once at creation and, for the current source, never mutated (the
``ON CONFLICT (natural_key) DO UPDATE`` write path only rewrites identical
values) — this stream has **no readiness gate** and reuses the shared driver
(:mod:`data_governance.processors._driver`) **verbatim**. It is a direct parallel
of the P-classification consumer over the ``interaction_payloads`` stream.

The wake path is the established stream shape: the ``dg_entities_notify`` trigger
(migration 0010) fires a payload-less ``pg_notify('dg_entity_ready', '')`` on
insert, the consumer holds a durable ``entity_ready`` cursor in
``processor_state``, and it wakes on the notification plus a periodic poll
backstop. A missed or lost notification costs latency only, never correctness —
the poll backstop still delivers the entity (ADR-0015).

There is no real downstream consumer yet (the risk/lineage/PDP processors are
future work), so this consumer's "processing" is a thin, real delivery: it hands
each entity to an injected observer (the downstream seam) and increments a
Prometheus counter. The drain semantics — exactly-once delivery past a durable
cursor, correct resume across a restart — are what this ticket validates.

Run as ``python -m data_governance.processors.entity_ready``.
"""
