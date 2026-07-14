"""P-classification processor.

A Layer-2 processor (sibling of ``interactions``) that consumes the
``interaction_payloads`` stream by ``seq`` and derives each **Payload**'s
**Classification** — the data-governance sensitivity verdict — writing one
write-once row per payload into ``payload_classifications`` (CONTEXT.md,
ADR-0024).

This tracer-bullet increment (issue #77) writes a *trivial stub* verdict —
``sensitivity_level='PUBLIC'``, zero **Findings**, ``model_version=1`` — with no
NER model and no real text projection. It exists to prove the end-to-end path
(schema -> cursor -> processor -> write -> API) before any model lands; issue
#78 swaps real NER + a text-projection rule in behind the same write path, and
the model ships in a separate image (ADR-0022/0023) — this stub ships in the
shared receiver/UI/interactions image.

Run as ``python -m data_governance.processors.classification``.
"""
