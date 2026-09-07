#!/usr/bin/env bash
# Create/refresh the `opa-policy` ConfigMap from the canonical in-repo rule
# catalog (issue #162). The Rego OPA serves is COMPILED from
# data_governance/risk/rules/_policy_data/rules_source.json by the #173
# compiler (data_governance/risk/rules/rego.py) — there is no hand-written
# policy file to keep in sync. Keeping the ConfigMap OUT of the static
# manifests means the catalog is the single source of truth: this script is
# the only path from repo to cluster, so the deployed policy can never drift
# from a second copy embedded in YAML.
#
# Usage: deploy/create-opa-configmap.sh
# Requires the repo's Python environment (`uv run`), since the compiler is
# Python. Idempotent (server-side apply of a generated manifest). After a
# catalog change: re-run this script, then restart the opa pod
# (`kubectl -n data-governance rollout restart deploy/opa`) — OPA loads
# /policies once at startup.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
rules="$repo_root/data_governance/risk/rules/_policy_data/rules_source.json"
[ -f "$rules" ] || { echo "missing $rules" >&2; exit 1; }

bundle="$(mktemp -t opa-bundle.XXXXXX)"
trap 'rm -f "$bundle"' EXIT
(cd "$repo_root" && uv run python -m data_governance.risk.rules.compile) > "$bundle"
[ -s "$bundle" ] || { echo "compiler produced an empty bundle" >&2; exit 1; }

kubectl create configmap opa-policy \
  --namespace data-governance \
  --from-file="data_governance.rego=$bundle" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "opa-policy ConfigMap applied: data_governance.rego compiled from"
echo "  $rules"
