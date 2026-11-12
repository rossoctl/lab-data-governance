#!/usr/bin/env bash
# gate-image.sh <cortex-clone> — RUNBOOK.md § 0: build the sidecar image that carries the Sentry gate, load it into
# the kind node, and make the platform inject it into every sidecar created from now on.
#
# The gate lives on the cortex fork's `sentry` branch (https://github.com/s-and-p-team/cortex), in the `full` plugin
# profile (the platform's `envoy` profile has no gate). The image is cmd/cortex/Dockerfile built with that profile's
# build tags, which the clone's own scripts/profile-tags prints (run in a Go container: the host needs no Go).
# The platform reads images.authbridge from rossoctl-system/rossoctl-platform-config when a pod is created, so pods
# that exist keep their old sidecar until they are recreated (§ 2's instrument does that).
#
# Prints the previous image first: that line is the undo (`gate-image.sh --set <it>`).
set -euo pipefail
source "$(dirname "$0")/env.sh"
CM=rossoctl-platform-config
current() { kubectl -n rossoctl-system get cm "$CM" -o jsonpath='{.data.config\.yaml}' | sed -n 's/^  authbridge: //p'; }
set_image() {
  local cfg; cfg=$(kubectl -n rossoctl-system get cm "$CM" -o jsonpath='{.data.config\.yaml}')
  grep -q '^  authbridge: ' <<<"$cfg" || { echo "STOP: no 'images.authbridge' line in $CM"; exit 9; }
  cfg=$(sed "s|^  authbridge: .*|  authbridge: $1|" <<<"$cfg")
  kubectl -n rossoctl-system patch cm "$CM" --type merge -p "$(python3 -c 'import json,sys; print(json.dumps({"data": {"config.yaml": sys.stdin.read()}}))' <<<"$cfg")" >/dev/null
  echo "images.authbridge: $(current)"
}
echo "images.authbridge before: $(current)"
if [ "${1:-}" = --set ]; then set_image "${2:?usage: gate-image.sh --set <image>}"; exit 0; fi
CORTEX=$(cd "${1:?usage: gate-image.sh <clone of s-and-p-team/cortex on branch sentry> | --set <image>}" && pwd)
[ -d "$CORTEX/core/plugins/sentrygate" ] || { echo "STOP: $CORTEX has no core/plugins/sentrygate; check out the fork's sentry branch"; exit 9; }
SHA=$(git -C "$CORTEX" rev-parse --short=8 HEAD)
IMAGE=localhost/authbridge:gate-$SHA
TAGS=$(podman run --rm -v "$CORTEX/scripts/profile-tags:/src:ro" -w /src docker.io/library/golang:1.26-alpine go run . full)
[ -n "$TAGS" ] || { echo "STOP: no build tags from $CORTEX/scripts/profile-tags (profile full)"; exit 9; }
grep -q sentrygate <<<"$TAGS" || { echo "STOP: the full profile's tags do not name the gate: $TAGS"; exit 9; }
echo ">> build $IMAGE (cortex $SHA)"
podman build -q -f "$CORTEX/cmd/cortex/Dockerfile" --build-arg GO_BUILD_TAGS="$TAGS" -t "$IMAGE" "$CORTEX" >/dev/null
echo ">> load into kind cluster $KIND_CLUSTER"
tar=$(mktemp -t gate-image.XXXXXX); podman save "$IMAGE" -o "$tar"
KIND_EXPERIMENTAL_PROVIDER=podman kind load image-archive "$tar" --name "$KIND_CLUSTER" >/dev/null; rm -f "$tar"
set_image "$IMAGE"
