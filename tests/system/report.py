"""Judge a case and write its record, ``cases/<id>/result.md``, grids included."""

from __future__ import annotations

import dataclasses

from .cases import MODES, Case, fmt_edges
from .classify import UncalibratedPayload, recorded_findings
from .engine import NOT_ON_BRANCH, LegRows, Observation, flagged_response_problems
from .grid import render_grid
from .render import render


@dataclasses.dataclass
class Outcome:
    case: Case
    runs: dict[str, Observation]   # mode -> its run; empty for a held case
    status: str                    # GREEN | RED | PENDING
    reasons: list[str]
    edge_diff: tuple[set, set]     # the cross run's (missing vs expected, extra vs expected)
    classifier_notes: list[str]


def _types(case: Case, unit: str) -> str:
    return "|".join([case.units[unit]] + sorted(case.accepts[unit] - {case.units[unit]}))


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


def calibration_problems(case: Case) -> list[str]:
    """What the case's files say against each other, read without a run: every payload calibrated and the authored
    record the one the case states now (:func:`classify.recorded_findings`), the classifier premise, the types
    the evidence states (R8), a kind stated wherever a crossing reaches (a run draws one there, and is red on a
    crossed label at a cell the case does not state), and nothing labelled in the flagged exchange's response. A
    held case is judged on this and on its ``pending.held``."""
    renderer = render(case.id, case.units, case.line)
    try:
        found = recorded_findings(case, renderer)
    except (ValueError, UncalibratedPayload) as exc:
        return [f"calibration: {exc.args[0]}"]
    except KeyError as exc:
        return [f"calibration: findings.json has no {exc.args[0]!r}"]
    except OSError as exc:
        return [f"calibration: {exc}"]
    legs = {i: LegRows(i, None, "request", None, {"findings": f}, [], None) for i, f in found.items()}
    notes = premise(case, Observation("files", legs, set(), [], None, {}, 0))
    if case.line.sink:
        notes += flagged_response_problems(renderer.rendered, legs, case.line.sink.index)
    for i, cells in case.evidence.items():
        for unit, kinds in cells.items():
            got = {f"classified {t}" for t, text in found[i] if text == case.values[unit]}
            if got != {k for k in kinds if k.startswith("classified")}:
                notes.append(f"leg {i} {unit} states {sorted(kinds)}, the model gave {sorted(got)}")
    reached = {(to, unit) for unit, _, to, _ in case.cross}
    notes += [f"leg {to} {unit}: a crossing reaches it and expect.evidence states no kind there"
              for to, unit in sorted(reached) if not case.evidence.get(to, {}).get(unit)]
    return notes


def _kinds(kinds) -> str:
    return "[" + ", ".join(sorted(kinds)) + "]"


def judge(case: Case, runs: dict[str, Observation]) -> Outcome:
    """Exact: GREEN when every run is what the case states. The conv run (the cross switch off) draws exactly
    ``expect.edges``; the cross run draws those and ``expect.cross``, with the stated evidence and marks; the off run
    (the cross switch on, inherited labels off) draws exactly what the cross run draws, and only its verdict differs.
    Problems reading the rows, a broken classifier premise, an edge drawn twice and an off or conv run with other
    edges are RED whatever the case says; a pending case is PENDING only when it differs in exactly what it states,
    and RED when it differs otherwise or passes."""
    wrong_verdict: set[str] = set()
    reasons: list[str] = []
    if case.line.sink is not None:
        for mode in MODES:
            if runs[mode].verdict != case.verdict[mode]:
                wrong_verdict.add(mode)
                reasons.append(f"verdict {mode}: want {case.verdict[mode]}, got {runs[mode].verdict}")
    cross = runs["cross"]
    missing = (case.edges | case.cross) - cross.edges
    extra = cross.edges - (case.edges | case.cross)
    levels = {e[:3]: e[3] for e in extra}
    reasons += [f"level of edge {u} {a}>{b}: want {lv or 'none'}, drawn {levels[(u, a, b)] or 'none'}"
                for u, a, b, lv in sorted(missing, key=str) if (u, a, b) in levels]
    if missing:
        reasons.append("edges missing: " + fmt_edges(missing))
    if extra:
        reasons.append("edges extra: " + fmt_edges(extra))
    wrong_evidence = set()
    for leg, cells in sorted(case.evidence.items()):
        for unit, kinds in sorted(cells.items()):
            got = cross.evidence.get(leg, {}).get(unit, frozenset())
            if got != kinds:
                wrong_evidence.add(leg)
                reasons.append(f"evidence at {leg} {unit}: want {_kinds(kinds)}, got {_kinds(got)}")
    for leg, cells in sorted(cross.evidence.items()):     # a crossed label nobody stated is wrong wherever it is
        for unit, kinds in sorted(cells.items()):
            if unit not in case.evidence.get(leg, {}) and any(k.startswith("crossed") for k in kinds):
                wrong_evidence.add(leg)
                reasons.append(f"evidence at {leg} {unit}: not stated, got {_kinds(kinds)}")
    wrong_marks = {leg for leg in set(case.marks) | set(cross.marks)
                   if case.marks.get(leg, frozenset()) != cross.marks.get(leg, frozenset())}
    reasons += [f"marks at {leg}: want {_kinds(case.marks.get(leg, ()))}, got {_kinds(cross.marks.get(leg, ()))}"
                for leg in sorted(wrong_marks)]

    hard = [f"{mode}: {p}" for mode in MODES for p in runs[mode].problems]
    for mode, want, what in (("conv", case.edges, "expect.edges (with the cross switch off only the "
                                                  "conversation's own)"),
                             ("off", cross.edges, "the cross run (inherited labels off changes the verdict only)")):
        if runs[mode].edges != want:
            hard.append(f"the {mode} run drew other edges than {what}: "
                        f"missing {fmt_edges(want - runs[mode].edges) or '-'}, "
                        f"extra {fmt_edges(runs[mode].edges - want) or '-'}")
    notes = [f"{mode}: {n}" for mode in MODES for n in premise(case, runs[mode])]
    hard += [f"classifier premise, {n}" for n in notes]

    p = case.pending
    diff = (frozenset(missing), frozenset(extra), frozenset(wrong_verdict), frozenset(wrong_evidence),
            frozenset(wrong_marks))
    if hard:
        status, reasons = "RED", hard + reasons
    elif p is None:
        status = "RED" if reasons else "GREEN"
    elif p.held:
        status = "RED"
        unused = sorted(p.held - case.uses)
        reasons.insert(0, f"held for {sorted(p.held)}, but the case does not use {unused}: hold it only for what it uses"
                       if unused else
                       f"held for {sorted(p.held)}, which this branch has: run it and state what it gets wrong")
    elif not any(diff):
        status = "RED"
        reasons.insert(0, f"passes while marked pending ({p.reason}): drop the pending block")
    elif diff == (p.missing, p.extra, p.verdict, p.evidence, p.marks):
        status = "PENDING"
        reasons.insert(0, f"pending: {p.reason}")
    else:
        status = "RED"
        reasons.insert(0, f"differs from what pending states (missing {fmt_edges(p.missing) or '-'}; "
                          f"extra {fmt_edges(p.extra) or '-'}; verdict {sorted(p.verdict) or '-'}; "
                          f"evidence {sorted(p.evidence) or '-'}; marks {sorted(p.marks) or '-'})")
    return Outcome(case, runs, status, reasons, (missing, extra), notes)


def judge_held(case: Case) -> Outcome:
    """A case that needs production paths this branch lacks is not run. It is PENDING when its pending block names
    exactly those paths and its files agree (:func:`calibration_problems`), and RED otherwise: never skipped in
    silence."""
    problems = calibration_problems(case)
    if problems:
        return Outcome(case, {}, "RED", problems, (set(), set()), [])
    needs = case.uses & set(NOT_ON_BRANCH)
    stated = case.pending.held if case.pending else frozenset()
    if stated == needs:
        return Outcome(case, {}, "PENDING", [f"pending: {case.pending.reason}"]
                       + [f"held, not run: needs {NOT_ON_BRANCH[n]}" for n in sorted(needs)], (set(), set()), [])
    return Outcome(case, {}, "RED", [f"the case needs {sorted(needs)} this branch lacks; expect.pending.held states "
                                     f"{sorted(stated)}"], (set(), set()), [])


def case_markdown(o: Outcome) -> str:
    """One case's record, ``cases/<id>/result.md``. Nothing in it changes from run to run unless the result does
    (timings go to the test's output), so a run that changes nothing rewrites no file."""
    c = o.case
    authored = (f"  ·  classifier **authored**: the model's findings subtracted at legs {sorted(c.must_miss)}"
                if c.authored else "")
    lines = [f"# {c.id} — {c.title}", "", f"cell `{c.cell}`  ·  status **{o.status}**{authored}", "",
             f"```\n{c.line_text}\n```", ""]
    lines += ["```", render_grid(c, o.runs), "```", ""]
    if o.reasons:
        lines += ["**Reasons.** " + " · ".join(o.reasons), ""]
    cross = o.runs.get("cross")
    if cross:
        lines += ["**Rows (cross run).**", "", "| leg | who | classification | inherited labels (from leg) | risk |",
                  "|---|---|---|---|---|"]
        for leg in c.line.legs:
            if leg.invisible:
                lines.append(f"| {leg.index} | ..{leg.to}.. | — | — | — |")
                continue
            r = cross.legs.get(leg.index)
            if r is None:
                lines.append(f"| {leg.index} | {leg.label()} | (not derived) | | |")
                continue
            cls = "—" if r.classification is None else (
                f"{r.classification['sensitivity']} " + " ".join(f"{t}={v}" for t, v in r.classification["findings"]))
            inh = ", ".join(f"{e['unit']}={e['value']} ({e['label']}, from {e['from']}"
                            + (f", {e['level']}" if e.get("level") else "")
                            + (f"; {e['known']})" if e.get("known", "inherited") != "inherited" else ")")
                            for e in r.inherited) or "—"
            risk = "—"
            if leg.sink and r.risk:
                risk = f"{r.risk['risk_level']} {r.risk['enforcement_type']} {r.risk['rules']} v{r.risk['version']}"
            lines.append(f"| {leg.index} | {leg.label()}{'⚑' if leg.sink else ''} | {cls} | {inh} | {risk} |")
        lines.append("")
        if cross.marks:
            marks = " · ".join(f"leg {leg} {_kinds(m)}" for leg, m in sorted(cross.marks.items()))
            lines += [f"Marks (cross run): {marks}", ""]
        lines += [f"Risk observer calls: {cross.observer_calls}", ""]
    if c.notes:
        lines += ["**Notes.** " + c.notes.strip(), ""]
    return "\n".join(lines)


def write(o: Outcome) -> None:
    (o.case.dir / "result.md").write_text(case_markdown(o) + "\n")
