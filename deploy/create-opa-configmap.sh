#!/usr/bin/env bash
# Create/refresh the `opa-policy` ConfigMap from the canonical in-repo policy
# source (issues #162/#173). The served Rego is COMPILED from the shipped rule
# catalog (data_governance/risk/rules/_policy_data/rules_source.json) by the
# in-repo compiler (data_governance.risk.rules.compile.compile_bundle) — the
# JSON catalog is the single source of truth, and this script is the only
# path from repo to cluster, so the deployed policy can never drift from a
# hand-edited Rego copy.
#
# Usage: deploy/create-opa-configmap.sh [catalog.json]
#   With no argument the shipped catalog is compiled and shipped (production).
#   With a path, THAT catalog is compiled — validated against the same schema,
#   by the same compiler — and shipped in its place, and the ConfigMap's
#   `rules_source.json` key carries that file, so the deployed policy and the
#   deployed catalog copy always come from one document. This is how a
#   staging or test catalog (tests/live/catalog_e2e.json) reaches OPA: the
#   production route, not a side door. Re-run with no argument to restore.
# Requires a Python able to import data_governance (repo venv or
# PYTHON=... override). Idempotent (server-side apply of a generated
# manifest). After a catalog change: re-run this script, then restart the opa
# pod (`kubectl -n data-governance rollout restart deploy/opa`) — OPA loads
# /policies once at startup.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
shipped="$repo_root/data_governance/risk/rules/_policy_data/rules_source.json"
rules="${1:-$shipped}"
case "$rules" in /*) ;; *) rules="$PWD/$rules" ;; esac
python="${PYTHON:-$repo_root/.venv/bin/python}"
[ -x "$python" ] || python="python3"

[ -f "$rules" ] || { echo "missing $rules" >&2; exit 1; }

compiled="$(mktemp)"
trap 'rm -f "$compiled"' EXIT
(cd "$repo_root" && "$python" -c \
  "import sys; from data_governance.risk.rules import compile; print(compile.compile_bundle(sys.argv[1]))" \
  "$rules" > "$compiled")

kubectl create configmap opa-policy \
  --namespace data-governance \
  --from-file="data_governance.rego=$compiled" \
  --from-file="rules_source.json=$rules" \
  --dry-run=client -o yaml | kubectl apply -f -

if [ "$rules" = "$shipped" ]; then which="shipped catalog"; else which="CATALOG OVERRIDE"; fi
echo "opa-policy ConfigMap applied ($which):"
echo "  data_governance.rego <- compile_bundle($rules)"
echo "  rules_source.json    <- $rules"
