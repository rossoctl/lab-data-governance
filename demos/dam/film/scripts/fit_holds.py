"""Fit the narration's holds to a take, so typing plays at its real speed.

  python3 scripts/fit_holds.py <take> [voice]

The film plays each typed prompt (and each typed description) at 1x, anchored to the narration line
that introduces it, so that line must be followed by enough silence for the typing to finish (and,
for a prompt, for the agent's work to fast-forward) before the next line speaks. Reads the take's
events and the voice's timeline.json (from a first tts.py pass), and rewrites the {hold=N} of those
lines in narration.txt. Holds only add silence after their own line, so one more tts.py pass gives
the final timing.
"""
import json
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parent.parent
take = sys.argv[1]
voice = sys.argv[2] if len(sys.argv) > 2 else "af_heart"
events = {e["step"]: e for e in json.loads((root / f"build/ui/{take}/events.json").read_text())}
lines = json.loads((root / f"voice/{voice}/timeline.json").read_text())

# (opening of the line the typing is anchored to, step, extra seconds after the typing ends)
# Prompts: 0.5 s lead-in, the typing, then 0.4 s to send and at least 1.4 s of fast-forward.
# Descriptions: the typing is anchored to the line itself (read aloud while typed), plus 0.6 s.
ANCHORS = [
    ("Issue triage assistant", "describe", 0.6, "self"),
    ("They edit the last sentence", "s2-desc", 0.6, "self"),
    ("We ask it to list", "s0-list", 2.3, "ask"),
    ("Now, let's ask it to close", "s0-close", 2.3, "ask"),
    ("Next, we ask it to search", "s0-search", 2.3, "ask"),
    ("Now, let's see what happens", "s1-close", 2.3, "ask"),
    ("What about adding", "s1-comment", 2.3, "ask"),
    ("And the search with", "s1-search", 2.3, "ask"),
    ("Now, when we ask for the same", "s2-comment", 2.3, "ask"),
    ("And closing the issue", "s2-close", 2.3, "ask"),
    ("Same search, same agent", "s3-search", 2.3, "ask"),
    ("And listing the open issues", "s3-list", 2.3, "ask"),
]
GAP_LINE = 0.30

path = root / "scripts/narration.txt"
src = path.read_text().splitlines()
text_lines = [l for l in src if l.strip()]
assert len(text_lines) == len(lines), (len(text_lines), len(lines))
HOLD = re.compile(r"\s*\{hold=[0-9.]+\}\s*$")

for opening, step, extra, mode in ANCHORS:
    idx = [i for i, l in enumerate(lines) if l["text"].startswith(opening)]
    assert len(idx) == 1, (opening, idx)
    i = idx[0]
    ev = events[step]
    typing = ev["typed"] - ev["typing"]
    spoken = lines[i]["end"] - lines[i]["start"]
    # "ask": the typing starts 0.3 s before the line; "self": with it.
    need = (0.5 + typing + extra - 0.3) if mode == "ask" else (typing + extra)
    hold = round(max(0.0, need - spoken - GAP_LINE), 1)
    line = text_lines[i]
    new = HOLD.sub("", line) + (f" {{hold={hold}}}" if hold > 0 else "")
    src[src.index(line)] = new
    text_lines[i] = new
    print(f"{step:11s} typing {typing:5.1f}s  line {spoken:4.1f}s  hold {hold}")

path.write_text("\n".join(src) + "\n")
