"""Case files: ``case.yaml`` next to its ``findings.json``. An example (not one of
the shipped cases; it loads, ``test_judge`` loads it), every key shown:

    id: X1
    title: the record number alone
    cell: identifier/exact/straight/one
    line: "U>A[-], A>T2[s1 s3 -], T2>V2[{s1 s3} -], V2>T2[-], T2>A[-], A>U[-] ~2min> U>A[-], A>T2[-], T2>V2[-], V2>T2[s1 -], T2>A[s1 -], A>T3[s1 -]⚑, T3>A[-], A>U[-]"
    units: {s1: SSN, s3: DOB}                     # unit -> the types the model may label it with; the first is the
                                                  # kind of value rendered
    scopes: {V2: persistent}                      # optional: a memory scope declared per party (call, conversation,
                                                  # persistent), applied through the production declaration
    config: {payload_bytes: 16}                   # optional: a limit, set through the production configuration
    steps: [{send: 7-14}, drain, {send: 1-6}, drain, {derive: 2}, drain]   # optional; by default every leg is
                                                  # sent in line order, then every drain runs
    expect:
      verdict: {'off': allow, conv: allow, cross: block}   # of the flagged exchange in each mode (quote 'off': a
                                                  # bare off is a YAML 1.1 boolean); compared as the record writes
                                                  # it; a list is the decision history, every version in order,
                                                  # a late finding marked ([allow, block late]; naming a tier,
                                                  # every version names it: [allow tier 1, block tier 2 late])
      edges: [s1 2>3, s3 2>3]                     # the in-conversation edges: every run draws exactly these
      cross: [s1 3>10 vo, s1 10>11, s1 11>12]     # what the cross run draws besides: crossings carry their level
                                                  # (vo value-only, co corroborated), and the edges that rest on them
      evidence: {10: {s1: [crossed vo]}, 2: {s1: [classified SSN], s3: [classified DOB]}}   # at a leg, per unit,
                                                  # how it is known (cross run): classified TYPE, inherited, crossed L
                                                  # [ambiguous]; a crossed label drawn at a cell not stated is wrong
      marks: {10: [incomplete payload_bytes]}     # at a leg (cross run): common N, incomplete LIMIT; every mark drawn
                                                  # must be stated
      pending:                                    # optional, for a case this branch cannot pass yet: exactly what it
        reason: one sentence                      # gets wrong; any other difference, or none, is red
        missing: [s1 3>10 vo]                     # expected edges the cross run did not draw
        extra: []                                 # edges the cross run drew though not expected
        verdict: [cross]                          # modes whose verdict is wrong
        evidence: [10]                            # legs whose stated evidence is wrong
        marks: []                                 # legs whose marks are wrong
        held: []                                  # instead of the above: the case is not run, it needs these
                                                  # production paths (scopes, config, switch, reclassify, tiers)
    classifier:
      must_see: {2: [s1, s3], 3: [s1, s3]}        # leg index -> units the model must label there (any of their types)
      must_miss: {10: [s1], 11: [s1], 12: [s1]}   # leg index -> units the model must NOT label there (none of their
                                                  # types)
      authored: true                              # calibration subtracts the model's findings at the must_miss legs
    notes: free text

Every unit present on a leg is in exactly one of ``must_see`` and ``must_miss`` at that leg, so the expected grid
(● must see, ○ must miss) states the whole classifier premise; an evidence cell says ``classified`` exactly where
the premise says must see. Nothing is inferred: a key the judge needs and the case does not state is an error.
"""

from __future__ import annotations

import dataclasses
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from . import caseline as cl
from .render import assign_values, render

CASES_DIR = Path(__file__).parent / "cases"

MODES = ("off", "conv", "cross")    # inherited labels off / on with the cross switch off / both on
LEVELS = ("vo", "co")
SCOPES = ("call", "conversation", "persistent")
LIMITS = ("holdings_per_entity", "payload_bytes", "candidates_per_payload")
PATHS = ("scopes", "config", "switch", "reclassify", "tiers")   # what a case can use beyond sending and draining

Edge = tuple[str, int, int, str | None]     # unit, from leg, to leg, level (a crossing's; None in a conversation)

_LEVEL_OR_NONE = f"(?: ({'|'.join(LEVELS)}))?"
_EDGE = re.compile(rf"(s\d+) (\d+)>(\d+){_LEVEL_OR_NONE}")
_KIND = re.compile(rf"classified (\S+)|inherited|crossed ({'|'.join(LEVELS)})( ambiguous)?")
_MARK = re.compile(rf"common [1-9]\d*|incomplete ({'|'.join(LIMITS)})")
_DECISION = re.compile(r"(?!(?:tier|late)\b)\S+( tier [012])?( late)?")


@dataclasses.dataclass(frozen=True)
class Pending:
    """What a pending case gets wrong on this branch, stated exactly: any other difference, or none, is red. A held
    case is not run: it needs production paths this branch does not have, named in ``held``."""
    reason: str
    missing: frozenset[Edge] = frozenset()
    extra: frozenset[Edge] = frozenset()
    verdict: frozenset[str] = frozenset()      # the modes whose verdict is wrong
    evidence: frozenset[int] = frozenset()     # the legs whose stated evidence is wrong
    marks: frozenset[int] = frozenset()        # the legs whose marks are wrong
    held: frozenset[str] = frozenset()


@dataclasses.dataclass(frozen=True)
class Case:
    id: str
    title: str
    cell: str
    line_text: str
    line: cl.CaseLine
    units: dict[str, str]                    # unit -> the kind of value rendered (its first type)
    accepts: dict[str, frozenset[str]]       # unit -> every type that counts as the model labelling it
    verdict: dict[str, str | tuple[str, ...]]  # mode -> decision, or the decision history ("allow tier 1", ...)
    edges: set[Edge]                         # in-conversation: every run draws exactly these
    pending: Pending | None
    must_see: dict[int, list[str]]
    must_miss: dict[int, list[str]]
    authored: bool                           # the classifier is a parameter of the case at the must_miss legs
    notes: str
    dir: Path
    cross: frozenset[Edge] = frozenset()     # what the cross run draws besides
    evidence: dict[int, dict[str, frozenset[str]]] = dataclasses.field(default_factory=dict)
    marks: dict[int, frozenset[str]] = dataclasses.field(default_factory=dict)
    scopes: dict[str, str] = dataclasses.field(default_factory=dict)
    config: dict[str, int] = dataclasses.field(default_factory=dict)
    steps: tuple[tuple, ...] = ()            # ("send", legs) | ("drain",) | ("derive", n) | ("reclassify", ...) | ("cross", on/off)

    @property
    def values(self) -> dict[str, str]:
        """unit -> the bytes it travels as."""
        return assign_values(self.id, self.units)

    @property
    def uses(self) -> frozenset[str]:
        """The production paths the case needs beyond sending legs and draining: the engine runs it only when the
        branch has them all."""
        kinds = {step[0] for step in self.steps}
        return frozenset(name for name, used in (
            ("scopes", bool(self.scopes)), ("config", bool(self.config)), ("switch", "cross" in kinds),
            ("reclassify", "reclassify" in kinds),
            ("tiers", any(" tier " in d for v in self.verdict.values() if not isinstance(v, str) for d in v))) if used)

    def premise_before(self, step: int | None = None) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
        """(must see, must miss) of the classification the rows hold before step *step* (``None``: after the last):
        the premise, with every unit a reclassify step before it takes from a leg moved from must see to must miss
        there (the step states it; nothing is inferred)."""
        cut: dict[int, set[str]] = defaultdict(set)
        for kind, *arg in self.steps[:step]:
            if kind == "reclassify":
                for leg, units in arg[0]:
                    cut[leg].update(units)
        see = {leg: [u for u in us if u not in cut.get(leg, ())] for leg, us in self.must_see.items()}
        miss = {leg: sorted(set(self.must_miss.get(leg, [])) | cut.get(leg, set()))
                for leg in set(self.must_miss) | set(cut)}
        return {leg: us for leg, us in see.items() if us}, miss

    @property
    def final_premise(self) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
        """The premise of the classification the rows end with (:meth:`premise_before` the end)."""
        return self.premise_before()

    def labelled(self, unit: str, findings: list[tuple[str, str]]) -> bool:
        """Whether the model labelled *unit* at a leg: a finding of one of its types on its own value.
        must_see wants yes, must_miss no, and the observed grid draws ● on yes."""
        value = self.values[unit]
        return any(t in self.accepts[unit] and text == value for t, text in findings)


def _edge(name: str, s: str) -> Edge:
    m = _EDGE.fullmatch(str(s))
    if not m:
        raise ValueError(f"{name}: bad edge {s!r}: \"s1 3>5\", or \"s1 11>22 vo\" for a crossing (vo, co)")
    return m[1], int(m[2]), int(m[3]), m[4]


def _edges(name: str, where: str, items: list[str]) -> set[Edge]:
    """Edges, each once and each forward in time (from an earlier leg to a later one)."""
    edges = [_edge(name, e) for e in items]
    plain = [e[:3] for e in edges]
    twice = sorted({e for e in plain if plain.count(e) > 1})
    if twice:
        raise ValueError(f"{name}: {where} lists edges twice: {twice}")
    backward = sorted(e for e in plain if e[1] >= e[2])
    if backward:
        raise ValueError(f"{name}: {where} has edges that do not run forward: {backward}")
    return set(edges)


def fmt_edges(edges) -> str:
    return ", ".join(f"{u} {a}>{b}" + (f" {lv}" if lv else "") for u, a, b, lv in sorted(edges, key=_order))


def _order(e: Edge):
    return e[0], e[1], e[2], e[3] or ""


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


def _modes(name: str, where: str, modes) -> frozenset[str]:
    if modes is not None and not isinstance(modes, (list, dict)):
        raise ValueError(f"{name}: {where} names the modes {MODES} in a list, got {modes!r}")
    bad = set(modes or []) - set(MODES)
    if bad:
        raise ValueError(f"{name}: {where} names the modes {MODES} (quote 'off'), got {sorted(map(str, bad))}")
    return frozenset(modes or [])


def _pending(name: str, d: Any) -> Pending | None:
    if d is None:
        return None
    keys = {"reason", "missing", "extra", "verdict", "evidence", "marks", "held"}
    if not isinstance(d, dict) or not d.get("reason") or set(d) - keys:
        raise ValueError(f"{name}: expect.pending states reason and exactly what it gets wrong "
                         f"(missing, extra, verdict, evidence, marks) or what it is held for (held); got {d!r}")
    if d.get("held") and set(d) - {"reason", "held"}:
        raise ValueError(f"{name}: a held case is not run; expect.pending states its reason and held, nothing else")
    held = d.get("held") or []
    if not isinstance(held, list) or set(held) - set(PATHS):
        raise ValueError(f"{name}: expect.pending.held lists production paths, each one of {PATHS}; got {held!r}")
    return Pending(reason=d["reason"], missing=frozenset(_edges(name, "expect.pending.missing", d.get("missing") or [])),
                   extra=frozenset(_edges(name, "expect.pending.extra", d.get("extra") or [])),
                   verdict=_modes(name, "expect.pending.verdict", d.get("verdict")),
                   evidence=_leg_list(name, "expect.pending.evidence", d.get("evidence")),
                   marks=_leg_list(name, "expect.pending.marks", d.get("marks")),
                   held=frozenset(held))


def _leg_list(name: str, where: str, items: Any) -> frozenset[int]:
    if items is None:
        return frozenset()
    if not isinstance(items, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in items):
        raise ValueError(f"{name}: {where} lists leg indices, got {items!r}")
    return frozenset(items)


def _verdict(name: str, d: dict) -> dict[str, str | tuple[str, ...]]:
    _modes(name, "expect.verdict", d)
    out: dict[str, str | tuple[str, ...]] = {}
    for mode, v in d.items():
        if isinstance(v, list):
            bad = [x for x in v if not _DECISION.fullmatch(str(x))]
            if not v or bad:
                raise ValueError(f"{name}: a decision history lists \"<decision>[ tier <0|1|2>][ late]\" "
                                 f"in order, got {v!r}")
            out[mode] = tuple(v)
        else:
            out[mode] = str(v)
    histories = [d for v in out.values() if not isinstance(v, str) for d in v]
    if any(" tier " in d for d in histories) and not all(" tier " in d for d in histories):
        raise ValueError(f"{name}: a case that names a tier states it on every version of every history, "
                         f"got {histories!r}")
    return out


def _wire(line: cl.CaseLine) -> dict[int, cl.Leg]:
    return {leg.index: leg for leg in line.legs if not leg.invisible}


def _evidence(name: str, line: cl.CaseLine, accepts: dict[str, frozenset[str]], must_see: dict[int, list[str]],
              d: dict | None) -> dict[int, dict[str, frozenset[str]]]:
    """leg -> unit -> how it is known there. Classified names the type (R8) and is stated exactly where the premise
    says must see."""
    wire = _wire(line)
    out: dict[int, dict[str, frozenset[str]]] = {}
    for k, cells in (d or {}).items():
        leg = wire.get(int(k))
        if leg is None or int(k) in out:
            raise ValueError(f"{name}: expect.evidence names leg {k}, which the line lacks or which it names twice")
        out[int(k)] = {}
        for unit, kinds in cells.items():
            if unit not in leg.units:
                raise ValueError(f"{name}: expect.evidence at leg {k}: {unit} is not on that leg")
            if unit not in accepts:
                raise ValueError(f"{name}: expect.evidence at leg {k}: {unit} is not declared in units")
            bad = [x for x in kinds if not _KIND.fullmatch(str(x))]
            if bad or len(set(kinds)) != len(kinds):
                raise ValueError(f"{name}: expect.evidence at leg {k} {unit}: each once of \"classified TYPE\", "
                                 f"\"inherited\", \"crossed vo|co\" (+ \" ambiguous\"), got {kinds!r}")
            types = {x.split()[1] for x in kinds if x.startswith("classified ")}
            if types - accepts[unit]:
                raise ValueError(f"{name}: expect.evidence at leg {k}: {unit} classified as {sorted(types)}, "
                                 f"not one of its types {sorted(accepts[unit])}")
            if bool(types) != (unit in must_see.get(int(k), ())):
                raise ValueError(f"{name}: expect.evidence at leg {k}: {unit} is classified there exactly when "
                                 f"classifier.must_see says so")
            out[int(k)][unit] = frozenset(kinds)
    return out


def _marks(name: str, line: cl.CaseLine, d: dict | None) -> dict[int, frozenset[str]]:
    wire = _wire(line)
    out: dict[int, frozenset[str]] = {}
    for k, marks in (d or {}).items():
        if int(k) in out:
            raise ValueError(f"{name}: expect.marks names leg {int(k)} twice")
        if not isinstance(marks, list):
            raise ValueError(f"{name}: expect.marks at leg {k}: a list of marks, got {marks!r}")
        if len(set(marks or [])) != len(marks or []):
            raise ValueError(f"{name}: expect.marks lists a mark twice at leg {int(k)}: {marks}")
        bad = [x for x in marks if not _MARK.fullmatch(str(x))]
        if int(k) not in wire or not marks or bad:
            raise ValueError(f"{name}: expect.marks at leg {k}: a leg on the line, and \"common N\" or "
                             f"\"incomplete <{'|'.join(LIMITS)}>\", got {marks!r}")
        out[int(k)] = frozenset(marks)
    return out


def _scopes(name: str, line: cl.CaseLine, d: dict | None) -> dict[str, str]:
    parties = {p for leg in line.legs for p in (leg.frm, leg.to)}
    if d is not None and not isinstance(d, dict):
        raise ValueError(f"{name}: scopes maps a party on the line to one of {SCOPES}, got {d!r}")
    for party, scope in (d or {}).items():
        if party not in parties or scope not in SCOPES:
            raise ValueError(f"{name}: scopes maps a party on the line to one of {SCOPES}, got {party}: {scope}")
    return dict(d or {})


def _config(name: str, d: dict | None) -> dict[str, int]:
    for key, value in (d or {}).items():
        if key not in LIMITS or isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name}: config sets one of {LIMITS} to a count, got {key}: {value!r}")
    return dict(d or {})


def _legs(name: str, line: cl.CaseLine, spec: str) -> tuple[int, ...]:
    """``17-32, 40``: legs by index, a range in its own direction (``32-17`` is reversed); a range takes the legs
    that go on the wire, a single index must be one."""
    wire = _wire(line)
    out: list[int] = []
    for part in str(spec).split(","):
        m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", part)
        if not m:
            raise ValueError(f"{name}: send takes leg indices and ranges (\"17-32, 40\"), got {spec!r}")
        a, b = int(m[1]), int(m[2] or m[1])
        if m[2] is None and a not in wire:
            raise ValueError(f"{name}: send names leg {a}, which puts nothing on the wire")
        out += [i for i in range(a, b + (1 if b >= a else -1), 1 if b >= a else -1) if i in wire]
    return tuple(out)


def _steps(name: str, line: cl.CaseLine, raw: Any) -> tuple[tuple, ...]:
    """The run's steps; by default every leg in line order, then the drains. Every leg is sent exactly once and every
    send, derivation and reclassification is followed by a drain."""
    wire = _wire(line)
    if raw is None:
        return ("send", tuple(wire)), ("drain",)
    if not isinstance(raw, list):
        raise ValueError(f"{name}: steps is a list of drain, send, derive, reclassify and cross steps; got {raw!r}")
    steps: list[tuple] = []
    for item in raw:
        if item == "drain":
            steps.append(("drain",))
            continue
        if not isinstance(item, dict) or len(item) != 1 or next(iter(item)) not in ("send", "derive", "reclassify",
                                                                                     "cross"):
            raise ValueError(f"{name}: a step is drain, or one of send, derive, reclassify, cross; got {item!r}")
        ((kind, arg),) = item.items()
        if kind == "send":
            steps.append(("send", _legs(name, line, arg)))
        elif kind == "derive":
            if arg not in range(1, len(line.conversations) + 1):
                raise ValueError(f"{name}: derive names a conversation 1..{len(line.conversations)}, got {arg!r}")
            steps.append(("derive", arg))
        elif kind == "reclassify":
            cells = _cells(name, "reclassify", arg)
            if any(i not in wire or set(us) - set(wire[i].units) for i, us in cells.items()):
                raise ValueError(f"{name}: reclassify maps a leg to units it carries, got {arg!r}")
            steps.append(("reclassify", tuple(sorted((i, tuple(us)) for i, us in cells.items()))))
        else:
            if arg not in ("on", "off"):
                raise ValueError(f"{name}: the cross step is 'on' or 'off' (quoted), got {arg!r}")
            steps.append(("cross", arg))
    sent = [i for s in steps if s[0] == "send" for i in s[1]]
    if sorted(sent) != sorted(wire):
        twice = sorted({i for i in sent if sent.count(i) > 1})
        raise ValueError(f"{name}: steps send every leg exactly once: twice {twice}, "
                         f"never {sorted(set(wire) - set(sent))}")
    last_work = max((i for i, s in enumerate(steps) if s[0] in ("send", "derive", "reclassify")), default=-1)
    if not any(s[0] == "drain" for s in steps[last_work + 1:]):
        raise ValueError(f"{name}: the steps end with work no drain follows")
    return tuple(steps)


class _StrictLoader(yaml.SafeLoader):
    """A mapping that names one key twice is an error, not "the last one wins"."""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(k, deep=deep) for k, _ in node.value]
        twice = sorted({str(k) for k in keys if keys.count(k) > 1})
        if twice:
            raise ValueError(f"duplicated key(s) {twice} at line {node.start_mark.line + 1}")
        return super().construct_mapping(node, deep=deep)


TOP_KEYS = {"id", "title", "cell", "line", "units", "scopes", "config", "steps", "expect", "classifier", "notes"}
EXPECT_KEYS = {"verdict", "edges", "cross", "evidence", "marks", "pending"}
CLASSIFIER_KEYS = {"must_see", "must_miss", "authored"}


def load_case(case_dir: Path) -> Case:
    name = case_dir.name
    d: dict[str, Any] = yaml.load((case_dir / "case.yaml").read_text(), Loader=_StrictLoader)
    expect = d.get("expect", {})
    classifier = d.get("classifier", {})
    for where, keys, allowed in (("top-level ", d, TOP_KEYS), ("expect ", expect, EXPECT_KEYS),
                                 ("classifier.", classifier, CLASSIFIER_KEYS)):
        if set(keys) - allowed:
            raise ValueError(f"{name}: unknown {where}keys {sorted(set(keys) - allowed)}")
    if d.get("id") != name:
        raise ValueError(f"{name}: id {d.get('id')!r} is not the case directory's name")
    verdict = _verdict(name, expect.get("verdict") or {})
    line = cl.parse(d["line"])
    if sum(leg.sink for leg in line.legs) > 1:
        raise ValueError(f"{name}: more than one leg is flagged (⚑); the verdict is read at one")
    if line.sink is not None and set(verdict) != set(MODES):
        raise ValueError(f"{name}: the line has a flagged leg (⚑); expect.verdict needs every mode {MODES}")
    if line.sink is None and verdict:
        raise ValueError(f"{name}: expect.verdict given but no leg is flagged (⚑)")
    if not isinstance(expect.get("edges"), list):
        raise ValueError(f"{name}: expect.edges is required, a list of \"s1 3>5\" strings (empty for none)")
    types = {u: [t] if isinstance(t, str) else list(t) for u, t in (d.get("units") or {}).items()}
    accepts = {u: frozenset(t) for u, t in types.items()}
    must_see, must_miss = (_cells(name, f"classifier.{key}", classifier.get(key)) for key in ("must_see", "must_miss"))
    authored = classifier.get("authored", False)
    if not isinstance(authored, bool):
        raise ValueError(f"{name}: classifier.authored is true or false, got {authored!r}")
    _check_premise(name, line, must_see, must_miss)
    edges = _edges(name, "expect.edges", expect["edges"])
    cross = _edges(name, "expect.cross", expect.get("cross") or [])
    pending = _pending(name, expect.get("pending"))
    _check_edges(name, line, set(types), edges, cross, pending)
    evidence = _evidence(name, line, accepts, must_see, expect.get("evidence"))
    if pending and pending.evidence - set(_wire(line)):
        raise ValueError(f"{name}: expect.pending.evidence names legs the line lacks: "
                         f"{sorted(pending.evidence - set(_wire(line)))}")
    steps = _steps(name, line, d.get("steps"))
    taken: set[tuple[int, str]] = set()
    for step in steps:
        if step[0] == "reclassify":
            cells = {(leg, u) for leg, units in step[1] for u in units}
            if any(u not in must_see.get(leg, []) for leg, u in cells) or cells & taken:
                raise ValueError(f"{name}: reclassify takes a unit only from a leg the premise says the model "
                                 f"labels there, and once: {sorted(cells)}")
            taken |= cells
    case = Case(
        id=d["id"], title=d.get("title", ""), cell=d.get("cell", ""),
        line_text=d["line"], line=line, units={u: t[0] for u, t in types.items()}, accepts=accepts,
        verdict=verdict, edges=edges, pending=pending, must_see=must_see, must_miss=must_miss, authored=authored,
        notes=d.get("notes", ""), dir=case_dir, cross=frozenset(cross), evidence=evidence,
        marks=_marks(name, line, expect.get("marks")), scopes=_scopes(name, line, d.get("scopes")),
        config=_config(name, d.get("config")), steps=steps,
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


def _check_edges(name: str, line: cl.CaseLine, units: set[str], edges: set[Edge], cross: set[Edge],
                 pending: Pending | None) -> None:
    """Edges name declared units and legs the line has, and join two legs that carry their unit; a level marks a
    crossing, which only the cross run draws; a pending case's missing edges are expected ones and its extra edges
    are not."""
    carries = {(leg.index, u) for leg in line.legs for u in leg.units}
    indices = set(_wire(line))
    expected = edges | cross
    stated = expected | (pending.missing | pending.extra if pending else set())
    bad = sorted((e for e in stated if e[0] not in units or e[1] not in indices or e[2] not in indices), key=_order)
    if bad:
        raise ValueError(f"{name}: edges naming an undeclared unit or a leg the line lacks: {fmt_edges(bad)}")
    absent = [e for e in expected if (e[1], e[0]) not in carries or (e[2], e[0]) not in carries]
    if absent:
        raise ValueError(f"{name}: expected edges between legs that do not both carry the unit: {fmt_edges(absent)}")
    if any(e[3] for e in edges):
        raise ValueError(f"{name}: expect.edges has a crossing ({fmt_edges(e for e in edges if e[3])}); "
                         f"a level belongs in expect.cross")
    if {e[:3] for e in edges} & {e[:3] for e in cross}:
        raise ValueError(f"{name}: an edge in both expect.edges and expect.cross")
    if cross and not any(e[3] for e in cross):
        raise ValueError(f"{name}: expect.cross has no crossing (an edge with a level) for its edges to rest on")
    if pending and not pending.missing <= expected:
        raise ValueError(f"{name}: pending missing edges that expect does not list: "
                         f"{fmt_edges(pending.missing - expected)}")
    if pending and pending.extra & expected:
        raise ValueError(f"{name}: pending extra edges that expect lists: {fmt_edges(pending.extra & expected)}")


def all_cases(root: Path = CASES_DIR) -> list[Case]:
    return [load_case(p) for p in sorted(root.iterdir()) if (p / "case.yaml").exists()]
