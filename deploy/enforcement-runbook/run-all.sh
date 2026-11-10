#!/usr/bin/env bash
# run-all.sh — the enforcement experiment of RUNBOOK.md §§ 2–7 in one command, each step with its expected outcome.
# Prints one line per step (PASS/FAIL + what was read; a dg.sh step by its exit status) to $RUN/summary.txt; the evidence goes to $RUN/rows.jsonl and the
# dg.sh outputs to $RUN/*.out. Changes the namespace's gate several times and scales the verdict service; ends in § 7's
# reset state (gate enforcing, fallback deny, no fail-mode document, verdict service at 2). About 25 minutes.
set -uo pipefail
unset RUN   # every run-all.sh is a run of its own: a RUN exported by a sourced env.sh is the manual walk's
source "$(dirname "$0")/env.sh" || exit 9
LOCK=${TMPDIR:-/tmp}/sentry-runbook-$NS.lock   # one run per namespace: two runs flip the same gate
mkdir "$LOCK" 2>/dev/null || { echo "STOP: another run-all.sh holds $LOCK (remove it if none is running)"; exit 8; }
trap 'rmdir "$LOCK"' EXIT
V=demand-forecast.example:8000 H2=sms-gateway.example:8000 H3=copay-assist.example:8000
RD=report-dispense SD=schedule-delivery
pass=0 fail=0
log() { echo "$*" | tee -a "$RUN/summary.txt"; }
# expect <label> <workload> <kind> <host> <delivered true|false> <gate action/reason> [shadow]
expect() {
  local label=$1 w=$2 kind=$3 host=$4 want_d=$5 want_g=$6 want_s=${7:-}
  local line; line=$(bash "$HERE/call.sh" "$label" "$w" "$kind" "$host")
  local got_d got_g got_s
  got_d=$(echo "$line" | sed -n 's/.*delivered=\([A-Za-z]*\).*/\1/p' | tr 'A-Z' 'a-z')
  got_g=$(echo "$line" | sed -n 's/.*| gate \([a-z_]*\/[a-z_]*\).*/\1/p')
  got_s=$(echo "$line" | grep -q ' SHADOW' && echo shadow || true)
  if [ "$got_d" = "$want_d" ] && [ "$got_g" = "$want_g" ] && [ "$got_s" = "$want_s" ]; then pass=$((pass+1)); log "PASS $line"
  else fail=$((fail+1)); log "FAIL (want delivered=$want_d gate $want_g $want_s) $line"; fi
}
# check <step> <want rc> <got rc> [detail]: one PASS/FAIL line for a command's exit status
check() { if [ "$3" = "$2" ]; then pass=$((pass+1)); log "PASS $1 rc=$3 $4"; else fail=$((fail+1)); log "FAIL $1 rc=$3 (want rc=$2) $4"; fi; }
# dgo <step> <want rc>[:<message>] <dg.sh args>: run dg.sh into $RUN/<step>.out and check its exit status;
# a refused command (want rc != 0) must also print its refusal <message> and leave the namespace source as it was
dgo() { local f=$1 want=${2%%:*} msg=; [[ $2 == *:* ]] && msg=${2#*:}; shift 2; local before after rc
  before=$(rv); DG "$@" > "$RUN/$f.out" 2>&1; rc=$?; after=$(rv)
  [ -z "$msg" ] || grep -qF -- "$msg" "$RUN/$f.out" || rc="$rc+no-refusal-message"
  [ "$want" = 0 ] || { [ -n "$before" ] && [ -n "$after" ]; } || rc="$rc+rv-unread"
  [ "$want" = 0 ] || [ "$before" = "$after" ] || rc="$rc+rv-moved"
  check "$f" "$want" "$rc" "(namespace source rv ${before:-?} -> ${after:-?})"; }
status() { DG namespace "$NS" status 2>&1 | grep -m1 'sentry-gate' | tee -a "$RUN/summary.txt"; }
log "=== run $(date '+%F %T') ns=$NS labdg=$(git -C "$LABDG" describe --always --dirty --abbrev=7) run=$RUN"

log "--- § 2 the gate on"
dgo 2-instrument-enforce 0 namespace "$NS" instrument --enforce --pass-host host.containers.internal --fallback deny; settle; status
log "--- § 3 a verdict decides"
expect 3.1-clean   $RD clean   $V  true  allow/verdict_allow
expect 3.2-pii     $RD pii     $V  false deny/verdict_block
expect 3.3-tunnel  $RD connect $V  false allow/not_inspected
log "--- § 4 no verdict: the fallback"
verdict 0
expect 4.1-down     $RD clean $V false deny/fallback_deny
expect 4.2-plain    $RD plain $V false deny/fallback_deny
dgo 4.3-allow-noflag "1:--fallback allow lets every request the pass list does not name" namespace "$NS" instrument --enforce --fallback allow
dgo 4.4-allow-flag   0 namespace "$NS" instrument --enforce --fallback allow --allow-open-default; settle; status
expect 4.5-open     $RD clean $V true  allow/fallback_allow
expect 4.6-big      $RD big   $V false deny/verdict_error
log "--- § 5 the fail-mode table"
dgo 5.0-validate-bad '1:fallback is deny or allow ("open")' namespace "$NS" fail-mode validate "$HERE/docs/bad-open.json"
dgo 5.1-set-3a 0 namespace "$NS" fail-mode set "$HERE/docs/doc-3a.json"; log "     $(grep -o 'revision [0-9a-f]\{12\}' "$RUN/5.1-set-3a.out")"
dgo 5.2-instrument 0 namespace "$NS" instrument --enforce --fallback deny --allow-open-default; settle; status
expect 5.3-row      $RD clean $V  true  allow/fallback_allow
expect 5.4-norow    $RD clean $H3 false deny/fallback_deny
dgo 5.5-set-3b 0 namespace "$NS" fail-mode set "$HERE/docs/doc-3b.json"; log "     $(grep -o 'revision [0-9a-f]\{12\}' "$RUN/5.5-set-3b.out")"
dgo 5.6-instrument 0 namespace "$NS" instrument --enforce --allow-open-default; settle; status
expect 5.7-source   $RD clean $V  true  allow/fallback_allow
expect 5.8-other    $SD clean $V  false deny/fallback_deny
expect 5.9-pair     $RD clean $H2 false deny/fallback_deny
expect 5.10-source2 $RD clean $H3 true  allow/fallback_allow
dgo 5.11-gate-report 0 namespace "$NS" gate-report; grep -v '^sidecar' "$RUN/5.11-gate-report.out" | tee -a "$RUN/summary.txt"
log "--- § 6 observe first"
dgo 6.1-observe 0 namespace "$NS" instrument --observe --allow-open-default; settle; status
expect 6.2-shadow   $SD clean $V  true  deny/fallback_deny shadow
dgo 6.3-gate-report 0 namespace "$NS" gate-report; grep -v '^sidecar' "$RUN/6.3-gate-report.out" | tee -a "$RUN/summary.txt"
log "--- § 7 reset"
verdict 2
kubectl -n "$NS" delete cm dg-fail-mode --ignore-not-found >/dev/null
dgo 7.1-instrument 0 namespace "$NS" instrument --enforce --fallback deny; settle; status
expect 7.2-clean    $RD clean $V true  allow/verdict_allow
expect 7.3-pii      $RD pii   $V false deny/verdict_block
log "=== done $(date '+%F %T'): $pass PASS, $fail FAIL"
[ "$fail" = 0 ]
