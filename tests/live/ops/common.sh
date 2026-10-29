# shellcheck shell=bash
# shellcheck disable=SC2034  # the variables are read by the scripts that source this file
# Shared by the scripts in this directory. Sourced, not run.
# Inputs (the same variables the tests read):
#   E2E_AGENT_EXAMPLES_SNP  clone of agent-examples-snp at the commit to pin
#   E2E_CORTEX_DIR          clone of cortex at the commit to pin (sidecar, proxy-init, attach kit)
#   E2E_KUBE_CONTEXT        default kind-rossoctl;  E2E_KIND_CLUSTER  default rossoctl
# Written for podman + kind, the combination the tier is run on.
set -euo pipefail
for tool in podman kind kubectl git; do
  command -v "$tool" >/dev/null || { echo "$tool is not on PATH; the tier needs podman + kind (a docker host is not supported, docs/LIVE-E2E.md)" >&2; exit 1; }
done
: "${E2E_AGENT_EXAMPLES_SNP:?set E2E_AGENT_EXAMPLES_SNP to the agent-examples-snp clone}"
: "${E2E_CORTEX_DIR:?set E2E_CORTEX_DIR to the cortex clone}"
KIND=${E2E_KIND_CLUSTER:-rossoctl}
CTX=${E2E_KUBE_CONTEXT:-kind-$KIND}
SNP=$E2E_AGENT_EXAMPLES_SNP
KIT=$E2E_CORTEX_DIR/deploy/lineage-attach
SNP_SHA=$(git -C "$SNP" rev-parse --short=7 HEAD)
CORTEX_SHA=$(git -C "$E2E_CORTEX_DIR" rev-parse --short=7 HEAD)
SIDECAR_IMAGE=docker.io/library/authbridge-envoy:$CORTEX_SHA
PROXY_INIT_IMAGE=docker.io/library/proxy-init:$CORTEX_SHA
export KIND_EXPERIMENTAL_PROVIDER=podman CONTAINER_TOOL=podman

k() { kubectl --context "$CTX" "$@"; }
say() { echo ">> $*"; }

# app <name>: the namespace and image names of one of the two apps.
app() {
  APP=${1:?usage: $0 travel_advisor|lineage_lab}
  case $APP in
    travel_advisor) NS=travel-advisor; REPO=agent-examples-snp ;;
    lineage_lab)    NS=lineage-lab;    REPO=agent-examples-snp-lab ;;
    *) echo "unknown app '$APP' (travel_advisor | lineage_lab)" >&2; exit 2 ;;
  esac
  APP_IMAGE=docker.io/library/$REPO:$SNP_SHA
  SHIM_IMAGE=docker.io/library/$REPO-otel:$SNP_SHA
}

# load <image>: into the kind node's image store, through an archive
# (`kind load docker-image` is unreliable under podman).
load() {
  local tar; tar=$(mktemp -t e2e-image.XXXXXX)
  podman save "$1" -o "$tar"
  kind load image-archive "$tar" --name "$KIND" >/dev/null
  rm -f "$tar"
}
