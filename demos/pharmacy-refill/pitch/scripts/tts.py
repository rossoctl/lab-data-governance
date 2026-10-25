"""Render narration with Kokoro.

Usage: tts.py <narration.txt> <out_dir> <voice> [speed]

Each non-empty line is "beat|text" (or bare text). Writes:
  <out_dir>/<voice>/NN.wav         one file per line
  <out_dir>/<voice>/narration.wav  all lines joined with pauses
  <out_dir>/<voice>/timeline.json  start/end of each line in narration.wav
  <out_dir>/<voice>/words.json     start/end of every spoken word in narration.wav (Kokoro's own
                                   token timestamps), which film/timeline.js uses for its cues
"""
import json
import pathlib
import re
import sys

import numpy as np
import soundfile as sf
from kokoro import KPipeline

SAMPLE_RATE = 24000
LEAD_IN = 0.4       # silence before the first line
GAP_LINE = 0.30     # between lines of the same beat
GAP_BEAT = 0.60     # between beats
GAP_BLOCKED = 0.35  # extra beat after a line that ends on a block, for the stamp
# Longer pause before these beats: "l1" lets the flagged edge land, "close" holds the payoff table.
BEAT_GAP = {"l1": 0.9, "close": 2.4}

# Spoken form for words the voice would otherwise misread. Captions keep the
# written form; only the text sent to Kokoro is rewritten.
PRONOUNCE = {
    r"\brossoctl\b": "Rosso C-T-L",
}


def spoken(text):
    for pattern, replacement in PRONOUNCE.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def parse(path):
    for line in pathlib.Path(path).read_text().splitlines():
        if not line.strip():
            continue
        beat, _, text = line.rpartition("|")
        yield beat.strip() or "-", text.strip()


def main():
    lines_path, out_dir, voice = sys.argv[1:4]
    speed = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
    out = pathlib.Path(out_dir) / voice
    out.mkdir(parents=True, exist_ok=True)

    pipeline = KPipeline(lang_code=voice[0], repo_id="hexgrad/Kokoro-82M")
    silence = lambda s: np.zeros(int(s * SAMPLE_RATE), dtype=np.float32)

    track, timeline, words, t, prev_beat = [silence(LEAD_IN)], [], [], LEAD_IN, None
    for i, (beat, text) in enumerate(parse(lines_path), 1):
        if prev_beat is not None:
            gap = GAP_LINE if beat == prev_beat else BEAT_GAP.get(beat, GAP_BEAT)
            track.append(silence(gap))
            t += gap
        chunks, offset = [], 0.0
        for r in pipeline(spoken(text), voice=voice, speed=speed):
            for tok in r.tokens or []:
                if tok.start_ts is not None and re.search(r"\w", tok.text):
                    words.append({"line": i, "beat": beat, "word": tok.text,
                                  "start": round(t + offset + tok.start_ts, 3), "end": round(t + offset + tok.end_ts, 3)})
            chunks.append(r.audio.numpy() if hasattr(r.audio, "numpy") else r.audio)
            offset += len(chunks[-1]) / SAMPLE_RATE
        audio = np.concatenate(chunks)
        sf.write(out / f"{i:02d}.wav", audio, SAMPLE_RATE)
        dur = len(audio) / SAMPLE_RATE
        timeline.append({"n": i, "beat": beat, "text": text, "start": round(t, 3), "end": round(t + dur, 3)})
        track.append(audio)
        print(f"{i:02d} {beat:5} {t:6.2f}-{t + dur:6.2f}  {text[:70]}")
        t += dur
        if re.search(r"\bblock(s|ed)?\b[^.]*\.$", text, flags=re.IGNORECASE):
            track.append(silence(GAP_BLOCKED))
            t += GAP_BLOCKED
        prev_beat = beat

    sf.write(out / "narration.wav", np.concatenate(track), SAMPLE_RATE)
    (out / "timeline.json").write_text(json.dumps(timeline, indent=1))
    (out / "words.json").write_text(json.dumps(words, indent=0))
    print(f"total {t:.2f}s")


if __name__ == "__main__":
    main()
