#!/usr/bin/env bash
# Render scene prompts with Z-Image Turbo, once per seed. Existing outputs are skipped,
# so the script can be re-run after adding scenes or seeds.
#   SET=v3 SEEDS="7 21" scripts/render_stills.sh [scene-id ...]
# Each prompt file is "STYLE|description", where STYLE picks one of the presets below.
set -u
cd "$(dirname "$0")/.."

SET=${SET:-v3}
SEEDS=${SEEDS:-"7 21"}
OUT=stills/draft_$SET
mkdir -p "$OUT"

style() {
  case $1 in
    EPIC) echo "Cinematic 3D animated film still, epic fantasy, dramatic lighting, highly detailed, rich textures, deep teal night and warm torch-gold palette, 16:9 wide shot, no text, no letters, no writing." ;;
    HOME) echo "Cinematic 3D animated film still, warm natural light, highly detailed, rich textures, 16:9 wide shot, no text, no letters, no writing." ;;
    ICON) echo "" ;;
  esac
}

prompts=(scripts/prompts/$SET/*.txt)
if [[ $# -gt 0 ]]; then
  prompts=()
  for id in "$@"; do prompts+=("scripts/prompts/$SET/$id.txt"); done
fi

for p in "${prompts[@]}"; do
  id=$(basename "$p" .txt)
  IFS='|' read -r preset desc < "$p"
  w=1344 h=768
  [[ $preset == ICON ]] && w=1024 h=1024
  for s in $SEEDS; do
    out="$OUT/${id}_s${s}.png"
    [[ -f $out ]] && continue
    if mflux-generate-z-image-turbo --prompt "$(style "$preset") $desc" --width $w --height $h --steps 9 --seed "$s" -q 8 \
        --output "$out" > "build/${SET}_${id}_s${s}.log" 2>&1; then
      echo "done $out"
    else
      echo "FAILED $out (see build/${SET}_${id}_s${s}.log)"
    fi
  done
done
