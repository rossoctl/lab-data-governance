#!/usr/bin/env bash
# Put the narration on the silent render: loudness-normalised to -16 LUFS (web/talk level), AAC, faststart.
#   scripts/mux.sh [voice]   -> sentry_pitch_demo.mp4
set -euo pipefail
cd "$(dirname "$0")/.."

VOICE=${1:-af_heart}
ffmpeg -v error -y -i build/film_silent.mp4 -i "voice/$VOICE/narration.wav" \
  -filter_complex "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad[a]" \
  -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart \
  sentry_pitch_demo.mp4
ffprobe -v error -show_entries format=duration,size -of default=nw=1 sentry_pitch_demo.mp4
