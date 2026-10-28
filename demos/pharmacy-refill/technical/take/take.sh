#!/usr/bin/env bash
# take.sh — one step of the take on the cluster in ../film.env (the pharmacy app, Sentry). Runs under macOS's bash 3.2 and Linux's bash 5 (GNU date/sha256sum).
#   take.sh key <full|mrn|none> <deploy>…   set DEMAND_KEY on the report tools; the pod rolls, then its saved lineage
#                                          config is patched back (a roll drops it on the proxy path)
#   take.sh relineage <deploy>…            only the patch-back, then wait until the sidecar runs with lineage on
#   take.sh reset                          empty the fills table (each release starts clean; straight to Postgres, no trace)
#   take.sh run <label> refill <who> | nightly    one conversation; records its trace id in the take's traces.txt
#   take.sh verdicts <trace>               per exchange: caller → callee, risk, enforcement, rules, crossings, carried-in labels
#   take.sh at <epoch seconds>             wait until then (date +%s)
#   take.sh since                          seconds since the last pharmacy conversation Sentry recorded
#   take.sh capture                        after instrument: payload capture on in every sidecar config; saves them (relineage's source)
#   take.sh pin                            pin the plain pods (records, demo client, vendors) to the digest they run now
# Runs and logs go to $TAKE (sequence.sh sets it to film/takes/<name>); restore copies are in ../state/<cluster>/restore/.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); PKG=$(cd "$HERE/.." && pwd)
[ -f "${FILM_ENV:-$PKG/film.env}" ] || { echo "no ${FILM_ENV:-$PKG/film.env} yet: cp $PKG/film.env.example $PKG/film.env, then read it" >&2; exit 2; }
set -a; . "${FILM_ENV:-$PKG/film.env}"; set +a
export KUBECONFIG; CTX=kind-$CLUSTER; NS=pharmacy
R=$PKG/state/$CLUSTER/restore; TAKE=${TAKE:-$HERE/runs-$(date +%F)}; mkdir -p "$R" "$TAKE"
k() { kubectl --context "$CTX" "$@"; }
dgsql() { k -n data-governance exec -i data-governance-postgres-0 -- sh -c 'psql -U "$POSTGRES_USER" -d data_governance -At -F "|" -v ON_ERROR_STOP=1'; }
cfgsum() { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum; fi; }   # GNU (Linux) / Perl (macOS)
envof() { k -n $NS get deploy "$1" -o jsonpath="{.spec.template.spec.containers[0].env[?(@.name==\"$2\")].value}"; }

relineage() {
  local since d ok same before after cfg samelist=""
  since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  for d in "$@"; do
    [ -s "$R/authbridge-config-$d.json" ] || { echo "no restore copy for $d in $R (setup/sentry-film.sh instrument saves them)" >&2; exit 1; }
    before=$(k -n $NS get cm "authbridge-config-$d" -o jsonpath='{.data.config\.yaml}' | cfgsum)
    k -n $NS patch cm "authbridge-config-$d" --type merge --patch-file "$R/authbridge-config-$d.json" >/dev/null
    after=$(k -n $NS get cm "authbridge-config-$d" -o jsonpath='{.data.config\.yaml}' | cfgsum)
    [ "$before" = "$after" ] && samelist="$samelist $d "
  done
  for d in "$@"; do
    ok=0
    # a roll that kept the saved config needs no hot swap: the pod started with lineage on (its start log says so)
    case "$samelist" in *" $d "*) k -n $NS logs "deploy/$d" -c authbridge-proxy 2>/dev/null | grep -q 'lineage-telemetry: initialized' && ok=1 ;; esac
    if [ "$ok" = 0 ]; then
      for _ in $(seq 1 40); do
        if k -n $NS logs "deploy/$d" -c authbridge-proxy --since-time="$since" 2>/dev/null | grep -q 'pipelines swapped'; then ok=1; break; fi; sleep 3
      done
    fi
    cfg=$(k -n $NS get cm "authbridge-config-$d" -o jsonpath='{.data.config\.yaml}')
    echo "$d: DEMAND_KEY=$(envof "$d" DEMAND_KEY) lineage $(grep -c lineage-telemetry <<<"$cfg")x capture $(grep -c 'capture_io: true' <<<"$cfg")x live=$ok"
    [ "$ok" = 1 ] || { echo "$d: the sidecar did not confirm lineage within 2 min (kubectl --context $CTX -n $NS logs deploy/$d -c authbridge-proxy)" >&2; exit 1; }
  done
}

case "${1:-}" in
  key)
    key=$2; shift 2; roll=""
    for d in "$@"; do if [ "$(envof "$d" DEMAND_KEY)" = "$key" ]; then echo "$d: DEMAND_KEY already $key (no roll)" | tee -a "$TAKE/keys.log"; else roll="$roll $d"; fi; done
    [ -n "$roll" ] || exit 0
    echo ">> $(date -u +%FT%TZ) key $key$roll" | tee -a "$TAKE/keys.log"
    for d in $roll; do k -n $NS set env "deploy/$d" "DEMAND_KEY=$key" >/dev/null; done
    for d in $roll; do k -n $NS rollout status "deploy/$d" --timeout=300s >/dev/null; done
    relineage $roll | tee -a "$TAKE/keys.log"; [ "${PIPESTATUS[0]}" = 0 ] ;;
  relineage) shift; relineage "$@" ;;
  capture)
    off=$R/../authbridge-configs-capture-off.yaml; first=0; [ -s "$off" ] || first=1   # the undo is the FIRST capture-off copy only
    k -n $NS get cm -o name | grep authbridge-config- | while read -r cm; do
      [ "$first" = 1 ] && { k -n $NS get "$cm" -o yaml >> "$off"; echo --- >> "$off"; }
      k -n $NS get "$cm" -o json | python3 -c 'import sys,json; d=json.load(sys.stdin); k="config.yaml"; print(json.dumps({"data":{k:d["data"][k].replace("capture_io: false","capture_io: true")}}))' > "$R/${cm#configmap/}.json"
      k -n $NS patch "$cm" --type merge --patch-file "$R/${cm#configmap/}.json" >/dev/null && echo "capture on: $cm"
    done ;;
  pin)       # the plain pods run registry…/agent-examples-snp:latest with imagePullPolicy Always: a restart could pull another build
    img=$(k -n $NS get pod -l app.kubernetes.io/name=records -o jsonpath='{.items[0].status.containerStatuses[0].imageID}' 2>/dev/null || true)
    [ -n "$img" ] || img=$(k -n $NS get pods -o jsonpath='{range .items[*]}{.metadata.name} {.status.containerStatuses[0].imageID}{"\n"}{end}' | awk '/^records-/ {print $2; exit}')
    case "$img" in *agent-examples-snp@sha256:*) ;; *) echo "records runs '$img', not a registry digest; nothing pinned" >&2; exit 1 ;; esac
    ref=registry.cr-system.svc.cluster.local:5000/agent-examples-snp@${img##*@}
    echo "pin to $ref" | tee "$R/../pin.log"
    for d in $(k -n $NS get deploy -o jsonpath='{range .items[*]}{.metadata.name} {.spec.template.spec.containers[0].image}{"\n"}{end}' | awk '/agent-examples-snp(:latest)?$/ && !/otel/ {print $1}'); do
      c=$(k -n $NS get deploy "$d" -o jsonpath='{.spec.template.spec.containers[0].name}')
      k -n $NS set image "deploy/$d" "$c=$ref" | tee -a "$R/../pin.log"
    done
    k -n $NS rollout status deploy --timeout=300s >/dev/null && echo "plain pods pinned" ;;
  at)        # a time already past returns at once
    utc() { python3 -c 'import sys,datetime as d; print(d.datetime.fromtimestamp(int(sys.argv[1]), d.timezone.utc).strftime(sys.argv[2]))' "$@"; }
    echo ">> $(date -u +%FT%TZ) waiting until $(utc "$2" %FT%TZ) ($(( $2 - $(date +%s) )) s)"
    while [ "$(date +%s)" -lt "$2" ]; do sleep 5; done; echo ">> $(date -u +%FT%TZ) at $(utc "$2" %T)" ;;
  since)
    dgsql <<'SQL'
select extract(epoch from now() - max(l.occurred_at))::int from interaction_legs l join interactions i on i.id = l.interaction_id
join entities c on c.id = i.caller_entity_id where c.natural_key in ('client:pharmacy-demo-client', 'agent:pharmacy/demo-client');
SQL
    ;;
  reset)
    echo ">> $(date -u +%FT%TZ) reset fills" | tee -a "$TAKE/keys.log"
    k -n $NS exec deploy/postgres -- sh -c 'psql -U "$POSTGRES_USER" -d pharmacy -c "TRUNCATE fills"' | tee -a "$TAKE/keys.log" ;;
  run)
    label=$2; shift 2
    t0=$(date -u +%FT%TZ)
    echo ">> $label $t0 demo.py $*" | tee "$TAKE/$label.log"
    k -n $NS exec deploy/demo-client -- python3 /app/app/demo.py "$@" 2>&1 | tee -a "$TAKE/$label.log"
    sleep 20
    root=pharmacy-assistant; [ "$1" = nightly ] && root=pharmacy-ops      # the conversation's root span (as rec_live.js picks it)
    tr=$(dgsql <<<"select trace_id from spans where started_at >= '$t0' and parent_id is null and name like '$root a2a%' order by started_at limit 1")
    [ -n "$tr" ] || { echo "Sentry recorded no trace since $t0: is the collector forwarding? (setup/sentry-film.sh doctor)" >&2; exit 1; }
    echo "trace $tr  t0 $t0  done $(date -u +%FT%TZ)" | tee -a "$TAKE/$label.log"
    echo "$label $tr $t0" >> "$TAKE/traces.txt" ;;
  verdicts)
    t=$2
    dgsql <<SQL
select coalesce(ce.display_name, ce.natural_key) || ' -> ' || coalesce(ee.display_name, ee.natural_key),
       r.risk_level, r.enforcement_type, array_to_string(r.triggered_rule_ids, ','),
       (select count(*) from payload_label_edges x where x.to_interaction_id = i.id and x.from_trace_id is not null and x.from_trace_id <> '$t'),
       (select string_agg(distinct x.unit_text || ':' || x.entity_type || '<' || left(coalesce(x.origin_trace_id, ''), 8), ' ')
          from payload_label_edges x where x.to_interaction_id = i.id and x.to_leg_type::text like '%request%' and x.origin_trace_id is not null and x.origin_trace_id <> '$t')
from interactions i
join entities ce on ce.id = i.caller_entity_id join entities ee on ee.id = i.callee_entity_id
left join lateral (select * from interaction_risk_records rr where rr.interaction_id = i.id order by rr.version desc limit 1) r on true
where i.trace_id = '$t'
order by (select min(l.occurred_at) from interaction_legs l where l.interaction_id = i.id);
SQL
    ;;
  *) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 2 ;;
esac
