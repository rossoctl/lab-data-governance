"""Collect every case's outcome and write ``tests/system/REPORT.md`` plus one
``result.md`` per case directory, grids included."""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

from .cases import Case
from .engine import Observation
from .grid import render_grid

REPORT = Path(__file__).parent / "REPORT.md"


@dataclasses.dataclass
class Outcome:
    case: Case
    on: Observation | None
    off: Observation | None
    status: str                    # GREEN | RED | RED-BY-DESIGN | PENDING
    reasons: list[str]
    edge_diff: tuple[set, set]     # (missing vs expected, extra vs expected)
    classifier_notes: list[str]


class Results:
    def __init__(self) -> None:
        self.outcomes: list[Outcome] = []


def _types(case: Case, unit: str) -> str:
    return "|".join([case.units[unit]] + sorted(case.accepts[unit] - {case.units[unit]}))


def _edges(edges) -> str:
    return ", ".join(f"{u} {a}>{b}" for u, a, b in sorted(edges))


def premise(case: Case, on: Observation) -> list[str]:
    """Where the model's findings break what the case states about the classifier (red: the case then tests
    something other than it says)."""
    notes: list[str] = []
    for want, cells in ((True, case.must_see), (False, case.must_miss)):
        for leg_index, units in sorted(cells.items()):
            c = on.legs[leg_index].classification if leg_index in on.legs else None
            if c is None:
                notes.append(f"leg {leg_index}: no classification row; the case states {', '.join(units)} there")
                continue
            for u in units:
                if case.labelled(u, c["findings"]) != want:
                    notes.append(f"leg {leg_index}: the model did NOT label {u} ({_types(case, u)}); "
                                 f"found {sorted(c['findings'])}" if want else
                                 f"leg {leg_index}: the model DID label {u} ({_types(case, u)}); "
                                 f"the case assumed it would not")
    return notes


def judge(case: Case, on: Observation, off: Observation) -> Outcome:
    """Exact: the observation is compared with what the case states and nothing else. Problems reading the rows,
    a broken classifier premise and on/off runs that drew different edges are red whatever the case says;
    a pending (or cannot) case is excused only for exactly the differences it states."""
    wrong_verdict: set[str] = set()
    reasons: list[str] = []
    if case.line.sink is not None:
        for mode, obs in (("on", on), ("off", off)):
            if obs.verdict != case.verdict[mode]:
                wrong_verdict.add(mode)
                reasons.append(f"verdict {mode}: want {case.verdict[mode]}, got {obs.verdict}")
    missing = case.edges - on.edges
    extra = on.edges - case.edges
    if missing:
        reasons.append("edges missing: " + _edges(missing))
    if extra:
        reasons.append("edges extra: " + _edges(extra))

    hard = [f"on: {p}" for p in on.problems] + [f"off: {p}" for p in off.problems]
    if on.edges != off.edges:
        hard.append(f"the on and off runs drew different edges: on only {_edges(on.edges - off.edges) or '-'}, "
                    f"off only {_edges(off.edges - on.edges) or '-'}")
    notes = premise(case, on)
    hard += [f"classifier premise: {n}" for n in notes]

    excuse = case.pending or case.cannot
    word = "pending" if case.pending else "cannot"
    diff = (frozenset(missing), frozenset(extra), frozenset(wrong_verdict))
    if hard:
        status = "RED"
        reasons = hard + reasons
    elif excuse is None:
        status = "GREEN" if not any(diff) else "RED"
    elif not any(diff):
        status = "RED"
        reasons.insert(0, f"passes while marked {word} ({excuse.reason}): drop the {word} block")
    elif diff == (excuse.missing, excuse.extra, excuse.verdict):
        status = "PENDING" if case.pending else "RED-BY-DESIGN"
        reasons.insert(0, f"{word}: {excuse.reason}")
    else:
        status = "RED"
        reasons.insert(0, f"differs from what {word} states (missing {_edges(excuse.missing) or '-'}; "
                          f"extra {_edges(excuse.extra) or '-'}; verdict {sorted(excuse.verdict) or '-'})")
    return Outcome(case, on, off, status, reasons, (missing, extra), notes)


def case_markdown(o: Outcome, timings: bool = True) -> str:
    """One case's record. ``timings=False`` (``result.md``) leaves out what changes on every run, so a run that
    changes nothing rewrites no case file."""
    c = o.case
    authored = (f"  ·  classifier **authored**: the model's findings subtracted at legs {sorted(c.must_miss)}"
                if c.authored else "")
    lines = [f"# {c.id} — {c.title}", "", f"cell `{c.cell}`  ·  status **{o.status}**{authored}", "",
             f"```\n{c.line_text}\n```", ""]
    lines += ["```", render_grid(c, o.on, o.off), "```", ""]
    if o.reasons:
        lines += ["**Reasons.** " + " · ".join(o.reasons), ""]
    if o.on:
        lines += ["**Rows (inherited labels on).**", "", "| leg | who | classification | inherited labels (from leg) | risk |", "|---|---|---|---|---|"]
        for leg in c.line.legs:
            if leg.invisible:
                lines.append(f"| {leg.index} | ..{leg.to}.. | — | — | — |")
                continue
            r = o.on.legs.get(leg.index)
            if r is None:
                lines.append(f"| {leg.index} | {leg.label()} | (not derived) | | |")
                continue
            cls = "—" if r.classification is None else (
                f"{r.classification['sensitivity']} " + " ".join(f"{t}={v}" for t, v in r.classification["findings"]))
            inh = ", ".join(f"{e['unit']}={e['value']} ({e['label']}, from {e['from']})" for e in r.inherited) or "—"
            risk = "—"
            if leg.sink and r.risk:
                risk = f"{r.risk['risk_level']} {r.risk['enforcement_type']} {r.risk['rules']} v{r.risk['version']}"
            lines.append(f"| {leg.index} | {leg.label()}{'⚑' if leg.sink else ''} | {cls} | {inh} | {risk} |")
        lines.append("")
        t = o.on.timings
        if timings:
            lines.append(f"**Timing (on).** total {t.get('total')} s: "
                         + ", ".join(f"{k} {v}" for k, v in t.items() if k != "total"))
        lines += [f"OPA calls: {o.on.opa_calls}", ""]
    if c.notes:
        lines += ["**Notes.** " + c.notes.strip(), ""]
    return "\n".join(lines)


def write(results: Results, expected_cases: int | None = None) -> None:
    import os
    now = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    tag = os.environ.get("SYSTEM_REPORT_TAG", "")
    full = expected_cases is None or len(results.outcomes) >= expected_cases
    if not full and not tag:
        tag = "partial"   # a subset ran (-k, -x): never overwrite the full record
    env = {k: v for k, v in os.environ.items()   # not RISK_INHERITED_LABELS: each run sets the mode itself
           if k.startswith(("LABEL_", "RISK_")) and k != "RISK_INHERITED_LABELS"}
    target = REPORT if not tag else REPORT.with_name(f"REPORT-{tag}.md")
    rows = ["# System tests — label lineage, exact match" + (f" ({tag})" if tag else ""), "",
            f"*Generated {now} by `pytest tests/system`; environment {env}.*", "",
            "| case | cell | classifier | line | on / off | status | note |", "|---|---|---|---|---|---|---|"]
    for o in results.outcomes:
        v = f"{o.on.verdict if o.on else '?'} / {o.off.verdict if o.off else '?'}"
        note = o.reasons[0] if o.reasons else ""
        clf = "authored" if o.case.authored else "real"
        rows.append(f"| [{o.case.id}](cases/{o.case.id}/result.md) | `{o.case.cell}` | {clf} | `{o.case.line_text}` | {v} | **{o.status}** | {note} |")
    counts = {}
    for o in results.outcomes:
        counts[o.status] = counts.get(o.status, 0) + 1
    rows += ["", "**" + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())) + "**", ""]
    for o in results.outcomes:
        rows += ["---", "", case_markdown(o)]
        if full and not tag:
            (o.case.dir / "result.md").write_text(case_markdown(o, timings=False) + "\n")
    target.write_text("\n".join(rows) + "\n")
