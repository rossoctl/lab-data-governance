#!/usr/bin/env bash
# Create/refresh the `opa-policy` ConfigMap from the canonical in-repo policy
# files (issue #162). Keeping the ConfigMap OUT of the static manifests means
# the repo files are the single source of truth — this script is the only
# path from repo to cluster, so the deployed policy can never drift from a
# second copy embedded in YAML.
#
# Usage: deploy/create-opa-configmap.sh
# Idempotent (server-side apply of a generated manifest). After a policy
# change: re-run this script, then restart the opa pod
# (`kubectl -n data-governance rollout restart deploy/opa`) — OPA loads
# /policies once at startup.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
rego="$repo_root/data_governance/risk/rules/rego/data_governance.rego"
rules="$repo_root/data_governance/risk/rules/_policy_data/rules_source.json"

[ -f "$rego" ] || { echo "missing $rego" >&2; exit 1; }
[ -f "$rules" ] || { echo "missing $rules" >&2; exit 1; }

kubectl create configmap opa-policy \
  --namespace data-governance \
  --from-file="data_governance.rego=$rego" \
  --from-file="rules_source.json=$rules" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "opa-policy ConfigMap applied from:"
echo "  $rego"
echo "  $rules"
