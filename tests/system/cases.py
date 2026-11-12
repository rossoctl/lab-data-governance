"""Case files: ``case.yaml`` next to its ``findings.json``. An example (not one of
the shipped cases), every key shown:

    id: X1
    title: the record number alone
    cell: identifier/exact/straight/one
    line: "U>A[-], A>T1[-], T1>A[s1 s2 s3 -], A>T2[s1@patient_ref -], T2>V[s1@external_id -]⚑, V>T2[-], T2>A[-], A>U[-]"
    units: {s1: MRN, s2: [NAME, PN], s3: PHONE}   # unit -> the types the model may label it with; the first
                                                  # is the kind of value rendered
    expect:
      verdict: {'on': block, 'off': allow} # of the flagged exchange, with inherited labels on / off (quote the keys:
                                           # bare on/off are YAML 1.1 booleans); compared as the record writes it
      edges: [s1 3>4, s1 4>5]              # every edge the fold must draw, and no other
      pending:                             # optional, for a case this branch cannot pass yet: exactly what it gets
        reason: one sentence               # wrong; any other difference, or none, is red
        missing: [s1 4>5]                  # expected edges not drawn
        extra: []                          # edges drawn though not expected
        verdict: ['on']                    # modes whose verdict is wrong
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
from .render import assign_values, render

CASES_DIR = Path(__file__).parent / "cases"

Edge = tuple[str, int, int]


@dataclasses.dataclass(frozen=True)
class Pending:
    """What a pending case gets wrong on this branch, stated exactly: any other difference, or none, is red."""
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
    pending: Pending | None
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


def _edges(name: str, where: str, items: list[str]) -> set[Edge]:
    """Edges, each once and each forward in time (from an earlier leg to a later one)."""
    edges = [_edge(e) for e in items]
    twice = sorted({e for e in edges if edges.count(e) > 1})
    if twice:
        raise ValueError(f"{name}: {where} lists edges twice: {twice}")
    backward = sorted(e for e in edges if e[1] >= e[2])
    if backward:
        raise ValueError(f"{name}: {where} has edges that do not run forward: {backward}")
    return set(edges)


def _cells(name: str, where: str, d: dict | None) -> dict[int, list[str]]:
    """leg index -> units, each leg once (``3`` and ``'3'`` are one leg) and each unit once per leg."""
    out: dict[int, list[str]] = {}
    for k, units in (d or {}).items():
        if int(k) in out:
            raise ValueError(f"{name}: {where} names leg {int(k)} twice")
        if len(set(units)) != len(units):
            raise ValueError(f"{name}: {where} lists a unit twice at leg {int(k)}: {units}")
        out[int(k)] = list(units)
    return out


def _pending(name: str, d: Any) -> Pending | None:
    if d is None:
        return None
    if not isinstance(d, dict) or not d.get("reason") or set(d) - {"reason", "missing", "extra", "verdict"}:
        raise ValueError(f"{name}: expect.pending states reason and exactly what it gets wrong "
                         f"(missing, extra, verdict); got {d!r}")
    bad = set(d.get("verdict") or []) - {"on", "off"}
    if bad:
        raise ValueError(f"{name}: expect.pending.verdict lists modes 'on'/'off', got {sorted(bad)}")
    return Pending(reason=d["reason"], missing=frozenset(_edges(name, "expect.pending.missing", d.get("missing") or [])),
                   extra=frozenset(_edges(name, "expect.pending.extra", d.get("extra") or [])),
                   verdict=frozenset(d.get("verdict") or []))


class _StrictLoader(yaml.SafeLoader):
    """A mapping that names one key twice is an error, not "the last one wins"."""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(k, deep=deep) for k, _ in node.value]
        twice = sorted({str(k) for k in keys if keys.count(k) > 1})
        if twice:
            raise ValueError(f"duplicated key(s) {twice} at line {node.start_mark.line + 1}")
        return super().construct_mapping(node, deep=deep)


TOP_KEYS = {"id", "title", "cell", "line", "units", "expect", "classifier", "notes"}
CLASSIFIER_KEYS = {"must_see", "must_miss", "authored"}


def load_case(case_dir: Path) -> Case:
    name = case_dir.name
    d: dict[str, Any] = yaml.load((case_dir / "case.yaml").read_text(), Loader=_StrictLoader)
    for where, keys, allowed in (("", d, TOP_KEYS), ("classifier.", d.get("classifier") or {}, CLASSIFIER_KEYS)):
        if set(keys) - allowed:
            raise ValueError(f"{name}: unknown {where or 'top-level '}keys {sorted(set(keys) - allowed)}")
    if d.get("id") != name:
        raise ValueError(f"{name}: id {d.get('id')!r} is not the case directory's name")
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
    unknown = set(expect) - {"verdict", "edges", "pending"}
    if unknown:
        raise ValueError(f"{name}: unknown expect keys {sorted(unknown)}")
    types = {u: [t] if isinstance(t, str) else list(t) for u, t in (d.get("units") or {}).items()}
    must_see, must_miss = (_cells(name, f"classifier.{key}", classifier.get(key)) for key in ("must_see", "must_miss"))
    authored = classifier.get("authored", False)
    if not isinstance(authored, bool):
        raise ValueError(f"{name}: classifier.authored is true or false, got {authored!r}")
    _check_premise(name, line, must_see, must_miss)
    edges = _edges(name, "expect.edges", expect["edges"])
    pending = _pending(name, expect.get("pending"))
    _check_edges(name, line, set(types), edges, pending)
    case = Case(
        id=d["id"], title=d.get("title", ""), cell=d.get("cell", ""),
        line_text=d["line"], line=line, units={u: t[0] for u, t in types.items()},
        accepts={u: frozenset(t) for u, t in types.items()},
        verdict={k: str(v) for k, v in verdict.items()},
        edges=edges,
        pending=pending,
        must_see=must_see,
        must_miss=must_miss,
        authored=authored,
        notes=d.get("notes", ""), dir=case_dir,
    )
    render(case.id, case.units, case.line)  # the renderer's refusals fire at load, not at run
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


def _check_edges(name: str, line: cl.CaseLine, units: set[str], edges: set[Edge], pending: Pending | None) -> None:
    """Edges name declared units and legs the line has, and join two legs that carry their unit; a pending case's
    missing edges are expected ones and its extra edges are not."""
    carries = {(leg.index, u) for leg in line.legs for u in leg.units}
    indices = {leg.index for leg in line.legs if not leg.invisible}
    stated = edges | (pending.missing | pending.extra if pending else set())
    bad = sorted(e for e in stated if e[0] not in units or e[1] not in indices or e[2] not in indices)
    if bad:
        raise ValueError(f"{name}: edges naming an undeclared unit or a leg the line lacks: {bad}")
    absent = sorted(e for e in edges if (e[1], e[0]) not in carries or (e[2], e[0]) not in carries)
    if absent:
        raise ValueError(f"{name}: expected edges between legs that do not both carry the unit: {absent}")
    if pending and not pending.missing <= edges:
        raise ValueError(f"{name}: pending missing edges that expect.edges does not list: {sorted(pending.missing - edges)}")
    if pending and pending.extra & edges:
        raise ValueError(f"{name}: pending extra edges that expect.edges lists: {sorted(pending.extra & edges)}")


def all_cases(root: Path = CASES_DIR) -> list[Case]:
    return [load_case(p) for p in sorted(root.iterdir()) if (p / "case.yaml").exists()]
