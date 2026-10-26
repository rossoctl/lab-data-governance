"""fixes/cards.py — the pitch's Sentry cards that name an earlier run, repainted with THIS take's names (the leg
numbers, the trace and the alert time come from <take>/ids.json, written by take/ids.py; the rest of each card, its
labels, border and fade, stays the pitch's own). As film v4.2 has them (the v4.2 take):

  the "External" box (frames 1530–1661):  pharmacy/enroll-adherence → api.adherence-insights.example:8000
                                       →  pharmacy/report-dispense → demand-forecast.example:8000
  level 2's lineage card (2733–2873):     #203801 … lookup-patient response · #204405 fulfillment-agent → enroll-adherence request
                                       →  #33650 … (run B's origin) · #34345 fulfillment-agent → report-dispense request
  level 3's cross-session card (3381–):   #203801 … · in trace 36b88b54…  →  #33650 … · in trace a4bc18da… (run N1's origin, in B)
  level 1's alert row (1597–1663):        pii_to_untrusted_external – api.adherence-insights.example:8000, enroll-adherence ·
                                          10/19/2026, 1:00:02 PM  →  run A's alert: … demand-forecast.example:8000, report-dispense ·
                                          10/25/2026, 10:03:18 PM (IST); its description is left as it was
  the castle hall's "Risky" tag (540–640): enroll-adherence → external host  →  report-dispense → external host
                                          (the graph crop beneath it stays the pitch's own, from the earlier run)

Each card is opaque (#1f1f1f); it slides ~20 px up into place as it fades in (~8 frames), then stays still. A line is repainted on the card's
own background in Sentry's UI fonts (Red Hat Text for the links, JetBrains Mono for the box), fitted to the old line: the
same font is first fitted to the old text's ink (size and place), then the new text is set at that size and place. In
the fades the change follows the card (its weight in each frame is read from the frame itself). Writes <take>/cards/card-<name>.mov (RGBA, lossless) per card, and <take>/cards/cards.json (where each goes) for
fixes/build4.py. Sentry's console must answer (its bundled font is used): the take's cluster, from film.env.

    FILM_TAKE=… $PYTHON film/long/v4/fixes/cards.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import numpy as np
from PIL import Image, ImageFilter

HERE = pathlib.Path(__file__).resolve().parent
PITCH = HERE.parents[2] / "pitch-2m35" / "master" / "sentry_pitch_demo.mp4"   # the master (lab-dg demo/pharmacy-refill-pitch)
sys.path.insert(0, str(HERE.parents[1]))
from env import DG, NODE_PATH as NODE, PORT, TAKE  # noqa: E402
IDS = json.loads((TAKE / "ids.json").read_text())["cards"]   # take/ids.py: this take's leg numbers, trace, alert time
OUT = TAKE / "cards"
BG = np.array([31, 31, 31], np.float32)
# name: (first frame, last frame, a clean full-opacity frame, the patch rect (even), [(old ink box, font, weight, old, new, colour), …])
CARDS = {
    "external": (1510, 1668, 1600, (1136, 838, 520, 104), [
        ((1147, 846, 1622, 892), "JetBrains Mono", 600, "pharmacy/enroll-adherence → api.adherence-\ninsights.example:8000",
         "pharmacy/report-dispense → demand-\nforecast.example:8000", "light")]),
    "chain": (2712, 2880, 2800, (996, 880, 640, 112), [
        ((1007, 888, 1574, 908), "RedHatText", 600, "#203801 pharmacy-assistant → lookup-patient response",
         IDS["origin"], "blue"),
        ((1008, 924, 1559, 944), "RedHatText", 600, "#204405 fulfillment-agent → enroll-adherence request",
         IDS["previous"], "blue")]),
    "cross": (3360, 3590, 3500, (150, 812, 680, 110), [
        ((164, 819, 785, 842), "RedHatText", 600, "#203801 pharmacy-assistant → lookup-patient response",
         IDS["cross"], "blue"),
        ((177, 859, 368, 874), "RedHatText", 600, "in trace 36b88b54…", IDS["cross_trace"], "light")]),
    "alert": (1590, 1672, 1630, (100, 892, 960, 72), [
        ((110, 899, 263, 918), "RedHatText", 600, "pii_to_untrusted_external – api.adherence-\ninsights.example:8000, enroll-adherence",
         "pii_to_untrusted_external – demand-\nforecast.example:8000, report-dispense", "light"),
        ((995, 898, 1045, 917), "JetBrains Mono", 600, "10/19/2026,\n1:00:02 PM", IDS["alert_time"], "light")]),
    # the pitch's own red "Risky" tag over the castle hall's graph (his chrome: Fira Sans SemiBold, white on red)
    "risky": (540, 640, 600, (840, 206, 316, 70), [
        ((853, 215, 1141, 229), "Fira Sans", 600, "enroll-adherence → external host", "report-dispense → external host", "light")], (238, 98, 80)),
}

# Text is set inside Sentry's own UI page (the take's cluster), so RedHatText is the UI's bundled face and its glyph fallbacks
# (the arrow) are the UI's; the mono box uses JetBrains Mono from Google Fonts.
page = """const { chromium } = require('playwright');
(async () => { const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 3600, height: 400 } });
  await p.route(u => /localtest\\.me:\\d+/.test(u.href) && !u.href.includes(':%s/'), r => r.abort());   // only this take's cluster
  await p.goto('%s/ui/traces', { waitUntil: 'networkidle' });
  await p.evaluate(([font, weight, html]) => { document.head.insertAdjacentHTML('beforeend', '<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;600&family=Fira+Sans:wght@600&display=block" rel="stylesheet">');
    document.documentElement.style.background = 'transparent'; document.body.style.background = 'transparent';
    document.body.innerHTML = `<div style="position:absolute;left:40px;top:40px;white-space:pre;font:${weight} 120px/1.3 ${font};color:#000">${html}</div>`; },
    [%s, %d, %s]);
  await p.evaluate(() => document.fonts.ready); await p.waitForTimeout(800);
  await p.screenshot({ path: '%s', omitBackground: true }); await b.close(); })();"""


def text_alpha(font: str, weight: int, text: str) -> np.ndarray:
    OUT.mkdir(parents=True, exist_ok=True); png = OUT / "card-text.png"
    stack = "RedHatText, helvetica, arial, sans-serif" if font == "RedHatText" else f"'{font}', sans-serif" if font == "Fira Sans" else f"'{font}', monospace"
    subprocess.run(["node", "-e", page % (PORT, DG, json.dumps(stack), weight, json.dumps(text.replace("\n", "<br>")), png)], check=True, env={**os.environ, "NODE_PATH": NODE})
    a = np.asarray(Image.open(png).getchannel("A"), np.float32) / 255; png.unlink()
    return a


def ink(a: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(a > 0.3)
    return xs.min(), ys.min(), xs.max(), ys.max()


def frames(n0: int, n1: int, rect=None) -> np.ndarray:
    vf = f"select=between(n\\,{n0}\\,{n1})" + (f",crop={rect[2]}:{rect[3]}:{rect[0]}:{rect[1]}" if rect else "")
    w, h = (rect[2], rect[3]) if rect else (1920, 1080)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(PITCH), "-vf", vf, "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(np.float32)


def detail(f: np.ndarray, r: int = 4) -> np.ndarray:
    a = f @ np.array([0.299, 0.587, 0.114], np.float32)
    k = np.ones(2 * r + 1) / (2 * r + 1)
    box = lambda v: np.convolve(np.pad(v, r, mode="edge"), k, "valid")
    return a - np.apply_along_axis(box, 1, np.apply_along_axis(box, 0, a))


def place(a: np.ndarray, box_canvas, box_frame, rect, scale) -> np.ndarray:
    """The canvas alpha `a`, scaled so its ink box lands on box_frame's left/top, cut to rect (frame px)."""
    img = Image.fromarray((a * 255).astype(np.uint8))
    ox, oy = box_frame[0] - box_canvas[0] * scale, box_frame[1] - box_canvas[1] * scale
    t = img.transform((rect[2], rect[3]), Image.AFFINE, (1 / scale, 0, (rect[0] - ox) / scale, 0, 1 / scale, (rect[1] - oy) / scale),
                      resample=Image.BICUBIC)
    return np.asarray(t, np.float32) / 255


out = {}
for name, (n0, n1, ref_n, rect, lines, *opt) in CARDS.items():
    x, y, w, h = rect
    ref = frames(ref_n, ref_n, rect)[0]
    flat = ref.reshape(-1, 3); BG = np.array(opt[0], np.float32) if opt else np.median(flat[flat.sum(-1) < 140], 0)   # the card's own background
    patch, reach = ref.copy(), np.zeros((h, w), bool)
    newA, newRGB, coverAll = np.zeros((h, w), np.float32), np.zeros((h, w, 3), np.float32), np.zeros((h, w), np.float32)
    lboxes = []                                          # each old line's ink box, in the rect's own coordinates
    for (x0, y0, x1, y1), font, weight, old, new, tone in lines:
        a_old, a_new = text_alpha(font, weight, old), text_alpha(font, weight, new)
        bo, bn = ink(a_old), ink(a_new)
        scale = (x1 - x0 + 1) / (bo[2] - bo[0] + 1)
        # the old ink as the frame has it (anything off the card's background inside the old box), and its colour: the
        # ink cores (the pixels farthest from the background)
        m_old = place(a_old, bo, (x0, y0), rect, scale)
        box = np.zeros((h, w), bool); box[max(y0 - y - 3, 0):y1 - y + 4, max(x0 - x - 3, 0):x1 - x + 4] = True
        dist = np.abs(ref - BG).sum(-1)
        ink_px = box & (dist > 30)
        core = ink_px & (dist >= np.percentile(dist[ink_px], 97))
        colour = np.median(ref[core], 0)
        # the new text is set at the old one's size, its first glyph where the old first glyph was; the old ink is
        # covered with the card's background
        a_n = place(a_new, bo, (x0, y0), rect, scale)
        # the old line's whole box is the card's background (a chip with its own background keeps an ink-shaped cover)
        if "in trace" in old:
            cover = np.asarray(Image.fromarray(((ink_px | (m_old > 0.02)) * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5)), np.float32) / 255
        else:
            cover = np.zeros((h, w), np.float32); cover[max(y0 - y - 2, 0):y1 - y + 3, max(x0 - x - 2, 0):x1 - x + 3] = 1
            cover = np.asarray(Image.fromarray((cover * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8)), np.float32) / 255
            ink_px = ink_px | (cover > 0.5)
        patch = patch * (1 - cover[..., None]) + BG * cover[..., None]
        patch = patch * (1 - a_n[..., None]) + colour * a_n[..., None]
        newRGB = newRGB * (1 - a_n[..., None]) + colour * a_n[..., None]; newA = np.maximum(newA, a_n); coverAll = np.maximum(coverAll, cover)
        lboxes.append((x0 - x, y0 - y, x1 - x, y1 - y))
        reach |= ink_px | (m_old > 0.02) | (a_n > 0.02)
        fit = float((m_old * (detail(ref) > 8)).sum() / max(m_old.sum(), 1))
        print(f"{name}: '{old[:30]}…' scale {scale:.4f} colour {colour.round()} fit {fit:.2f}")
    mask = np.asarray(Image.fromarray((reach * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(1.5)), np.float32) / 255
    clean = detail(ref)
    F = frames(n0, n1, rect)
    mov = OUT / f"card-{name}.mov"
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", "30", "-i", "-",
                            "-c:v", "png", "-pix_fmt", "rgba", str(mov)], stdin=subprocess.PIPE)
    weights = []
    cc = (clean * clean).sum()
    down = lambda v, dy: np.vstack([np.zeros((dy, *v.shape[1:]), v.dtype), v[:v.shape[0] - dy]]) if dy > 0 else v
    # the card slides up into place as it fades in (and back as it fades out). Pass 1: each frame's offset below the
    # final place. Pass 2: the card's weight there, read from horizontal detail only (the slide blurs the card
    # vertically, which a full high-pass reads as a fainter card), against the clean card blurred by the same motion;
    # the change is moved and blurred the same way.
    def hdetail(v, r=4):
        a = v @ np.array([0.299, 0.587, 0.114], np.float32)
        k = np.ones(2 * r + 1) / (2 * r + 1)
        return a - np.apply_along_axis(lambda row: np.convolve(np.pad(row, r, mode="edge"), k, "valid"), 1, a)
    def vblur(v, n):
        if n <= 1:
            return v
        k = np.ones(n) / n
        return np.apply_along_axis(lambda col: np.convolve(np.pad(col, (n // 2, n - 1 - n // 2), mode="edge"), k, "valid"), 0, v)
    # both passes look only at the text area (where the patch goes), which moves with the card; the scenery around and
    # under the card moves on its own and would dilute the reading
    W = (mask > 0.01).astype(np.float32)
    cw = clean * W
    DF = [detail(f) for f in F]
    static = name == "risky"                             # the tag only fades in; it does not slide
    dys = [0 if static else max((float((df * down(cw, d)).sum() / max((cw * cw).sum(), 1e-6)), d) for d in range(0, 41))[1] for df in DF]
    hclean = hdetail(ref) * W
    def wipe(v, dy):
        """v with each old line removed: every row of a line's box (moved down by dy) filled by a straight blend between
        the pixels just left and right of it (the card's own surface, or the scene through it)."""
        o = v.copy()
        for bx0, by0, bx1, by1 in lboxes:
            l0, l1 = max(bx0 - 3, 0), min(bx1 + 4, w - 1)
            for r in range(max(by0 - 2 + dy, 0), min(by1 + 3 + dy, h)):
                L, Rt = v[r, max(l0 - 3, 0):l0 + 1].mean(0), v[r, l1:min(l1 + 4, w)].mean(0)
                o[r, l0:l1] = L + (Rt - L) * np.linspace(0, 1, l1 - l0)[:, None]
        return o
    textref = ref - wipe(ref, 0)
    G = []
    for i, f in enumerate(F):
        v = max(abs(dys[i] - dys[i - 1]) if i else 0, abs(dys[i + 1] - dys[i]) if i + 1 < len(F) else 0)
        tmpl = down(vblur(hclean, v + 1), dys[i])
        G.append(float(np.clip((hdetail(f) * tmpl).sum() / max((tmpl * tmpl).sum(), 1e-6), 0, 1)))
    # (a card's first faint frame, below the gate, is left as it is: there the scene, not the card, fills the box)
    for i, f in enumerate(F):
        dy = dys[i]; v = max(abs(dy - dys[i - 1]) if i else 0, abs(dys[i + 1] - dy) if i + 1 < len(F) else 0)
        g = 0.0 if G[i] < (0.02 if static else 0.06) else G[i]
        if g == 0.0 or static or (dy == 0 and g > 0.9):
            # still (or gone): the change, weighted by the card's share of the frame (exact when nothing moves)
            weights.append((round(g, 2), dy, v, "still"))
            p = f + g * down(patch - ref, dy)
            alpha = down(vblur(mask, v + 1), dy) * 255 * (g > 0)
        else:
            # moving: the old text is wiped (a median keeps what is behind thin strokes: the card or the scene) and the new
            # text drawn at the card's visibility, read from the text's energy (blind to sub-pixel offsets) against the
            # clean card's, moved and blurred by this frame's motion
            m = wipe(f, dy)
            tr = down(vblur(textref, v + 1), dy)
            a_t = float(np.clip(np.sqrt(((f - m) ** 2).sum() / max((tr ** 2).sum(), 1e-6)), 0, 1))
            A = down(vblur(newA, v + 1), dy)[..., None] * a_t
            C = down(vblur(newRGB, v + 1), dy) / np.maximum(down(vblur(newA, v + 1), dy)[..., None], 1e-3)
            p = m * (1 - A) + C * A
            weights.append((round(a_t, 2), dy, v, "moving"))
            alpha = np.full((h, w), 255.0)
        enc.stdin.write(np.dstack([np.clip(p, 0, 255), alpha]).round().astype(np.uint8).tobytes())
    enc.stdin.close(); enc.wait()
    out[name] = {"mov": mov.name, "x": x, "y": y, "n": n0}
    print(f"{mov.name}: frames {n0}–{n1} at ({x}, {y}); weights {weights[:12]} … {weights[-10:]}")
(OUT / "cards.json").write_text(json.dumps(out, indent=1))
