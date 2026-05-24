#!/usr/bin/env bash
# Build the data-governance container image and load it into the local Kind
# cluster (issue #38).
#
# The v1 manifests in deploy/k8s/ reference data-governance/receiver:latest
# and data-governance/ui:latest with imagePullPolicy: IfNotPresent. This
# script builds ONE image from the repo-root Containerfile, tags it under
# both names, and `kind load`s both tags into the cluster named `kagenti`
# (the upstream Kagenti convention).
#
# Run from anywhere: paths are resolved relative to this file's location.
#
# Usage:
#   ./deploy/build-and-load.sh           # uses defaults
#
# Environment overrides (mostly for CI / non-default setups):
#   KIND_CLUSTER     Kind cluster name to load into (default: kagenti)
#   IMAGE_REPO       Image repo prefix (default: data-governance)
#   IMAGE_TAG        Image tag (default: latest)
#   CONTAINER_TOOL   docker | podman (default: auto-detect, prefers docker)

set -euo pipefail

KIND_CLUSTER="${KIND_CLUSTER:-kagenti}"
IMAGE_REPO="${IMAGE_REPO:-data-governance}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

RECEIVER_IMAGE="${IMAGE_REPO}/receiver:${IMAGE_TAG}"
UI_IMAGE="${IMAGE_REPO}/ui:${IMAGE_TAG}"

# When containerd (the runtime under Kind) resolves a bare image reference
# like ``data-governance/receiver:latest``, it expands it to
# ``docker.io/data-governance/receiver:latest``. Some `kind load`
# / podman combinations end up storing the image under a ``localhost/``
# prefix instead, and kubelet then tries to actually pull from
# docker.io and fails with ErrImagePull. Loading both names below
# ensures the bare reference in the manifests resolves regardless of
# how the local toolchain ended up storing the image.
RECEIVER_DOCKERIO="docker.io/${RECEIVER_IMAGE}"
UI_DOCKERIO="docker.io/${UI_IMAGE}"

# Resolve the repo root from this script's location so the build context is
# stable regardless of caller cwd.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

# Pick a container tool: explicit override wins, then docker, then podman.
# Both the upstream Kagenti `kind` cluster and this repo are tested against
# both runtimes; on this dev machine `docker` is a podman shim.
if [[ -n "${CONTAINER_TOOL:-}" ]]; then
    : # caller-provided
elif command -v docker >/dev/null 2>&1; then
    CONTAINER_TOOL=docker
elif command -v podman >/dev/null 2>&1; then
    CONTAINER_TOOL=podman
else
    echo "error: no container tool found; install docker or podman" >&2
    exit 1
fi

if ! command -v kind >/dev/null 2>&1; then
    echo "error: 'kind' is not on PATH; install it from https://kind.sigs.k8s.io" >&2
    exit 1
fi

echo ">> Building ${RECEIVER_IMAGE} from ${REPO_ROOT}/Containerfile"
"${CONTAINER_TOOL}" build \
    -f "${REPO_ROOT}/Containerfile" \
    -t "${RECEIVER_IMAGE}" \
    "${REPO_ROOT}"

echo ">> Tagging ${RECEIVER_IMAGE} as ${UI_IMAGE} and as docker.io/* aliases"
"${CONTAINER_TOOL}" tag "${RECEIVER_IMAGE}" "${UI_IMAGE}"
"${CONTAINER_TOOL}" tag "${RECEIVER_IMAGE}" "${RECEIVER_DOCKERIO}"
"${CONTAINER_TOOL}" tag "${RECEIVER_IMAGE}" "${UI_DOCKERIO}"

# Load the docker.io-prefixed names: that's how containerd will resolve the
# bare references in deploy/k8s/*.yaml, so loading under those names
# guarantees a hit at pod-create time.
echo ">> Loading ${RECEIVER_DOCKERIO} into Kind cluster '${KIND_CLUSTER}'"
kind load docker-image "${RECEIVER_DOCKERIO}" --name "${KIND_CLUSTER}"

echo ">> Loading ${UI_DOCKERIO} into Kind cluster '${KIND_CLUSTER}'"
kind load docker-image "${UI_DOCKERIO}" --name "${KIND_CLUSTER}"

cat <<EOF

Done. Both tags are present in the '${KIND_CLUSTER}' Kind cluster:
  - ${RECEIVER_IMAGE}
  - ${UI_IMAGE}

Next:
  kubectl apply -f ${REPO_ROOT#${PWD}/}/deploy/k8s/

EOF
