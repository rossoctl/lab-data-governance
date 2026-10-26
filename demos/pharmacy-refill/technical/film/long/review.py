"""review.py — checks one cut of the film and writes <take>/review/<stem>/REPORT.md (+ the raw JSON of every check).

    FILM_TAKE=… GEMINI_API_KEY=… $PYTHON film/long/review.py film.mp4 [--no-watch] [--no-claims] [--watch-model gemini-4-argon]

The film is <take>/<name>.mp4 with its <name>.plan.json beside it (film4.py join writes both). Truth files: REVIEW_FACTS
(colon-separated; default <take>/take-facts.yaml) — the review knows nothing else. The model pass's brief: REVIEW_PROMPT
(default film/long/v4/review-prompt-onevoice.md). Without GEMINI_API_KEY, run with --no-claims --no-watch.

Checks, cheapest first:
  script   Whisper transcript of the film against the words the film's plan says it speaks: word error rate and every
           differing span. With music in, a rising rate = music covers the voice.
  frozen   every frame held still >= 1.5 s (ffmpeg freezedetect).
  loudness integrated loudness and true peak of the whole film (EBU R128).
  claims   every spoken line against the truth files (Gemini, text only): supported / contradicts / unsupported / words.
  watch    Gemini watches the whole film with sound and lists timestamped findings (uploads the film to the Gemini
           File API, paid tier, and deletes it afterwards). Findings are leads to check against the frame, not verdicts.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from env import TAKE  # noqa: E402
FACTS = [pathlib.Path(f) for f in os.environ.get("REVIEW_FACTS", str(TAKE / "take-facts.yaml")).split(":") if f]
PROMPT = pathlib.Path(os.environ.get("REVIEW_PROMPT", HERE / "v4" / "review-prompt-onevoice.md"))
API = "https://generativelanguage.googleapis.com"
PRICE = {"gemini-4-argon": (2.00, 10.00), "gemini-3.8-flash": (0.75, 3.75)}   # $ per 1M tokens in/out, through 2026-11-30


def sh(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def where(t: float, ranges) -> str:
    return "film"


# ---- script ---------------------------------------------------------------------------------------------------------
def transcript(video: pathlib.Path, f: pathlib.Path) -> list[tuple[float, float, str]]:
    # vad_filter + no carry-over: without them Whisper drops whole lines of a long film (3 of v1's, 10-23)
    if not f.exists() or f.stat().st_mtime < video.stat().st_mtime:   # a film rebuilt under the same name is heard again
        from faster_whisper import WhisperModel
        segs, _ = WhisperModel("small.en", compute_type="int8").transcribe(
            str(video), language="en", vad_filter=True, condition_on_previous_text=False)
        f.write_text("".join(f"[{s.start:6.2f}-{s.end:6.2f}] {s.text.strip()}\n" for s in segs))
    rows = []
    for line in f.read_text().splitlines():
        m = re.match(r"\[\s*([\d.]+)-\s*([\d.]+)\]\s*(.*)", line)
        if m:
            rows.append((float(m[1]), float(m[2]), m[3]))
    return rows


def script_text() -> str:
    """The words the film speaks, in order: its plan's "script" (film4.py join writes it)."""
    return " ".join(json.loads(PLAN.read_text())["script"])


def norm(text: str) -> list[str]:
    text = re.sub(r"\b([a-z])\s+(?=[a-z]\b)", r"\1", text.lower())       # "c t l" -> "ctl", "d g" -> "dg"
    words = re.findall(r"[a-z]+|\d+", text.replace("rossoctl", "rosso ctl"))   # "dg1" -> "dg", "1"
    return [NUM.get(w, w) for w in words]


NUM = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten".split())}


def check_script(rows) -> dict:
    ref, hyp = norm(script_text()), norm(" ".join(t for _, _, t in rows))
    sm = difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False)
    diffs = [{"op": op, "script": " ".join(ref[a1:a2]), "heard": " ".join(hyp[b1:b2])}
             for op, a1, a2, b1, b2 in sm.get_opcodes() if op != "equal"]
    errors = sum(max(a2 - a1, b2 - b1) for op, a1, a2, b1, b2 in sm.get_opcodes() if op != "equal")
    return {"words": len(ref), "wer": round(errors / len(ref), 3), "diffs": diffs}


# ---- frozen frames, loudness ----------------------------------------------------------------------------------------
def check_frozen(video: pathlib.Path, ranges) -> list[dict]:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-map", "0:v", "-vf",
                          "freezedetect=n=-60dB:d=1.5", "-f", "null", "-"], capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", err)]
    lens = [float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", err)]
    return [{"at": round(s, 2), "seconds": round(d, 2), "in": where(s, ranges)} for s, d in zip(starts, lens)]


def check_loudness(video: pathlib.Path) -> dict:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-map", "0:a", "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True).stderr.split("Summary:")[-1]
    num = lambda k: float(re.search(k + r":\s+(-?[\d.]+)", err)[1])
    return {"integrated_lufs": num("I"), "true_peak_dbfs": num("Peak"), "lra": num("LRA")}


# ---- Gemini ---------------------------------------------------------------------------------------------------------
def call(url: str, body: dict | bytes | None = None, headers: dict | None = None, method: str | None = None):
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    h = {"x-goog-api-key": os.environ["GEMINI_API_KEY"], **({"Content-Type": "application/json"} if isinstance(body, dict) else {}), **(headers or {})}
    with urllib.request.urlopen(urllib.request.Request(url, data, h, method=method), timeout=900) as r:
        return r.headers, (json.loads(r.read() or b"{}"))


def generate(model: str, parts: list, schema: dict, usage: dict) -> dict:
    _, r = call(f"{API}/v1beta/models/{model}:generateContent",
                {"contents": [{"parts": parts}],
                 "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema,
                                      "mediaResolution": "MEDIA_RESOLUTION_HIGH", "temperature": 0.2}})
    u = r.get("usageMetadata", {})
    pin, pout = PRICE.get(model, (0, 0))
    tin, tout = u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    usage[model] = {"in": tin, "out": tout, "usd": round(tin * pin / 1e6 + tout * pout / 1e6, 3)}
    return json.loads(r["candidates"][0]["content"]["parts"][0]["text"])


def facts_text() -> str:
    return "\n\n".join(f"=== {f.name} ===\n{f.read_text()}" for f in FACTS)


CLAIMS_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "required": ["time", "line", "verdict", "note"], "properties": {
    "time": {"type": "STRING"}, "line": {"type": "STRING"},
    "verdict": {"type": "STRING", "enum": ["supported", "contradicts", "unsupported", "words", "no claim"]},
    "fact": {"type": "STRING"}, "note": {"type": "STRING"}}}}


def check_claims(rows, usage) -> list[dict]:
    lines = "\n".join(f"[{mmss(a)}] {t}" for a, _, t in rows)
    prompt = f"""You check the narration of a product demo film against its truth files. For EVERY narration line below,
give one verdict: "contradicts" (a truth file says otherwise), "unsupported" (a factual claim no truth file backs),
"words" (the claim may be true but uses a word the files list under never/never say, or breaks their vocabulary),
"supported" (a truth file backs it; quote the fact), or "no claim" (a question, a transition, story with no factual
claim). Quote the fact you rely on in `fact`, briefly. Be strict: the film is shown to engineers who will check.
Note: the story world (a knight, a squire, the patient Rosa) is a metaphor and makes no claim by itself.

TRUTH FILES
{facts_text()}

NARRATION (time in the film, then the words as heard)
{lines}"""
    return generate("gemini-3.8-flash", [{"text": prompt}], CLAIMS_SCHEMA, usage)


WATCH_SCHEMA = {"type": "OBJECT", "required": ["summary", "findings"], "properties": {
    "summary": {"type": "STRING"},
    "findings": {"type": "ARRAY", "items": {"type": "OBJECT", "required": ["time", "category", "severity", "what", "evidence"], "properties": {
        "time": {"type": "STRING"}, "end": {"type": "STRING"},
        "category": {"type": "STRING", "enum": ["claim", "clarity", "pacing", "legibility", "attention", "audio", "seam", "story", "other"]},
        "severity": {"type": "STRING", "enum": ["must-fix", "should-fix", "nit"]},
        "what": {"type": "STRING"}, "evidence": {"type": "STRING"}, "suggestion": {"type": "STRING"}}}}}}


def watch(video: pathlib.Path, model: str, usage: dict, prompt: str) -> dict:
    size = video.stat().st_size
    h, _ = call(f"{API}/upload/v1beta/files", {"file": {"display_name": video.name}},
                {"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                 "X-Goog-Upload-Header-Content-Length": str(size), "X-Goog-Upload-Header-Content-Type": "video/mp4"})
    _, r = call(h["x-goog-upload-url"], video.read_bytes(), {"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"})
    f = r["file"]
    try:
        while f.get("state") == "PROCESSING":
            time.sleep(5); _, f = call(f"{API}/v1beta/{f['name']}")
        if f.get("state") != "ACTIVE":
            sys.exit(f"upload failed: {f.get('state')} {f.get('error')}")
        return generate(model, [{"file_data": {"mime_type": "video/mp4", "file_uri": f["uri"]}}, {"text": prompt}], WATCH_SCHEMA, usage)
    finally:
        call(f"{API}/v1beta/{f['name']}", method="DELETE")


# ---- report ---------------------------------------------------------------------------------------------------------
def report(video, res, usage) -> str:
    spent = ", ".join(f"{m}: {u['in']:,} in / {u['out']:,} out" for m, u in usage.items())
    L = [f"# Review of {video.name}", "", f"{time.strftime('%Y-%m-%d %H:%M')} · review.py · Gemini cost "
         f"${sum(u['usd'] for u in usage.values()):.2f} ({spent})", ""]
    s = res["script"]
    L += [f"## Script vs heard: WER {s['wer']:.1%} of {s['words']} words", ""] + \
         [f"- {d['op']}: script «{d['script']}» · heard «{d['heard']}»" for d in s["diffs"]] + [""]
    L += [f"## Frozen frames ≥ 1.5 s: {len(res['frozen'])}, {sum(f['seconds'] for f in res['frozen']):.1f} s in all", ""] + \
         [f"- {mmss(f['at'])} · {f['seconds']:.1f} s · {f['in']}" for f in res["frozen"]] + [""]
    lo = res["loudness"]
    L += ["## Loudness", "", f"{lo['integrated_lufs']} LUFS integrated · true peak {lo['true_peak_dbfs']} dBFS · LRA {lo['lra']} LU", ""]
    if "claims" in res:
        bad = [c for c in res["claims"] if c["verdict"] not in ("supported", "no claim")]
        L += [f"## Claims: {len(bad)} of {len(res['claims'])} lines need a look", ""] + \
             [f"- **{c['verdict']}** {c['time']} «{c['line']}» — {c['note']}" + (f" (fact: {c['fact']})" if c.get("fact") else "") for c in bad] + [""]
    if "watch" in res:
        w = res["watch"]; order = {"must-fix": 0, "should-fix": 1, "nit": 2}
        L += ["## Gemini watched the film", "", w["summary"], ""] + \
             [f"- **{f['severity']}** · {f['time']}{'–' + f['end'] if f.get('end') else ''} · {f['category']} · {f['what']}  \n"
              f"  evidence: {f['evidence']}" + (f"  \n  suggestion: {f['suggestion']}" if f.get("suggestion") else "")
              for f in sorted(w["findings"], key=lambda f: order[f["severity"]])] + [""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("video"); ap.add_argument("--no-watch", action="store_true")
    ap.add_argument("--no-claims", action="store_true"); ap.add_argument("--watch-model", default="gemini-4-argon")
    a = ap.parse_args()
    global PLAN
    video = pathlib.Path(a.video) if pathlib.Path(a.video).is_absolute() else TAKE / a.video
    PLAN = video.with_suffix(".plan.json")
    missing = [str(f) for f in [video, PLAN, *FACTS] if not f.exists()]
    if missing:
        sys.exit(f"missing: {', '.join(missing)}")
    out = TAKE / "review" / video.stem; out.mkdir(parents=True, exist_ok=True)
    usage, res = {}, {}
    rows = transcript(video, out / "transcript.txt")
    res["script"] = check_script(rows); res["frozen"] = check_frozen(video, []); res["loudness"] = check_loudness(video)
    if not a.no_claims:
        res["claims"] = check_claims(rows, usage)
    if not a.no_watch:
        res["watch"] = watch(video, a.watch_model, usage, PROMPT.read_text() + "\nTRUTH FILES\n" + facts_text())
    res["usage"] = usage
    (out / "review.json").write_text(json.dumps(res, indent=1))
    (out / "REPORT.md").write_text(report(video, res, usage))
    print(f"{out / 'REPORT.md'} · Gemini ${sum(u['usd'] for u in usage.values()):.2f}")


if __name__ == "__main__":
    main()
