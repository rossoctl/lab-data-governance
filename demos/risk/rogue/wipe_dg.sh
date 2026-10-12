#!/usr/bin/env bash
# wipe_dg.sh [<kind-cluster>=rossoctl] — empty every DG table (spans, interactions, entities, payloads,
# classifications, risk records, alerts, cursors). The five processors are scaled to 0 first,
# every table except alembic_version is truncated with RESTART IDENTITY, then the processors
# come back at cursor 0. The schema (alembic head) is untouched. ~1 min. Proven 2026-10-12.
# Refuses unless the current KUBECONFIG points at the named kind cluster (default rossoctl).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); NS=data-governance; CLUSTER=${1:-rossoctl}
P="data-governance-classification data-governance-data-lineage data-governance-interactions data-governance-leg-ready data-governance-risk-trace-trigger"
kubectl get nodes -o name | grep -q "^node/$CLUSTER-control-plane$" || { echo "current KUBECONFIG is not kind cluster $CLUSTER (nodes: $(kubectl get nodes -o name | tr '\n' ' ')); pass its name as the argument to wipe it"; exit 1; }
up() { for d in $P; do kubectl -n $NS scale deploy/$d --replicas=1 >/dev/null; done; }
trap up EXIT                         # whatever happens below, the processors come back
for d in $P; do kubectl -n $NS scale deploy/$d --replicas=0 >/dev/null; done
until [ "$(kubectl -n $NS get pods --no-headers | grep -c -E 'classification|data-lineage|interactions|leg-ready|risk-trace-trigger')" = 0 ]; do sleep 2; done
echo ">> processors down"
"$HERE/dgq.sh" "TRUNCATE blocked_span_counts, rejected_spans, entity_spans, interactions, processor_state, spans, interaction_payloads, payload_classifications, interaction_spans, interaction_legs, lineage_metadata, lineage_trace_status, alerts, interaction_policy_decisions, trace_risk_records, interaction_risk_records, entities RESTART IDENTITY CASCADE"
for d in $P; do kubectl -n $NS scale deploy/$d --replicas=1 >/dev/null; done; trap - EXIT
for d in $P; do kubectl -n $NS rollout status deploy/$d --timeout=5m >/dev/null; done
echo ">> processors up; rows left: $("$HERE/dgq.sh" "select coalesce(sum(n_live_tup),0) from pg_stat_user_tables where relname<>'alembic_version'")"
rm -f "$HERE/runs/KEEP"; echo ">> runs/KEEP removed"
