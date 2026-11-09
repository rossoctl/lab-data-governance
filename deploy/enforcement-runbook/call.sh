#!/usr/bin/env bash
# call.sh <label> <workload> <clean|pii|big|plain|connect> <host:port> — one request from inside the workload's app
# container through its sidecar; prints one summary line and appends the full evidence to $RUN/rows.jsonl.
source "$(dirname "$0")/env.sh" || exit 9
label=$1 w=$2 kind=$3 host=$4
c=$(kubectl -n "$NS" get deploy "$w" -o jsonpath='{.spec.template.spec.containers[0].name}')
kubectl -n "$NS" exec -i "deploy/$w" -c "$c" -- sh -c 'cat > /tmp/call.py && python3 /tmp/call.py "$@"' _ "$kind" "$host" < "$HERE/call.py" 2>&1 \
  | tail -1 | python3 "$HERE/summarize.py" "$label" "$w" "$RUN/rows.jsonl"
