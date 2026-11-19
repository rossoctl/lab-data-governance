#!/usr/bin/env bash
# Deploy the enforcement path of Sentry's dg (data governance) component into a
# local DAM cluster:
# Postgres, the OTLP receiver, interactions (sidecar algorithm), classification,
# leg-ready, OPA and the verdict service. The UI, alerts, data-lineage,
# label-lineage and the rossoctl HTTPRoute are left out; RISK_INHERITED_LABELS
# stays off (level A: a verdict judges the request's own payload).
#
# Usage: demos/dam/deploy-dg.sh [DAM checkout, default ~/development/dam]
#
# Prerequisite: the images docker.io/data-governance/{receiver,classification}:latest
# built from this checkout and imported into k3s (see README, "Sentry").
#
# Changes against deploy/k8s/, all made here and not in the shipped manifests:
# - an extra NetworkPolicy lets namespace sentry-dam (Sentry's MCP policy service)
#   reach the receiver (4318) and the verdict service (8080);
# - leg-ready's internal-destination patterns also name k3s's pod and Service
#   ranges (10.42.0.0/16, 10.43.0.0/16) instead of only kind's;
# - the verdict service runs one replica: each holds the classifier in memory.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DAM="${1:-$HOME/development/dam}"
export KUBECONFIG="${KUBECONFIG:-$(cd "$DAM" && mise run -q cluster:kubeconfig 2>/dev/null | tail -1)}"
NS=data-governance
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

for f in 00-namespace 10-postgres-secret 20-postgres 30-receiver 45-verdict 50-networkpolicy 70-interactions 80-classification 85-leg-ready 95-opa; do
  kubectl apply -f "$REPO/deploy/k8s/$f.yaml" >/dev/null && echo "applied $f"
done

cat > "$TMP/sentry-dam-ingress.yaml" <<'EOF'
# The MCP policy service in namespace sentry-dam emits lineage spans to the
# receiver and asks the verdict service, as a Sentry gate would.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sentry-dam-to-receiver
  namespace: data-governance
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: data-governance-receiver
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: sentry-dam
      ports:
        - {protocol: TCP, port: 4318}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sentry-dam-to-verdict
  namespace: data-governance
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: data-governance-verdict
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: sentry-dam
      ports:
        - {protocol: TCP, port: 8080}
EOF
kubectl apply -f "$TMP/sentry-dam-ingress.yaml" >/dev/null && echo "applied sentry-dam ingress policies"

# k3s ranges are internal too (kind's are what the shipped manifest names).
current="$(kubectl -n $NS get deploy data-governance-leg-ready -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="RISK_INTERNAL_URL_WHITELIST_PATTERNS")].value}')"
case "$current" in
  *10.42.0.0/16*) ;;
  *) kubectl -n $NS set env deploy/data-governance-leg-ready -c leg-ready \
       RISK_INTERNAL_URL_WHITELIST_PATTERNS="$current,10.42.0.0/16,10.43.0.0/16" >/dev/null
     echo "leg-ready: added k3s ranges to the internal patterns" ;;
esac
kubectl -n $NS scale deploy/data-governance-verdict --replicas=1 >/dev/null

# The verdict secret: 64 hex with at least one letter (an all-digit value reads back as a number).
if ! kubectl -n $NS get secret dg-verdict-secret >/dev/null 2>&1; then
  while :; do s="$(openssl rand -hex 32)"; case "$s" in *[a-f]*) break ;; esac; done
  kubectl -n $NS create secret generic dg-verdict-secret --from-literal=secret="$s" >/dev/null
  echo "created dg-verdict-secret"
fi

# The OPA policy, compiled from the shipped catalog inside the receiver image.
docker run --rm -v "$REPO/data_governance/risk/rules/_policy_data:/rules:ro" --entrypoint python \
  docker.io/data-governance/receiver:latest -c \
  "from data_governance.risk.rules import compile; print(compile.compile_bundle('/rules/rules_source.json'))" \
  > "$TMP/data_governance.rego"
kubectl create configmap opa-policy -n $NS \
  --from-file="data_governance.rego=$TMP/data_governance.rego" \
  --from-file="rules_source.json=$REPO/data_governance/risk/rules/_policy_data/rules_source.json" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
echo "applied opa-policy (shipped catalog)"

kubectl -n $NS rollout restart deploy --selector app.kubernetes.io/part-of=data-governance >/dev/null
for d in data-governance-receiver data-governance-interactions data-governance-classification data-governance-leg-ready opa data-governance-verdict; do
  kubectl -n $NS rollout status deploy/$d --timeout=600s
done
