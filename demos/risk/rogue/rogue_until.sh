#!/bin/sh
# rogue_until.sh <prefix> [<max>=5] — run rogue turns until one notifies the partner (analyze.py column 4), at most <max>.
[ -n "$1" ] || { echo "usage: rogue_until.sh <prefix> [<max>=5]"; exit 1; }
cd "$(dirname "$0")"; P=$1; MAX=${2:-5}; n=0
while [ $n -lt $MAX ]; do n=$((n+1)); L=$P-$n
  echo "### $L $(date -u +%H:%M:%SZ)"
  out=$(mktemp); python3 one_turn.py $L >"$out" 2>&1 || { cat "$out"; rm -f "$out"; echo "### one_turn.py $L failed; stopping"; exit 2; }
  grep -E '^trace|"secs"' "$out"; rm -f "$out"
  sleep 20; row=$(python3 analyze.py $L); echo "$row"
  echo "$row" | cut -d'|' -f4 | grep -q True && { echo "### notified on turn $n"; exit 0; }
done; echo "### no notification in $MAX turns"; exit 1
