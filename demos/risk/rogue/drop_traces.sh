#!/bin/sh
# drop_traces.sh [<trace_id>[,<trace_id>...]] — delete every DG row of the traces NOT in the keep list
# (the argument, or the file runs/KEEP next to this script when no argument is given).
# Trace-scoped deletes only (entities, payloads and classifications are content-keyed and stay).
# Used to drop the one-interaction startup traces that every pod roll mints (MCP initialize / tools/list
# without a traceparent → contract v1.6 restarts a trace per exchange).
HERE=$(cd "$(dirname "$0")" && pwd)
LIST=${1:-$(cat "$HERE/runs/KEEP" 2>/dev/null)}      # no argument: read runs/KEEP (one line, comma-separated ids)
LIST=$(echo "$LIST" | tr -d ' ')
echo "$LIST" | grep -qE '^[0-9a-f]{32}(,[0-9a-f]{32})*$' || { echo "usage: drop_traces.sh <id>[,<id>...]   (32-hex trace ids, comma-separated; or write them to runs/KEEP)"; exit 1; }
echo "keeping $(echo "$LIST" | tr ',' '\n' | wc -l | tr -d ' ') trace(s): $LIST" >&2
KEEP=$(echo "$LIST" | sed "s/[^,]*/'&'/g")
"$HERE/dgq.sh" "
with gone as (select trace_id from spans where trace_id not in ($KEEP) union select trace_id from interactions where trace_id not in ($KEEP)),
     gi as (select id from interactions where trace_id in (select trace_id from gone))
delete from interaction_legs where interaction_id in (select id from gi);
delete from lineage_metadata where interaction_id in (select id from interactions where trace_id not in ($KEEP));
delete from interaction_policy_decisions where interaction_id in (select id from interactions where trace_id not in ($KEEP));
delete from interaction_risk_records where trace_id not in ($KEEP);
delete from trace_risk_records where trace_id not in ($KEEP);
delete from alerts where trace_id not in ($KEEP);
delete from interaction_spans where trace_id not in ($KEEP);
delete from entity_spans where trace_id not in ($KEEP);
delete from lineage_trace_status where trace_id not in ($KEEP);
delete from interactions where trace_id not in ($KEEP);
delete from spans where trace_id not in ($KEEP);
delete from rejected_spans where trace_id not in ($KEEP);
select 'traces left', count(distinct trace_id) from spans;"
