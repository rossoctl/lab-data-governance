#!/usr/bin/env bash
# dg.sh — data-governance cluster management CLI.
#
# Give the data-governance component a first-class install / uninstall story on
# a rossoctl cluster, and a way to activate lineage telemetry on the agents and
# tools in a namespace so their traffic surfaces in the data-governance UI.
#
# Authoritative design: docs/cli.md
# Decision records:     docs/adr/0031-non-reversible-namespace-lineage-activation.md
#                       docs/adr/0032-dg-sh-builds-on-cortex-lineage-attach-kit.md
#
# ---------------------------------------------------------------------------
# THIS SLICE (issue #181): skeleton + shared cluster helpers.
#
#   * command-grammar dispatch,
#   * `namespaces list` (the one verb that WORKS this ticket),
#   * the shared helpers the later verbs reuse (entity enumeration,
#     user-namespace filter, preflight primitives),
#   * parse + carry the `--cortex-local-path` global (consumed only by the
#     later `namespace instrument` verb, #184).
#
# The `component` and `namespace instrument|status` verbs are recognised but
# stubbed here; later tickets fill them in.
# ---------------------------------------------------------------------------
#
# Grammar:
#
#   dg.sh                                                 # → component status
#   dg.sh component  [install|uninstall|status]
#   dg.sh namespaces [list]
#   dg.sh namespace  <ns> [instrument|status] [<entity>]
#
#   --cortex-local-path <dir>   # (env CORTEX_LOCAL_PATH) path to a cortex
#                               # checkout; `instrument` locates the #852 kit
#                               # under it. Required for `instrument`; unused by
#                               # the other verbs.
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

# The platform's own selector for agents/tools. rossoctl.io/type is
# operator-reserved and VAP-protected, so dg.sh NEVER reads or sets it.
ENTITY_COMPONENT_SELECTOR='app.kubernetes.io/component in (agent, mcp-tool)'
# Namespace label marking a user (rossoctl-enabled) namespace.
NS_ENABLED_SELECTOR='rossoctl-enabled=true'

# --cortex-local-path / CORTEX_LOCAL_PATH is a GLOBAL option: parsed here,
# carried for the later `instrument` verb, consumed by no verb in this slice.
CORTEX_LOCAL_PATH="${CORTEX_LOCAL_PATH:-}"

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

Global options:
  --cortex-local-path <dir>   Path to a cortex checkout (env CORTEX_LOCAL_PATH);
                              locates the lineage-attach kit for 'instrument'.
                              Required for 'instrument'; unused by other verbs.
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

    local selector="${ENTITY_COMPONENT_SELECTOR}"
    if [[ -n "${entity}" ]]; then
        # Single-entity select: the component selector AND the name label.
        selector="${selector},app.kubernetes.io/name=${entity}"
    fi

    # A failed `kubectl get` (unreachable/RBAC-denied API) is a loud, non-zero
    # exit carrying kubectl's own diagnostic — NOT a swallowed empty result that
    # would masquerade as 'no agents/tools in the namespace'. Capture stdout, let
    # kubectl's stderr pass through (never 2>/dev/null it), check status.
    local raw status
    status=0
    raw="$(kubectl get deployments -n "${ns}" \
        -l "${selector}" \
        -o 'jsonpath={range .items[*]}{.metadata.labels.app\.kubernetes\.io/name}{"\n"}{end}')" \
        || status=$?
    if [[ "${status}" -ne 0 ]]; then
        die "failed to list agents/tools in namespace '${ns}' (kubectl get deployments failed; see the kubectl error above)"
    fi

    local names
    names="$(printf '%s' "${raw}" | awk 'NF' | sort -u)"

    if [[ -n "${entity}" && -z "${names}" ]]; then
        die "entity '${entity}' is not an agent/tool (app.kubernetes.io/component in agent,mcp-tool) in namespace '${ns}'"
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
#   * plugin — is `lineage-telemetry` wired into the sidecar's effective
#     pipeline. The effective pipeline is the config the sidecar mounts at
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
        -l "${ENTITY_COMPONENT_SELECTOR},app.kubernetes.io/name=${entity}" \
        -o json)" || status=$?
    if [[ "${status}" -ne 0 ]]; then
        die "failed to read deployment for entity '${entity}' in namespace '${ns}' (kubectl get failed; see the kubectl error above)"
    fi
    printf '%s' "${out}"
}

# get_pod_json <ns> <entity>: print the live Pod listing JSON for a single entity
# (by app.kubernetes.io/name, Running only). This carries a WEBHOOK-INJECTED
# sidecar container that the Deployment template does NOT (the injection webhook
# mutates the admitted Pod, not the Deployment), so it is the fallback source for
# sidecar detection + pipeline-CM resolution when the template shows no sidecar.
# A failed `kubectl get` is a LOUD non-zero die (mirrors get_deployment_json) —
# never a swallowed empty. Returns the items list JSON.
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
    printf '%s' "${out}"
}

# get_pod_json_soft <ns> <entity>: like get_pod_json, but a failed `kubectl get`
# is NON-fatal — it prints nothing and returns non-zero (leaving the caller's
# prior verdict standing) instead of dying. This is the READ-ONLY status verb's
# fallback probe: a transient/RBAC failure of the pod read must NOT abort the
# whole best-effort diagnostic (which, before webhook-injected detection, simply
# printed sidecar=none and moved on). It mirrors crashloop_detected's rule that a
# failed pod probe is inconclusive, not proof. The mutating `instrument` verb
# keeps get_pod_json's loud die — there, a probe we cannot trust must halt.
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
    printf '%s' "${out}"
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
# a `lineage-telemetry` plugin entry, 1 (false) otherwise. The data is the map of
# ConfigMap keys → file contents (jsonpath {.data}); the pipeline lives in
# config.yaml as `- name: lineage-telemetry`, so we scan every value.
plugin_wired_in_cm_data() {
    local data="$1"
    [[ -n "${data}" ]] || return 1
    printf '%s' "${data}" | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    sys.exit(1)
try:
    data = json.loads(raw)
except Exception:
    # jsonpath {.data} of a plain map may already be a python-ish repr on some
    # kubectl versions; fall back to a substring scan of the raw text.
    sys.exit(0 if "lineage-telemetry" in raw else 1)
if not isinstance(data, dict):
    sys.exit(1)
for v in data.values():
    if isinstance(v, str) and "lineage-telemetry" in v:
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

    local name json type cm cm_data plugin
    while IFS= read -r name; do
        [[ -n "${name}" ]] || continue
        json="$(get_deployment_json "${ns}" "${name}")"
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
            printf '%s\tsidecar=none\ttype=none\tplugin=no (lineage-telemetry not wired)\n' "${name}"
            continue
        fi

        cm="$(sidecar_config_cm "${json}")"
        cm_data=""
        if [[ -n "${cm}" ]]; then
            cm_data="$(get_configmap_data "${ns}" "${cm}")"
        fi
        if plugin_wired_in_cm_data "${cm_data}"; then
            plugin="yes (lineage-telemetry wired)"
        else
            plugin="no (lineage-telemetry not wired)"
        fi
        printf '%s\tsidecar=present\ttype=%s\tplugin=%s\n' "${name}" "${type}" "${plugin}"
    done <<< "${names}"
}

# --- namespace <ns> instrument [<entity>] -- NON-REVERSIBLE activation (#184) --
#
# Activate lineage for the agents/tools in <ns> — all of them, or the single
# <entity> when named. Additive-only, and it NEVER changes a namespace's sidecar
# mode (ADR-0031). Per targeted entity, the least mutation that works, with the
# owner split ADR-0032 records:
#
#   | current sidecar        | action                                | owner   |
#   |------------------------|---------------------------------------|---------|
#   | none                   | inject envoy lineage sidecar (+ shim  | #852    |
#   |                        | for a Python app)                     | kit     |
#   | envoy-sidecar in place | add lineage-telemetry to its pipeline | #852    |
#   |                        | in place                              | kit     |
#   | proxy-sidecar in place | add lineage-telemetry to its pipeline | dg.sh's |
#   |                        | in place                              | OWN edit|
#
# The #852 kit is envoy-sidecar-only (its ConfigMap hardcodes mode: envoy-sidecar;
# its patch adds envoy-proxy as a NATIVE sidecar — an initContainer with
# restartPolicy: Always, k8s >= 1.29 — plus proxy-init). So the first two rows
# are the kit's job (sidecar-patch.sh) and the proxy row stays dg.sh's own.
#
# Two guard LAYERS, complementary (ADR-0032 decision 4):
#   * the kit runs `kubectl patch --dry-run=server` BEFORE any write — its own
#     pre-apply "version guard" that rejects a bad merge / admission / RBAC /
#     cluster < 1.29;
#   * dg.sh watches the rollout the attach triggers and detects a crash-looping
#     sidecar (the `unknown plugin "lineage-telemetry"` / DisallowUnknownFields
#     boot-crash surfaces as CrashLoopBackOff within seconds) — the POST-apply
#     failure the dry-run cannot see. On a failed rollout / crash-loop dg.sh
#     dumps the sidecar log, surfaces the kit's PRINTED back-out line verbatim
#     (a reverse-patch, NOT a rollout undo — envoy-proxy is a native sidecar, so
#     a whole-revision undo restores too much), fails loud, and does NOT proceed
#     to the next entity.

# The platform collector the component tee carries to the receiver — the kit's
# own OTEL_ENDPOINT default too, so passing it is a no-op belt-and-braces that
# also documents the destination in the kit-invocation log.
DG_OTEL_ENDPOINT="otel-collector.rossoctl-system.svc.cluster.local:4317"

# resolve_kit_dir: echo the lineage-attach kit directory under CORTEX_LOCAL_PATH,
# or die loud if the path is unset / the kit's scripts are absent. Mutates
# nothing (ADR-0032: resolve the kit, do not guess). The three scripts that must
# exist are the kit's documented surface: sidecar-patch.sh (live applier),
# build-otel-shim.sh (the propagate-only shim), attach-lineage.sh (the generator).
resolve_kit_dir() {
    [[ -n "${CORTEX_LOCAL_PATH}" ]] \
        || die "instrument needs the cortex lineage-attach kit: pass --cortex-local-path <dir> (or set CORTEX_LOCAL_PATH) pointing at a cortex checkout"
    local kit="${CORTEX_LOCAL_PATH%/}/authbridge/lineage-attach"
    [[ -d "${kit}" ]] \
        || die "cortex lineage-attach kit not found under '${CORTEX_LOCAL_PATH}' (expected ${kit}); mutating nothing"
    local s
    for s in sidecar-patch.sh build-otel-shim.sh attach-lineage.sh; do
        [[ -x "${kit}/${s}" ]] \
            || die "cortex lineage-attach kit is incomplete: ${kit}/${s} is missing or not executable; mutating nothing"
    done
    printf '%s' "${kit}"
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
# installed + collector tee wired. Both fail loud, mutating nothing. (The kit's
# own six read-only preconditions run inside sidecar-patch.sh per entity — we do
# NOT re-implement them; ADR-0032 decision 3.)
instrument_preflight() {
    component_installed \
        || die "the data-governance component is not installed (namespace ${DG_NAMESPACE} absent) — run 'dg.sh component install' first; mutating nothing"
    if ! tee_is_wired; then
        die "the collector tee is not wired (pipeline ${DG_TEE_PIPELINE} absent in ${DG_COLLECTOR_NAMESPACE}/${DG_COLLECTOR_CONFIGMAP}) — spans would not reach the receiver; run 'dg.sh component install'; mutating nothing"
    fi
}

# ensure_envoy_config <ns>: the kit's envoy sidecar mounts the platform
# `envoy-config` ConfigMap and its `require_envoy_config` precondition REQUIRES
# it in the target namespace — but the kit never CREATES it. The rossoctl Helm
# chart renders `envoy-config` only into the namespaces it manages (team1/team2,
# rossoctl-system); an ad-hoc, kubectl-applied namespace (the travel_advisor
# demo) never got it. Since `dg.sh instrument` is what enrolls such a namespace
# for lineage, it OWNS provisioning envoy-config there so `instrument` is
# self-sufficient on any rossoctl-enabled namespace (issue #184). envoy-config is
# namespace-agnostic, so we copy it verbatim from a chart-managed source ns.
# Idempotent: a no-op when the target already has it. We NEVER fabricate an
# envoy.yaml — if the target lacks it and no source is found, refuse loud.
ensure_envoy_config() {
    local ns="$1"
    # Already present in the target ns → nothing to do.
    if kubectl -n "${ns}" get cm envoy-config >/dev/null 2>&1; then
        return 0
    fi
    # Discover a chart-managed source ns: the explicit override, else the Helm
    # release namespace, else any namespace that carries envoy-config.
    local src="${ENVOY_CONFIG_SOURCE_NS:-}"
    if [[ -z "${src}" ]]; then
        if kubectl -n "${DG_COLLECTOR_NAMESPACE}" get cm envoy-config >/dev/null 2>&1; then
            src="${DG_COLLECTOR_NAMESPACE}"
        else
            src="$(kubectl get cm envoy-config --all-namespaces \
                     -o jsonpath='{.items[0].metadata.namespace}' 2>/dev/null || true)"
        fi
    fi
    [[ -n "${src}" ]] \
        || die "instrument: '${ns}' is missing the platform 'envoy-config' ConfigMap (the kit's envoy sidecar mounts it) and no chart-managed source namespace has one to copy from. Install the rossoctl platform (which renders envoy-config), or set ENVOY_CONFIG_SOURCE_NS=<ns>. Mutating nothing."
    err ">> instrument: '${ns}' lacks the platform envoy-config ConfigMap; copying it from '${src}' (namespace-agnostic)."
    local body
    body="$(kubectl -n "${src}" get cm envoy-config -o jsonpath='{.data.envoy\.yaml}' 2>/dev/null || true)"
    [[ -n "${body}" ]] \
        || die "instrument: source namespace '${src}' has an envoy-config ConfigMap with no 'envoy.yaml' key — refusing to copy an empty config. Mutating nothing."
    printf '%s' "${body}" \
        | kubectl -n "${ns}" create configmap envoy-config --from-file=envoy.yaml=/dev/stdin \
        || die "instrument: failed to create the envoy-config ConfigMap in '${ns}' (copied from '${src}')."
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

# watch_rollout_for_crashloop <ns> <entity> <backout_line> <attach_stdout>:
# after an attach, detect a crash-looping sidecar. On a crash-loop (or the
# already-known failed attach) dump the sidecar container log, surface the kit's
# PRINTED back-out line verbatim, and die loud — halting the whole run so we do
# NOT proceed to the next entity. $4 is captured so we can echo the kit's own
# output back to the operator when we fail after a rollout the kit reported OK.
crashloop_detected() {
    local ns="$1" entity="$2"
    # A crash-looping sidecar shows CrashLoopBackOff in the pods of the entity.
    local out status
    status=0
    out="$(kubectl -n "${ns}" get pods \
        -l "app.kubernetes.io/name=${entity}" \
        -o 'jsonpath={range .items[*].status.containerStatuses[*]}{.state.waiting.reason}{"\n"}{end}' 2>/dev/null)" || status=$?
    if [[ "${status}" -ne 0 ]]; then
        # A failed pod probe is not proof of health; treat as inconclusive (not a
        # crash) — the rollout status is the primary signal. Return non-crash.
        return 1
    fi
    case "${out}" in
        *CrashLoopBackOff*|*RunContainerError*|*CreateContainerError*) return 0 ;;
        *) return 1 ;;
    esac
}

# fail_with_backout <ns> <entity> <sidecar-container> <backout_line>: dump the
# sidecar log, surface the kit's back-out line, and die loud. Used on any
# post-apply failure (crash-loop, or the kit's own rollout wait returning error).
fail_with_backout() {
    local ns="$1" entity="$2" container="$3" backout="$4"
    err ">> instrument: attach FAILED for '${entity}' in '${ns}' — the ${container} sidecar did not come up healthy."
    err ">> instrument: ${container} container log follows:"
    kubectl -n "${ns}" logs "deploy/${entity}" -c "${container}" 2>&1 | sed 's/^/    /' >&2 || true
    if [[ -n "${backout}" ]]; then
        err ">> instrument: back out with the kit's printed reverse-patch line:"
        # Surface the kit's OWN back-out line verbatim (a reverse-patch, NOT a
        # rollout undo — ADR-0032 decision 4).
        err "    ${backout}"
    fi
    die "instrument halted at '${entity}' (crash-looping / failed rollout); did NOT proceed to the remaining entities"
}

# drive_kit_attach <kit> <ns> <entity> <app_container> <app_image>:
# run the kit's sidecar-patch.sh for a kit-owned row (no-sidecar / envoy). When
# <app_container> is non-empty the attach flips propagation on it (APP_CONTAINER
# + APP_IMAGE); empty means capture-only. We capture the kit's stdout so we can
# (a) surface its printed back-out line on a failure, and (b) detect a
# crash-loop after it returns. The kit's own --dry-run=server guard runs inside
# it (pre-apply); our crash-loop watch runs after (post-apply).
drive_kit_attach() {
    local kit="$1" ns="$2" entity="$3" app_container="$4" app_image="$5"
    local out status backout
    err ">> instrument: attaching lineage sidecar to '${entity}' via the #852 kit (sidecar-patch.sh)"
    status=0
    # NOTE: DEPLOY/NAMESPACE/SELF_ID/APP_CONTAINER are read by sidecar-patch.sh
    # itself; APP_IMAGE/OTEL_ENDPOINT/SIDECAR_IMAGE/PROXY_INIT_IMAGE are inherited
    # by attach-lineage.sh. SIDECAR_IMAGE/PROXY_INIT_IMAGE are passed THROUGH from
    # the environment (the operator supplies the locally-built plugin-bearing
    # tags until the cortex PRs land) — we do not default them here.
    # OUTBOUND_PORTS_EXCLUDE is passed THROUGH too (explicitly, so it is not a
    # mere accident of env inheritance): the envoy sidecar's proxy-init redirects
    # ALL outbound ports into envoy, which speaks HTTP — so an app's plaintext
    # NON-HTTP egress (Postgres 5432, SMTP 1025, an object store) must be excluded
    # or it breaks. The kit reads it and hands it to proxy-init; dg.sh does not
    # guess a port list (empty = the kit default), the operator passes what their
    # tools need (issue #184).
    out="$(
        DEPLOY="${entity}" \
        NAMESPACE="${ns}" \
        SELF_ID="${entity}" \
        APP_CONTAINER="${app_container}" \
        APP_IMAGE="${app_image}" \
        OTEL_ENDPOINT="${OTEL_ENDPOINT:-${DG_OTEL_ENDPOINT}}" \
        OUTBOUND_PORTS_EXCLUDE="${OUTBOUND_PORTS_EXCLUDE:-}" \
        "${kit}/sidecar-patch.sh" 2>&1
    )" || status=$?
    # Echo the kit's output to the operator regardless of outcome.
    printf '%s\n' "${out}"
    # The back-out line the kit prints (before its rollout wait, so present even
    # on a failed wait): capture it verbatim to surface on any failure.
    backout="$(printf '%s\n' "${out}" | grep -m1 '^>> back out:' || true)"

    if [[ "${status}" -ne 0 ]]; then
        # The kit's rollout wait (or an apply) failed — a post-apply failure the
        # dry-run could not catch. Surface the back-out line + sidecar log.
        fail_with_backout "${ns}" "${entity}" "envoy-proxy" "${backout}"
    fi
    # The kit reported success; still watch for a crash-loop the rollout status
    # may have missed (a pod that started, then crash-looped).
    if crashloop_detected "${ns}" "${entity}"; then
        fail_with_backout "${ns}" "${entity}" "envoy-proxy" "${backout}"
    fi
    err ">> instrument: '${entity}' attached (kit-owned row)."
}

# instrument_entity <kit> <ns> <entity>: dispatch one entity through the decision
# table. Reuses #183 sidecar detection (detect_sidecar_type).
instrument_entity() {
    local kit="$1" ns="$2" entity="$3"
    local json type
    json="$(get_deployment_json "${ns}" "${entity}")"
    type="$(detect_sidecar_type "${json}")"

    # Fall back to the live Pod when the Deployment TEMPLATE shows no sidecar — a
    # webhook-injected sidecar exists only in the admitted Pod. The fallback's
    # sole purpose here is DETECTION: if the Pod carries a sidecar, classify from
    # the Pod doc so an injected sidecar is correctly SEEN (→ skipped below),
    # instead of being misclassified 'none' and wrongly instrumented (the kit
    # would inject a SECOND sidecar onto an entity that already has one).
    if [[ "${type}" == "none" ]]; then
        local pod_json pod_type
        pod_json="$(get_pod_json "${ns}" "${entity}")"
        pod_type="$(detect_sidecar_type "${pod_json}")"
        if [[ "${pod_type}" != "none" ]]; then
            json="${pod_json}"
            type="${pod_type}"
        fi
    fi

    case "${type}" in
        none)
            # No sidecar → the kit injects an envoy lineage sidecar. That sidecar
            # mounts the platform envoy-config CM, which the kit REQUIRES but does
            # not create — so ensure it exists in this namespace first (dg.sh owns
            # provisioning it for an ad-hoc namespace; #184). Done here (only for a
            # kit-bound entity, and before the slow shim bake) so an all-skipped
            # namespace never needs envoy-config, and a genuinely missing one fails
            # loud before we bake anything. Idempotent across entities.
            ensure_envoy_config "${ns}"
            # For a Python app also bake + attach the propagate-only shim
            # (LINEAGE_PROPAGATE=1 via APP_CONTAINER/APP_IMAGE) — the difference
            # between one trace and N fragments. A self-instrumenting app is
            # refused the shim by the kit's bake interlock (exit 3) → capture-only.
            local app_container app_image otel_image bake_status
            app_container="$(app_container_of "${json}")"
            app_image=""
            if [[ -n "${app_container}" ]]; then
                app_image="$(app_image_of "${json}" "${app_container}")"
            fi
            local shim_container="" shim_image="" bake_out
            if [[ -n "${app_container}" && -n "${app_image}" ]]; then
                err ">> instrument: baking the propagate-only shim for '${entity}' (build-otel-shim.sh ${app_image})"
                bake_status=0
                # Capture the bake's combined output, echo it for the operator,
                # then parse the loaded ref from it. Kept as two steps (not one
                # fragile `grep -m1 | sed` pipeline) so a SIGPIPE from an early
                # grep exit cannot masquerade as a bake failure under pipefail.
                bake_out="$("${kit}/build-otel-shim.sh" "${app_image}" 2>&1)" || bake_status=$?
                printf '%s\n' "${bake_out}" >&2
                otel_image="$(printf '%s\n' "${bake_out}" \
                    | sed -nE 's/.*loaded ([^ ]+) into.*/\1/p' | head -n1)"
                # The kit uses DISTINCT exit codes (build-otel-shim.sh header):
                #   3 = image REFUSED (bake interlock: self-instrumenting /
                #       already-baked / non-runnable-Python / base missing) — the
                #       ONLY sanctioned capture-only case (#184 re-scope decision 5).
                #   4 = ATTESTATION FAILED (verify_inert / verify_propagates) — the
                #       shim was built but does not actually propagate.
                #   other non-zero = a Docker build error, etc.
                # A genuinely broken shim (exit 4 / other) is NOT decoration we can
                # drop: it is the difference between one trace and N fragments, so
                # halt loud rather than silently attach capture-only. Only exit 3
                # legitimately downgrades to capture-only.
                if [[ "${bake_status}" -eq 3 ]]; then
                    # Bake interlock refused (self-instrumenting app, or outside
                    # the shim envelope). Capture-only, NOT a failure.
                    err ">> instrument: shim refused by the kit's bake interlock for '${entity}' (exit 3: self-instrumenting / non-Python) — attaching CAPTURE-ONLY"
                    shim_container=""
                    shim_image=""
                elif [[ "${bake_status}" -ne 0 ]]; then
                    # exit 4 (attestation) / any other non-zero → genuinely broken.
                    die "instrument: the propagate-only shim for '${entity}' failed to build (build-otel-shim.sh exited ${bake_status}) — this is NOT the exit-3 bake interlock: the shim is broken (attestation failed, or a build error), so it cannot be treated as an intentional downgrade. Refusing to attach a lineage sidecar that would lose trace-context propagation. Fix the shim bake (see the build-otel-shim.sh output above) and re-run."
                elif [[ -z "${otel_image}" ]]; then
                    # Exit 0 but nothing loadable parsed out: the bake claimed
                    # success yet produced no ref to attach. Fail loud rather than
                    # silently drop propagation.
                    die "instrument: the propagate-only shim for '${entity}' reported success but no loaded image ref could be parsed from build-otel-shim.sh output (above). Refusing to attach a sidecar that would lose trace-context propagation."
                else
                    shim_container="${app_container}"
                    shim_image="${otel_image}"
                fi
            fi
            drive_kit_attach "${kit}" "${ns}" "${entity}" "${shim_container}" "${shim_image}"
            ;;
        proxy|envoy)
            # Already has a sidecar → OUT OF SCOPE. instrument only wires lineage
            # onto entities with NO sidecar, via the #852 kit (ADR-0032, revised
            # to a kit-only, no-sidecar prerequisite). Retrofitting an existing
            # sidecar is not a supported route: an in-place edit of an
            # operator-owned (webhook-injected) pipeline CM is clobbered on the
            # next roll, and the platform's enforcing sidecar 401s the demo — see
            # docs/proposals/with-sidecar-live-validation-findings.md. Skip and
            # continue; mutate nothing.
            err ">> instrument: '${entity}' already has a ${type} sidecar — skipping."
            err "   instrument only wires lineage onto entities with NO sidecar (via the #852 kit)."
            return 0
            ;;
        *)
            die "instrument: could not classify the sidecar of '${entity}' in '${ns}' (got '${type}')"
            ;;
    esac
}

# namespace_instrument <ns> [<entity>]: the non-reversible activation verb. Runs
# the dg.sh-side preflights (kit resolvable, component installed, tee wired),
# enumerates the targeted entities (loud on a bad <entity>), and drives each
# through the decision table. A crash-loop on any entity halts the whole run.
namespace_instrument() {
    local ns="$1"
    local entity="${2:-}"
    [[ -n "${ns}" ]] || die "namespace_instrument: namespace is required"
    require_kubectl
    command -v python3 >/dev/null 2>&1 \
        || die "'python3' is required for sidecar detection (namespace instrument); install it"

    # Preflight 1: the kit must be resolvable (refuse, mutate nothing).
    local kit
    kit="$(resolve_kit_dir)"

    # Preflight 2: the consumer side must be up (component installed + tee wired).
    instrument_preflight

    # Enumerate up front so a bad <entity> fails loud before any mutation.
    local names
    names="$(enumerate_entities "${ns}" "${entity}")"
    if [[ -z "${names}" ]]; then
        err "namespace ${ns}: no agents/tools found — nothing to instrument"
        return 0
    fi

    local name
    while IFS= read -r name; do
        [[ -n "${name}" ]] || continue
        instrument_entity "${kit}" "${ns}" "${name}"
    done <<< "${names}"

    err ">> instrument: done for namespace '${ns}'."
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
            --cortex-local-path)
                [[ $# -ge 2 ]] || usage_error "--cortex-local-path requires a <dir> argument"
                CORTEX_LOCAL_PATH="$2"
                export CORTEX_LOCAL_PATH
                shift 2
                ;;
            --cortex-local-path=*)
                CORTEX_LOCAL_PATH="${1#*=}"
                export CORTEX_LOCAL_PATH
                shift
                ;;
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
