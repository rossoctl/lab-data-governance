"""common.py — what every part's build shares: the voice (Eleven v4, cached), the short's own lines re-voiced with its
picture retimed to them, and the ffmpeg assembly. (v4's film4.py uses synth and render; beat/assemble/build_part are
the earlier parts' builders, kept for them.)

The short's picture is cut to its own narrator; re-voicing it moves every line. Each beat of the short (one sentence
and the picture from just before it to just before the next one) is time-scaled to the new line plus the air the
original left after it, within MAX_STRETCH; beyond that, its last frame holds (longer) or its tail is cut (shorter).
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import urllib.request
import wave

HERE = pathlib.Path(__file__).resolve().parent
SHORT = HERE / "short-fixed.mp4"          # the earlier parts' short (not shipped; film4.py does not use it)
SHORT_LINES = json.loads((HERE.parent / "analysis" / "transcript.json").read_text())
TTS = HERE / "tts11"; TTS.mkdir(exist_ok=True)
VOICE = {"id": "JBFqnCBsd6RMkjVDRZzb", "name": "George", "model": "eleven_v4"}   # ElevenLabs voice id + model; a new voice re-speaks every line
from env import NODE_PATH as NODE  # noqa: E402   (Playwright: npm install in the package root)
FPS, LEAD, MAX_STRETCH = 30, 0.3, 1.3
TEMPO = float(os.environ.get("VOICE_TEMPO", "1"))   # George reads ~1.3x slower than the short's narrator; >1 = brisker, pitch kept


def sh(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def synth(text: str) -> tuple[pathlib.Path, float]:
    key = hashlib.sha256(json.dumps([VOICE["model"], VOICE["id"], text]).encode()).hexdigest()[:16]
    f = TTS / f"{key}.wav"
    if not f.exists():
        if not os.environ.get("ELEVENLABS_API_KEY"):
            raise SystemExit(f"this line is not in the voice cache (film/long/tts11/{f.name}) and ELEVENLABS_API_KEY is not set: «{text}»\n"
                             "export the key to have it spoken (it costs credits), or keep the line's words as they were; "
                             f"voiced elsewhere with the same VOICE ({VOICE['model']}, {VOICE['id']}), its wav goes in as tts11/{f.name}")
        req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE['id']}?output_format=pcm_24000",
                                     json.dumps({"text": text, "model_id": VOICE["model"]}).encode(),
                                     {"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"})
        pcm = urllib.request.urlopen(req, timeout=120).read()
        with wave.open(str(f), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(pcm)
        print(f"  tts: {text[:60]}…")
    if TEMPO != 1:
        g = f.with_name(f"{f.stem}-t{TEMPO:g}.wav")
        if not g.exists():
            sh(["ffmpeg", "-v", "error", "-y", "-i", str(f), "-af", f"atempo={TEMPO}", str(g)])
        f = g
    with wave.open(str(f)) as w:
        return f, w.getnframes() / w.getframerate()


def beat(segs: list[int], text: str, a: float, b: float) -> dict:
    """One beat of the short: its lines `segs` (indices in transcript.json), re-voiced as `text`, picture [a, b)."""
    wav, d = synth(text)
    first, last = SHORT_LINES[segs[0]], SHORT_LINES[segs[-1]]
    lead = first["start"] - a
    air = max(b - last["end"], 0.35)
    want = lead + d + air
    k = min(max(want / (b - a), 1 / MAX_STRETCH), MAX_STRETCH)
    return {"segs": segs, "text": text, "a": a, "b": b, "wav": str(wav), "dur": d, "len": want, "k": k, "lead": lead * k}


def beat_video(i: int, bt: dict) -> str:
    """ffmpeg filter for beat i, from input 0 (the short): scaled by k, then held or cut to its length."""
    f = f"[0:v]trim={bt['a']:.3f}:{bt['b']:.3f},setpts=(PTS-STARTPTS)*{bt['k']:.4f},fps={FPS}"
    extra = bt["len"] - (bt["b"] - bt["a"]) * bt["k"]
    if extra > 0:
        f += f",tpad=stop_mode=clone:stop_duration={extra:.3f}"
    return f + f",trim=0:{bt['len']:.3f},setpts=PTS-STARTPTS,format=yuv420p[v{i}]"


def render(stage: pathlib.Path, cues: dict, out: pathlib.Path) -> None:
    cf = out.with_suffix(".cues.json"); cf.write_text(json.dumps(cues, indent=1))
    part = out.with_name(out.stem + ".part.mp4")         # a failed render must not replace a good section
    subprocess.run(["node", str(HERE / "render.js"), str(stage), str(cf), str(part)], check=True, env={**os.environ, "NODE_PATH": NODE})
    os.replace(part, out)


def assemble(pieces: list[dict], lines: list[tuple[str, float]], out: pathlib.Path) -> float:
    """pieces: {"beat": beat-dict} or {"clip": path, "len": s}, in order; lines: (wav, film time) for every voice line."""
    ins, vf, t = ["-i", str(SHORT)], [], 0.0
    for i, pc in enumerate(pieces):
        if "beat" in pc:
            vf.append(beat_video(i, pc["beat"])); t += pc["beat"]["len"]
        else:
            ins += ["-i", str(pc["clip"])]; vf.append(f"[{len(ins) // 2 - 1}:v]setpts=PTS-STARTPTS,fps={FPS},format=yuv420p[v{i}]"); t += pc["len"]
    n_v = len(ins) // 2
    mix = []
    for j, (wav, at) in enumerate(lines):
        ins += ["-i", wav]; mix.append(f"[{n_v + j}:a]adelay={int(at * 1000)}:all=1[a{j}]")
    fc = ";".join(vf + [ "".join(f"[v{i}]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=1:a=0[v]"] + mix +
                  ["".join(f"[a{j}]" for j in range(len(lines))) +
                   f"amix=inputs={len(lines)}:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad,atrim=0:{t:.3f}[a]"])
    sh(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "16",
        "-preset", "slow", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
    return t


def build_part(name: str, here: pathlib.Path, before: list[dict], stage_text: dict[str, str], cues, after: list[dict]) -> pathlib.Path:
    """A part = the short's `before` beats → here/stage.html (its lines `stage_text`, timed by `cues(durations) -> (cues,
    starts)`, both in stage seconds) → the short's `after` beats. Writes here/out/<name>[-t<tempo>].mp4 + .plan.json."""
    out_dir = here / "out"; out_dir.mkdir(exist_ok=True)
    tag = name if TEMPO == 1 else f"{name}-t{TEMPO:g}"
    E = {k: synth(t) for k, t in stage_text.items()}
    c, at = cues({k: v[1] for k, v in E.items()}) if stage_text else ({"end": 0.0}, {})
    stage = out_dir / f"{tag}.stage.mp4"
    if stage_text:                                       # a part without an expansion (P8) is the short's beats only
        render(here / "stage.html", c, stage)
    lines, t = [], 0.0
    for b in before:
        lines.append((b["wav"], t + b["lead"])); t += b["len"]
    lines += [(str(E[k][0]), t + at[k]) for k in E]
    t += c["end"]
    for b in after:
        lines.append((b["wav"], t + b["lead"])); t += b["len"]
    out = out_dir / f"{tag}.mp4"
    middle = [{"clip": stage, "len": c["end"]}] if stage_text else []
    total = assemble([{"beat": b} for b in before] + middle + [{"beat": b} for b in after], lines, out)
    script = [b["text"] for b in before] + list(stage_text.values()) + [b["text"] for b in after]   # the words, in film order
    out.with_suffix(".plan.json").write_text(json.dumps({"script": script, "cues": c, "stage_lines": {k: [round(at[k], 2), round(E[k][1], 2)] for k in E},
                                                         "beats": before + after, "film_lines": [[w, round(a, 2)] for w, a in lines]}, indent=1))
    print(f"{out.name}: {total:.1f} s (short beats {sum(b['len'] for b in before + after):.1f} s, stage {c['end']:.1f} s); "
          f"stretch {[round(b['k'], 2) for b in before + after]}")
    return out
