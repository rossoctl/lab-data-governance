#!/usr/bin/env bash
# Expose the narration's line and word timings to film.html as film/cues.js.
#   scripts/cues.sh [voice]
set -euo pipefail
cd "$(dirname "$0")/.."
V=voice/${1:-af_heart}
{ printf 'window.LINES = '; cat "$V/timeline.json"; printf ';\nwindow.WORDS = '; cat "$V/words.json"; printf ';\n'; } > film/cues.js
echo "film/cues.js from $V"
