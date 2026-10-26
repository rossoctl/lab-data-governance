"""score.py — the film's score: the film's layout from its cuts and the sections'
plans, four ElevenLabs Music pieces at chapter boundaries (one chunk per pitch segment or section), then one score track
the length of the film (<take>/out/score*.mp3) that film4.py join --music lays under the voice, ducked. The four pieces are
shared by every take (film/long/v4/music/, film v4's score); a take whose sections run longer or shorter stretches them.

    ELEVENLABS_API_KEY=… $PYTHON film/long/v4/score.py compose   # → v4/music/<piece>.mp3 + .plan.json (skips a piece that exists:
                                                                 #   delete a piece to compose it again; it costs credits)
    FILM_TAKE=… $PYTHON film/long/v4/score.py track              # → <take>/out/score.mp3 (film4.py join --music calls it)
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

V4 = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(V4)); sys.path.insert(0, str(V4.parent))
from env import TAKE  # noqa: E402
CUT = {"I1": 246, "I2": 626, "I3": 974, "I4": 1180, "I5": 1945, "I6": 3078, "I7": 3556, "I8": 3904, "I9": 4468}   # = film4.CUT
END = 4645                                                                                                       # the pitch's frames
FPS = 30
STORY = {0: "a grandmother at her kitchen table, gentle and warm, sparse piano", 1: "a busy castle hall, light pulse, sense of scale",
         2: "a knight on a bridge at night, steady, noble", 3: "a rule nailed to a gate, serious, one sustained low string",
         4: "level one, a clear firm statement, low strings", 5: "level two, the context is cut away, then a lantern finds the thread, warm figure",
         6: "level three, a monk at night with old archives, quiet tension", 7: "the gate opens, release into a warm major key",
         8: "Rosa at her door in the sun, then the closing table, warm and settled",
         9: "the end card, a clear quiet final cadence"}
HOOD = "under the hood: a lighter, steady, curious underscore with a soft pulse, transparent, never busy"
GLOBAL = ["instrumental underscore for a narrated technical product film", "calm, modern, warm piano and soft strings, light pulse",
          "restrained, leaves room for a voice", "no lead melody over speech"]
NEG = ["vocals", "singing", "choir", "lyrics", "heavy drums", "EDM", "aggressive", "distorted", "loud brass"]
PIECES = {"A": ("P0", "P3"), "B": ("I4", "I5"), "C": ("P5", "P6"), "D": ("I7", "P9")}   # first and last segment of each


def layout() -> list[tuple[str, float, str]]:
    """(segment, length s, style) in film order: P0, I1, P1, I2, … P9."""
    names, bounds, out = list(CUT), [0] + list(CUT.values()) + [END], []
    for i in range(len(bounds) - 1):
        out.append((f"P{i}", (bounds[i + 1] - bounds[i]) / FPS, STORY[i]))
        if i < len(names):
            out.append((names[i], json.loads((TAKE / "out" / f"{names[i]}.plan.json").read_text())["cues"]["end"], HOOD))
    return out


def pieces() -> dict[str, list[tuple[str, float, str]]]:
    L, names = layout(), [n for n, _, _ in layout()]
    return {k: L[names.index(a):names.index(b) + 1] for k, (a, b) in PIECES.items()}


def compose() -> None:
    (V4 / "music").mkdir(exist_ok=True)
    for name, segs in pieces().items():
        f = V4 / "music" / f"{name}.mp3"
        if f.exists():
            continue
        plan = {"chunks": [{"text": f"[{n}]", "duration_ms": int(round(d * 1000)), "positive_styles": GLOBAL + [st], "negative_styles": NEG}
                           for n, d, st in segs]}
        f.with_suffix(".plan.json").write_text(json.dumps(plan, indent=1))
        req = urllib.request.Request("https://api.elevenlabs.io/v1/music?output_format=mp3_48000_192",
                                     json.dumps({"composition_plan": plan, "model_id": "music_v2_5"}).encode(),
                                     {"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"})
        try:
            f.write_bytes(urllib.request.urlopen(req, timeout=900).read())
        except urllib.error.HTTPError as e:
            sys.exit(f"{name}: {e.code} {e.read()[:500]!r}")
        print(f"{f.name}: {sum(d for _, d, _ in segs):.1f} s planned")


def dur(f: pathlib.Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                                check=True, capture_output=True, text=True).stdout)


def track(lengths: dict[str, float] | None = None, name: str = "score.mp3") -> pathlib.Path:
    """The pieces end to end, each starting where its first segment starts; a piece whose segments changed length since
    it was composed is stretched to them (pitch kept); short fades at the joins (the pieces meet on a segment boundary)."""
    ins, fc, n = [], [], len(pieces())
    for i, (piece, segs) in enumerate(pieces().items()):
        now = sum((lengths or {}).get(n, d) for n, d, _ in segs)   # the segments as the film has them (one voice: longer)
        planned = sum(c["duration_ms"] for c in json.loads((V4 / "music" / f"{piece}.plan.json").read_text())["chunks"]) / 1000
        got = dur(V4 / "music" / f"{piece}.mp3")
        k = got / now                                          # stretch the piece as delivered to the segments as they are now
        ins += ["-i", str(V4 / "music" / f"{piece}.mp3")]
        fc.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,atempo={k:.5f},atrim=0:{now:.3f},asetpts=PTS-STARTPTS,apad=whole_dur={now:.3f},"
                  f"afade=t=in:d={0 if i == 0 else 0.15},afade=t=out:st={now - 0.25:.3f}:d=0.25[m{i}]")
        print(f"{piece}: planned {planned:.1f} s, delivered {got:.1f} s, now {now:.1f} s → atempo {k:.4f}")
    fc.append("".join(f"[m{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[s]")
    out = TAKE / "out" / name
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(fc), "-map", "[s]", "-c:a", "libmp3lame", "-b:a", "192k",
                    str(out)], check=True)
    print(f"{out.name}: {dur(out):.1f} s")
    return out


if __name__ == "__main__":
    {"compose": compose, "track": track, "layout": lambda: [print(f"{n:3} {d:6.1f}") for n, d, _ in layout()]}[sys.argv[1]]()
