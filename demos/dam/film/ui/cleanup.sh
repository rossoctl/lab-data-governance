#!/usr/bin/env bash
# After a take: reopen s-and-p-team/demo-public#1 and delete the comments the takes post on it.
set -euo pipefail
cd ~/development/dam
REPO=s-and-p-team/demo-public
state=$(mise exec -- gh issue view 1 -R $REPO --json state --jq .state)
[ "$state" = OPEN ] || mise exec -- gh issue reopen 1 -R $REPO
for id in $(mise exec -- gh api repos/$REPO/issues/1/comments --jq '.[] | select(.body | test("Could you share which browser|Sentry demo")) | .id'); do
  mise exec -- gh api -X DELETE repos/$REPO/issues/comments/$id >/dev/null && echo "deleted comment $id"
done
mise exec -- gh issue view 1 -R $REPO --json state,comments --jq '"demo-public#1: " + .state + ", " + (.comments|length|tostring) + " comments"'
