#!/usr/bin/env bash
# Deploy one app from the agent-examples-snp clone's plain manifests on the
# images build-app.sh made, attach the lineage sidecar to every workload the
# fleet declaration names (capture on, every declared non-HTTP egress port
# excluded), and roll the agents in dependency order. Idempotent.
#   usage: deploy-app.sh travel_advisor|lineage_lab
# The namespace needs the platform's sidecar config (envoy-config and the two
# authbridge ConfigMaps); each one the namespace lacks is copied from
# E2E_PLATFORM_NS (default team1), a namespace the platform set up for agents.
cd "$(dirname "$0")" && . ./common.sh
app "${1:-}"
M=$SNP/apps/$APP/manifests
PLATFORM_NS=${E2E_PLATFORM_NS:-team1}
for i in "$APP_IMAGE" "$SHIM_IMAGE" "$SIDECAR_IMAGE" "$PROXY_INIT_IMAGE"; do
  podman image exists "$i" || { echo "missing image $i: run build-sidecar.sh and build-app.sh $APP first" >&2; exit 1; }
done

render() { sed "s|image: agent-examples-snp:latest|image: $APP_IMAGE|g; s|__IMAGE__|$APP_IMAGE|g; s|__SMTP_MODE__|local|g; s|__ROSSOCTL_NS__|$NS|g" "$M/$1.yaml"; }
secret() { local n=$1; shift; k -n "$NS" create secret generic "$n" "$@" --dry-run=client -o yaml | k apply -f - >/dev/null; }
rolled() { for d in "$@"; do k -n "$NS" rollout status "deploy/$d" --timeout=300s | tail -1; done; }
# attach <deploy> <app container> [KIT_VAR=value ...]
attach() {
  local d=$1 c=$2; shift 2
  if k -n "$NS" get deploy "$d" -o jsonpath='{.spec.template.spec.initContainers[*].name}' | grep -q envoy-proxy; then
    k -n "$NS" set image "deploy/$d" "$c=$SHIM_IMAGE" \
        envoy-proxy="$SIDECAR_IMAGE" proxy-init="$PROXY_INIT_IMAGE" >/dev/null   # the sidecar too: a cortex bump must reach an already-attached workload
    say "$d: attached already; app image -> $SHIM_IMAGE, sidecar -> $SIDECAR_IMAGE"
    k -n "$NS" rollout restart "deploy/$d" >/dev/null   # same tags, new images (steps 1 and 3 reload them): the pod must be recreated
    rolled "$d" >/dev/null       # one workload at a time: a fleet-wide roll doubles every pod at once
  else
    (cd "$KIT" && env NAMESPACE="$NS" DEPLOY="$d" APP_CONTAINER="$c" APP_IMAGE="$SHIM_IMAGE" CAPTURE_IO=true \
        SIDECAR_IMAGE="$SIDECAR_IMAGE" PROXY_INIT_IMAGE="$PROXY_INIT_IMAGE" "$@" ./sidecar-patch.sh 2>&1 | grep -vE '^\s*$' | tail -2)
  fi
}

say "namespace $NS"
render 00-namespace | k apply -f - >/dev/null
for cm in envoy-config authbridge-runtime-config authbridge-config; do
  k -n "$NS" get cm "$cm" >/dev/null 2>&1 && continue
  k -n "$PLATFORM_NS" get cm "$cm" -o json | python3 -c "
import json, sys
d = json.load(sys.stdin)
d['metadata'] = {'name': d['metadata']['name'], 'namespace': '$NS', 'labels': d['metadata'].get('labels', {})}
print(json.dumps(d))" | k apply -f - >/dev/null
  say "copied configmap/$cm from $PLATFORM_NS"
done

if [ "$APP" = travel_advisor ]; then
  # the sample app's own development credentials, as its manifests expect them
  secret llm-credentials --from-literal=api_key=none
  secret postgres-credentials --from-literal=user=travel --from-literal=password=travel-pass
  secret minio-credentials --from-literal=access_key=minioadmin --from-literal=secret_key=minioadmin123
  say "manifests ($APP_IMAGE)"
  k -n "$NS" delete job postgres-bootstrap minio-bootstrap --ignore-not-found >/dev/null   # a Job's template is immutable
  for f in 05-postgres 06-weather-service 07-mailhog 08-minio 09-psp 10-tools 45-partner \
           20-agent-research 25-agent-payment 30-agents 50-demo-client; do
    render "$f" | k -n "$NS" apply -f - >/dev/null
  done
  AGENTS="payment-agent research-agent booking-agent travel-advisor"          # dependency order, the entry last
  TOOLS="search-destinations create-booking get-payment-info get-weather get-flights send-notification charge-card notify-partner"
  say "LLM on the host (plaintext, so inference is captured); the ledger on every app workload"
  for d in $AGENTS; do
    k -n "$NS" set env "deploy/$d" LLM_URL=http://host.containers.internal:11434/v1 LLM_MODEL=qwen2.5:7b >/dev/null
  done
  for d in $TOOLS $AGENTS demo-client weather-service; do k -n "$NS" set env "deploy/$d" ROSSOCTL_LEDGER=stdout >/dev/null; done
  # ROSSOCTL_VARIANT=rogue|fixed on booking-agent selects L14's case; unset, L14 skips
  [ -n "${E2E_VARIANT:-}" ] && k -n "$NS" set env deploy/booking-agent "ROSSOCTL_VARIANT=$E2E_VARIANT" >/dev/null
  rolled postgres minio mailhog weather-service psp-mock travel-partner
  rolled $TOOLS $AGENTS demo-client >/dev/null       # the manifests and env settle before the attach rolls anything
  k -n "$NS" wait --for=condition=complete job/postgres-bootstrap job/minio-bootstrap --timeout=300s >/dev/null
  say "attach"
  attach search-destinations mcp OUTBOUND_PORTS_EXCLUDE=5432
  attach create-booking mcp OUTBOUND_PORTS_EXCLUDE=5432,1025
  attach get-payment-info mcp OUTBOUND_PORTS_EXCLUDE=5432
  for d in get-weather get-flights send-notification charge-card notify-partner; do attach "$d" mcp; done
  for d in $AGENTS; do attach "$d" agent; done
  attach demo-client client
else
  say "manifests ($APP_IMAGE)"
  k -n "$NS" delete job postgres-bootstrap --ignore-not-found >/dev/null
  for f in 02-secrets 05-postgres 40-partner 10-tools 20-agents 50-demo-client; do render "$f" | k -n "$NS" apply -f - >/dev/null; done
  AGENTS="lab-b lab-c lab-a"
  TOOLS="lab-store lab-tool-x lab-tool-y"
  k -n "$NS" wait --for=condition=complete job/postgres-bootstrap --timeout=300s >/dev/null
  rolled postgres lab-partner
  rolled $TOOLS $AGENTS demo-client >/dev/null
  say "attach"
  attach lab-store mcp OUTBOUND_PORTS_EXCLUDE=5432
  attach lab-tool-x mcp
  attach lab-tool-y mcp
  for d in $AGENTS; do attach "$d" agent; done
  attach demo-client client
fi

say "roll: tools first, then the agents in dependency order (an agent resolves its peers once, at startup)"
rolled $TOOLS
for d in $AGENTS demo-client; do
  k -n "$NS" rollout restart "deploy/$d" >/dev/null; rolled "$d"; sleep 3
done
# a replaced pod may still be terminating (or already Error on its way out); preflight wants exactly one pod per workload
for _ in $(seq 60); do k -n "$NS" get pods --no-headers | grep -vE 'Running|Completed' >/dev/null || break; sleep 2; done
k -n "$NS" get pods --no-headers | grep -v Completed | awk '{print "   " $1, $2, $3}'
say "done: $APP in $NS (app $SNP_SHA, cortex $CORTEX_SHA). Next: fill_pins.py $APP"
