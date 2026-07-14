"""The detector seam — the narrow text-in/annotations-out boundary (issue #78).

P-classification detects sensitive regions behind one narrow seam: a
:class:`~data_governance.processors.classification.detector.Detector` takes the
**Classifiable text** and returns annotations — ``(start, end, tag)`` triples, one
per detected **Finding** region. Issue #78 ships the seam plus a no-op default
(:class:`NullDetector`, which finds nothing) so the ported logic is fully testable
without a model; issue #79 swaps in the in-process NER model behind this exact
seam, changing nothing else (ADR-0023: the text-in/findings-out seam is the
reversibility hook).

A finding's type is an NER **tag** (``SSN``, ``PN``), never an **Entity**
(CONTEXT.md flagged ambiguity); the detected region is a **Finding**, never a span.
"""

from __future__ import annotations

from data_governance.processors.classification.detector import (
    Detector,
    NullDetector,
)


def test_null_detector_finds_nothing() -> None:
    """The default detector reports no findings for any text — the stand-in until
    the real NER model lands (issue #79). A payload run through it yields a real
    zero-**Findings** verdict, not a null (ADR-0024)."""
    assert NullDetector().detect("John Smith SSN 123-45-6789") == []


def test_null_detector_satisfies_the_detector_protocol() -> None:
    """The no-op default is a structural :class:`Detector` — so #79's model
    swaps in behind the same seam with no call-site change."""
    detector: Detector = NullDetector()
    assert detector.detect("") == []


def test_a_fake_detector_drives_the_seam_with_injected_annotations() -> None:
    """The seam is text-in / annotations-out: a fake detector returning fixed
    ``(start, end, tag)`` triples exercises the whole classification path without
    a model, which is how the ported logic is tested independently of #79."""

    class FakeDetector:
        def detect(self, text: str) -> list[tuple[int, int, str]]:
            return [(0, 10, "PN"), (11, 22, "SSN")]

    detector: Detector = FakeDetector()
    assert detector.detect("anything") == [(0, 10, "PN"), (11, 22, "SSN")]
