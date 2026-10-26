#!/usr/bin/env bash
# sequence.sh [name] — the film's take: five sessions in the pitch's order, into film/takes/<name>/ (default
# <date>-<cluster>). About 45 minutes; one run at a time on the cluster.
#   A   level 1 · the report sends the full record     report-dispense DEMAND_KEY=full   (recorded live: rec/A.webm)
#   B   level 2 · only the number                       report-dispense mrn                (> 15 min after A)
#   N1  level 3 · the nightly batch, its own session    export-dispense-batch mrn          (~3.5 min after B)
#   C   the fixed app · no patient key                  report-dispense none, export none  (> 15 min after N1)
#   N2  the fixed app's nightly batch                   export none                        (~3.5 min after C)
# Why the gaps: Sentry links a value to another session within LABEL_CROSS_TRACE_WINDOW_HOURS (0.25 h = 15 min). Each
# refill starts outside the window of every run before it, so no release inherits another's labels; each nightly runs
# inside its own refill's window only. The fills table is emptied before each refill (a release starts clean).
# A key switch rolls the tool's pod, which drops its lineage wiring; take.sh key puts it back (take.sh relineage).
# Afterwards: take/film.sh check <name> (the verdicts), then take/film.sh all <name> (the film).
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); PKG=$(cd "$HERE/.." && pwd)
[ -f "${FILM_ENV:-$PKG/film.env}" ] || { echo "no ${FILM_ENV:-$PKG/film.env} yet: cp $PKG/film.env.example $PKG/film.env, then read it" >&2; exit 2; }
set -a; . "${FILM_ENV:-$PKG/film.env}"; set +a
NAME=${1:-$(date +%F)-$CLUSTER}; export TAKE=$PKG/film/takes/$NAME
[ -e "$TAKE/traces.txt" ] && { echo "STOP: $TAKE already holds a take; takes are evidence and are never re-run into. Pick another name." >&2; exit 1; }
mkdir -p "$TAKE/rec"; exec > >(tee -a "$TAKE/sequence.log") 2>&1
T=$HERE/take.sh
echo ">> $(date -u +%FT%TZ) take $NAME on cluster $CLUSTER"
"$PKG/setup/sentry-film.sh" doctor || { echo "STOP: the doctor is not happy (above); fix that first" >&2; exit 1; }
after() {   # epoch seconds 15.5 min after a run's "done" stamp
  python3 -c 'import re,sys,datetime as d; t=re.search(r"done (\S+)Z", open(sys.argv[1]).read()).group(1)
print(int((d.datetime.fromisoformat(t).replace(tzinfo=d.timezone.utc)+d.timedelta(minutes=15, seconds=30)).timestamp()))' "$TAKE/$1.log"
}
$T reset; $T key full report-dispense; $T key mrn export-dispense-batch
(cd "$PKG/film/long" && NODE_PATH=$PKG/node_modules HTTP_PORT=$HTTP_PORT node v4/rec_live.js "$TAKE/rec" A)
$T reset; $T key mrn report-dispense
$T at "$(after A)"; $T run B refill rosa
sleep 200
$T run N1 nightly
$T reset; $T key none report-dispense export-dispense-batch
$T at "$(after N1)"; $T run C refill rosa
sleep 200
$T run N2 nightly
echo ">> $(date -u +%FT%TZ) take done: $TAKE"
echo "next: take/film.sh check $NAME"
