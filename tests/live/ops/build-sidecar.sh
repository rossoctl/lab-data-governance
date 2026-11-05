#!/usr/bin/env bash
# Build the lineage sidecar and proxy-init images from the cortex clone and
# load them into the kind node (the attach kit's RECIPE step 1), tagged by
# the cortex commit so the pins can name them.
cd "$(dirname "$0")" && . ./common.sh
# cortex plugins are opt-in by build tag: the Dockerfile refuses an untagged
# build. The envoy profile's tags come from the clone's own helper, run in a Go
# container (no Go toolchain is needed on the host).
TAGS=$(podman run --rm -v "$E2E_CORTEX_DIR/scripts/profile-tags:/src:ro" -w /src docker.io/library/golang:1.26-alpine go run . envoy)
[ -n "$TAGS" ] || { echo "no build tags from $E2E_CORTEX_DIR/scripts/profile-tags (profile envoy)" >&2; exit 1; }
say "build $SIDECAR_IMAGE"
podman build -q --build-arg GO_BUILD_TAGS="$TAGS" -f "$E2E_CORTEX_DIR/cmd/cortex-envoy/Dockerfile" -t "$SIDECAR_IMAGE" "$E2E_CORTEX_DIR" >/dev/null
say "build $PROXY_INIT_IMAGE"
podman build -q -f "$E2E_CORTEX_DIR/deploy/proxy-init/Dockerfile.init" -t "$PROXY_INIT_IMAGE" "$E2E_CORTEX_DIR/deploy/proxy-init/" >/dev/null
for i in "$SIDECAR_IMAGE" "$PROXY_INIT_IMAGE"; do say "load $i"; load "$i"; done
say "done: cortex $CORTEX_SHA"
