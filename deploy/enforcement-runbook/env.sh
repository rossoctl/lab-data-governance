# source me — the enforcement experiment's settings (RUNBOOK.md § 0). Override by exporting before sourcing.
export KUBECONFIG=${KUBECONFIG:-$HOME/.kube/config}          # the cluster's kubeconfig
export EXPECT_CONTEXT=${EXPECT_CONTEXT:-kind-rossoctl}         # its context name; every script refuses another
export KIND_CLUSTER=${KIND_CLUSTER:-${EXPECT_CONTEXT#kind-}}   # the kind cluster's name (gate-image.sh loads into it)
export NS=${NS:-pharmacy}                                      # the namespace of the pharmacy_refills app
[ "$(kubectl config current-context)" = "$EXPECT_CONTEXT" ] || { echo "STOP: kubectl is on $(kubectl config current-context), not $EXPECT_CONTEXT"; return 9 2>/dev/null || exit 9; }
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
LABDG=$(cd "$HERE/../.." && pwd)                               # this lab-data-governance checkout
RUN=${RUN:-${TMPDIR:-/tmp}/enforcement-runbook/$(date +%Y%m%d-%H%M%S)}; mkdir -p "$RUN"   # outputs, outside the repo
DG() { (cd "$LABDG" && bash deploy/dg.sh "$@"); }
rv() { kubectl -n "$NS" get cm authbridge-runtime-config -o jsonpath='{.metadata.resourceVersion}'; }
verdict() {  # verdict <replicas>: scale the verdict service and wait
  kubectl -n data-governance scale deploy/data-governance-verdict --replicas="$1" >/dev/null
  if [ "$1" = 0 ]; then kubectl -n data-governance wait --for=delete pod -l app.kubernetes.io/name=data-governance-verdict --timeout=120s >/dev/null 2>&1
  else kubectl -n data-governance rollout status deploy/data-governance-verdict --timeout=180s >/dev/null; fi; sleep 3; }
settle() {   # every deployment of $NS rolled out, no pod still terminating
  sleep 5; for d in $(kubectl -n "$NS" get deploy -o name); do kubectl -n "$NS" rollout status "$d" --timeout=300s >/dev/null 2>&1; done
  local t=0; while kubectl -n "$NS" get pods --no-headers | grep -Eqv ' (Running|Completed) '; do sleep 5; t=$((t+5)); [ $t -ge 300 ] && break; done; sleep 10; }
