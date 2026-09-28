#!/usr/bin/env bash
# dg.sh — data-governance cluster management CLI.
#
# Give the data-governance component a first-class install / uninstall story on
# a rossoctl cluster, and a way to activate lineage telemetry on the agents and
# tools in a namespace so their traffic surfaces in the data-governance UI.
#
# Authoritative design: docs/cli.md
# Decision records:     docs/adr/0031-non-reversible-namespace-lineage-activation.md
#                       docs/adr/0033-dg-sh-vendors-lineage-attach-proxy-default-one-trace.md
#
# ---------------------------------------------------------------------------
# `namespace instrument` drives the lineage-attach kit VENDORED into this repo
# at deploy/lineage-attach/ (ADR-0033 decision 1, supersedes ADR-0032). No
# cortex checkout is required for any verb; the extension stands alone.
# ---------------------------------------------------------------------------
#
# Grammar:
#
#   dg.sh                                                 # → component status
#   dg.sh component  [install|uninstall|status]
#   dg.sh namespaces [list]
#   dg.sh namespace  <ns> [instrument|status] [<entity>]
#
# Fail-loud posture: every preflight and every unresolved input is a loud,
# non-zero exit — never a silent no-op.

set -euo pipefail

PROG="$(basename -- "${BASH_SOURCE[0]}")"

# Directory holding this script and its sibling deploy/*.sh helpers. The
# component verb reuses build-and-load.sh + patch-rossoctl-collector.sh from
# here rather than reimplementing them. Tests override the two script paths via
# DG_BUILD_AND_LOAD / DG_PATCH_COLLECTOR so they can drive `component` without a
# real image build or a live collector.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BUILD_AND_LOAD="${DG_BUILD_AND_LOAD:-${SCRIPT_DIR}/build-and-load.sh}"
PATCH_COLLECTOR="${DG_PATCH_COLLECTOR:-${SCRIPT_DIR}/patch-rossoctl-collector.sh}"
K8S_DIR="${DG_K8S_DIR:-${SCRIPT_DIR}/k8s}"
# The lineage-attach kit is VENDORED into this repo (deploy/lineage-attach/) so
# `instrument` needs no cortex checkout (ADR-0033 decision 1, supersedes 0032's
# external --cortex-local-path arrangement). dg.sh shells out to its own local
# copies. Tests override the dir via DG_LINEAGE_ATTACH_DIR to point at a fake
# stub kit — the same override pattern as DG_BUILD_AND_LOAD / DG_K8S_DIR above.
KIT_DIR="${DG_LINEAGE_ATTACH_DIR:-${SCRIPT_DIR}/lineage-attach}"

# The data-governance component's own resources.
DG_NAMESPACE="data-governance"
# The load-bearing deployments to rollout-restart on install (manifests pin
# :latest with imagePullPolicy: IfNotPresent, so `apply` alone will NOT cycle
# pods onto a freshly-loaded image — the restart is the documented, easy-to-
# forget step; root CLAUDE.md § 1).
DG_ROLLOUT_DEPLOYMENTS=(
    data-governance-receiver
    data-governance-ui
    data-governance-interactions
)
# All component workload deployments (the superset the --keep-data teardown
# deletes individually while preserving the Postgres PVC).
DG_ALL_DEPLOYMENTS=(
    data-governance-receiver
    data-governance-ui
    data-governance-interactions
    data-governance-classification
    data-governance-data-lineage
)
# The UI ingress edge lives partly in rossoctl-system (the HTTPRoute) and partly
# in data-governance (the ReferenceGrant permitting the cross-namespace edge).
DG_HTTPROUTE_NAME="data-governance-ui"
DG_HTTPROUTE_NAMESPACE="rossoctl-system"
DG_REFERENCEGRANT_NAME="allow-rossoctl-system-httproute-to-ui"
# The dedicated collector pipeline the tee adds; its presence in the collector
# ConfigMap is how `status` decides the tee is wired.
DG_TEE_PIPELINE="traces/data_governance"
DG_COLLECTOR_NAMESPACE="${COLLECTOR_NAMESPACE:-rossoctl-system}"
DG_COLLECTOR_CONFIGMAP="${COLLECTOR_CONFIGMAP:-otel-collector-config}"

# Trusted Rossoctl workloads are selected by the operator-owned type label.  A
# legacy component-label fallback keeps the #239 bare-namespace workflow usable
# where no trusted labels exist; it is never mixed with a trusted selection.
# Namespace label marking a user (rossoctl-enabled) namespace.
NS_ENABLED_SELECTOR='rossoctl-enabled=true'

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

err()  { printf '%s\n' "$*" >&2; }
die()  { err "error: $*"; exit 1; }

usage() {
    cat >&2 <<EOF
${PROG} — data-governance cluster management

Usage:
  ${PROG}                                        # component status
  ${PROG} component  [install|uninstall|status]
  ${PROG} namespaces [list]
  ${PROG} namespace  <ns> [instrument|status] [<entity>]
EOF
}

# usage_error <message>: print the message, the usage block, and exit non-zero.
usage_error() {
    err "error: $*"
    usage
    exit 2
}

# ---------------------------------------------------------------------------
# Preflight primitives (fail loud, never guess)
# ---------------------------------------------------------------------------

# require_kubectl: kubectl must be on PATH and its client runnable. This is a
# purely-local check (`version --client` never contacts the API server), so it
# proves the binary works — NOT that the cluster is reachable. The commands that
# actually hit the API (list_user_namespaces / enumerate_entities) detect a
# failed `kubectl get` themselves and fail loud with kubectl's own diagnostic.
require_kubectl() {
    command -v kubectl >/dev/null 2>&1 \
        || die "'kubectl' is not on PATH; install it and point it at the rossoctl cluster"
    kubectl version --client >/dev/null 2>&1 \
        || die "'kubectl' is present but not runnable (kubectl version --client failed)"
}

# detect_container_tool: echo the chosen container tool, matching
# build-and-load.sh conventions (explicit CONTAINER_TOOL wins, then docker,
# then podman). Fails loud if neither is present. Prints only the tool name to
# stdout so callers can capture it.
detect_container_tool() {
    if [[ -n "${CONTAINER_TOOL:-}" ]]; then
        printf '%s\n' "${CONTAINER_TOOL}"
    elif command -v docker >/dev/null 2>&1; then
        printf 'docker\n'
    elif command -v podman >/dev/null 2>&1; then
        printf 'podman\n'
    else
        die "no container tool found; install docker or podman (matching deploy/build-and-load.sh)"
    fi
}

# ---------------------------------------------------------------------------
# Shared cluster helpers
# ---------------------------------------------------------------------------

# list_user_namespaces: print user namespaces one per line — those labelled
# rossoctl-enabled=true, excluding kube-* and *-system. Requires kubectl.
#
# A failed `kubectl get` (unreachable/RBAC-denied API — which `require_kubectl`
# cannot catch) is a LOUD, non-zero exit carrying kubectl's own diagnostic, NOT
# a swallowed empty exit indistinguishable from the legitimate zero-namespace
# case. We capture stdout and stderr separately and check the status explicitly.
list_user_namespaces() {
    require_kubectl

    # Capture stdout; let kubectl's OWN stderr pass through to our stderr (never
    # 2>/dev/null it), and check the status explicitly so a failed get dies with
    # a diagnostic rather than an empty exit.
    local out status
    status=0
    out="$(kubectl get namespaces \
        -l "${NS_ENABLED_SELECTOR}" \
        -o 'jsonpath={range .items[*]}{.metadata.name}{"\n"}{end}')" || status=$?
    if [[ "${status}" -ne 0 ]]; then
        die "failed to list namespaces (kubectl get namespaces failed; see the kubectl error above)"
    fi

    printf '%s\n' "${out}" | while IFS= read -r ns; do
        [[ -n "${ns}" ]] || continue
        case "${ns}" in
            kube-*|*-system) continue ;;
        esac
        printf '%s\n' "${ns}"
    done
}

# enumerate_entities <ns> [<entity>]: print the agent/tool names in <ns> one per
# line. With <entity>, restrict to that single name (app.kubernetes.io/name) and
# error loud if it does not resolve to an agent/tool in <ns>. Requires kubectl.
enumerate_entities() {
    local ns="$1"
    local entity="${2:-}"
    [[ -n "${ns}" ]] || die "enumerate_entities: namespace is required"
    require_kubectl

    local raw status
    status=0
    raw="$(kubectl get deployments -n "${ns}" -o json)" || status=$?
    if [[ "${status}" -ne 0 ]]; then
        die "failed to list agents/tools in namespace '${ns}' (kubectl get deployments failed; see the kubectl error above)"
    fi

    local names
    names="$(printf '%s' "${raw}" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
items = doc.get("items") or []
trusted = [item for item in items if (item.get("metadata", {}).get("labels", {}).get("rossoctl.io/type") in {"agent", "tool"})]
selected = trusted or [item for item in items if (item.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") in {"agent", "mcp-tool"})]
only = sys.argv[1]
names = sorted({item.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/name") for item in selected})
for name in names:
    if name and (not only or name == only):
        print(name)
' "${entity}")"

    if [[ -n "${entity}" && -z "${names}" ]]; then
        die "entity '${entity}' is not a trusted Rossoctl agent/tool (or legacy component-labelled entity) in namespace '${ns}'"
    fi
    printf '%s' "${names}"
    [[ -n "${names}" ]] && printf '\n'
    return 0
}

# ---------------------------------------------------------------------------
# Verb handlers
# ---------------------------------------------------------------------------

# --- component -------------------------------------------------------------

# tee_is_wired: 0 (true) if the collector ConfigMap carries the dedicated
# traces/data_governance pipeline, 1 (false) if it does not, and a LOUD non-zero
# die if the probe itself fails (unreachable/RBAC-denied API — never a swallowed
# empty that masquerades as "not wired"). Prints nothing.
tee_is_wired() {
    local out status
    status=0
    out="$(kubectl -n "${DG_COLLECTOR_NAMESPACE}" get cm "${DG_COLLECTOR_CONFIGMAP}" \
        -o 'jsonpath={.data.base\.yaml}')" || status=$?
    if [[ "${status}" -ne 0 ]]; then
        die "failed to read the collector ConfigMap ${DG_COLLECTOR_NAMESPACE}/${DG_COLLECTOR_CONFIGMAP} (kubectl get failed; see the kubectl error above)"
    fi
    case "${out}" in
        *"${DG_TEE_PIPELINE}"*) return 0 ;;
        *) return 1 ;;
    esac
}

# --- component install -----------------------------------------------------

component_install() {
    local no_build=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --no-build) no_build=1; shift ;;
            *) usage_error "unknown component install option: '$1'" ;;
        esac
    done

    require_kubectl

    # 1. build + kind-load the images (skippable when already loaded).
    if [[ "${no_build}" -eq 1 ]]; then
        err ">> component install: --no-build, skipping image build+load"
    else
        [[ -x "${BUILD_AND_LOAD}" ]] \
            || die "build-and-load helper not found or not executable: ${BUILD_AND_LOAD}"
        err ">> component install: building + loading images (${BUILD_AND_LOAD})"
        "${BUILD_AND_LOAD}" \
            || die "image build+load failed (${BUILD_AND_LOAD}); aborting install"
    fi

    # 2. apply the component manifests.
    [[ -d "${K8S_DIR}" ]] || die "manifest directory not found: ${K8S_DIR}"
    err ">> component install: applying manifests (kubectl apply -f ${K8S_DIR}/)"
    kubectl apply -f "${K8S_DIR}/" \
        || die "kubectl apply -f ${K8S_DIR}/ failed; aborting install"

    # 3. tee the platform collector to the receiver (idempotent).
    [[ -x "${PATCH_COLLECTOR}" ]] \
        || die "collector-tee helper not found or not executable: ${PATCH_COLLECTOR}"
    err ">> component install: wiring the collector tee (${PATCH_COLLECTOR})"
    "${PATCH_COLLECTOR}" \
        || die "collector-tee patch failed (${PATCH_COLLECTOR}); aborting install"

    # 4. rollout restart + status the load-bearing deployments (the
    #    :latest/IfNotPresent cycle step — apply alone will not pick up a fresh
    #    image).
    err ">> component install: rolling the receiver/ui/interactions deployments"
    local d
    for d in "${DG_ROLLOUT_DEPLOYMENTS[@]}"; do
        kubectl -n "${DG_NAMESPACE}" rollout restart "deployment/${d}" \
            || die "rollout restart deployment/${d} failed"
    done
    for d in "${DG_ROLLOUT_DEPLOYMENTS[@]}"; do
        kubectl -n "${DG_NAMESPACE}" rollout status "deployment/${d}" --timeout=120s \
            || die "rollout status deployment/${d} did not become ready"
    done

    err ">> component install: done."
}

# --- component uninstall ---------------------------------------------------

component_uninstall() {
    local keep_data=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --keep-data) keep_data=1; shift ;;
            *) usage_error "unknown component uninstall option: '$1'" ;;
        esac
    done

    require_kubectl

    # 1. revert the collector tee so the SHARED otel-collector is left clean.
    #    (Idempotent: reverting an already-reverted tee is a no-op.)
    [[ -x "${PATCH_COLLECTOR}" ]] \
        || die "collector-tee helper not found or not executable: ${PATCH_COLLECTOR}"
    err ">> component uninstall: reverting the collector tee (${PATCH_COLLECTOR} --revert)"
    "${PATCH_COLLECTOR}" --revert \
        || die "collector-tee revert failed (${PATCH_COLLECTOR} --revert)"

    # 2. delete the UI ingress edge: the HTTPRoute (rossoctl-system) + the
    #    ReferenceGrant (data-governance). --ignore-not-found keeps uninstall
    #    idempotent.
    err ">> component uninstall: deleting the UI HTTPRoute + ReferenceGrant"
    kubectl -n "${DG_HTTPROUTE_NAMESPACE}" delete httproute "${DG_HTTPROUTE_NAME}" \
        --ignore-not-found \
        || die "failed to delete HTTPRoute ${DG_HTTPROUTE_NAMESPACE}/${DG_HTTPROUTE_NAME}"
    kubectl -n "${DG_NAMESPACE}" delete referencegrant "${DG_REFERENCEGRANT_NAME}" \
        --ignore-not-found \
        || die "failed to delete ReferenceGrant ${DG_NAMESPACE}/${DG_REFERENCEGRANT_NAME}"

    if [[ "${keep_data}" -eq 1 ]]; then
        # --keep-data: delete the workloads individually, PRESERVE the namespace
        # (and with it the Postgres PVC). Never `delete namespace`.
        err ">> component uninstall: --keep-data, deleting workloads individually (PVC preserved)"
        local d
        for d in "${DG_ALL_DEPLOYMENTS[@]}"; do
            kubectl -n "${DG_NAMESPACE}" delete deployment "${d}" --ignore-not-found \
                || die "failed to delete deployment/${d}"
        done
    else
        # default: delete the whole namespace — this takes the Postgres PVC with
        # it. --ignore-not-found keeps a re-run idempotent.
        err ">> component uninstall: deleting namespace ${DG_NAMESPACE} (takes the Postgres PVC)"
        kubectl delete namespace "${DG_NAMESPACE}" --ignore-not-found \
            || die "failed to delete namespace ${DG_NAMESPACE}"
    fi

    err ">> component uninstall: done."
}

# --- component status ------------------------------------------------------

component_status() {
    require_kubectl

    local overall_ready=1  # 0 = ready

    # Namespace presence.
    local ns_status
    ns_status=0
    kubectl get namespace "${DG_NAMESPACE}" >/dev/null 2>/dev/null || ns_status=$?
    if [[ "${ns_status}" -ne 0 ]]; then
        # Distinguish NotFound (component not installed) from an API failure. A
        # real API failure must fail loud, not print a misleading "not installed".
        local probe pstatus
        pstatus=0
        probe="$(kubectl get namespace 2>&1)" || pstatus=$?
        if [[ "${pstatus}" -ne 0 ]]; then
            die "failed to query the cluster (kubectl get namespace failed): ${probe}"
        fi
        printf 'component: NOT installed (namespace %s absent)\n' "${DG_NAMESPACE}"
        overall_ready=0
        # Still report the tee below — it lives in a different namespace.
    else
        # Per-deployment readiness.
        local d ready_out status any_missing=0 all_ready=1
        for d in "${DG_ALL_DEPLOYMENTS[@]}"; do
            status=0
            ready_out="$(kubectl -n "${DG_NAMESPACE}" get deployment "${d}" \
                -o 'jsonpath={.status.readyReplicas}' 2>/dev/null)" || status=$?
            if [[ "${status}" -ne 0 ]]; then
                printf '  deployment/%s: MISSING\n' "${d}"
                any_missing=1
                all_ready=0
                continue
            fi
            if [[ "${ready_out:-0}" -ge 1 ]]; then
                printf '  deployment/%s: ready\n' "${d}"
            else
                printf '  deployment/%s: NOT ready (0 ready replicas)\n' "${d}"
                all_ready=0
            fi
        done
        if [[ "${all_ready}" -eq 1 ]]; then
            printf 'component: installed, all deployments ready\n'
        else
            printf 'component: installed, some deployments NOT ready\n'
        fi
    fi

    # Collector tee state (lives in rossoctl-system, independent of the DG ns).
    if tee_is_wired; then
        printf 'collector tee: wired (pipeline %s present)\n' "${DG_TEE_PIPELINE}"
    else
        printf 'collector tee: NOT wired (pipeline %s absent)\n' "${DG_TEE_PIPELINE}"
    fi
}

cmd_component() {
    local action="${1:-status}"
    shift || true
    case "${action}" in
        install)
            component_install "$@"
            ;;
        uninstall)
            component_uninstall "$@"
            ;;
        status)
            component_status "$@"
            ;;
        *)
            usage_error "unknown component action: '${action}'"
            ;;
    esac
}

# --- namespaces ------------------------------------------------------------

cmd_namespaces() {
    local action="${1:-list}"
    case "${action}" in
        list)
            list_user_namespaces
            ;;
        *)
            usage_error "unknown namespaces action: '${action}'"
            ;;
    esac
}

# --- namespace <ns> status [<entity>] -- sidecar detection (read-only) -----
#
# Detection is dg.sh's OWN read model (issue #183 re-scope: the cortex #852 kit
# offers no status query, so nothing here delegates). Per agent/tool it answers
# three facts, from the Deployment's pod template and the ConfigMap the sidecar
# mounts — never mutating anything:
#
#   * sidecar presence / type — the AuthBridge sidecar CONTAINER NAME decides it:
#       authbridge-proxy → proxy-sidecar,  envoy-proxy → envoy-sidecar,  else none.
#     (These are the exact container names the operator's injection webhook and
#     the cortex attach kits use; distinguishing them is what ADR-0032's
#     proxy-row-vs-kit split hinges on.)
#   * lineage — is `lineage-telemetry` wired into the sidecar's effective
#     pipeline (surfaced as the per-entity `lineage=yes/no` token — ADR-0033
#     decision 5, the no-marker idempotency signal). The effective pipeline is
#     the config the sidecar mounts at
#     /etc/authbridge (both modes mount `config.yaml` there from a ConfigMap):
#     we resolve that container's /etc/authbridge volumeMount → the backing
#     volume → its ConfigMap, read the ConfigMap, and look for a
#     `lineage-telemetry` plugin entry. Reading the *mounted* config (rather
#     than assuming a name) keeps detection mode-agnostic and honest about what
#     the sidecar actually runs.

# get_deployment_json <ns> <entity>: print the Deployment JSON for a single
# entity (by app.kubernetes.io/name). A failed `kubectl get` is a LOUD non-zero
# die carrying kubectl's own diagnostic — never a swallowed empty that would
# masquerade as "no such entity".
get_deployment_json() {
    local ns="$1" entity="$2"
    local out status
    status=0
    out="$(kubectl get deployments -n "${ns}" \
        -l "app.kubernetes.io/name=${entity}" \
        -o json)" || status=$?
    if [[ "${status}" -ne 0 ]]; then
        die "failed to read deployment for entity '${entity}' in namespace '${ns}' (kubectl get failed; see the kubectl error above)"
    fi
    printf '%s' "${out}"
}

# deployment_is_trusted <deployment-json>: true only for operator-labelled
# Rossoctl agents/tools.  The label is read-only input; dg.sh never writes it.
deployment_is_trusted() {
    local json="$1"
    printf '%s' "${json}" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
items = doc.get("items")
if items is None:
    items = [doc]
labels = (items[0].get("metadata", {}).get("labels", {}) if items else {})
sys.exit(0 if labels.get("rossoctl.io/type") in {"agent", "tool"} else 1)
'
}

# get_pod_json <ns> <entity>: print the live Pod listing JSON for a single entity
# (by app.kubernetes.io/name, Running only). This carries a WEBHOOK-INJECTED
# sidecar container that the Deployment template does NOT (the injection webhook
# mutates the admitted Pod, not the Deployment), so it is the fallback source for
# sidecar detection + pipeline-CM resolution when the template shows no sidecar.
# A failed `kubectl get` is a LOUD non-zero die (mirrors get_deployment_json) —
# never a swallowed empty. Returns the items list JSON.
select_current_pods() {
    python3 -c '
import json, sys
doc = json.load(sys.stdin)
items = [item for item in (doc.get("items") or []) if not item.get("metadata", {}).get("deletionTimestamp")]
def ready(item):
    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in item.get("status", {}).get("conditions", []))
items.sort(key=lambda item: (ready(item), item.get("metadata", {}).get("creationTimestamp", "")), reverse=True)
doc["items"] = items
json.dump(doc, sys.stdout)
'
}

get_pod_json() {
    local ns="$1" entity="$2"
    local out status
    status=0
    out="$(kubectl get pods -n "${ns}" \
        -l "app.kubernetes.io/name=${entity}" \
        --field-selector=status.phase=Running \
        -o json)" || status=$?
    if [[ "${status}" -ne 0 ]]; then
        die "failed to read live pod for entity '${entity}' in namespace '${ns}' (kubectl get failed; see the kubectl error above)"
    fi
    printf '%s' "${out}" | select_current_pods
}

# get_pod_json_soft <ns> <entity>: like get_pod_json, but a failed `kubectl get`
# is NON-fatal — it prints nothing and returns non-zero (leaving the caller's
# prior verdict standing) instead of dying. This is the READ-ONLY status verb's
# fallback probe: a transient/RBAC failure of the pod read must NOT abort the
# whole best-effort diagnostic. A failed pod probe is inconclusive, not proof.
# The mutating `instrument` verb keeps get_pod_json's loud die — there, a probe
# we cannot trust must halt.
get_pod_json_soft() {
    local ns="$1" entity="$2"
    local out status
    status=0
    out="$(kubectl get pods -n "${ns}" \
        -l "app.kubernetes.io/name=${entity}" \
        --field-selector=status.phase=Running \
        -o json 2>/dev/null)" || status=$?
    if [[ "${status}" -ne 0 ]]; then
        return 1
    fi
    printf '%s' "${out}" | select_current_pods
}

# pod_items_count <pod-listing-json>: print the number of Pods in a `kubectl get
# pods -o json` listing (the length of .items). Used to distinguish a pod read
# that AUTHORITATIVELY shows no sidecar (>=1 Running pod, none of them carrying a
# sidecar) from one that is simply EMPTY (0 Running pods). Takes the JSON as $1.
pod_items_count() {
    local json="$1"
    printf '%s' "${json}" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
print(len(doc.get("items") or []))
'
}

# get_configmap_data <ns> <cm>: print the ConfigMap's .data map, or empty on
# NotFound (a dangling volume reference is reported as "plugin absent", not a
# crash). Any OTHER failure (apiserver unreachable/500, RBAC-denied) is a LOUD
# non-zero die carrying kubectl's own diagnostic — never a swallowed empty that
# would masquerade as a legitimate NotFound → false "plugin absent". Mirrors the
# capture-status-and-die shape of enumerate_entities / get_deployment_json /
# tee_is_wired.
get_configmap_data() {
    local ns="$1" cm="$2"
    local out status errfile
    status=0
    # One read: capture stdout into ${out}, stderr into a temp so we can classify
    # the failure. Only the NotFound signature is a legitimate empty; everything
    # else dies loud carrying kubectl's own diagnostic.
    errfile="$(mktemp)"
    out="$(kubectl -n "${ns}" get configmap "${cm}" -o 'jsonpath={.data}' 2>"${errfile}")" \
        || status=$?
    if [[ "${status}" -ne 0 ]]; then
        local msg
        msg="$(cat "${errfile}")"
        rm -f "${errfile}"
        if [[ "${msg}" == *"NotFound"* ]]; then
            # Legitimate absence: no such ConfigMap → no pipeline → plugin absent.
            return 0
        fi
        die "failed to read ConfigMap '${cm}' in namespace '${ns}': ${msg}"
    fi
    rm -f "${errfile}"
    printf '%s' "${out}"
}

# detect_sidecar_type <deployment-or-pod-json>: print proxy | envoy | none by
# inspecting the pod-spec container names. Accepts either a Deployment doc/list
# (containers at .spec.template.spec) or a Pod doc/list (containers at .spec
# directly), so the SAME detection works for a template-embedded sidecar
# (manual-attach / #852 kit) and a WEBHOOK-INJECTED pod-only sidecar (which the
# Deployment template never carries). Takes the JSON as $1.
#
# The #852 kit attaches envoy-proxy as a NATIVE sidecar — an initContainer with
# restartPolicy: Always (k8s >= 1.29), NOT an ordinary `containers` entry — so we
# union `containers` + `initContainers` when looking for the sidecar. Without the
# union an instrumented pod (envoy-proxy in initContainers) reads as sidecar=none.
detect_sidecar_type() {
    local json="$1"
    printf '%s' "${json}" | python3 -c '
import json, sys
def podspec(item):
    # A Pod carries its containers/volumes at .spec directly; a Deployment (or
    # any other workload with a pod template) carries them at .spec.template.spec.
    spec = item.get("spec") or {}
    if item.get("kind") == "Pod" or "containers" in spec:
        return spec
    return (spec.get("template") or {}).get("spec") or {}
doc = json.load(sys.stdin)
items = doc.get("items")
if items is None:
    items = [doc] if doc.get("kind") in ("Deployment", "Pod") else []
if not items:
    print("none"); sys.exit(0)
spec = podspec(items[0])
all_containers = (spec.get("containers") or []) + (spec.get("initContainers") or [])
names = {c.get("name") for c in all_containers}
if "authbridge-proxy" in names:
    print("proxy")
elif "envoy-proxy" in names:
    print("envoy")
else:
    print("none")
'
}

# sidecar_config_cm <deployment-or-pod-json>: print the name of the ConfigMap the
# sidecar container mounts at /etc/authbridge (its effective pipeline config), or
# empty if there is no sidecar or no such mount. Resolves container mount →
# volume → configMap.name, so it works for both sidecar shapes AND for a Pod doc
# (webhook-injected sidecar) whose containers/volumes live at .spec directly.
# Like detect_sidecar_type, unions `containers` + `initContainers` so a NATIVE
# sidecar (the #852 kit's envoy-proxy initContainer) is found and its pipeline CM
# resolved — otherwise plugin detection reads a native sidecar as unwired.
sidecar_config_cm() {
    local json="$1"
    printf '%s' "${json}" | python3 -c '
import json, sys
def podspec(item):
    spec = item.get("spec") or {}
    if item.get("kind") == "Pod" or "containers" in spec:
        return spec
    return (spec.get("template") or {}).get("spec") or {}
doc = json.load(sys.stdin)
items = doc.get("items")
if items is None:
    items = [doc] if doc.get("kind") in ("Deployment", "Pod") else []
if not items:
    sys.exit(0)
spec = podspec(items[0])
containers = (spec.get("containers") or []) + (spec.get("initContainers") or [])
sidecar = None
for c in containers:
    if c.get("name") in ("authbridge-proxy", "envoy-proxy"):
        sidecar = c
        break
if sidecar is None:
    sys.exit(0)
vol_name = None
for m in sidecar.get("volumeMounts") or []:
    if m.get("mountPath") == "/etc/authbridge":
        vol_name = m.get("name")
        break
if vol_name is None:
    sys.exit(0)
for v in spec.get("volumes") or []:
    if v.get("name") == vol_name:
        cm = (v.get("configMap") or {}).get("name")
        if cm:
            print(cm)
        break
'
}

# plugin_wired_in_cm_data <cm-data-json>: 0 (true) if the ConfigMap data carries
# a `lineage-telemetry` plugin ENTRY, 1 (false) otherwise. The data is the map of
# ConfigMap keys → file contents (jsonpath {.data}); the pipeline lists it as
# `- name: lineage-telemetry`, so we match the `name:` ENTRY form, NOT the bare
# token — a comment or descriptive string mentioning "lineage-telemetry" must not
# read as wired (code-review #245: substring match caused false idempotency).
plugin_wired_in_cm_data() {
    local data="$1"
    [[ -n "${data}" ]] || return 1
    printf '%s' "${data}" | python3 -c '
import json, re, sys
# The plugin ENTRY: `name: lineage-telemetry` (optionally quoted), as opposed to
# the bare token in a comment / config string. Whitespace-tolerant so it matches
# both real config.yaml and the flattened Go-map {.data} repr some kubectl
# versions print. A leading `#` comment marker on the same segment is excluded.
entry_re = re.compile(r"name:\s*[\x22\x27]?lineage-telemetry[\x22\x27]?")
def has_entry(text):
    for seg in re.split(r"[\r\n]", text):
        code = seg.split("#", 1)[0]  # drop trailing comment
        if entry_re.search(code):
            return True
    return False
raw = sys.stdin.read().strip()
if not raw:
    sys.exit(1)
try:
    data = json.loads(raw)
except Exception:
    # jsonpath {.data} of a plain map may already be a python-ish repr on some
    # kubectl versions; fall back to scanning the raw text for the entry form.
    sys.exit(0 if has_entry(raw) else 1)
if not isinstance(data, dict):
    sys.exit(1)
for v in data.values():
    if isinstance(v, str) and has_entry(v):
        sys.exit(0)
sys.exit(1)
'
}

# namespace_status <ns> [<entity>]: the read-only inspector. Enumerates the
# agents/tools (reusing #181's enumerate_entities — loud error on an unresolved
# named entity), and prints one line per entity: name, sidecar presence + type,
# and whether lineage-telemetry is wired.
namespace_status() {
    local ns="$1"
    local entity="${2:-}"
    [[ -n "${ns}" ]] || die "namespace_status: namespace is required"
    require_kubectl

    command -v python3 >/dev/null 2>&1 \
        || die "'python3' is required for sidecar detection (namespace status); install it"

    # Enumerate up front so a bad <entity> fails loud before any per-entity work.
    local names
    names="$(enumerate_entities "${ns}" "${entity}")"

    if [[ -z "${names}" ]]; then
        # Zero agents/tools is a legitimate empty result, not an error.
        err "namespace ${ns}: no agents/tools found"
        return 0
    fi

    local name json deployment_json type cm cm_data lineage
    while IFS= read -r name; do
        [[ -n "${name}" ]] || continue
        deployment_json="$(get_deployment_json "${ns}" "${name}")"
        json="${deployment_json}"
        type="$(detect_sidecar_type "${json}")"

        # Fall back to the live Pod when the Deployment TEMPLATE shows no sidecar:
        # a webhook-injected sidecar exists only in the admitted Pod, never in the
        # template. If the Pod carries one, switch to the Pod doc for the rest of
        # this entity's detection + pipeline-CM resolution. The template path is
        # unchanged: a template-embedded sidecar is detected above and never
        # triggers the fallback.
        #
        # status is a read-only, best-effort diagnostic, so the pod probe here is
        # SOFT (get_pod_json_soft): a transient/RBAC failure of the pod read is
        # inconclusive — we keep the template's 'none' verdict and continue,
        # rather than aborting the whole status run. (The mutating `instrument`
        # verb uses the loud-die get_pod_json instead.)
        if [[ "${type}" == "none" ]]; then
            local pod_json pod_type
            if pod_json="$(get_pod_json_soft "${ns}" "${name}")"; then
                pod_type="$(detect_sidecar_type "${pod_json}")"
                if [[ "${pod_type}" != "none" ]]; then
                    json="${pod_json}"
                    type="${pod_type}"
                fi
            fi
        fi

        if [[ "${type}" == "none" ]]; then
            printf '%s\tsidecar=none\ttype=none\tlineage=no (lineage-telemetry not wired)\n' "${name}"
            continue
        fi

        cm="$(sidecar_config_cm "${json}")"
        cm_data=""
        if [[ -n "${cm}" ]]; then
            cm_data="$(get_configmap_data "${ns}" "${cm}")"
        fi
        if plugin_wired_in_cm_data "${cm_data}"; then
            lineage="yes (lineage-telemetry wired)"
        else
            lineage="no (lineage-telemetry not wired)"
        fi
        if deployment_is_trusted "${deployment_json}" && [[ "${type}" == "proxy" ]]; then
            local live="yes" reason="live" pod_json pod_name cm_json app_name
            pod_json="$(get_pod_json_soft "${ns}" "${name}" || true)"
            if ! deployment_activation_valid "${deployment_json}"; then
                live="no"; reason="application shim or LINEAGE_PROPAGATE missing"
            elif ! proxy_environment_valid "${pod_json}"; then
                live="no"; reason="admission proxy environment missing"
            elif ! pod_proxy_ready "${pod_json}"; then
                live="no"; reason="pod or authbridge-proxy not ready"
            elif ! app_name="$(app_container_of "${pod_json}")" || [[ -z "${app_name}" ]]; then
                live="no"; reason="application container unresolved"
            elif ! pod_shim_attested "${ns}" "$(pod_name_of "${pod_json}")" "${app_name}"; then
                live="no"; reason="running application failed two-shim attestation"
            elif [[ -z "${cm}" ]]; then
                live="no"; reason="mounted ConfigMap unresolved"
            else
                cm_json="$(kubectl -n "${ns}" get configmap "${cm}" -o json 2>/dev/null || true)"
                if ! configmap_is_canonical "${cm_json}" "${name}" "${pod_json}"; then
                    live="no"; reason="mounted ConfigMap drifted"
                else
                    pod_name="$(pod_name_of "${pod_json}")"
                    if ! live_pipeline_is_canonical "${ns}" "${pod_name}" "${name}" "${cm_json}"; then
                        live="no"; reason="live pipeline has not converged"
                    fi
                fi
            fi
            printf '%s\tsidecar=present\ttype=%s\tlineage=%s\tlive=%s (%s)\n' \
                "${name}" "${type}" "${lineage}" "${live}" "${reason}"
        else
            printf '%s\tsidecar=present\ttype=%s\tlineage=%s\n' "${name}" "${type}" "${lineage}"
        fi
    done <<< "${names}"
}

# --- namespace <ns> instrument [<entity>] -- NON-REVERSIBLE activation (#184) --
#
# Activate lineage for Rossoctl-managed agents/tools in <ns>. Every target must
# already have the trusted Rossoctl identity label and an admitted AuthBridge
# proxy. Bare workloads fail during the transaction preflight, before any image,
# Deployment, or ConfigMap mutation, with guidance to deploy/import them through
# Rossoctl first. Data Governance never creates or replaces a sidecar.

# The platform collector the component tee carries to the receiver — the kit's
# own OTEL_ENDPOINT default too, so passing it is a no-op belt-and-braces that
# also documents the destination in the kit-invocation log.
DG_OTEL_ENDPOINT="otel-collector.rossoctl-system.svc.cluster.local:4317"

# The Kind cluster the shim bake `kind load`s the -otel image into. The kit's
# container-runtime.sh reads KIND_CLUSTER_NAME (default `rossoctl`), but dg.sh
# used to drive build-otel-shim.sh WITHOUT forwarding a cluster name — so on a
# cluster NOT named `rossoctl` the load silently targeted the wrong cluster and
# the operator's cluster never received the image (#241 review → parked on #244).
# Resolve the operator's choice once and forward it as KIND_CLUSTER_NAME:
#   KIND_CLUSTER_NAME (the kit's own var) wins; else the DG-wide KIND_CLUSTER
#   (build-and-load.sh, deploy/k8s/README.md); else the historical `rossoctl`.
# Bridging KIND_CLUSTER keeps `dg.sh instrument` and `build-and-load.sh` on the
# SAME cluster when an operator sets only the DG-wide var. An empty value is
# treated as unset (so `KIND_CLUSTER_NAME= dg.sh ...` still gets the default).
resolve_kind_cluster_name() {
    if [[ -n "${KIND_CLUSTER_NAME:-}" ]]; then
        printf '%s' "${KIND_CLUSTER_NAME}"
    elif [[ -n "${KIND_CLUSTER:-}" ]]; then
        printf '%s' "${KIND_CLUSTER}"
    else
        printf '%s' "rossoctl"
    fi
}

# require_vendored_kit: die loud if the VENDORED lineage-attach kit under
# ${KIT_DIR} is missing or incomplete. This is a repo-integrity check, not a
# user-facing "pass a path" preflight — the kit ships in this repo
# (deploy/lineage-attach/), so an absent file means a broken checkout, not a
# missing --cortex-local-path (ADR-0033: the kit is vendored, retired the flag).
# Mutates nothing.
#
# Check the complete shim-build and existing-proxy reconciliation surface before
# doing any cluster mutation.
require_vendored_kit() {
    [[ -d "${KIT_DIR}" ]] \
        || die "the vendored lineage-attach kit is missing (expected ${KIT_DIR}); this is a broken checkout — the kit ships in deploy/lineage-attach/. Mutating nothing."
    [[ -x "${KIT_DIR}/build-otel-shim.sh" ]] \
        || die "the vendored lineage-attach kit is incomplete: ${KIT_DIR}/build-otel-shim.sh is missing or not executable. Mutating nothing."
    # Sourced / build-input files the shim bake needs: must be present (they are
    # read, not exec'd, so an executable bit is not required). Both shims are
    # build inputs — Dockerfile.otel-shim COPYs the propagate hook AND the
    # turn-span shim (rossoctl_turnspan.py + its .pth) into the image; a checkout
    # missing either would die mid-bake, so refuse before cluster mutation.
    local f
    for f in container-runtime.sh Dockerfile.otel-shim otel-instrumentors.txt \
             lineage-propagate-hook.py \
             rossoctl_turnspan.py rossoctl_turnspan.pth \
             attest-otel-shim.py \
             reconcile-existing-proxy.py; do
        [[ -e "${KIT_DIR}/${f}" ]] \
            || die "the vendored lineage-attach kit is incomplete: ${KIT_DIR}/${f} is missing (the shim bake needs it). Mutating nothing."
    done
}

# component_installed: 0 (true) if the data-governance namespace is present,
# 1 (false) if it is a genuine NotFound; a LOUD die on any other API failure
# (never a swallowed empty that masquerades as "not installed"). This is a
# dg.sh-side preflight the kit does not make — the kit points anywhere it is told.
component_installed() {
    local out status
    status=0
    out="$(kubectl get namespace "${DG_NAMESPACE}" 2>&1)" || status=$?
    if [[ "${status}" -eq 0 ]]; then
        return 0
    fi
    case "${out}" in
        *NotFound*) return 1 ;;
        *) die "failed to probe the data-governance component (kubectl get namespace failed): ${out}" ;;
    esac
}

# instrument_preflight: the dg.sh-side checks the kit does not make — component
# installed + collector tee wired. Both fail loud, mutating nothing.
instrument_preflight() {
    component_installed \
        || die "the data-governance component is not installed (namespace ${DG_NAMESPACE} absent) — run 'dg.sh component install' first; mutating nothing"
    if ! tee_is_wired; then
        die "the collector tee is not wired (pipeline ${DG_TEE_PIPELINE} absent in ${DG_COLLECTOR_NAMESPACE}/${DG_COLLECTOR_CONFIGMAP}) — spans would not reach the receiver; run 'dg.sh component install'; mutating nothing"
    fi
}


# app_container_of <deployment-or-pod-json>: print the app container's name — the
# sole non-sidecar container in the pod spec (excludes authbridge-proxy /
# envoy-proxy). Accepts a Pod doc as well as a Deployment. Empty if it cannot be
# determined unambiguously.
app_container_of() {
    local json="$1"
    printf '%s' "${json}" | python3 -c '
import json, sys
def podspec(item):
    spec = item.get("spec") or {}
    if item.get("kind") == "Pod" or "containers" in spec:
        return spec
    return (spec.get("template") or {}).get("spec") or {}
doc = json.load(sys.stdin)
items = doc.get("items")
if items is None:
    items = [doc] if doc.get("kind") in ("Deployment", "Pod") else []
if not items:
    sys.exit(0)
spec = podspec(items[0])
sidecars = {"authbridge-proxy", "envoy-proxy"}
apps = [c for c in (spec.get("containers") or []) if c.get("name") not in sidecars]
if len(apps) == 1:
    print(apps[0].get("name") or "")
'
}

# app_image_of <deployment-or-pod-json> <container-name>: print that container's
# image. Accepts a Pod doc as well as a Deployment.
app_image_of() {
    local json="$1" cname="$2"
    printf '%s' "${json}" | python3 -c '
import json, sys
cname = sys.argv[1]
def podspec(item):
    spec = item.get("spec") or {}
    if item.get("kind") == "Pod" or "containers" in spec:
        return spec
    return (spec.get("template") or {}).get("spec") or {}
doc = json.load(sys.stdin)
items = doc.get("items")
if items is None:
    items = [doc] if doc.get("kind") in ("Deployment", "Pod") else []
if not items:
    sys.exit(0)
spec = podspec(items[0])
for c in (spec.get("containers") or []):
    if c.get("name") == cname:
        print(c.get("image") or "")
        break
' "${cname}"
}

pod_name_of() {
    local json="$1"
    printf '%s' "${json}" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
items = doc.get("items") or []
if items:
    print(items[0].get("metadata", {}).get("name", ""))
'
}

# Print the admission-owned listener contract as three tab-separated values:
# forward proxy address, reverse proxy address, and reverse backend. Rossoctl
# assigns different ports to agents and tools, so derive them from the admitted
# pod instead of assuming the agent defaults.
proxy_listener_contract() {
    local json="$1" app
    app="$(app_container_of "${json}")"
    [[ -n "${app}" ]] || return 1
    printf '%s' "${json}" | python3 -c '
import json, sys
from urllib.parse import urlsplit
doc = json.load(sys.stdin); items = doc.get("items") or []
if not items: sys.exit(1)
spec = items[0].get("spec") or {}
app = next((c for c in spec.get("containers", []) if c.get("name") == sys.argv[1]), None)
if app is None: sys.exit(1)
env = {e.get("name"): e.get("value") for e in app.get("env", [])}
http_proxy = env.get("HTTP_PROXY") or ""
if http_proxy != env.get("HTTPS_PROXY"): sys.exit(1)
try:
    parsed = urlsplit(http_proxy)
    forward_port = parsed.port
except ValueError:
    sys.exit(1)
if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or not forward_port:
    sys.exit(1)
no_proxy = {part.strip() for part in (env.get("NO_PROXY") or "").split(",")}
if not {"127.0.0.1", "localhost"} <= no_proxy: sys.exit(1)
proxy = next((c for c in spec.get("containers", []) if c.get("name") == "authbridge-proxy"), None)
if proxy is None: sys.exit(1)
proxy_ports = {p.get("name"): p.get("containerPort") for p in proxy.get("ports", [])}
if proxy_ports.get("forward-proxy") != forward_port: sys.exit(1)
reverse_port = proxy_ports.get("reverse-proxy")
app_ports = {p.get("name"): p.get("containerPort") for p in app.get("ports", [])}
app_port = app_ports.get("http")
if not reverse_port or not app_port: sys.exit(1)
print(f":{forward_port}\t:{reverse_port}\thttp://127.0.0.1:{app_port}")
' "${app}"
}

proxy_environment_valid() {
    proxy_listener_contract "$1" >/dev/null
}

render_existing_proxy_config() {
    local cm_json="$1" self_id="$2" pod_json="$3"
    local contract forward_addr reverse_addr reverse_backend
    contract="$(proxy_listener_contract "${pod_json}")" || return 1
    IFS=$'\t' read -r forward_addr reverse_addr reverse_backend <<< "${contract}"
    printf '%s' "${cm_json}" | python3 "${KIT_DIR}/reconcile-existing-proxy.py" render \
        --self-id "${self_id}" --otel-endpoint "${OTEL_ENDPOINT:-${DG_OTEL_ENDPOINT}}" \
        --forward-proxy-addr "${forward_addr}" \
        --reverse-proxy-addr "${reverse_addr}" \
        --reverse-proxy-backend "${reverse_backend}"
}

deployment_activation_valid() {
    local json="$1" app
    app="$(app_container_of "${json}")"
    [[ -n "${app}" ]] || return 1
    printf '%s' "${json}" | python3 -c '
import json, sys
doc = json.load(sys.stdin); items = doc.get("items")
if items is None: items = [doc]
if not items: sys.exit(1)
spec = ((items[0].get("spec") or {}).get("template") or {}).get("spec") or {}
app = next((c for c in spec.get("containers", []) if c.get("name") == sys.argv[1]), None)
if app is None: sys.exit(1)
env = {e.get("name"): e.get("value") for e in app.get("env", [])}
image = app.get("image") or ""
sys.exit(0 if image and env.get("LINEAGE_PROPAGATE") == "1" else 1)
' "${app}"
}

shim_image_attested() {
    local image="$1"
    "${KIT_DIR}/build-otel-shim.sh" --attest-existing "${image}" >/dev/null 2>&1
}

pod_shim_attested() {
    local ns="$1" pod="$2" app="$3" interpreter
    [[ -n "${pod}" && -n "${app}" ]] || return 1
    for interpreter in /app/.venv/bin/python /opt/venv/bin/python python3; do
        if kubectl -n "${ns}" exec -i "${pod}" -c "${app}" -- \
            "${interpreter}" - propagates \
            < "${KIT_DIR}/attest-otel-shim.py" >/dev/null 2>&1; then
            return 0
        fi
    done
    return 1
}

pod_proxy_ready() {
    local json="$1"
    printf '%s' "${json}" | python3 -c '
import json, sys
items = json.load(sys.stdin).get("items") or []
if not items: sys.exit(1)
pod = items[0]
ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in (pod.get("status", {}).get("conditions") or []))
containers = {c.get("name"): c.get("ready") for c in (pod.get("status", {}).get("containerStatuses") or [])}
sys.exit(0 if ready and containers.get("authbridge-proxy") is True else 1)
'
}

configmap_is_canonical() {
    local cm_json="$1" self_id="$2" pod_json="$3" rendered
    rendered="$(render_existing_proxy_config "${cm_json}" "${self_id}" "${pod_json}" 2>/dev/null)" \
        || return 1
    python3 -c '
import json, sys
current, rendered = (json.loads(value) for value in sys.argv[1:])
sys.exit(0 if current.get("data", {}).get("config.yaml") == rendered.get("data", {}).get("config.yaml") else 1)
' "${cm_json}" "${rendered}"
}

proxy_catalog_has_lineage() {
    local ns="$1" pod="$2" catalog status
    status=0
    catalog="$(kubectl -n "${ns}" exec "${pod}" -c authbridge-proxy -- \
        wget -qO- http://127.0.0.1:9094/v1/plugins 2>/dev/null)" || status=$?
    [[ "${status}" -eq 0 && -n "${catalog}" ]] || return 1
    printf '%s' "${catalog}" | python3 "${KIT_DIR}/reconcile-existing-proxy.py" catalog-valid
}

live_pipeline_is_canonical() {
    local ns="$1" pod="$2" self_id="$3" cm_json="$4" body expected status
    status=0
    body="$(kubectl -n "${ns}" exec "${pod}" -c authbridge-proxy -- \
        wget -qO- http://127.0.0.1:9094/v1/pipeline 2>/dev/null)" || status=$?
    [[ "${status}" -eq 0 && -n "${body}" ]] || return 1
    expected="$(printf '%s' "${cm_json}" | python3 "${KIT_DIR}/reconcile-existing-proxy.py" pipeline-contract 2>/dev/null)" \
        || return 1
    printf '%s' "${body}" | python3 "${KIT_DIR}/reconcile-existing-proxy.py" pipeline-valid \
        --self-id "${self_id}" --otel-endpoint "${OTEL_ENDPOINT:-${DG_OTEL_ENDPOINT}}" \
        --expected "${expected}"
}

# watch_rollout_for_crashloop <ns> <entity> <backout_line> <attach_stdout>:
# after an attach, detect a crash-looping sidecar. On a crash-loop (or the
# already-known failed attach) dump the sidecar container log, surface the kit's
# PRINTED back-out line verbatim, and die loud — halting the whole run so we do
# NOT proceed to the next entity. $4 is captured so we can echo the kit's own
# output back to the operator when we fail after a rollout the kit reported OK.
preflight_trusted_proxies() {
    local ns="$1" names="$2" plan_file="$3"
    : > "${plan_file}"
    local cache_file
    cache_file="$(mktemp)"
    local entity dep pod type pod_name cm cm_json app base cached shim bake_out bake_status
    while IFS= read -r entity; do
        [[ -n "${entity}" ]] || continue
        dep="$(get_deployment_json "${ns}" "${entity}")"
        deployment_is_trusted "${dep}" \
            || die "instrument: '${entity}' is not a Rossoctl-managed workload. Deploy or import agents/tools through Rossoctl so admission supplies the trusted identity label and AuthBridge sidecar, then retry. Mutating nothing."
        pod="$(get_pod_json "${ns}" "${entity}")"
        [[ "$(pod_items_count "${pod}")" -gt 0 ]] \
            || die "instrument: '${entity}' has no Running pod; cannot verify its admitted platform proxy. Mutating nothing."
        type="$(detect_sidecar_type "${pod}")"
        [[ "${type}" == "proxy" ]] \
            || die "instrument: trusted Rossoctl workload '${entity}' requires an admitted AuthBridge proxy sidecar (found '${type}'). Redeploy it through Rossoctl with proxy mode enabled, then retry. Mutating nothing."
        proxy_environment_valid "${pod}" \
            || die "instrument: '${entity}' does not have the platform HTTP_PROXY/HTTPS_PROXY/NO_PROXY contract. Mutating nothing."
        pod_name="$(pod_name_of "${pod}")"
        [[ -n "${pod_name}" ]] || die "instrument: could not resolve the live pod for '${entity}'. Mutating nothing."
        proxy_catalog_has_lineage "${ns}" "${pod_name}" \
            || die "instrument: '${entity}'s AuthBridge image does not advertise the a2a-parser, mcp-parser, inference-parser, and lineage-telemetry plugins. Deploy a lineage-capable Cortex proxy image first; mutating nothing."
        cm="$(sidecar_config_cm "${pod}")"
        [[ -n "${cm}" ]] || die "instrument: '${entity}' has no mounted AuthBridge ConfigMap. Mutating nothing."
        cm_json="$(kubectl -n "${ns}" get configmap "${cm}" -o json)" \
            || die "instrument: could not read ConfigMap '${cm}' for '${entity}'. Mutating nothing."
        render_existing_proxy_config "${cm_json}" "${entity}" "${pod}" >/dev/null \
            || die "instrument: '${entity}'s existing proxy ConfigMap is outside the safe #256 reconciliation envelope. Mutating nothing."

        app="$(app_container_of "${dep}")"
        [[ -n "${app}" ]] || die "instrument: '${entity}' must have exactly one application container. Mutating nothing."
        base="$(app_image_of "${dep}" "${app}")"
        [[ -n "${base}" ]] || die "instrument: '${entity}' application image could not be resolved. Mutating nothing."
        cached="$(awk -F '\t' -v image="${base}" '$1 == image {print $2; exit}' "${cache_file}")"
        if [[ -n "${cached}" ]]; then
            shim="${cached}"
        elif shim_image_attested "${base}"; then
            shim="${base}"
            printf '%s\t%s\n' "${base}" "${shim}" >> "${cache_file}"
        else
            err ">> instrument: preflight baking + attesting '${base}' for trusted proxy workloads"
            bake_status=0
            bake_out="$(KIND_CLUSTER_NAME="$(resolve_kind_cluster_name)" \
                "${KIT_DIR}/build-otel-shim.sh" "${base}" 2>&1)" || bake_status=$?
            printf '%s\n' "${bake_out}" >&2
            [[ "${bake_status}" -eq 0 ]] \
                || die "instrument: required shim for '${base}' failed preflight (exit ${bake_status}); trusted existing proxies cannot downgrade to capture-only. Mutating no workloads."
            shim="$(printf '%s\n' "${bake_out}" | sed -nE \
                's/.*loaded ([^ ]+) into.*/\1/p; s/.*built \+ attested ([^ ]+) .*/\1/p' | head -n1)"
            [[ -n "${shim}" ]] \
                || die "instrument: shim bake for '${base}' succeeded without a parseable image reference. Mutating no workloads."
            printf '%s\t%s\n' "${base}" "${shim}" >> "${cache_file}"
        fi
        printf '%s\t%s\t%s\n' "${entity}" "${app}" "${shim}" >> "${plan_file}"
    done <<< "${names}"
    rm -f "${cache_file}"
}

instrument_existing_proxy() {
    local ns="$1" entity="$2" app="$3" shim="$4"
    local patch
    patch="$(python3 -c '
import json, sys
name, image = sys.argv[1:]
print(json.dumps({"spec":{"template":{"spec":{"containers":[{"name":name,"image":image,"imagePullPolicy":"IfNotPresent","env":[{"name":"LINEAGE_PROPAGATE","value":"1"}]}]}}}}))
' "${app}" "${shim}")"
    err ">> instrument: rolling '${entity}' onto the attested application shim; platform sidecar/auth remain admission-owned"
    kubectl -n "${ns}" patch "deployment/${entity}" --type strategic -p "${patch}" \
        || die "instrument: failed to patch the application container for '${entity}'"
    kubectl -n "${ns}" rollout status "deployment/${entity}" --timeout=180s \
        || die "instrument: rollout of '${entity}' did not become ready"

    local pod pod_name cm cm_json amended
    pod="$(get_pod_json "${ns}" "${entity}")"
    [[ "$(detect_sidecar_type "${pod}")" == "proxy" ]] \
        || die "instrument: '${entity}' lost its platform proxy after rollout"
    proxy_environment_valid "${pod}" \
        || die "instrument: '${entity}' lost its platform proxy environment after rollout"
    pod_name="$(pod_name_of "${pod}")"
    cm="$(sidecar_config_cm "${pod}")"
    [[ -n "${pod_name}" && -n "${cm}" ]] \
        || die "instrument: could not resolve '${entity}'s post-rollout pod/ConfigMap"
    cm_json="$(kubectl -n "${ns}" get configmap "${cm}" -o json)" \
        || die "instrument: failed to read '${entity}'s post-rollout ConfigMap '${cm}'"
    amended="$(render_existing_proxy_config "${cm_json}" "${entity}" "${pod}")" \
        || die "instrument: failed to reconcile '${entity}'s post-rollout proxy pipeline"
    printf '%s' "${amended}" | kubectl apply -f - \
        || die "instrument: failed to apply '${entity}'s reconciled proxy ConfigMap"

    # A projected ConfigMap can take up to the kubelet sync period plus cache
    # propagation (commonly around two minutes) to reach the mounted file.
    local attempt max_attempts="${DG_PIPELINE_VERIFY_ATTEMPTS:-90}"
    for ((attempt=1; attempt<=max_attempts; attempt++)); do
        if live_pipeline_is_canonical "${ns}" "${pod_name}" "${entity}" "${amended}"; then
            err ">> instrument: '${entity}' lineage pipeline hot-reloaded and is live (no second rollout)."
            return 0
        fi
        sleep 2
    done
    die "instrument: '${entity}' ConfigMap was reconciled but /v1/pipeline did not converge after hot reload; no second rollout was performed"
}


# namespace_instrument <ns> [<entity>]: the non-reversible activation verb. Runs
# the dg.sh-side preflights (vendored kit intact, component installed, tee
# wired), enumerates the targeted entities (loud on a bad <entity>), validates
# the complete Rossoctl/AuthBridge transaction, and only then mutates workloads.
namespace_instrument() {
    local ns="$1"
    local entity="${2:-}"
    [[ -n "${ns}" ]] || die "namespace_instrument: namespace is required"
    require_kubectl
    command -v python3 >/dev/null 2>&1 \
        || die "'python3' is required for sidecar detection (namespace instrument); install it"

    # Preflight 1: the vendored kit must be intact (refuse, mutate nothing).
    require_vendored_kit

    # Preflight 2: the consumer side must be up (component installed + tee wired).
    instrument_preflight

    # Enumerate up front so a bad <entity> fails loud before any mutation.
    local names
    names="$(enumerate_entities "${ns}" "${entity}")"
    if [[ -z "${names}" ]]; then
        err "namespace ${ns}: no agents/tools found — nothing to instrument"
        return 0
    fi

    # All checks and shim builds finish before the first workload mutation.
    # This preflight also refuses bare and non-Rossoctl workloads.
    local plan_file name app shim
    plan_file="$(mktemp)"
    preflight_trusted_proxies "${ns}" "${names}" "${plan_file}"
    while IFS=$'\t' read -r name app shim; do
        [[ -n "${name}" ]] || continue
        instrument_existing_proxy "${ns}" "${name}" "${app}" "${shim}"
    done < "${plan_file}"
    rm -f "${plan_file}"

    err ">> instrument: done for trusted proxy namespace '${ns}'."
}

# --- namespace <ns> [instrument|status] [<entity>] -------------------------

cmd_namespace() {
    local ns="${1:-}"
    [[ -n "${ns}" ]] || usage_error "namespace: a <ns> argument is required"
    local action="${2:-status}"
    local entity="${3:-}"

    case "${action}" in
        instrument)
            namespace_instrument "${ns}" "${entity}"
            ;;
        status)
            namespace_status "${ns}" "${entity}"
            ;;
        *)
            usage_error "unknown namespace action: '${action}'"
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Global-option + command dispatch
# ---------------------------------------------------------------------------

main() {
    local -a positional=()

    # Parse global options, which may appear BEFORE the verb only. The first
    # non-option token is the verb; everything from there on (the verb and its
    # own arguments, including per-verb --flags like --no-build / --keep-data) is
    # passed through verbatim to the verb dispatcher. Unknown --flags in the
    # GLOBAL position are a usage error.
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            --*)
                usage_error "unknown option: '$1'"
                ;;
            *)
                # First positional = the verb; stop global parsing and take the
                # rest of the command line as the verb + its arguments.
                positional=("$@")
                break
                ;;
        esac
    done

    # No verb → component status.
    if [[ ${#positional[@]} -eq 0 ]]; then
        cmd_component status
        return
    fi

    local verb="${positional[0]}"
    local -a rest=("${positional[@]:1}")

    case "${verb}" in
        component)
            cmd_component "${rest[@]+"${rest[@]}"}"
            ;;
        namespaces)
            cmd_namespaces "${rest[@]+"${rest[@]}"}"
            ;;
        namespace)
            cmd_namespace "${rest[@]+"${rest[@]}"}"
            ;;
        *)
            usage_error "unknown command: '${verb}'"
            ;;
    esac
}

main "$@"
