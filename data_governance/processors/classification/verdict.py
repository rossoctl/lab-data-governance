"""The P-classification **Classification** verdict — the tracer-bullet stub.

Isolated here as the single seam issue #78 replaces: right now
:func:`classify` returns a trivial ``PUBLIC`` / zero-**Findings** verdict for
every **Payload**, ignoring the payload's bytes entirely. Issue #78 swaps in the
real path — project the payload's JSONB ``content`` into its **Classifiable
text** (the **Text projection rule**), run the NER model to detect sensitive
regions as **Findings**, and aggregate those up to the document-level verdict —
behind this same function signature and the same write path in
:mod:`.driver`. Keeping the stub obvious and in one place is deliberate
(issue #77): the rest of the slice (schema, cursor, write, API) is real, only
the verdict is a placeholder.

The ``model_version`` is a monotonic integer (ADR-0024) stamped on every row so
verdicts from different model/config generations are comparable. The stub is
generation 1; when the real model lands its image tag maps to a higher
``model_version`` (ADR-0023).
"""

from __future__ import annotations

import dataclasses

# The stub is model generation 1 (ADR-0024). The real NER model (issue #78) will
# bump this — its image tag maps to the model_version it writes (ADR-0023).
STUB_MODEL_VERSION = 1


@dataclasses.dataclass(frozen=True)
class Verdict:
    """The document-level **Classification** verdict over one **Payload**, plus
    its **Findings**. Mirrors the ``payload_classifications`` row shape
    (CONTEXT.md **Classification**); the driver writes it verbatim.

    ``regulatory_tags`` and ``findings`` are collections owned by the verdict;
    ``findings`` is the list of NER-detected sensitive items (empty for the
    stub, and for any genuinely clean payload — a clean verdict is a real
    zero-**Findings** row, never a null; ADR-0024).
    """

    sensitivity_level: str
    regulatory_tags: list[str]
    contains_identity_bundle: bool
    is_personalized: bool
    primary_domain: str | None
    findings: list[dict]
    model_version: int


def classify(content_hash: str, content_kind: str, content: object) -> Verdict:
    """Return the **Classification** verdict for one **Payload**.

    TRACER-BULLET STUB (issue #77): ignores the payload's bytes and returns a
    trivial clean verdict — ``PUBLIC``, no regulatory tags, no identity bundle,
    not personalized, no primary domain, zero **Findings**, model generation 1.
    Every payload is classified uniformly (no per-kind skipping), so this is a
    real ``PUBLIC`` verdict, never a signal to skip the write (ADR-0024).

    The arguments are the full payload shape the real classifier (issue #78)
    needs — content_hash, its **Content kind**, and the JSONB ``content`` it
    will project into **Classifiable text** — accepted now so the seam is the
    verdict, not the call site.
    """
    return Verdict(
        sensitivity_level="PUBLIC",
        regulatory_tags=[],
        contains_identity_bundle=False,
        is_personalized=False,
        primary_domain=None,
        findings=[],
        model_version=STUB_MODEL_VERSION,
    )
