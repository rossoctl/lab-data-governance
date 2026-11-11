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
    origin_edges: set = dataclasses.field(default_factory=set)  # edges from an earlier holding of the same unit, beside the expected one


class Results:
    def __init__(self) -> None:
        self.outcomes: list[Outcome] = []


def _types(case: Case, unit: str) -> str:
    return "|".join([case.units[unit]] + sorted(case.accepts[unit] - {case.units[unit]}))


def judge(case: Case, on: Observation, off: Observation) -> Outcome:
    reasons: list[str] = []
    want_on, want_off = case.verdict.get("on"), case.verdict.get("off")
    ok = True
    if case.line.sink is not None:
        if want_on and on.verdict != want_on:
            ok = False
            reasons.append(f"verdict on: want {want_on}, got {on.verdict}")
        if want_off and off.verdict != want_off:
            ok = False
            reasons.append(f"verdict off: want {want_off}, got {off.verdict}")
    expected = case.expected_edges
    observed = on.edges
    missing = expected - observed
    extra = observed - expected
    # An origin edge: the same unit reaches the same leg from an earlier holding too
    # (the agent remembers). The short chain says where the value was born, the long
    # one which way it went; both are true. The case declares them (``also``), the
    # judge never guesses them. Reported, not red.
    origin_edges = extra & case.also
    extra -= origin_edges
    if missing:
        reasons.append("edges missing: " + ", ".join(f"{u} {a}>{b}" for u, a, b in sorted(missing)))
    if extra:
        reasons.append("edges extra: " + ", ".join(f"{u} {a}>{b}" for u, a, b in sorted(extra)))
    if origin_edges:
        reasons.append("also drawn, from an earlier holding (where it was born): "
                       + ", ".join(f"{u} {a}>{b}" for u, a, b in sorted(origin_edges)))
    if missing or extra:
        ok = False
    notes: list[str] = []
    for leg_index, units in case.must_see.items():
        c = on.legs[leg_index].classification if leg_index in on.legs else None
        tags = {t for t, _ in (c or {"findings": []})["findings"]}
        for u in units:
            if not case.labelled(u, tags):
                notes.append(f"leg {leg_index}: the model did NOT label {u} ({_types(case, u)}); found {sorted(tags)}")
    for leg_index, units in case.must_miss.items():
        c = on.legs[leg_index].classification if leg_index in on.legs else None
        tags = {t for t, _ in (c or {"findings": []})["findings"]}
        for u in units:
            if case.labelled(u, tags):
                notes.append(f"leg {leg_index}: the model DID label {u} ({_types(case, u)}); the case assumed it would not")
    if case.pending and not ok:
        status = "PENDING"
        reasons.insert(0, f"pending: {case.pending}")
    elif case.pending and ok:
        status = "RED"
        reasons.insert(0, f"passes while marked pending ({case.pending}): drop the pending line")
    elif ok:
        status = "GREEN"
    elif case.cannot:
        status = "RED-BY-DESIGN"
        reasons.insert(0, f"cannot: {case.cannot}")
    else:
        status = "RED"
    return Outcome(case, on, off, status, reasons, (missing, extra), notes, origin_edges)


def case_markdown(o: Outcome) -> str:
    c = o.case
    authored = (f"  ·  classifier **authored**: the model's findings subtracted at legs {sorted(c.must_miss)}"
                if c.authored else "")
    lines = [f"# {c.id} — {c.title}", "", f"cell `{c.cell}`  ·  status **{o.status}**{authored}", "",
             f"```\n{c.line_text}\n```", ""]
    lines += ["```", render_grid(c, o.on, o.off), "```", ""]
    if o.reasons:
        lines += ["**Reasons.** " + " · ".join(o.reasons), ""]
    if o.classifier_notes:
        lines += ["**Classifier calibration.**"] + [f"- {n}" for n in o.classifier_notes] + [""]
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
        lines += [f"**Timing (on).** total {t.get('total')} s: " + ", ".join(f"{k} {v}" for k, v in t.items() if k != "total"),
                  f"OPA calls: {o.on.opa_calls}", ""]
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
            (o.case.dir / "result.md").write_text(case_markdown(o) + "\n")
    target.write_text("\n".join(rows) + "\n")
