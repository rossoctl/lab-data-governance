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
                                           # bare on/off are YAML 1.1 booleans); compared as the record writes it
      edges: [s1 3>4, s1 4>5]              # every edge the fold must draw, and no other
      pending: null                        # or, for a case this branch cannot pass yet, exactly what it gets wrong:
                                           #   {reason: one sentence, missing: [edges], extra: [edges], verdict: ['on']}
      cannot: null                         # the same shape, for what no wire observer could get right
    classifier:
      must_see: {3: [s1, s2, s3], 4: [s1]} # leg index -> units the model must label there (any of their types)
      must_miss: {5: [s1]}                 # leg index -> units the model must NOT label there (none of their types)
      authored: false                      # true: calibration subtracts the model's findings at the must_miss legs
    notes: free text

Every unit present on a leg is in exactly one of ``must_see`` and ``must_miss`` at that leg, so the expected grid
(● must see, ○ must miss) states the whole classifier premise. Nothing is inferred: a key the judge needs and the
case does not state is an error.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

from . import caseline as cl
from .render import assign_values

CASES_DIR = Path(__file__).parent / "cases"

Edge = tuple[str, int, int]


@dataclasses.dataclass(frozen=True)
class Excuse:
    """What a pending (or cannot) case gets wrong, stated exactly: any other difference, or none, is red."""
    reason: str
    missing: frozenset[Edge] = frozenset()
    extra: frozenset[Edge] = frozenset()
    verdict: frozenset[str] = frozenset()      # the modes ("on", "off") whose verdict is wrong


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
    edges: set[Edge]
    cannot: Excuse | None
    pending: Excuse | None
    must_see: dict[int, list[str]]
    must_miss: dict[int, list[str]]
    authored: bool                           # the classifier is a parameter of the case at the must_miss legs
    notes: str
    dir: Path

    @property
    def values(self) -> dict[str, str]:
        """unit -> the bytes it travels as."""
        return assign_values(self.id, self.units)

    def labelled(self, unit: str, findings: list[tuple[str, str]]) -> bool:
        """Whether the model labelled *unit* at a leg: a finding of one of its types on its own value.
        must_see wants yes, must_miss no, and the observed grid draws ● on yes."""
        value = self.values[unit]
        return any(t in self.accepts[unit] and text == value for t, text in findings)


def _edge(s: str) -> Edge:
    unit, span = s.split()
    a, b = span.split(">")
    return unit, int(a), int(b)


def _excuse(name: str, key: str, d: Any) -> Excuse | None:
    if d is None:
        return None
    if not isinstance(d, dict) or not d.get("reason") or set(d) - {"reason", "missing", "extra", "verdict"}:
        raise ValueError(f"{name}: expect.{key} states reason and exactly what it gets wrong "
                         f"(missing, extra, verdict); got {d!r}")
    bad = set(d.get("verdict") or []) - {"on", "off"}
    if bad:
        raise ValueError(f"{name}: expect.{key}.verdict lists modes 'on'/'off', got {sorted(bad)}")
    return Excuse(reason=d["reason"], missing=frozenset(_edge(e) for e in d.get("missing") or []),
                  extra=frozenset(_edge(e) for e in d.get("extra") or []), verdict=frozenset(d.get("verdict") or []))


def load_case(case_dir: Path) -> Case:
    name = case_dir.name
    d: dict[str, Any] = yaml.safe_load((case_dir / "case.yaml").read_text())
    expect = d.get("expect", {})
    classifier = d.get("classifier", {})
    verdict = expect.get("verdict") or {}
    bad = set(verdict) - {"on", "off"}
    if bad:
        raise ValueError(f"{name}: expect.verdict keys must be 'on'/'off' (quoted), got {sorted(map(str, bad))}")
    line = cl.parse(d["line"])
    if sum(leg.sink for leg in line.legs) > 1:
        raise ValueError(f"{name}: more than one leg is flagged (⚑); the verdict is read at one")
    if line.sink is not None and set(verdict) != {"on", "off"}:
        raise ValueError(f"{name}: the line has a flagged leg (⚑); expect.verdict needs both 'on' and 'off'")
    if line.sink is None and verdict:
        raise ValueError(f"{name}: expect.verdict given but no leg is flagged (⚑)")
    if not isinstance(expect.get("edges"), list):
        raise ValueError(f"{name}: expect.edges is required, a list of \"s1 3>5\" strings (empty for none)")
    unknown = set(expect) - {"verdict", "edges", "pending", "cannot"}
    if unknown:
        raise ValueError(f"{name}: unknown expect keys {sorted(unknown)}")
    types = {u: [t] if isinstance(t, str) else list(t) for u, t in (d.get("units") or {}).items()}
    must_see = {int(k): list(v) for k, v in (classifier.get("must_see") or {}).items()}
    must_miss = {int(k): list(v) for k, v in (classifier.get("must_miss") or {}).items()}
    _check_premise(name, line, must_see, must_miss)
    pending, cannot = _excuse(name, "pending", expect.get("pending")), _excuse(name, "cannot", expect.get("cannot"))
    if pending and cannot:
        raise ValueError(f"{name}: a case is pending or cannot, not both")
    case = Case(
        id=d["id"], title=d.get("title", ""), cell=d.get("cell", ""),
        line_text=d["line"], line=line, units={u: t[0] for u, t in types.items()},
        accepts={u: frozenset(t) for u, t in types.items()},
        verdict={k: str(v) for k, v in verdict.items()},
        edges={_edge(e) for e in expect["edges"]},
        cannot=cannot,
        pending=pending,
        must_see=must_see,
        must_miss=must_miss,
        authored=bool(classifier.get("authored", False)),
        notes=d.get("notes", ""), dir=case_dir,
    )
    case.values  # equal bytes for two units are refused here
    return case


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
