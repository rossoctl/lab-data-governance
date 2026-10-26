#!/usr/bin/env bash
# film.sh <step> [take] — the film from a take (film/takes/<take>, default v4.2: the take film v4.2 was made from).
# Steps that read Sentry need the take's cluster up (film.env); the rest work on the take folder alone.
#
#   film.sh check  <take>   the five verdicts against the film's; stops when one differs (the words follow the run)   [cluster]
#   film.sh live   <take>   the live graph frames, from rec/A.webm (its graph-live mark → 4 s after the refill ends, ×7)
#   film.sh ids    <take>   every id the stills and cards need (take/ids.py → ids.json)                               [cluster]
#   film.sh counts <take>   the counts and verdicts the narration reads (counts4.py → counts.json)                    [cluster]
#   film.sh facts  <take>   take-facts.yaml, the review's truth file, from take/take-facts.template.yaml
#   film.sh stills <take>   Sentry's pages for every level, rossoctl's catalogs, the policy, the app's map            [cluster]
#   film.sh opa    <take>   the exact input Sentry sends OPA, and OPA's answer, for A, A's courier, B, N1            [cluster]
#   film.sh panels <take>   the OPA panels, the closing grid, the pipeline / inside / Rego panels
#   film.sh cards  <take>   the pitch's Sentry cards repainted with this take's names; the pitch with them; door frames [cluster]
#   film.sh stage  <take> [I1 …]   the nine sections (or the named ones), STAGE_JOBS at a time (default 3)
#   film.sh join   <take> [name]   the film: one voice + music → <take>/<name>.mp4 (default film.mp4, never overwritten) + .srt
#   film.sh review <take> [name]   review.py: words heard vs the script, frozen frames, loudness, claims, Gemini's watch
#   film.sh all    <take>   check live ids counts facts stills opa panels cards stage join review
#   film.sh words  <take> [I…]     after a change of words: stage (the named sections, or all) → join → review
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); PKG=$(cd "$HERE/.." && pwd); LONG=$PKG/film/long
[ -f "${FILM_ENV:-$PKG/film.env}" ] || { echo "no ${FILM_ENV:-$PKG/film.env} yet: cp $PKG/film.env.example $PKG/film.env, then read it" >&2; exit 2; }
set -a; . "${FILM_ENV:-$PKG/film.env}"; set +a
step=${1:-}; NAME=${2:-v4.2}; shift 2 2>/dev/null || shift $#
case "$NAME" in /*) TAKE=$NAME ;; *) TAKE=$PKG/film/takes/$NAME ;; esac
[ -d "$TAKE" ] || { echo "no take at $TAKE (take/sequence.sh <name> records one)" >&2; exit 1; }
export FILM_TAKE=$TAKE NODE_PATH=$PKG/node_modules HTTP_PORT KUBECONFIG
PY=${PYTHON:-python3.11}
say() { printf '>> %s\n' "$*"; }
id() { python3 -c 'import json,sys,functools; print(functools.reduce(lambda d, k: d[k], sys.argv[2:], json.load(open(sys.argv[1]))))' "$TAKE/ids.json" "$@"; }

do_check() {
  say "the five verdicts on the call to the demand forecaster, against the film's"
  local r t; : > "$TAKE/verdicts.txt"
  while read -r r t _; do echo "== $r $t" >> "$TAKE/verdicts.txt"; "$HERE/take.sh" verdicts "$t" >> "$TAKE/verdicts.txt"; done < "$TAKE/traces.txt"
  python3 - "$TAKE" <<'PY'
import sys, pathlib
take = pathlib.Path(sys.argv[1]); runs, cur = {}, None
for l in (take / "verdicts.txt").read_text().splitlines():
    if l.startswith("== "): cur = l.split()[1]; runs[cur] = {"trace": l.split()[2], "rows": []}
    elif l.strip(): runs[cur]["rows"].append(l.split("|"))
want = {"A": "critical block DG-001,DG-002,DG-004", "B": "critical block DG-001,DG-002,DG-004", "N1": "critical block DG-001,DG-002,DG-004",
        "C": "none allow -", "N2": "none allow -"}
bad = 0
missing = [k for k, r in runs.items() if not r["rows"]]
if missing:
    sys.exit(f"STOP: Sentry on this cluster has no exchanges for {', '.join(missing)} ({', '.join(runs[k]['trace'][:8] for k in missing)}): "
             "the take was recorded on another cluster, or its data is gone. Check a take on the cluster that recorded it.")
for k, w in want.items():
    rows = [r for r in runs.get(k, {}).get("rows", []) if "-> demand-forecast" in r[0]]
    got = " / ".join(f"{r[1]} {r[2]} {r[3] or '-'}" for r in rows) or "no call to the forecaster"
    ok = len(rows) == 1 and got == w
    note = ""
    if k == "N1" and rows:
        origins = {o.split("<")[1] for o in rows[0][5].split() if "<" in o and "MRN" in o}
        b8 = runs["B"]["trace"][:8]
        if origins != {b8}: ok = False; note = f" · MRN carried in from {sorted(origins) or 'nowhere'}, want B ({b8}) only"
        else: note = f" · MRN carried in from B ({b8}) only"
    if k in ("A", "B") and rows and (rows[0][4] not in ("", "0") or rows[0][5]):
        ok = False; note += f" · carries labels from another session ({rows[0][5] or rows[0][4]}): the refills were not 15 min apart"
    if k == "A":
        c = [r for r in runs["A"]["rows"] if "-> courier" in r[0]]
        if not c or c[0][2] != "allow": ok = False; note += " · the courier exchange is not allowed"
        else: note += " · courier allowed"
    print(f"   {'ok  ' if ok else 'FAIL'}  {k:3} {got}{note}" + ("" if ok else f"   (the film says: {w})"))
    bad += not ok
if bad:
    sys.exit(f"STOP: {bad} verdict(s) differ from the film's. The words follow the run: do not build the film from this take; "
             "find out why (take.sh verdicts <trace>), or change the narration to what this run shows.")
print("   all five verdicts match the film")
PY
}

do_live() {
  local m s e; m=$TAKE/rec/A.marks.json
  [ -s "$TAKE/rec/A.webm" ] && [ -s "$m" ] || { echo "no rec/A.webm + A.marks.json in $TAKE: a git checkout gets them with setup/sentry-film.sh media <the zip> (pins.yaml → media); a new take records them (take/sequence.sh <new name>)" >&2; exit 1; }
  s=$(python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print(next(x["at"] for x in m if x.get("seg")=="graph-live"))' "$m")
  e=$(python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print(next(x["at"] for x in m if x["ev"]=="conv-exit") + 4)' "$m")
  rm -rf "$TAKE/live"; mkdir -p "$TAKE/live"
  ffmpeg -v error -y -ss "$s" -to "$e" -i "$TAKE/rec/A.webm" -vf "setpts=PTS/${LIVE_SPEED:-7},fps=30,scale=1920:976:flags=lanczos,pad=1920:1080:0:0:color=0x0e1215" "$TAKE/live/%04d.png"
  say "live: $(ls "$TAKE/live" | wc -l | tr -d ' ') frames ($s s → $e s of rec/A.webm, ×${LIVE_SPEED:-7})"
}

do_ids()    { python3 "$HERE/ids.py" "$TAKE" > /dev/null && say "ids.json: $(id cards origin) · $(id cards cross_trace)"; }
do_counts() { "$PY" "$LONG/v4/counts4.py" > /dev/null && say "counts.json: $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(" ".join("%s=%s" % (k, d[k]) for k in "NMTJF"))' "$TAKE/counts.json")"; }
do_facts()  { python3 "$HERE/facts.py" "$TAKE"; }

do_stills() {
  cd "$LONG"
  local A B N1 C N2; A=$(id traces A); B=$(id traces B); N1=$(id traces N1); C=$(id traces C); N2=$(id traces N2)
  say "Sentry's pages (5 modes), rossoctl's catalogs, the policy, the map"
  node v4/shots.js "$TAKE/shots/tap" mode=tap t="$A" look="$(id A look)" | tail -1 | cut -c1-120
  node v4/shots.js "$TAKE/shots/level1" mode=level1 t="$A" rep="$(id A rep)" courier="$(id A courier)" | tail -1 | cut -c1-120
  node v4/shots.js "$TAKE/shots/level2" mode=level2 t="$B" rep="$(id B rep)" | tail -1 | cut -c1-120
  node v4/shots.js "$TAKE/shots/night" mode=night t="$N1" b="$B" rec="$(id B rec)" ans="$(id N1 ans)" rep="$(id N1 rep)" bAt="$(id B bAt)" nAt="$(id N1 nAt)" | tail -1 | cut -c1-120
  node v4/shots.js "$TAKE/shots/fixed" mode=fixed t="$C" rep="$(id C rep)" n2="$N2" n2rep="$(id N2 rep)" | tail -1 | cut -c1-120
  node v3/p1/shots.js "$TAKE/shots/rossoctl" | tail -1 | cut -c1-120
  node v3/p3/shots.js "$TAKE/shots/policy" | tail -1 | cut -c1-120
  node v3/map_shots.js v4/map-views.json "$TAKE/map"
  python3 - "$TAKE/map/boxes.json" <<'PY'   # the two rings I1 draws on the map: the refill inside the pharmacy, the report leaving it
import json, sys
p = sys.argv[1]; b = json.load(open(p)); n = b["flow"]["nodes"]
U = lambda *ks: (lambda xs: [min(x[0] for x in xs), min(x[1] for x in xs), max(x[0] + x[2] for x in xs) - min(x[0] for x in xs),
                             max(x[1] + x[3] for x in xs) - min(x[1] for x in xs)])([n[k] for k in ks])
g = lambda r, d=8: [r[0] - d, r[1] - d, r[2] + 2 * d, r[3] + 2 * d]
b["flow"]["path1"] = g(U("assistant", "lookup", "clinical", "review", "records")); b["flow"]["path2"] = g(U("report", "fcst"))
json.dump(b, open(p, "w"), indent=1)
PY
}

do_opa() { "$LONG/v4/opa_input.sh" A "$(id A rep)" courierA "$(id A courier)" B "$(id B rep)" N1 "$(id N1 rep)"; }

do_panels() {
  cd "$LONG"
  "$PY" v4/panels/opa_panel.py A B N1 >/dev/null; "$PY" v4/panels/grid_panel.py
  local ex; ex=$(id A exchange); [ -n "$ex" ] || { echo "no exchange id for A's report in ids.json (take/film.sh ids)" >&2; exit 1; }
  sed "s/dfd584f3594abc54/$ex/g" v4/panels/pipeline.html > "$TAKE/panels/pipeline.html"   # the example exchange: this take's A report
  node v4/panels.js "$TAKE/panels/out" pipeline="$TAKE/panels/pipeline.html" "inside=v4/panels/policy.html?v=inside" "rego=v4/panels/policy.html?v=rego" \
    opaA="$TAKE/panels/opa-A.html" opaB="$TAKE/panels/opa-B.html" opaN1="$TAKE/panels/opa-N1.html" grid="$TAKE/panels/grid.html" | tail -1 | cut -c1-120
}

do_cards() {
  [ -s "$PKG/film/pitch-2m35/master/sentry_pitch_demo.mp4" ] || { echo "no pitch film (setup/sentry-film.sh sources downloads it)" >&2; exit 1; }
  "$PY" "$LONG/v4/fixes/cards.py" | grep -v "^ " | tail -6; "$PY" "$LONG/v4/fixes/build4.py"
}

ensure_media() {   # what a take ships without because it is rebuilt here, with no cluster: the live frames, the repainted pitch, the door frames
  [ -n "$(ls "$TAKE/live" 2>/dev/null)" ] || { say "live frames missing: cutting them from rec/A.webm"; do_live; }
  if [ ! -s "$TAKE/pitch-fixed.mp4" ] || [ "$(ls "$TAKE/src" 2>/dev/null | wc -l | tr -d ' ')" -lt 9 ]; then
    [ -s "$TAKE/cards/cards.json" ] || { echo "no repainted cards in $TAKE/cards (take/film.sh cards, with the take's cluster up)" >&2; exit 1; }
    [ -s "$PKG/film/pitch-2m35/master/sentry_pitch_demo.mp4" ] || { echo "no pitch film (setup/sentry-film.sh sources downloads it)" >&2; exit 1; }
    say "the pitch with this take's cards, and its door frames (fixes/build4.py, ~3 min)"; "$PY" "$LONG/v4/fixes/build4.py" | tail -1
  fi
}

do_stage() {
  ensure_media
  local names=("$@"); [ ${#names[@]} -gt 0 ] || names=(I1 I2 I3 I4 I5 I6 I7 I8 I9)
  mkdir -p "$TAKE/out"; local j=0 n
  for n in "${names[@]}"; do
    "$PY" "$LONG/v4/film4.py" stage "$n" > "$TAKE/out/stage-$n.log" 2>&1 &
    j=$((j + 1)); if [ $j -ge "${STAGE_JOBS:-3}" ]; then wait; j=0; fi
  done; wait
  for n in "${names[@]}"; do tail -1 "$TAKE/out/stage-$n.log"; grep -q "lines$" "$TAKE/out/stage-$n.log" || { echo "stage $n failed: $TAKE/out/stage-$n.log" >&2; exit 1; }; done
}

film_name() { local n=${1:-film} i=2; n=${n%.mp4}; local f=$n; while [ -e "$TAKE/$f.mp4" ]; do f=$n-$i; i=$((i + 1)); done; echo "$f"; }
do_join() {
  ensure_media
  local f; f=$(film_name "${1:-film}")
  "$PY" "$LONG/v4/film4.py" join --music --onevoice --out "$f.mp4"; "$PY" "$LONG/v4/film4.py" srt --out "$f.mp4"
  echo "$f" > "$TAKE/out/last-film"
  say "$TAKE/$f.mp4 ($(ffprobe -v error -show_entries format=duration -of csv=p=0 "$TAKE/$f.mp4" | cut -d. -f1) s) + $f.srt"
}
do_review() {
  local f=${1:-$(cat "$TAKE/out/last-film" 2>/dev/null || echo film)}; f=${f%.mp4}
  local x=(); [ -n "${GEMINI_API_KEY:-}" ] || { x=(--no-claims --no-watch); say "no GEMINI_API_KEY: the review skips claims and the watch"; }
  REVIEW_FACTS=${REVIEW_FACTS:-$TAKE/take-facts.yaml} "$PY" "$LONG/review.py" "$f.mp4" "${x[@]+"${x[@]}"}"
}

case "$step" in
  check|live|ids|counts|facts|stills|opa|panels|cards) "do_$step" ;;
  stage) do_stage "$@" ;;
  join) do_join "$@" ;;
  review) do_review "$@" ;;
  all) do_check; do_live; do_ids; do_counts; do_facts; do_stills; do_opa; do_panels; do_cards; do_stage; do_join; do_review ;;
  words) do_stage "$@"; do_join; do_review ;;
  *) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 2 ;;
esac
