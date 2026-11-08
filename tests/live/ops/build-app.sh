#!/usr/bin/env bash
# Build one app's image from the agent-examples-snp clone, check its
# dependency closure, load it into kind, and bake the propagation shim onto it
# with the attach kit of the cortex clone. Both images are tagged by the app
# commit.      usage: build-app.sh travel_advisor|lineage_lab
cd "$(dirname "$0")" && . ./common.sh
app "${1:-}"
B=$(mktemp -d -t e2e-build.XXXXXX); trap 'rm -rf "$B"' EXIT
mkdir "$B/app"
for p in __init__.py stores.py demo.py agents tools; do cp -R "$SNP/apps/$APP/$p" "$B/app/"; done
cp -R "$SNP/apps/$APP/bootstrap" "$B/bootstrap"
[ "$APP" = travel_advisor ] && cp "$SNP/apps/$APP/scripts/psp_mock.py" "$B/bootstrap/psp_mock.py"
cp "$SNP/dockerfiles/Dockerfile" "$B/Dockerfile"
cp -R "$SNP/rossoctl" "$SNP/observe" "$SNP/ledger" "$SNP/entrypoint.py" "$SNP/constraints.txt" "$B/"
say "build $APP_IMAGE"
podman build -q --build-arg EXTRA_RUNTIMES=openai_agents,langgraph,crewai,google_sdk \
  --build-arg INSTRUMENTATION=off -t "$APP_IMAGE" "$B" >/dev/null
say "pip check"
podman run --rm --entrypoint sh "$APP_IMAGE" -c 'pip check'
say "load $APP_IMAGE"; load "$APP_IMAGE"
say "bake $SHIM_IMAGE"
bake_log=$B/bake.log
if (cd "$KIT" && KIND_CLUSTER_NAME=$KIND ./build-otel-shim.sh "$APP_IMAGE" "$SHIM_IMAGE") >"$bake_log" 2>&1; then
  grep -E '^>> (detected app OTel|built|loaded)|^NOTE: the base image' "$bake_log" || true
else
  echo "the bake failed; the kit's last lines:" >&2; tail -n 15 "$bake_log" >&2
  echo "fix: read the kit's message above (REFUSING, ATTESTATION FAILED or a resolver conflict means the cortex clone's kit predates rossoctl/cortex#1311: step 0's table); to see all of it run $KIT/build-otel-shim.sh $APP_IMAGE $SHIM_IMAGE" >&2
  exit 1
fi
podman image exists "$SHIM_IMAGE" || { echo "the bake reported success but $SHIM_IMAGE is not in the local store: run $KIT/build-otel-shim.sh $APP_IMAGE $SHIM_IMAGE and read its output" >&2; exit 1; }
say "done: $APP at $SNP_SHA"
