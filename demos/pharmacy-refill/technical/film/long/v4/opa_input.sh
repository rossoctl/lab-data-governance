#!/usr/bin/env bash
# opa_input.sh <label> <interaction_id> … — runs opa_input.py for each (label, interaction) pair against the take's cluster
# (film.env): port-forwards its Sentry Postgres and OPA, copies leg-ready's RISK_* settings, and uses the pinned Sentry
# checkout ($SRC/lab-data-governance) with its own Python env (made once with uv: uv sync --frozen --no-dev).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd); PKG=$(cd "$here/../../.." && pwd)
set -a; . "${FILM_ENV:-$PKG/film.env}"; set +a
export KUBECONFIG; ns=data-governance; k() { kubectl --context "kind-$CLUSTER" "$@"; }
LABDG=${SRC:-$HOME/sentry-film/src}/lab-data-governance
take=${FILM_TAKE:-$PKG/film/takes/v4.2}; case "$take" in /*) ;; *) take=$PKG/$take ;; esac; export FILM_TAKE_DIR=$take
[ -x "$LABDG/.venv/bin/python" ] || { echo ">> Sentry's Python env, once (uv sync in $LABDG)"; (cd "$LABDG" && uv sync --frozen --no-dev --python 3.11 >/dev/null); }
p1=$((20000 + RANDOM % 20000)); p2=$((p1 + 1))
kubectl --context "kind-$CLUSTER" -n $ns port-forward pod/data-governance-postgres-0 $p1:5432 >/dev/null 2>&1 & pf1=$!   # kubectl itself, so the trap stops it
kubectl --context "kind-$CLUSTER" -n $ns port-forward svc/opa $p2:8181 >/dev/null 2>&1 & pf2=$!
trap 'kill $pf1 $pf2 2>/dev/null' EXIT
sleep 3
envof() { k -n $ns get deploy data-governance-leg-ready -o jsonpath="{.spec.template.spec.containers[0].env[?(@.name==\"$1\")].value}"; }
user=$(k -n $ns exec data-governance-postgres-0 -- sh -c 'echo $POSTGRES_USER')
pass=$(k -n $ns exec data-governance-postgres-0 -- sh -c 'echo $POSTGRES_PASSWORD')
export DG_DSN="postgresql://$user:$pass@localhost:$p1/data_governance" OPA_URL="http://localhost:$p2"
export RISK_INTERNAL_URL_WHITELIST_PATTERNS="$(envof RISK_INTERNAL_URL_WHITELIST_PATTERNS)"
export RISK_INHERITED_LABELS="$(envof RISK_INHERITED_LABELS)"
export RISK_INHERITED_LABELS_CROSS_CONVERSATIONS="$(envof RISK_INHERITED_LABELS_CROSS_CONVERSATIONS)"
while [ $# -ge 2 ]; do
  (cd "$LABDG" && PYTHONPATH=. .venv/bin/python "$here/opa_input.py" "$1" "$2")
  shift 2
done
