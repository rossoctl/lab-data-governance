"""reel/pairs.py — the pairs of a changes reel, from two films' plans: each section line whose words or picture changed,
cut from both films where that line is spoken (its own length, 0.4 s either side), under a short note. The lists name the lines
to show (both clips, or only the new one when just the words changed); the shipped lists are v4.3 → v4.4, and the pitch's own
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

# a picture changed: both films' clips; only words changed: the new clip, its note the old words
NOTE = {("I5", "L6"): ("“…The second, for health data, fires on the number: lineage knows it is her medical record number.”", "“The first and third rules fire on her name.” Classification alone")}
DECISION = {
    ("I6", "L9"): ("“The same three rules fire: critical, recommend block.”", "“Three rules fire. The decision: risk level critical, suggested enforcement: block.” · the ring holds O P A's answer"),
    ("I7", "L5"): ("“…Critical, recommend block, on the same three rules…”", "“The decision: risk level critical, suggested enforcement: block…”"),
}
PICTURES = {
    ("I1", "L3"): ("the map's box cuts through report-dispense", "the box holds report-dispense"),
    ("I2", "L2"): ("“One exchange” off its nodes", "“One exchange” on the client and the assistant"),
    ("I5", "L3"): ("the ring on “Found here” and “Carried in”", "the ring on “Found here” only"),
}
WORDS = {("I5", "L6b"): "was “…suggested enforcement block.”: the colon gives the word its pause"}
POLICY = ("I4", "L1")
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
    both = lambda n, key, nb, na: [["before", *clip(*where(B, n, order[n].index(key))), f"BEFORE · v4.3 · {m(where(B, n, order[n].index(key))[0])}", nb],
                                   ["after", *clip(*where(A, n, order[n].index(key))), f"AFTER · v4.4 · {m(where(A, n, order[n].index(key))[0])}", na]]
    rows = [["card", "v4.3 → v4.4", "level one on classification alone · the risk dashboard\none policy, two apps · a fresh take", 4],
            ["card", "Level one", "the verdict at level one does not need lineage\nthe film no longer leans on what it has not told yet", 4]]
    for (n, key), (nb, na) in NOTE.items(): rows += both(n, key, nb, na)
    rows.append(["card", "One policy", "six rules, graded from critical to low; three matter for this story\nthe same policy over every app", 4])
    rows += both(*POLICY, "“…as three rules.”", "“…as six rules, graded from critical to low. Three of them matter for this story.”")
    rows.append(["card", "The decision", "“risk level critical, suggested enforcement: block”\nsaid the same way at every level", 4])
    for (n, key), was in WORDS.items():
        k = order[n].index(key); ta, da = where(A, n, k); rows.append(["after", *clip(ta, da), f"AFTER · v4.4 · {m(ta)}", was])
    for (n, key), (nb, na) in DECISION.items(): rows += both(n, key, nb, na)
    rows.append(["card", "The pictures", "the tap's outline · the map's box · “One exchange” · the classifier's row", 4])
    rows += [["before", 2.5, 6.5, "BEFORE · v4.3 · 0:03", "the tap's outline does not follow the button"], ["after", 2.5, 6.5, "AFTER · v4.4 · 0:03", "the outline on the button"]]
    for (n, key), (nb, na) in PICTURES.items(): rows += both(n, key, nb, na)
    rows.append(["card", "The risk dashboard", "new, before the end: the summary picture\nthe pharmacy beside a travel booking app, one run, one policy", 4])
    i9 = order["I9"]; t2, _ = where(A, "I9", i9.index("L2")); t5, d5 = where(A, "I9", i9.index("L5"))
    rows.append(["after", round(t2 - PAD, 2), round(t5 + d5 + PAD, 2), f"NEW · v4.4 · {m(t2)}", "the whole section"])
    rows.append(["card", "The take", f"recorded anew from the package's runbook · the same five verdicts\n{m(length(before))} → {m(length(after))}: the new section", 4])
    print(json.dumps(rows, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
