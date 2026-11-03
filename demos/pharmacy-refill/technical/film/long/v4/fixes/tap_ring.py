"""fixes/tap_ring.py — the tap on "Refill my prescription", 3–4 s into the pitch. The master's own highlight doesn't fit its
button: its white outline is wider than the button and sits too high, leaving a pale strip across the button's bottom. This
card puts the clean button back and draws the outline on the button's own edge, keeping the master's timing. Master only: no cluster.
"""
from __future__ import annotations

import pathlib
import subprocess

import numpy as np
from PIL import Image, ImageDraw

FPS = 30
AREA = (170, 410, 370, 136)                 # x, y, w, h in the master: the button and everything its highlight touches
BUTTON = (206, 457, 288, 71, 22)            # x, y, w, h, corner radius of the button in the master
RING = (28, 110, 95)                        # the outline's colour
SEARCH = (60, 200)                          # the frames (2–6.6 s) the highlight is looked for in; the scene changes at 250


def _frames(pitch: pathlib.Path) -> np.ndarray:
    x, y, w, h = AREA
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(pitch), "-vf", f"select='between(n,{SEARCH[0]},{SEARCH[1] - 1})',crop={w}:{h}:{x}:{y}",
                          "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(np.float32)


def build(pitch: pathlib.Path, out: pathlib.Path) -> dict:
    f = _frames(pitch); clean = f[0]
    moved = np.abs(f - clean).mean(axis=(1, 2, 3))
    on = np.flatnonzero(moved > 0.8)            # after it, the area stays 0.6 from the first frame
    if not len(on): raise SystemExit("tap_ring: no highlight found in the master")
    a, b = int(on[0]), int(on[-1]) + 1; n = b - a; x, y, w, h = AREA; bx, by, bw, bh, br = BUTTON; ss = 4
    peak = moved[a:b] / moved[a:b].max()
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-",
                            "-c:v", "libx264", "-crf", "6", "-preset", "fast", "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    for i in range(n):
        ring = Image.new("L", (w * ss, h * ss), 0); d = ImageDraw.Draw(ring)
        g = 5 * ss                                  # a small gap, so the outline frames the button rather than touching it
        d.rounded_rectangle([(bx - x) * ss - g, (by - y) * ss - g, (bx - x + bw) * ss + g, (by - y + bh) * ss + g], (br * ss) + g, outline=255, width=4 * ss)
        al = np.asarray(ring.resize((w, h), Image.LANCZOS), np.float32)[..., None] / 255 * float(peak[i])
        frame = clean * (1 - al) + np.array(RING, np.float32) * al    # the button's own green: white wouldn't show on the app's pale page
        enc.stdin.write(frame.astype(np.uint8).tobytes())
    enc.stdin.close(); enc.wait()
    print(f"tap_ring: frames {SEARCH[0] + a}–{SEARCH[0] + b - 1} of the master")
    return {"mov": out.name, "x": x, "y": y, "n": SEARCH[0] + a}
