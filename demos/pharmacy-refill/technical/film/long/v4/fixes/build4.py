"""fixes/build4.py — the pitch with this take's card fixes (fixes/cards.py → <take>/cards/cards.json) laid in:
<take>/pitch-fixed.mp4, the pitch film4.py reads. Its sound is the pitch's, copied; only the cards' rectangles change, in
the frames each card is on screen. Then the nine sections' door frames (<take>/src/pitch-<n>.png, n = film4.CUT) are cut
from it.

    FILM_TAKE=… $PYTHON film/long/v4/fixes/build4.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
V4 = HERE.parent
PITCH = V4.parents[1] / "pitch-2m35" / "master" / "sentry_pitch_demo.mp4"   # the master (lab-dg demo/pharmacy-refill-pitch)
sys.path.insert(0, str(V4.parent))
from env import TAKE  # noqa: E402
CUT = {"I1": 626, "I2": 626, "I3": 974, "I4": 1180, "I5": 1945, "I6": 3078, "I7": 3556, "I8": 3904, "I9": 4468}   # = film4.CUT
OUT = TAKE / "pitch-fixed.mp4"
cards = json.loads((TAKE / "cards" / "cards.json").read_text())
ins, chain, last = ["-i", str(PITCH)], [], "0:v"
for j, c in enumerate(cards.values()):
    ins += ["-i", str(TAKE / "cards" / c["mov"])]
    chain.append(f"[{j + 1}:v]setpts=PTS-STARTPTS+{c['n']}/30/TB[m{j}]")
    chain.append(f"[{last}][m{j}]overlay={c['x']}:{c['y']}:eof_action=pass:repeatlast=0[w{j}]")
    last = f"w{j}"
subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(chain), "-map", f"[{last}]", "-map", "0:a",
                "-c:v", "libx264", "-crf", "10", "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "copy", str(OUT)], check=True)
(TAKE / "src").mkdir(exist_ok=True)
for n in CUT.values():
    f = TAKE / "src" / f"pitch-{n}.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(OUT), "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", str(f)], check=True)
print(OUT, "+ door frames", ", ".join(f"pitch-{n}" for n in CUT.values()))
