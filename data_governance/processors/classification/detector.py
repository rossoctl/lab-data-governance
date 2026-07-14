"""The detector seam: the narrow text-in / annotations-out boundary (issue #78).

P-classification finds sensitive regions in a **Payload**'s **Classifiable text**
behind a single narrow interface. A :class:`Detector` takes the text and returns
:data:`Annotation` triples — ``(start, end, tag)``, one per detected **Finding**
region, where ``start``/``end`` are char offsets into the *Classifiable text* (not
the stored JSONB) and ``tag`` is the NER tag naming the finding's detected type
(``SSN``, ``PN``, …). It is emphatically NOT an **Entity** (the interaction
participant) and the region is a **Finding**, never a span (CONTEXT.md flagged
ambiguities).

Issue #78 ships this seam with a no-op default (:class:`NullDetector`), so the
ported classification logic (:mod:`.logic`) is exercised end-to-end from injected
annotations without any model. Issue #79 swaps the in-process fine-tuned NER model
in behind this *exact* seam — same ``detect(text) -> list[Annotation]`` signature,
same call site in :mod:`.verdict` — which ADR-0023 names as the load-bearing
reversibility hook: keep it narrow and the in-process→remote-service move stays
localized too.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# One detected **Finding** region: (start, end, tag). ``start``/``end`` are char
# offsets into the **Classifiable text**; ``tag`` is the NER tag (the finding's
# detected type). Matches the reference tool's ``[start, end, TAG]`` annotation
# shape so the ported logic consumes detector output unchanged.
Annotation = tuple[int, int, str]


@runtime_checkable
class Detector(Protocol):
    """Detects **Findings** in **Classifiable text** — the narrow seam #79 fills.

    The one method a real NER model or any fake must implement. Nothing else in
    P-classification depends on *how* findings are detected, only on this shape.
    """

    def detect(self, text: str) -> list[Annotation]:
        """Return the detected ``(start, end, tag)`` regions in *text*."""
        ...


class NullDetector:
    """The no-op default detector: finds nothing (issue #78).

    The stand-in until the real NER model lands (issue #79). Running a payload
    through it produces a real ``PUBLIC`` / zero-**Findings** verdict — never a
    null — so P-classification's write path is fully real ahead of the model
    (ADR-0024).
    """

    def detect(self, text: str) -> list[Annotation]:
        return []
