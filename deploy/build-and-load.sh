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
#   CONTAINER_TOOL   build/tag tool (default: podman). This repo is
#                    podman-only; setting this to anything else is rejected.

set -euo pipefail

# podman-only. Build/tag with podman and drive `kind` through its podman
# provider so nothing in this script ever talks to a docker daemon (mirrors
# the podman-only posture of agent-examples-snp/deploy.sh).
export KIND_EXPERIMENTAL_PROVIDER=podman

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

# podman-only by design. The `docker` CLI on this setup is just a thin client
# pointed at the podman engine, and its default buildx `docker-container`
# builder silently leaves the build in the cache (not the image store) unless
# handed --load -- which makes the `kind load` below a no-op that ships stale
# code. Using podman directly sidesteps that whole class of silent-no-op
# deploys, and there is deliberately no docker fallback.
CONTAINER_TOOL="${CONTAINER_TOOL:-podman}"
if [[ "${CONTAINER_TOOL}" != "podman" ]]; then
    echo "error: this repo is podman-only; refusing CONTAINER_TOOL=${CONTAINER_TOOL}" >&2
    exit 1
fi
if ! command -v podman >/dev/null 2>&1; then
    echo "error: 'podman' is not on PATH; install it from https://podman.io" >&2
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

# Load via `podman save` + `kind load image-archive`, NOT `kind load
# docker-image`. Under the podman provider, kind (v0.31) still shells out to
# `docker image inspect` to resolve the image id for `load docker-image`, so
# that path breaks on a docker-free host. Saving the tar with podman and
# importing the archive keeps the entire load on podman. Both docker.io-
# prefixed tags go into ONE archive — that's how containerd resolves the bare
# references in deploy/k8s/*.yaml, so the node gets a hit at pod-create time.
ARCHIVE="$(mktemp)"
trap 'rm -f "${ARCHIVE}"' EXIT
echo ">> Saving ${RECEIVER_DOCKERIO} + ${UI_DOCKERIO} to an archive"
"${CONTAINER_TOOL}" save -o "${ARCHIVE}" "${RECEIVER_DOCKERIO}" "${UI_DOCKERIO}"

echo ">> Loading the archive into Kind cluster '${KIND_CLUSTER}'"
kind load image-archive "${ARCHIVE}" --name "${KIND_CLUSTER}"

cat <<EOF

Done. Both tags are present in the '${KIND_CLUSTER}' Kind cluster:
  - ${RECEIVER_IMAGE}
  - ${UI_IMAGE}

Next:
  kubectl apply -f ${REPO_ROOT#${PWD}/}/deploy/k8s/

EOF
