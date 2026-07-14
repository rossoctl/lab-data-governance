"""The P-classification **Classification** verdict — the real path (issue #78).

Issue #77 shipped :func:`classify` as a tracer-bullet stub that ignored the
payload's bytes and returned a trivial ``PUBLIC`` verdict for every **Payload**.
Issue #78 replaces the stub body with the real path, behind the *same* signature
and the same :mod:`.driver` write path:

1. Project the payload's JSONB ``content`` into its **Classifiable text** — the
   **Text projection rule** (:mod:`.projection`), one branch per **Content kind**.
2. Detect sensitive regions in that text as **Finding** annotations — through the
   narrow :class:`~.detector.Detector` seam (:mod:`.detector`).
3. Aggregate the findings up to the document-level verdict — the ported
   classification logic (:mod:`.logic`), parity-tested against the reference tool.

The NER model is not wired until issue #79, so detection defaults to
:class:`~.detector.NullDetector` (finds nothing) — a payload with no sensitive
text still yields a real ``PUBLIC`` / zero-**Findings** verdict, never a null
(ADR-0024). Issue #79 injects the in-process fine-tuned model as the ``detector``
here, changing nothing else; ADR-0023 names this narrow text-in/findings-out seam
the load-bearing reversibility hook.

The ``model_version`` is a monotonic integer (ADR-0024) stamped on every row so
verdicts from different model/config generations are comparable. This generation —
real projection + logic, no model — is still generation 1; the real model (issue
#79) bumps it (its image tag maps to the ``model_version`` it writes, ADR-0023).
"""

from __future__ import annotations

import dataclasses

from . import config as _config
from . import logic, projection
from .detector import Detector, NullDetector

# The current model generation (ADR-0024). Issue #77's stub was generation 1;
# issue #78 keeps it 1 (no model yet — the logic is real but detection is the
# no-op default). The real NER model (issue #79) bumps this.
STUB_MODEL_VERSION = 1

# The default no-op detector, shared across calls (it is stateless). Issue #79
# replaces this default with the in-process NER model.
_DEFAULT_DETECTOR: Detector = NullDetector()


@dataclasses.dataclass(frozen=True)
class Verdict:
    """The document-level **Classification** verdict over one **Payload**, plus
    its **Findings**. Mirrors the ``payload_classifications`` row shape
    (CONTEXT.md **Classification**); the driver writes it verbatim.

    ``regulatory_tags`` and ``findings`` are collections owned by the verdict;
    ``findings`` is the list of NER-detected sensitive items (empty for a
    genuinely clean payload — a clean verdict is a real zero-**Findings** row,
    never a null; ADR-0024).
    """

    sensitivity_level: str
    regulatory_tags: list[str]
    contains_identity_bundle: bool
    is_personalized: bool
    primary_domain: str | None
    findings: list[dict]
    model_version: int


def classify(
    content_hash: str,
    content_kind: str,
    content: object,
    detector: Detector | None = None,
) -> Verdict:
    """Return the **Classification** verdict for one **Payload**.

    Projects ``content`` into its **Classifiable text** per *content_kind*, detects
    **Findings** in that text through *detector* (defaulting to the no-op
    :class:`~.detector.NullDetector` until issue #79 wires the model), and
    aggregates the findings into the document-level verdict via the ported logic.

    ``content_hash`` is accepted for parity with the driver's call site (and for
    future per-payload logging); the verdict is a pure function of *content_kind*
    and *content*. A payload that projects to prose but has no detected findings
    is a real ``PUBLIC`` / zero-**Findings** verdict, not a null and not a skip.
    """
    det = detector if detector is not None else _DEFAULT_DETECTOR

    classifiable_text = projection.project(content_kind, content)
    annotations = det.detect(classifiable_text)

    entity_metadata = _config.load_entity_metadata()
    cfg = _config.load_config()
    result = logic.classify_text(classifiable_text, annotations, entity_metadata, cfg)

    return Verdict(
        sensitivity_level=result.sensitivity_level,
        regulatory_tags=result.regulatory_tags,
        contains_identity_bundle=result.contains_identity_bundle,
        is_personalized=result.is_personalized,
        primary_domain=result.primary_domain,
        findings=result.findings,
        model_version=STUB_MODEL_VERSION,
    )
