#!/usr/bin/env bash
# Build the data-governance container image and load it into the local Kind
# cluster (issue #38).
#
# The manifests in deploy/k8s/ reference data-governance/receiver:latest and
# data-governance/ui:latest with imagePullPolicy: IfNotPresent. This script
# builds ONE image from the repo-root Containerfile, tags it under both names,
# and `kind load`s both tags into the cluster named `kagenti` (the upstream
# Kagenti convention).
#
# It ALSO builds the separate P-classification image
# data-governance/classification:latest (issue #79 / ADR-0022) from
# Containerfile.classification — a distinct image because its torch + ~500 MB
# model-weight closure diverges too heavily to share the receiver/UI image.
# Before that build it materializes the git-LFS model weights (ADR-0023) so the
# real weights are baked in, not the ~134-byte LFS pointer.
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
# The SEPARATE P-classification image (issue #79 / ADR-0022): its torch + ~500 MB
# model-weight closure diverges too heavily to fold into the shared image, so it
# is built from its own Containerfile.classification with the weights baked in.
CLASSIFICATION_IMAGE="${IMAGE_REPO}/classification:${IMAGE_TAG}"

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
CLASSIFICATION_DOCKERIO="docker.io/${CLASSIFICATION_IMAGE}"

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

# Refresh uv.lock to match pyproject.toml BEFORE either build. Both Containerfiles
# `COPY ... uv.lock` and run `uv sync --frozen`, which ABORTS if the lockfile is
# missing or out of sync with pyproject.toml — and uv.lock is .gitignored, so a
# checkout carries whatever (possibly stale, possibly absent) lock the host last
# generated. This matters acutely for the classification image: issue #79 added
# torch/transformers to pyproject's `classification` extra, so a pre-#79 lock lacks
# them and `uv sync --frozen --extra classification` would fail the build. `uv lock`
# is idempotent — a no-op when already in sync. If uv is not installed, we can only
# proceed when a lockfile already exists (the `--frozen` builds need one either way).
if command -v uv >/dev/null 2>&1; then
    echo ">> Refreshing uv.lock to match pyproject.toml (uv lock)"
    uv lock --project "${REPO_ROOT}"
elif [[ ! -f "${REPO_ROOT}/uv.lock" ]]; then
    echo "error: uv.lock is absent and 'uv' is not installed to generate it; the " \
         "'uv sync --frozen' build step requires a lockfile. Install uv " \
         "(https://docs.astral.sh/uv/) or provide uv.lock." >&2
    exit 1
fi

# NOTE: classification image build disabled — the git-LFS weight materialization
# below is only needed for that image, so it is commented out too.
# # Materialize the git-LFS model weights BEFORE building the classification image
# # (ADR-0023). classification/model/<generation>/model.safetensors is a ~500 MB
# # git-LFS artifact; a fresh checkout holds only a ~134-byte pointer. Without this
# # step the classification build would bake the pointer, not the weights, and the
# # model load would fail at startup. `git lfs pull` is idempotent — a no-op once
# # the object is present.
# if ! command -v git-lfs >/dev/null 2>&1 && ! git lfs version >/dev/null 2>&1; then
#     echo "error: git-lfs is required to materialize the ~500 MB model weights " \
#          "for the classification image; install it from https://git-lfs.com" >&2
#     exit 1
# fi
# echo ">> Materializing git-LFS model weights (classification/model/)"
# git -C "${REPO_ROOT}" lfs install --local
# git -C "${REPO_ROOT}" lfs pull --include="classification/model/**"

# Compute the UI version stamp HERE on the host (short SHA + commit date): the
# .git tree is not in the image build context, so vite.config.ts cannot derive
# it inside the container. Passed in as --build-arg APP_VERSION; the ui-builder
# stage exports it to `npm run build`, which bakes it into the masthead. Falls
# back to 'dev' if git fails. Changes every commit → fresh each deploy.
APP_VERSION="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo dev)"
if [[ "${APP_VERSION}" != "dev" ]]; then
    APP_VERSION="${APP_VERSION} ($(git -C "${REPO_ROOT}" show -s --format=%cd --date=short HEAD 2>/dev/null))"
fi
echo ">> UI version stamp: ${APP_VERSION}"

echo ">> Building ${RECEIVER_IMAGE} from ${REPO_ROOT}/Containerfile"
"${CONTAINER_TOOL}" build \
    -f "${REPO_ROOT}/Containerfile" \
    --build-arg "APP_VERSION=${APP_VERSION}" \
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

# The SEPARATE classification image (issue #79 / ADR-0022): built from
# Containerfile.classification with the classification extra (torch/transformers)
# and the baked-in model weights. Built and loaded as its own image because its
# dependency closure diverges too heavily to share the receiver/UI image.
#
# DISABLED: the torch + ~500 MB model-weight build is slow and not needed for a
# UI/receiver-only deploy. Re-enable this block (and the git-LFS step above) when
# the classification image itself needs rebuilding.
# echo ">> Building ${CLASSIFICATION_IMAGE} from ${REPO_ROOT}/Containerfile.classification"
# "${CONTAINER_TOOL}" build \
#     -f "${REPO_ROOT}/Containerfile.classification" \
#     -t "${CLASSIFICATION_IMAGE}" \
#     "${REPO_ROOT}"
#
# echo ">> Tagging ${CLASSIFICATION_IMAGE} as its docker.io/* alias"
# "${CONTAINER_TOOL}" tag "${CLASSIFICATION_IMAGE}" "${CLASSIFICATION_DOCKERIO}"
#
# echo ">> Loading ${CLASSIFICATION_DOCKERIO} into Kind cluster '${KIND_CLUSTER}'"
# kind load docker-image "${CLASSIFICATION_DOCKERIO}" --name "${KIND_CLUSTER}"

cat <<EOF

Done. These tags are present in the '${KIND_CLUSTER}' Kind cluster:
  - ${RECEIVER_IMAGE}
  - ${UI_IMAGE}
  (classification image build is currently disabled in this script)

Next:
  kubectl apply -f ${REPO_ROOT#${PWD}/}/deploy/k8s/

EOF
