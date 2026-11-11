"""Case files: ``case.yaml`` next to its ``findings.json``. An example (not one of
the shipped cases), every key shown:

    id: X1
    title: the record number alone
    cell: identifier/exact/straight/one
    line: "U>A[-], A>T1[-], T1>A[s1 s2 s3 -], A>T2[s1@patient_ref -], T2>V[s1@external_id -]⚑"
    units: {s1: MRN, s2: [NAME, PN], s3: PHONE}   # unit -> the types the model may label it with; the first
                                                  # is the kind of value rendered
    expect:
      verdict: {'on': block, 'off': allow} # at the flagged leg, with inherited labels on / off (quote the keys:
                                           # bare on/off are YAML 1.1 booleans)
      edges: derived                       # or a list of "s1 3>5" strings
      also: []                             # edges the fold may also draw, from an earlier holding of the
                                           # same unit (where it was born): never red, reported when drawn
      cannot: null                         # or one sentence: why the wire cannot get this right
      pending: null                        # or one sentence: why this case cannot pass on this branch yet
    classifier:
      must_see: {3: [s1, s2, s3], 4: [s1]} # leg index -> units the model must label there (any of their types)
      must_miss: {5: [s1]}                 # leg index -> units the model must NOT label there (none of their types)
      authored: false                      # true: calibration subtracts the model's findings at the must_miss legs
    notes: free text

Every unit present on a leg is in exactly one of ``must_see`` and ``must_miss`` at that leg, so the expected grid
(● must see, ○ must miss) states the whole classifier premise.
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
    units: dict[str, str]                    # unit -> the kind of value rendered (its first type)
    accepts: dict[str, frozenset[str]]       # unit -> every type that counts as the model labelling it
    verdict: dict[str, str]                  # {"on": "block", "off": "allow"}
    edges: set[tuple[str, int, int]] | None  # None = derived
    also: set[tuple[str, int, int]]          # tolerated beside the expected edges, declared per case
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

    def labelled(self, unit: str, tags: set[str]) -> bool:
        """Whether the model's finding tags at a leg label *unit*: any of its accepted types. must_see wants yes,
        must_miss no, and the observed grid draws ● on yes."""
        return bool(self.accepts[unit] & tags)


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
    if sum(leg.sink for leg in line.legs) > 1:
        raise ValueError(f"{case_dir.name}: more than one leg is flagged (⚑); the verdict is read at one")
    if line.sink is not None and set(verdict) != {"on", "off"}:
        raise ValueError(f"{case_dir.name}: the line has a flagged leg (⚑); expect.verdict needs both 'on' and 'off'")
    if line.sink is None and verdict:
        raise ValueError(f"{case_dir.name}: expect.verdict given but no leg is flagged (⚑)")
    types = {u: [t] if isinstance(t, str) else list(t) for u, t in (d.get("units") or {}).items()}
    must_see = {int(k): list(v) for k, v in (classifier.get("must_see") or {}).items()}
    must_miss = {int(k): list(v) for k, v in (classifier.get("must_miss") or {}).items()}
    _check_premise(case_dir.name, line, must_see, must_miss)
    return Case(
        id=d["id"], title=d.get("title", ""), cell=d.get("cell", ""),
        line_text=d["line"], line=line, units={u: t[0] for u, t in types.items()},
        accepts={u: frozenset(t) for u, t in types.items()},
        verdict={k: str(v) for k, v in verdict.items()},
        edges=None if edges == "derived" else {_edge(e) for e in edges},
        also={_edge(e) for e in expect.get("also") or []},
        cannot=expect.get("cannot"),
        pending=expect.get("pending"),
        must_see=must_see,
        must_miss=must_miss,
        authored=bool(classifier.get("authored", False)),
        notes=d.get("notes", ""), dir=case_dir,
    )


def _check_premise(name: str, line: cl.CaseLine, must_see: dict[int, list[str]],
                   must_miss: dict[int, list[str]]) -> None:
    """Every unit present on a leg is stated exactly once there: must see or must miss."""
    present = {(leg.index, u) for leg in line.legs for u in leg.units}
    see = {(i, u) for i, us in must_see.items() for u in us}
    miss = {(i, u) for i, us in must_miss.items() for u in us}
    fmt = lambda cells: ", ".join(f"{u}@{i}" for i, u in sorted(cells))
    if see & miss:
        raise ValueError(f"{name}: in both must_see and must_miss: {fmt(see & miss)}")
    if (see | miss) - present:
        raise ValueError(f"{name}: must_see/must_miss name a unit the leg does not carry: {fmt((see | miss) - present)}")
    if present - see - miss:
        raise ValueError(f"{name}: present but in neither must_see nor must_miss: {fmt(present - see - miss)}")


def all_cases(root: Path = CASES_DIR) -> list[Case]:
    return [load_case(p) for p in sorted(root.iterdir()) if (p / "case.yaml").exists()]
