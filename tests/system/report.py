"""Judge a case and write its record, ``cases/<id>/result.md``, grids included."""

from __future__ import annotations

import dataclasses

from .cases import Case
from .engine import Observation
from .grid import render_grid


@dataclasses.dataclass
class Outcome:
    case: Case
    on: Observation | None
    off: Observation | None
    status: str                    # GREEN | RED | PENDING
    reasons: list[str]
    edge_diff: tuple[set, set]     # (missing vs expected, extra vs expected)
    classifier_notes: list[str]


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
    """Exact: GREEN when the observation is what the case states. Problems reading the rows, a broken classifier
    premise and on/off runs that drew different edges are RED whatever the case says; a pending case is PENDING
    only when it differs in exactly what it states, and RED when it differs otherwise or passes."""
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
    notes = [f"{mode}: {n}" for mode, obs in (("on", on), ("off", off)) for n in premise(case, obs)]
    hard += [f"classifier premise, {n}" for n in notes]

    p = case.pending
    diff = (frozenset(missing), frozenset(extra), frozenset(wrong_verdict))
    if hard:
        status, reasons = "RED", hard + reasons
    elif p is None:
        status = "RED" if reasons else "GREEN"
    elif not any(diff):
        status = "RED"
        reasons.insert(0, f"passes while marked pending ({p.reason}): drop the pending block")
    elif diff == (p.missing, p.extra, p.verdict):
        status = "PENDING"
        reasons.insert(0, f"pending: {p.reason}")
    else:
        status = "RED"
        reasons.insert(0, f"differs from what pending states (missing {_edges(p.missing) or '-'}; "
                          f"extra {_edges(p.extra) or '-'}; verdict {sorted(p.verdict) or '-'})")
    return Outcome(case, on, off, status, reasons, (missing, extra), notes)


def case_markdown(o: Outcome) -> str:
    """One case's record, ``cases/<id>/result.md``. Nothing in it changes from run to run unless the result does
    (timings go to the test's output), so a run that changes nothing rewrites no file."""
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
        lines += [f"Risk observer calls: {o.on.observer_calls}", ""]
    if c.notes:
        lines += ["**Notes.** " + c.notes.strip(), ""]
    return "\n".join(lines)



def write(o: Outcome) -> None:
    (o.case.dir / "result.md").write_text(case_markdown(o) + "\n")
