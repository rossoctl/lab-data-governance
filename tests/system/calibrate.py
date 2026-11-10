"""Calibrate one or more cases with the real NER model.

    .venv-classif/bin/python -m tests.system.calibrate tests/system/cases/C1 [more...]

Loads the model once (about 20 s), renders every case's payloads, runs the model
on each distinct payload text, and writes ``findings.json`` beside ``case.yaml``.
Prints what the model found per payload so the author can check the case's
``classifier`` predictions against it.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from . import classify
from .cases import load_case
from .render import payload_texts, render


def authored_map(case, renderer) -> dict[str, set[str]]:
    """Payload text -> unit values to subtract, from the case's must_miss legs.
    A text shared by a must_see leg is refused: subtraction must not touch it."""
    from data_governance.processors.classification import projection

    text_of = {r.leg.index: projection.project(r.content) for r in renderer.rendered}
    out: dict[str, set[str]] = {}
    for leg, units in case.must_miss.items():
        out.setdefault(text_of[leg], set()).update(renderer.values[u] for u in units)
    for leg in case.must_see:
        if text_of.get(leg) in out:
            raise ValueError(f"{case.id}: leg {leg} must_see shares its payload text with a must_miss leg")
    return out


def main(argv: list[str]) -> int:
    from data_governance.processors.classification.detector import ModelDetector

    t = time.time()
    detector = ModelDetector(model_dir=classify.MODEL_DIR)
    print(f"model loaded in {time.time() - t:.1f} s", file=sys.stderr)
    for arg in argv:
        case_dir = Path(arg)
        case = load_case(case_dir)
        renderer = render(case.id, case.units, case.line)
        texts = payload_texts(renderer)
        authored = authored_map(case, renderer) if case.authored else None
        t = time.time()
        out = classify.calibrate(case_dir, texts, detector, authored)
        n_auth = sum(out["authored"].values()) if out.get("authored") else 0
        print(f"{case.id}: {len(texts)} payloads in {time.time() - t:.2f} s"
              + (f"; authored: {n_auth} findings subtracted at legs {sorted(case.must_miss)}" if case.authored else ""))
        for text, s in out["summary"].items():
            print(f"  {s['sensitivity_level']:<12} bundle={str(s['identity_bundle']):<5} {s['findings']}  <- {text[:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
