"""The classifier in the system tests: calibrated once with the real NER model,
replayed after.

``findings.json`` in a case directory maps each payload's **Classifiable text**
to the annotations the model returned, with the model generation and the date.
:class:`ReplayDetector` serves those through the production seam
(``Detector.detect``), so the classification processor runs its own code on the
model's own output. A text it has never seen raises: an uncalibrated payload is
a broken case, not a PUBLIC one.

``python -m tests.system.calibrate <case-dir>...`` (run from the venv that has the
``classification`` extra) fills ``findings.json``.

An **authored** case (``classifier.authored: true``) treats the classifier as a
parameter: after the real model has run, its findings on the case's units are
removed at the ``must_miss`` legs (subtracted, never invented), and
``findings.json`` records where. The report prints it on the case. Decided
2026-11-09: the essence of label lineage does not depend on where one model
happens to miss.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from data_governance.processors.classification.detector import Annotation

MODEL_DIR = "classification/model/20260304"
MODEL_VERSION = 2


class UncalibratedPayload(KeyError):
    pass


class ReplayDetector:
    def __init__(self, findings: dict[str, Any]) -> None:
        self._by_text: dict[str, list[list]] = findings["by_text"]
        self.model_version: int = int(findings.get("model_version", MODEL_VERSION))

    def detect(self, text: str) -> list[Annotation]:
        try:
            return [tuple(a) for a in self._by_text[text]]  # type: ignore[misc]
        except KeyError as exc:
            raise UncalibratedPayload(f"{text[:120]!r}: not in findings.json; run `.venv-classif/bin/python -m tests.system.calibrate <case dir>`") from exc


def load(case_dir: Path) -> ReplayDetector:
    return ReplayDetector(json.loads((case_dir / "findings.json").read_text()))


def subtract(by_text: dict[str, list[list]], text: str, values: set[str]) -> int:
    """Remove every annotation whose span is one of *values*; returns how many."""
    before = len(by_text[text])
    by_text[text] = [a for a in by_text[text] if text[a[0]:a[1]] not in values]
    return before - len(by_text[text])


def calibrate(case_dir: Path, texts: dict[str, dict[str, Any]], detector,
              authored: dict[str, set[str]] | None = None) -> dict[str, Any]:
    """Run *detector* (the real model) on every payload text and write findings.json.
    *authored* maps a payload text to the unit values to subtract there."""
    from data_governance.processors.classification import logic
    from data_governance.processors.classification import config as _config

    meta = _config.load_entity_metadata()
    cfg = _config.load_config()
    by_text: dict[str, list[list]] = {}
    summary: dict[str, dict[str, Any]] = {}
    subtracted: dict[str, int] = {}
    for text in texts:
        anns = detector.detect(text)
        by_text[text] = [list(a) for a in anns]
        if authored and text in authored:
            subtracted[text] = subtract(by_text, text, authored[text])
            anns = [tuple(a) for a in by_text[text]]
        c = logic.classify_text(text, anns, meta, cfg)
        summary[text] = {
            "sensitivity_level": c.sensitivity_level,
            "regulatory_tags": c.regulatory_tags,
            "identity_bundle": c.contains_identity_bundle,
            "findings": [[f["entity_type"], text[a[0]:a[1]]] for f, a in zip(c.findings, anns)],
        }
    out = {
        "model_dir": MODEL_DIR,
        "model_version": MODEL_VERSION,
        "calibrated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "by_text": by_text,
        "summary": summary,
        "authored": {t: n for t, n in subtracted.items()} if authored else None,
    }
    (case_dir / "findings.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    return out
