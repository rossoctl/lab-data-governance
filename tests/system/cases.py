"""Case files: ``case.yaml`` next to its ``findings.json``.

    id: C1
    title: the record number alone
    cell: identifier/exact/straight/one
    line: "U>A[-], A>T1[-], T1>A[s1 s2 s3 -], A>T2[s1@patient_ref -], T2>V[s1@external_id -]⚑"
    units: {s1: MRN, s2: NAME, s3: PHONE}
    expect:
      verdict: {'on': block, 'off': allow} # at the flagged leg, with inherited labels on / off (quote the keys:
                                           # bare on/off are YAML 1.1 booleans)
      edges: derived                       # or a list of "s1 3>5" strings
      cannot: null                         # or one sentence: why the wire cannot get this right
      pending: null                        # or one sentence: why this case cannot pass on this branch yet
    classifier:
      must_see: {3: [s1, s2, s3]}          # leg index -> units the model must label there
      must_miss: {5: [s1]}                 # leg index -> units the model must NOT label there
      authored: false                      # true: calibration subtracts the model's findings at the must_miss legs
    notes: free text
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

from . import caseline as cl

CASES_DIR = Path(__file__).parent / "cases"


@dataclasses.dataclass(frozen=True)
class Case:
    id: str
    title: str
    cell: str
    line_text: str
    line: cl.CaseLine
    units: dict[str, str]
    verdict: dict[str, str]                  # {"on": "block", "off": "allow"}
    edges: set[tuple[str, int, int]] | None  # None = derived
    cannot: str | None
    pending: str | None
    must_see: dict[int, list[str]]
    must_miss: dict[int, list[str]]
    authored: bool                           # the classifier is a parameter of the case at the must_miss legs
    notes: str
    dir: Path

    @property
    def expected_edges(self) -> set[tuple[str, int, int]]:
        return self.edges if self.edges is not None else cl.naive_expected_edges(self.line)


def _edge(s: str) -> tuple[str, int, int]:
    unit, span = s.split()
    a, b = span.split(">")
    return unit, int(a), int(b)


def load_case(case_dir: Path) -> Case:
    d: dict[str, Any] = yaml.safe_load((case_dir / "case.yaml").read_text())
    expect = d.get("expect", {})
    edges = expect.get("edges", "derived")
    classifier = d.get("classifier", {})
    verdict = expect.get("verdict") or {}
    bad = set(verdict) - {"on", "off"}
    if bad:
        raise ValueError(f"{case_dir.name}: expect.verdict keys must be 'on'/'off' (quoted), got {sorted(map(str, bad))}")
    line = cl.parse(d["line"])
    if line.sink is not None and set(verdict) != {"on", "off"}:
        raise ValueError(f"{case_dir.name}: the line has a flagged leg (⚑); expect.verdict needs both 'on' and 'off'")
    if line.sink is None and verdict:
        raise ValueError(f"{case_dir.name}: expect.verdict given but no leg is flagged (⚑)")
    return Case(
        id=d["id"], title=d.get("title", ""), cell=d.get("cell", ""),
        line_text=d["line"], line=line, units=dict(d.get("units", {})),
        verdict={k: str(v) for k, v in verdict.items()},
        edges=None if edges == "derived" else {_edge(e) for e in edges},
        cannot=expect.get("cannot"),
        pending=expect.get("pending"),
        must_see={int(k): list(v) for k, v in (classifier.get("must_see") or {}).items()},
        must_miss={int(k): list(v) for k, v in (classifier.get("must_miss") or {}).items()},
        authored=bool(classifier.get("authored", False)),
        notes=d.get("notes", ""), dir=case_dir,
    )


def all_cases(root: Path = CASES_DIR) -> list[Case]:
    return [load_case(p) for p in sorted(root.iterdir()) if (p / "case.yaml").exists()]
