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

import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

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


# The classification image bakes the model weights + tokenizer under this path
# (ADR-0022/0023: weights + config travel as one unit, image tag ↔ model_version).
# Overridable via ``CLASSIFICATION_MODEL_DIR`` so tests and non-default image
# layouts can point elsewhere; production leaves it unset and uses this baked-in
# default. The default matches the model generation baked into the image
# (``classification/model/<generation>``), copied to ``/app/model`` at build time.
DEFAULT_MODEL_DIR = os.environ.get("CLASSIFICATION_MODEL_DIR", "/app/model")


class ModelDetector:
    """The in-process fine-tuned NER model behind the seam (issue #79, ADR-0023).

    Loads the fine-tuned ``RobertaForTokenClassification`` weights (baked into the
    classification image) and its base tokenizer ONCE at construction — i.e. at
    processor startup, not per payload (ADR-0023: keep the hot path load-free) —
    and runs inference over each **Classifiable text**, returning the detected
    **Finding** annotations (``(start, end, tag)``; the tag is an NER tag, never an
    **Entity**).

    ``torch``/``transformers`` are imported lazily inside :meth:`_load` (and the
    inference in :mod:`.model`), so this class only pulls them in when actually
    constructed — inside the classification image, which installs the
    ``classification`` extra (ADR-0022). Importing :mod:`.detector` (for the seam
    or :class:`NullDetector`) stays torch-free.

    The seam is deliberately narrow — text in, annotations out — so a future move
    to a remote inference service is a localized swap of this one class
    (ADR-0023 reversibility hook).
    """

    def __init__(
        self,
        model_dir: str | os.PathLike[str] | None = None,
        base_tokenizer: str | None = None,
    ) -> None:
        # Local import so constructing the detector is the only thing that pulls
        # the model module (and its lazy torch import) into play.
        from . import model as _model

        resolved_dir = str(model_dir) if model_dir is not None else DEFAULT_MODEL_DIR
        resolved_tokenizer = base_tokenizer or _model.BASE_TOKENIZER

        # Load once, here — this is the startup cost the drain loop must not pay
        # per payload. ``_load`` also resolves the device and moves the model onto
        # it, so every ``detect`` call reuses the already-placed model.
        (
            self._model,
            self._tokenizer,
            self._id2label,
            self._device,
            self._is_cuda,
        ) = self._load(resolved_dir, resolved_tokenizer)

    @staticmethod
    def _load(
        model_dir: str, base_tokenizer: str
    ) -> tuple[Any, Any, dict[int, str], Any, bool]:
        """Load ``(model, tokenizer, id2label, device, is_cuda)`` from the baked-in
        artifacts, with the model moved onto the chosen device.

        ``torch``/``transformers`` are imported here, not at module top, so the
        shared image (which never constructs a ``ModelDetector``) stays torch-free
        (ADR-0022). Split out as its own method — the single load seam — so the
        drain-path tests can stub the heavy load without torch or the ~500 MB
        weights.
        """
        from transformers import (
            AutoModelForTokenClassification,
            AutoTokenizer,
        )

        from . import model as _model

        device, is_cuda = _model.get_optimal_device()
        tokenizer = AutoTokenizer.from_pretrained(base_tokenizer)
        model = AutoModelForTokenClassification.from_pretrained(str(model_dir)).to(device)
        return model, tokenizer, model.config.id2label, device, is_cuda

    def detect(self, text: str) -> list[Annotation]:
        """Run the loaded model over *text*, returning its **Finding**
        annotations — the narrow text-in/annotations-out seam."""
        from . import model as _model

        return _model.predict_entities(
            text,
            self._model,
            self._tokenizer,
            self._id2label,
            self._device,
            self._is_cuda,
        )
