"""P-classification processor.

A Layer-2 processor (sibling of ``interactions``) that consumes the
``interaction_payloads`` stream by ``seq`` and derives each **Payload**'s
**Classification** — the data-governance sensitivity verdict — writing one
write-once row per payload into ``payload_classifications`` (CONTEXT.md,
ADR-0024).

The tracer-bullet increment (issue #77) wrote a *trivial stub* verdict; issue #78
replaced the stub with the real classification path, behind the same write path:

- :mod:`.projection` — the **Text projection rule**: project a **Payload**'s JSONB
  ``content`` into its **Classifiable text**, one branch per **Content kind**.
- :mod:`.detector` — the narrow text-in/findings-out **Detector** seam, with a
  no-op :class:`~.detector.NullDetector` default.
- :mod:`.logic` — the classification logic ported from the reference batch tool
  (``classification/process_entities_enhanced.py``), parity-tested against it:
  **Findings** + a document-level summary out.
- :mod:`.verdict` — wires the three together into the driver's :class:`Verdict`.

The NER model is not wired until issue #79: detection defaults to the no-op
:class:`~.detector.NullDetector`, so a payload with no sensitive text is a real
``PUBLIC`` / zero-**Findings** verdict (never a null; ADR-0024). Issue #79 injects
the in-process fine-tuned model as the ``detector`` — changing nothing else — and
ships it in a separate image (ADR-0022/0023). ``model_version`` stays 1 until the
model lands.

Run as ``python -m data_governance.processors.classification``.
"""
