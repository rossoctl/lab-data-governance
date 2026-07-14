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
    ModelDetector,
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


# --- ModelDetector: the in-process fine-tuned NER model behind the seam (#79) --
#
# ModelDetector loads the fine-tuned token-classification model + tokenizer ONCE
# at construction (processor startup, not per payload; ADR-0023) and runs
# inference over the Classifiable text, returning **Finding** annotations. The
# torch/transformers load is exercised in the classification image (the weights
# are a ~500 MB git-LFS artifact baked into that image, absent from the dev
# checkout — like test_image_build.py, the real load/inference is a manual/image
# concern). These tests pin the seam contract and the load-once + orchestration
# behaviour with the heavy load stubbed out, so no torch is needed here.


class _StubLoaded:
    """A stand-in for a loaded (model, tokenizer, id2label) bundle. The
    ``ModelDetector`` treats these opaquely and hands them to the inference
    function, so a stub with the right attributes drives the seam torch-free."""

    def __init__(self) -> None:
        self.id2label = {0: "O", 1: "B-PN"}
        self.load_count = 0


def test_model_detector_is_a_structural_detector(monkeypatch) -> None:
    """The in-process model swaps in behind the *exact* seam — a
    ``ModelDetector`` is a structural :class:`Detector`, so the driver injects it
    with no call-site change (ADR-0023 reversibility hook)."""
    stub = _StubLoaded()
    monkeypatch.setattr(
        ModelDetector,
        "_load",
        staticmethod(
            lambda model_dir, base_tokenizer: (
                "model",
                "tok",
                stub.id2label,
                "cpu",
                False,
            )
        ),
    )

    detector: Detector = ModelDetector(model_dir="/baked/in")
    assert isinstance(detector, Detector)


def test_model_detector_loads_the_model_once_at_construction(monkeypatch) -> None:
    """The model is loaded at construction (startup), NOT per ``detect`` call —
    the hot path stays load-free (ADR-0023). Two detect calls trigger no
    re-load."""
    stub = _StubLoaded()

    def _fake_load(model_dir, base_tokenizer):
        stub.load_count += 1
        return ("model", "tok", stub.id2label, "cpu", False)

    monkeypatch.setattr(ModelDetector, "_load", staticmethod(_fake_load))
    monkeypatch.setattr(
        "data_governance.processors.classification.model.predict_entities",
        lambda *a, **k: [],
    )

    detector = ModelDetector(model_dir="/baked/in")
    assert stub.load_count == 1
    detector.detect("some text")
    detector.detect("more text")
    assert stub.load_count == 1, "detect() must not reload the model"


def test_model_detector_detect_returns_the_models_annotations(monkeypatch) -> None:
    """``detect`` runs inference over the given text and returns the model's
    ``(start, end, tag)`` **Findings** — the text-in / annotations-out seam. The
    text is passed through to the inference unchanged."""
    stub = _StubLoaded()
    monkeypatch.setattr(
        ModelDetector,
        "_load",
        staticmethod(
            lambda model_dir, base_tokenizer: ("M", "T", stub.id2label, "cpu", False)
        ),
    )

    seen: dict = {}

    def _fake_predict(text, model, tokenizer, id2label, device, is_cuda):
        seen["text"] = text
        seen["model"] = model
        seen["id2label"] = id2label
        return [(0, 10, "PN"), (11, 22, "SSN")]

    monkeypatch.setattr(
        "data_governance.processors.classification.model.predict_entities",
        _fake_predict,
    )

    detector = ModelDetector(model_dir="/baked/in")
    annotations = detector.detect("John Smith 123-45-6789")

    assert annotations == [(0, 10, "PN"), (11, 22, "SSN")]
    assert seen["text"] == "John Smith 123-45-6789"
    assert seen["model"] == "M"
    assert seen["id2label"] == stub.id2label


def test_importing_the_seam_does_not_pull_in_torch() -> None:
    """Load-bearing for ADR-0022: the shared receiver/UI/interactions image stays
    torch-free. Importing the detector seam (and the whole classification package)
    must NOT import torch/transformers — only *constructing* a
    :class:`ModelDetector` does, and that happens only inside the classification
    image. If this fails, the shared image would drag in the ~2 GB ML stack."""
    import importlib
    import sys

    # Import the modules the shared image loads (the processor package, its
    # detector seam, verdict, driver) fresh, then assert torch never entered
    # sys.modules as a side effect of import.
    for name in (
        "data_governance.processors.classification.detector",
        "data_governance.processors.classification.model",
        "data_governance.processors.classification.verdict",
        "data_governance.processors.classification.driver",
    ):
        importlib.import_module(name)

    assert "torch" not in sys.modules, "importing the classification seam pulled in torch"
    assert "transformers" not in sys.modules, (
        "importing the classification seam pulled in transformers"
    )
