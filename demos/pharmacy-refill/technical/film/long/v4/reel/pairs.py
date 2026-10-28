"""reel/pairs.py — the pairs of a changes reel, from two films' plans: each section line whose words or picture changed,
cut from both films where that line is spoken (its own length, 0.4 s either side), under a short note. NOTES lists the
lines to show (PICTURES: both clips; WORDS: the new one); the shipped lists are v4.2 → v4.3. The order change and the
pace get a card each, and the order change one pair of its own (the pitch's opening in both films).

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
PICTURES = {
    ("I1", "L2"): ("caption “each in its own pod, beside its sidecar”", "caption “each in its own pod”"),
    ("I1", "L3"): ("map card “the pharmacy app on rossoctl … beside its own sidecar”", "map card “the pharmacy app … each in its own pod”"),
    ("I4", "L2"): ("“Rule D G one…” · caption “DG-001”, ring on the conditions", "“The first rule…” · its risk level and enforcement"),
    ("I4", "L5"): ("“Here is rule D G one, in Rego…” · our Rego panel", "“…the first rule's conditions, as Sentry shows them”"),
    ("I4", "L6"): ("“…an item tagged P I I…” · the Rego lines", "“…personal data…” · the conditions' rows"),
    ("I5", "L7"): ("“…In this setup it does not hold the call…” · POST → 200", "“Sentry raises an alert and recommends the block.”"),
    ("I7", "L5"): ("caption “DG-001 · DG-002 · DG-004”", "caption “the same three rules”"),
}
WORDS = {
    ("I2", "L2"): "was “Each node is an agent, a tool, the model, or a vendor.”",
    ("I4", "L1"): "was “In Sentry, the company policy is written down as three rules.”",
    ("I4", "L3"): "was “Rule D G two says the same for health data, and D G four…”",
    ("I5", "L1"): "was “Level one, in Sentry…” · caption “Level 1 · version A”",
    ("I5", "L3"): "was “…A trained model finds each value…, and a catalog gives each type…”",
    ("I5", "L6"): "was “Rules D G one and D G four fire… D G two… level two shows why.”",
    ("I6", "L1"): "was “Level two, in Sentry.” · caption “Level 2 · version B”",
    ("I6", "L4"): "was “…Here the label is born.”",
    ("I7", "L1"): "was “Level three, in Sentry… In this demo it runs a few minutes after the refill.”",
    ("I8", "L1"): "was “The fixed app, in Sentry.”",
    ("I9", "L1"): "was “…from one run on Rosso C T L…”",
}
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
    rows = [["card", "v4.2 → v4.3", "the review notes of the technical film\nthe order · the words · the pictures · the pace", 4],
            ["card", "The order", "“That one tap…” now plays before “Under the hood”:\nthe pitch's opening runs whole, then the app, then Sentry's console", 4]]
    tap = lambda p: next(at for (f, at), txt in zip(p["film_lines"], p["script"]) if txt.startswith("That one tap"))
    rows += [["before", round(tap(B) - 9.5, 2), round(tap(B) + 3.5, 2), f"BEFORE · v4.2 · {m(tap(B) - 9.5)}", "the app's section cuts in before “That one tap”"],
             ["after", round(tap(A) - 4.5, 2), round(tap(A) + 8.5, 2), f"AFTER · v4.3 · {m(tap(A) - 4.5)}", "“That one tap” follows “…an agentic flow”"]]
    clip = lambda t, d: [round(t - PAD, 2), round(min(t + d + PAD, t - PAD + LONGEST), 2)]
    rows.append(["card", "The pictures", "captions without the sidecar · the rule as Sentry's own page shows it\n"
                 "Sentry's verdict instead of the HTTP status · no rule ids in our captions", 4])
    for (n, key), (nb, na) in PICTURES.items():
        k = order[n].index(key); (tb, db), (ta, da) = where(B, n, k), where(A, n, k)
        rows += [["before", *clip(tb, db), f"BEFORE · v4.2 · {m(tb)}", nb], ["after", *clip(ta, da), f"AFTER · v4.3 · {m(ta)}", na]]
    rows.append(["card", "The words", "“the first / second / third rule” · “what actually happens inside Sentry”\n"
                 "Kubernetes, not rossoctl, but where its UI is on screen · two sentences fewer", 4])
    for (n, key), was in WORDS.items():
        k = order[n].index(key); ta, da = where(A, n, k)
        rows.append(["after", *clip(ta, da), f"AFTER · v4.3 · {m(ta)}", was])
    rows.append(["card", "The pace", f"{m(length(before))} → {m(length(after))} · gaps between lines 0.3 → 0.2 s · quicker exits\n"
                 "the pitch's pauses at most 1.2 s · the voice's speed unchanged", 4])
    print(json.dumps(rows, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
