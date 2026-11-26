#!/usr/bin/env bash
# Deploy (or update) the MCP policy service, its OPA and the aiac onboarder
# into a local DAM cluster.
#
# Usage: demos/dam/deploy.sh [DAM checkout, default ~/development/dam] [aiac checkout, default ~/development/sentry/aiac]
#
# - The onboarder's code, tool catalogue and organisation policy come from the
#   aiac checkout's demo/dam/. Its image is localhost/aiac-agent:local, which
#   must already be imported into the cluster (see README).
# - The LLM Secret is made once, from the key of the DAM install's ibm-litellm
#   connection; the key is copied, never printed. LLM_MODEL and LLM_BASE_URL
#   may be overridden in the environment.
# - The decisions ConfigMap is seeded from decisions/ only when it does not
#   exist yet: after that, the onboarder owns it.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAM="${1:-$HOME/development/dam}"
AIAC="${2:-$HOME/development/sentry/aiac}"
LLM_MODEL="${LLM_MODEL:-aws/claude-sonnet-5-5}"
LLM_BASE_URL="${LLM_BASE_URL:-https://ete-litellm.ai-models.vpc.res.ibm.com/v1}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
kc() { (cd "$DAM" && mise run -q cluster:kubectl -- "$@") 2>&1 | grep -v '^→'; }
apply_cm() { # name, then --from-file arguments
  local name=$1; shift
  kc create configmap "$name" -n sentry-dam "$@" --dry-run=client -o yaml > "$TMP/cm.yaml"
  kc apply -f - < "$TMP/cm.yaml"
}

kc apply -f - < "$HERE/k8s/policy-service.yaml"

apply_cm mcp-policy-server --from-file=server.py="$HERE/policy-service/server.py" \
  --from-file=operations.py="$HERE/policy-service/operations.py" \
  --from-file=dg_check.py="$HERE/policy-service/dg_check.py"
apply_cm mcp-policy-rego --from-file=mcp.rego="$HERE/opa/mcp.rego"
apply_cm aiac-dam-onboarder \
  --from-file=controller.py="$AIAC/demo/dam/controller.py" \
  --from-file=onboard_dam_agent.py="$AIAC/demo/dam/onboard_dam_agent.py" \
  --from-file=company_policy.py="$AIAC/demo/dam/company_policy.py" \
  --from-file=github-mcp-tools.json="$AIAC/demo/dam/github-mcp-tools.json" \
  --from-file=policy.md="$AIAC/demo/dam/policy.md"

if ! kc get configmap mcp-policy-data -n sentry-dam -o name | grep -q configmap; then
  python3 -I "$HERE/opa/build_data.py" "$HERE/decisions" > "$TMP/data.json"
  apply_cm mcp-policy-data --from-file=data.json="$TMP/data.json"
  echo "seeded mcp-policy-data from decisions/"
fi

# dg's rule catalogue, which the company policy's data rules are mapped onto.
apply_cm dg-rules-source --from-file=rules_source.json="$HERE/../../data_governance/risk/rules/_policy_data/rules_source.json"

# The token Sentry presents to DAM's in-cluster MCP policy API (whom an agent acts for, the
# company security policy): one value, in DAM's release namespace and in sentry-dam. The
# api-server reads it at start, so it restarts once when the token is new.
if ! kc get secret mcp-policy-service-token -n default -o name | grep -q secret; then
  openssl rand -hex 32 | tr -d '\n' > "$TMP/token"
  for ns in default sentry-dam; do
    kc create secret generic mcp-policy-service-token -n "$ns" --from-file=token="$TMP/token" --dry-run=client -o yaml > "$TMP/s.yaml"
    kc apply -f - < "$TMP/s.yaml" >/dev/null
  done
  kc rollout restart deployment/platform-apiserver -n default >/dev/null
  kc rollout status deployment/platform-apiserver -n default --timeout=300s
  echo "created mcp-policy-service-token (default, sentry-dam) and restarted the api-server"
fi

# Sentry's own Keycloak client, to read the realm's roles and who holds them (the company
# policy's role rules). Made with kcadm inside the Keycloak pod, from that pod's admin env.
if ! kc get secret sentry-policy-keycloak -n sentry-dam -o name | grep -q secret; then
  kc exec -n default deploy/platform-keycloak -- sh -c '
    set -e
    K=/opt/keycloak/bin/kcadm.sh
    $K config credentials --config /tmp/kcadm.config --server "http://localhost:${KC_HTTP_PORT:-8080}" --realm master \
      --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null
    id=$($K get clients --config /tmp/kcadm.config -r platform -q clientId=sentry-policy --fields id --format csv --noquotes)
    if [ -z "$id" ]; then
      id=$($K create clients --config /tmp/kcadm.config -r platform -s clientId=sentry-policy -s publicClient=false \
        -s serviceAccountsEnabled=true -s standardFlowEnabled=false -s directAccessGrantsEnabled=false -i)
    fi
    $K add-roles --config /tmp/kcadm.config -r platform --uusername service-account-sentry-policy \
      --cclientid realm-management --rolename view-users --rolename view-realm >/dev/null
    $K get "clients/$id/client-secret" --config /tmp/kcadm.config -r platform --fields value --format csv --noquotes
    rm -f /tmp/kcadm.config' | tail -1 | tr -d '\r\n' > "$TMP/kc"
  [ -s "$TMP/kc" ] || { echo "could not make Keycloak client sentry-policy" >&2; exit 1; }
  kc create secret generic sentry-policy-keycloak -n sentry-dam --from-file=secret="$TMP/kc" --dry-run=client -o yaml > "$TMP/s.yaml"
  kc apply -f - < "$TMP/s.yaml" >/dev/null
  echo "created Keycloak client sentry-policy and Secret sentry-policy-keycloak"
fi

if ! kc get secret dg-verdict-secret -n sentry-dam -o name | grep -q secret; then
  kc get secret dg-verdict-secret -n data-governance -o jsonpath='{.data.secret}' | base64 -d > "$TMP/dg"
  [ -s "$TMP/dg" ] || { echo "no dg-verdict-secret in data-governance: run deploy-dg.sh first" >&2; exit 1; }
  kc create secret generic dg-verdict-secret -n sentry-dam --from-file=secret="$TMP/dg" --dry-run=client -o yaml > "$TMP/s.yaml"
  kc apply -f - < "$TMP/s.yaml" >/dev/null
  echo "copied dg-verdict-secret into sentry-dam"
fi

if ! kc get secret aiac-dam-llm -n sentry-dam -o name | grep -q secret; then
  src="$(kc get secrets -n platform-agents -o name | grep -m1 'secret/platform-secret-connection-ibm-litellm-')"
  [ -n "$src" ] || { echo "no ibm-litellm connection Secret in platform-agents" >&2; exit 1; }
  kc get "$src" -n platform-agents -o jsonpath='{.data.value}' | base64 -d > "$TMP/key"
  kc create secret generic aiac-dam-llm -n sentry-dam \
    --from-file=LLM_API_KEY="$TMP/key" \
    --from-literal=LLM_MODEL="$LLM_MODEL" \
    --from-literal=LLM_BASE_URL="$LLM_BASE_URL" --dry-run=client -o yaml > "$TMP/secret.yaml"
  kc apply -f - < "$TMP/secret.yaml" >/dev/null
  echo "created Secret aiac-dam-llm from ${src#secret/}"
fi

SHA="$(cat "$HERE/policy-service/"{server.py,operations.py,dg_check.py} "$HERE/opa/mcp.rego" "$AIAC/demo/dam/"{controller.py,onboard_dam_agent.py,company_policy.py,github-mcp-tools.json,policy.md} | shasum -a 256 | cut -c1-12)"
kc patch deployment mcp-policy -n sentry-dam --type merge \
  -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"sentry-dam/server-sha\":\"$SHA\"}}}}}"
kc rollout status deployment/mcp-policy -n sentry-dam --timeout=180s
