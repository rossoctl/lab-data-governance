#!/bin/sh
# dgq.sh "<sql>" — run one SQL string against the DG database, inside its postgres pod (used by the other tools)
POD=$(kubectl get pods -A | awk '/data-governance-postgres/{print $1" "$2; exit}')
set -- "$1" $POD
kubectl -n "$2" exec "$3" -- sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d "${POSTGRES_DB:-$POSTGRES_USER}" -P pager=off -F "|" -Atc "$0"' "$1"
