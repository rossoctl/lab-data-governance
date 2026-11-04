"""reel/pairs.py — the pairs of a changes reel, from two films' plans: each section line whose words or picture changed,
cut from both films where that line is spoken (its own length, 0.4 s either side), under a short note. The lists name the lines
to show (both clips, or only the new one when just the words changed); the shipped lists are v4.4 → v4.5, and the pitch's own
tap gets a pair by its time.

    $PYTHON film/long/v4/reel/pairs.py <before.plan.json> <after.plan.json> > pairs.json
    $PYTHON film/long/v4/reel/reel.py <before.mp4> <after.mp4> <out.mp4> pairs.json
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import yaml

# v4.4 → v4.5: two review notes on the risk dashboard (final/sentry-technical-film-v4.5-changes.md); the v4.3 → v4.4 lists are in the v4.4 zip
GRADES = ("I9", "L3")               # the counts: this take has 2 low exchanges (v4.4: none), and the line names them
RULES = ("I9", "L4")                # Sentry's Top rules card: low → critical before, critical → low after (a Sentry fix, not a film edit)
LOWER = ("I9", "L6")                # new: the alerts' rows below the critical ones
PAD = 0.4
LONGEST = 9.0                       # a clip at most this long, from just before its line


def where(plan: dict, n: str, k: int) -> tuple[float, float]:
    """The film time of line k of section n, and its length."""
    w, _, d, _ = plan["sections"][n]["lines"][k]
    return next(at for f, at in plan["film_lines"] if f == w), d


def length(plan: str) -> float:
    """The film's length (the .mp4 beside its plan)."""
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 plan.replace(".plan.json", ".mp4")], capture_output=True, text=True, check=True).stdout)


def m(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def main(before: str, after: str) -> None:
    B, A = json.loads(open(before).read()), json.loads(open(after).read())
    order = {n: list(v) for n, v in yaml.safe_load((pathlib.Path(__file__).resolve().parents[1] / "narration.yaml").read_text()).items()}
    # a section's lines are in its plan in narration.yaml's order (film4.section keeps it)
    clip = lambda t, d: [round(t - PAD, 2), round(min(t + d + PAD, t - PAD + LONGEST), 2)]
    both = lambda n, key, nb, na: [["before", *clip(*where(B, n, order[n].index(key))), f"BEFORE · v4.4 · {m(where(B, n, order[n].index(key))[0])}", nb],
                                   ["after", *clip(*where(A, n, order[n].index(key))), f"AFTER · v4.5 · {m(where(A, n, order[n].index(key))[0])}", na]]
    rows = [["card", "v4.4 → v4.5", "two notes on the risk dashboard\nthe rules' order · the lower-severity alerts · and this take's counts", 4]]
    rows.append(["card", "The counts", "a new take: the dashboard's numbers are this run's\nit shows low exchanges too, and the line names them", 4])
    rows += both(*GRADES, "“12 are high and 10 medium”", "“12 are high, 11 medium and 2 low”")
    rows += [["card", "Top rules", "Sentry listed them by a count it does not show: low first\nnow by severity, then traces, as the Rules page in I4", 4]]
    rr = both(*RULES, "low → critical", "critical → low")   # to the next line: the camera holds on the card after its short line
    for r, P in zip(rr, (B, A)): r[2] = round(where(P, "I9", order["I9"].index("L5"))[0] - 0.2, 2)
    rows += rr
    rows.append(["card", "The alerts", "below the three critical sessions, the lower-severity traces\nlisted for follow-up", 4])
    i9 = order["I9"]; t5, _ = where(A, "I9", i9.index("L5")); t6, d6 = where(A, *LOWER[:1], i9.index(LOWER[1]))
    rows.append(["after", round(t5 - PAD, 2), round(t6 + d6 + PAD, 2), f"NEW · v4.5 · {m(t6)}", "“Below them, lower-severity traces, listed for follow-up.”"])
    rows.append(["card", "The take", f"recorded anew from an empty machine by the package's runbook · the same five verdicts\n{m(length(before))} → {m(length(after))}", 4])
    print(json.dumps(rows, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
