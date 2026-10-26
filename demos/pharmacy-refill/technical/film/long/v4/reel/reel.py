"""reel/reel.py — a changes reel for a reviewer: every change from one cut to the next as a before/after pair cut from the
two films, 2 s around the moment it shows, each under a BEFORE / AFTER badge saying what changed. Badges in the pitch's
own Fira Sans (fonts/, from lab-dg demo/pharmacy-refill-pitch, OFL). REEL below is the list of changes: the one shipped
is v4 → v4.2 (two review notes); for a new reel, write your own pairs (times from the two films' plan.json line lists).

    $PYTHON film/long/v4/reel/reel.py <before.mp4> <after.mp4> <out.mp4>
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
if len(sys.argv) != 4:
    sys.exit(__doc__)
BEFORE, AFTER, OUT = (pathlib.Path(a).resolve() for a in sys.argv[1:4])
TMP = HERE / "build"; TMP.mkdir(exist_ok=True)
F = lambda w, s: ImageFont.truetype(str(HERE / "fonts" / f"FiraSans-{w}.ttf"), s)
MONO = lambda s: ImageFont.truetype(str(HERE / "fonts" / "FiraMono-Medium.ttf"), s)
INK, MUTED, GOLD, BG = (236, 233, 224), (160, 166, 172), (232, 198, 106), (17, 20, 24)

# (film, start, end, badge, note[, crop]): every change as a before (v4) / after (v4.2) pair, 2 s around the moment the
# change is seen or heard; times from the two films' plan.json line lists and Whisper's word times (the "Read" of each
# "Read alone"). A removed line has a zero-length "after": 2 s either side of where it began. A crop (w:h:x:y) zooms into
# a detail, and a ring (x0, y0, x1, y1, in the reel's frame) circles it: the caption pair changes only in the picture.
# Each clip is shown at 90 % under its badge, so the badge covers nothing.
CAP = "960:540:0:180"
CAP_RING = (206, 211, 1125, 263)          # around the caption's old extent (its ink: x 226–1105, y 227–247, measured)
REEL = [
    (BEFORE, 184.94, 188.94, "BEFORE · v4 · 3:07", "picture only · the caption over the list: “WRITTEN BY PEOPLE · A DEPLOYMENT SETTING”", CAP, CAP_RING),
    (AFTER, 184.94, 188.94, "AFTER · v4.2 · 3:07", "picture only · the caption over the list: “A DEPLOYMENT SETTING”", CAP, CAP_RING),
    (BEFORE, 210.09, 219.16, "BEFORE · v4 · 3:32", "“People write the rules and that list. Everything else, Sentry works out from the traffic.”"),
    (AFTER, 210.09, 214.09, "AFTER · v4.2 · 3:32", "the line is gone"),
    (BEFORE, 367.98, 372.24, "BEFORE · v4 · 6:10", "“Read alone” said as “reed”"),
    (AFTER, 362.60, 366.88, "AFTER · v4.2 · 6:05", "“Read alone” said as “red”"),
    (BEFORE, 454.67, 458.85, "BEFORE · v4 · 7:37", "“Read alone” said as “reed”"),
    (AFTER, 449.54, 453.80, "AFTER · v4.2 · 7:32", "“Read alone” said as “red”"),
]


def card(title: str, sub: str, path: pathlib.Path) -> None:
    im = Image.new("RGB", (1920, 1080), BG); d = ImageDraw.Draw(im)
    d.text((160, 330), "SENTRY · TECHNICAL DEMO · CHANGES", font=MONO(26), fill=GOLD)
    d.text((160, 390), title, font=F("Bold", 76), fill=INK)
    d.multiline_text((160, 500), sub, font=F("Regular", 38), fill=MUTED, spacing=14)
    im.save(path)


def badge(text: str, note: str, after: bool, path: pathlib.Path) -> None:
    fill, ink = ((23, 51, 35, 235), (127, 224, 160)) if after else ((70, 30, 26, 235), (255, 157, 143))
    f1, f2 = F("SemiBold", 34), F("Regular", 26)
    w1 = f1.getlength(text)
    im = Image.new("RGBA", (int(w1 + 48 + f2.getlength(note) + 24), 64), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w1 + 40, 63), 14, fill=fill, outline=ink + (255,), width=2)
    d.text((20, 10), text, font=f1, fill=ink); d.text((w1 + 64, 16), note, font=f2, fill=(200, 202, 204))
    im.save(path)


def enc(args: list[str], out: pathlib.Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-r", "30", "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(out)], check=True)


parts = []
for i, row in enumerate(REEL):
    seg = TMP / f"{i:02d}.mp4"
    if row[0] == "card":
        _, title, sub, dur = row
        png = TMP / f"{i:02d}.png"; card(title, sub, png)
        enc(["-loop", "1", "-t", str(dur), "-i", str(png), "-f", "lavfi", "-t", str(dur), "-i", "anullsrc=r=48000:cl=stereo",
             "-filter_complex", f"[0:v]fade=in:d=0.3,fade=out:st={dur - 0.3}:d=0.3[v]", "-map", "[v]", "-map", "1:a"], seg)
    else:
        film, a, b, text, note, *extra = row
        crop, ring = (extra + [None, None])[:2]
        png = TMP / f"{i:02d}-badge.png"; badge(text, note, film == AFTER, png); dur = b - a
        zoom = f"crop={crop}," if crop else ""
        rpng = TMP / f"{i:02d}-ring.png"; im = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
        if ring:
            ImageDraw.Draw(im).rounded_rectangle(ring, 12, outline=(127, 224, 160, 255) if film == AFTER else (255, 120, 104, 255), width=5)
        im.save(rpng)
        enc(["-ss", str(a), "-to", str(b), "-i", str(film), "-i", str(png), "-i", str(rpng), "-filter_complex",
             f"[0:v]{zoom}scale=1728:972,pad=1920:1080:96:96:color=0x111418[c];[c][1:v]overlay=96:16,crop=1920:1080:0:0[o];[o][2:v]overlay=0:0,crop=1920:1080:0:0,"   # this ffmpeg's overlay can return one row too many
             f"fade=in:d=0.2,fade=out:st={dur - 0.2:.2f}:d=0.2[v];"
             f"[0:a]afade=in:d=0.15,afade=out:st={dur - 0.15:.2f}:d=0.15[a]", "-map", "[v]", "-map", "[a]"], seg)
    parts.append(seg)
lst = TMP / "list.txt"; lst.write_text("".join(f"file '{p}'\n" for p in parts))
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(OUT)], check=True)
print(OUT.name, subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(OUT)],
                               capture_output=True, text=True).stdout.strip(), "s")
