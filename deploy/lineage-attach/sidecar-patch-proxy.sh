#!/usr/bin/env bash
# sidecar-patch-proxy.sh — attach the AUTH-FREE lineage PROXY sidecar to an
# EXISTING Deployment. The proxy sibling of sidecar-patch.sh (the envoy applier).
#
# The live applier for the ADR-0033 proxy path. attach-lineage-proxy.sh is
# stdout-only (it prints EMIT=cm / patch / undo and never touches the cluster);
# this script is what actually applies those objects, exactly as sidecar-patch.sh
# does for the envoy path: generate all three before the first write, dry-run the
# merged patch (the version guard), apply the ConfigMap then the patch, print the
# reverse-patch back-out line, and wait for the rollout.
#
# It differs from sidecar-patch.sh only where the proxy path differs:
#   * NO envoy-config precondition — the auth-free proxy mounts no envoy-config
#     (sidecar-patch.sh's require_envoy_config is dropped here);
#   * the collision guards range the PROXY's own container names (proxy-init /
#     authbridge-proxy — both native initContainers), its volume
#     (authbridge-runtime), and its ports (8081 forward, 8082 transparent,
#     9091 ab-health);
#   * it forwards OUTBOUND_PORTS_INCLUDE (the include-only allowlist) through the
#     environment to the generator — NOT the envoy path's OUTBOUND_PORTS_EXCLUDE
#     denylist.
#
# Not durable: same as sidecar-patch.sh — the owner keeps owning the object and a
# platform rewrite (an operator reconcile, a UI redeploy) silently drops the
# patch. Re-run after any platform-side change, or keep the attachment in your own
# manifests. To back out: the reverse-patch line this script prints before the
# rollout wait, then delete the CM. (A `rollout undo` is NOT the back-out: it
# restores a whole earlier pod template — and the proxy is a native sidecar, so an
# undo would take too much.)
#
# Usage:
#   DEPLOY=research-agent NAMESPACE=travel-advisor \
#     SIDECAR_IMAGE=docker.io/library/authbridge:lineage ./sidecar-patch-proxy.sh
#   DEPLOY=my-agent APP_CONTAINER=agent \
#     APP_IMAGE=docker.io/library/my-agent-otel:latest ./sidecar-patch-proxy.sh
#
# Env — read here:
#   DEPLOY         target Deployment (required)
#   NAMESPACE      default team1
#   SELF_ID        lineage identity (default: DEPLOY)
#   APP_CONTAINER  the app container to switch propagation on (optional)
# Env — inherited by attach-lineage-proxy.sh and validated there (see its header):
#   APP_IMAGE, OTEL_ENDPOINT, CAPTURE_IO, MAX_PAYLOAD_BYTES, SIDECAR_IMAGE,
#   PROXY_INIT_IMAGE, NO_EMIT,
#   OUTBOUND_PORTS_INCLUDE (the include-only egress allowlist; default 8080,8000 —
#                   A2A + MCP. Only these dports are redirected into the proxy;
#                   everything else stays direct).
#
# Requires in the namespace: the sidecar + proxy-init images resolvable from the
# cluster. NO envoy-config (the auth-free proxy mounts none).
#
# Structure mirrors sidecar-patch.sh: read_inputs → preconditions (read-only;
# each returns or exits) → note_capture_only → apply (the only cluster writes).
# gen() is the one bridge to the generator.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

read_inputs() {
  DEPLOY="${DEPLOY:?usage: DEPLOY=<deployment> [NAMESPACE=team1] [SELF_ID=<id>] [APP_CONTAINER=<name> [APP_IMAGE=<ref>]] [SIDECAR_IMAGE=<ref> PROXY_INIT_IMAGE=<ref>] [OUTBOUND_PORTS_INCLUDE=ports] sidecar-patch-proxy.sh}"
  NAMESPACE="${NAMESPACE:-team1}"
  SELF_ID="${SELF_ID:-$DEPLOY}"
  APP_CONTAINER="${APP_CONTAINER:-}"
}

require_deployment() {
  kubectl get deploy -n "$NAMESPACE" "$DEPLOY" >/dev/null
}

refuse_name_collision() {
  # Lists merge by NAME: a target already carrying either name is merged over,
  # not added beside. Both proxy-init and authbridge-proxy land in initContainers
  # (the native-sidecar shape), so range initContainers + containers.
  local names n
  names="$(kubectl get deploy -n "$NAMESPACE" "$DEPLOY" \
    -o jsonpath='{range .spec.template.spec.initContainers[*]}{.name}{" "}{end}{range .spec.template.spec.containers[*]}{.name}{" "}{end}')"
  for n in proxy-init authbridge-proxy; do
    case " $names " in
      *" $n "*)
        echo "error: $DEPLOY already has a container named $n — the patch would merge over it, not add beside it" >&2
        echo "  (an operator-injected sidecar, another mesh's init, or an earlier attach) — refusing" >&2
        exit 1 ;;
    esac
  done
}

refuse_port_collision() {
  # An existing sidecar or the app on a proxy port. The auth-free proxy binds
  # 8081 (forward), 8082 (transparent redirect target) and 9091 (ab-health).
  # initContainers included: the proxy is a native sidecar holding its ports.
  local declared_ports p
  declared_ports="$(kubectl get deploy -n "$NAMESPACE" "$DEPLOY" \
    -o jsonpath='{range .spec.template.spec.initContainers[*].ports[*]}{.containerPort}{" "}{end}{range .spec.template.spec.containers[*].ports[*]}{.containerPort}{" "}{end}')"
  for p in 8081 8082 9091; do
    case " $declared_ports " in
      *" $p "*)
        echo "error: $DEPLOY already declares containerPort $p, which the lineage proxy sidecar binds" >&2
        echo "  (an operator-injected sidecar, or the app itself on that port) — refusing to patch over it" >&2
        exit 1 ;;
    esac
  done
}

refuse_volume_collision() {
  # Volumes merge by NAME too: an existing authbridge-runtime volume would have
  # its source silently repointed at our ConfigMap. The proxy mounts ONLY
  # authbridge-runtime (no envoy-config), so that is the one name to guard.
  local volumes v
  volumes="$(kubectl get deploy -n "$NAMESPACE" "$DEPLOY" \
    -o jsonpath='{range .spec.template.spec.volumes[*]}{.name}{" "}{end}')"
  for v in authbridge-runtime; do
    case " $volumes " in
      *" $v "*)
        echo "error: $DEPLOY already has a volume named $v — the patch would repoint its source, not add beside it" >&2
        echo "  (the owner mounts that volume somewhere; the merge would silently change what the mount serves) — refusing" >&2
        exit 1 ;;
    esac
  done
}

require_app_container() {
  # A strategic merge ADDS a stub container for an unknown name instead of
  # failing — so the name must exist. The generator cannot check this.
  [ -n "$APP_CONTAINER" ] || return 0
  local containers
  containers="$(kubectl get deploy -n "$NAMESPACE" "$DEPLOY" \
    -o jsonpath='{range .spec.template.spec.containers[*]}{.name}{" "}{end}')"
  case " $containers " in
    *" $APP_CONTAINER "*) ;;
    *)
      echo "error: deploy/$DEPLOY has no container named '$APP_CONTAINER' (it has: ${containers% })" >&2
      echo "  — refusing: the patch would ADD a stub container by that name instead of failing" >&2
      exit 1 ;;
  esac
}

note_capture_only() {
  [ -z "$APP_CONTAINER" ] || return 0
  echo "NOTE: the proxy records every hop; whether $DEPLOY's outbound hops attribute to" >&2
  echo "      their inbound depends on the app carrying the trace context (traceparent +" >&2
  echo "      tracestate) from inbound to outbound itself (its own instrumentation, or the" >&2
  echo "      baked shim + APP_CONTAINER=<name>)." >&2
}

gen() {  # $1 = EMIT mode; the other knobs reach the generator through the environment
  EMIT="$1" NAME="$DEPLOY" SELF_ID="$SELF_ID" NAMESPACE="$NAMESPACE" \
    "${SCRIPT_DIR}/attach-lineage-proxy.sh"
}

apply() {
  # All three objects — ConfigMap, patch, and its reverse — are generated before
  # the first write, so a generator refusal stops the script with nothing applied.
  local cm patch undo restored_image cm_existed
  cm="$(gen cm)"
  patch="$(gen patch)"
  # The image is the one piece the patch REPLACES rather than adds, so the
  # reverse patch needs a value, not a delete: capture the ref the owner runs
  # now, before the patch swaps it. Every other added piece un-merges by name.
  restored_image=""
  if [ -n "${APP_IMAGE:-}" ]; then
    restored_image="$(kubectl get deploy -n "$NAMESPACE" "$DEPLOY" \
      -o jsonpath="{.spec.template.spec.containers[?(@.name=='$APP_CONTAINER')].image}")"
    [ -n "$restored_image" ] || {
      echo "error: could not read the current image of container '$APP_CONTAINER' — nothing was applied" >&2
      exit 1
    }
  fi
  undo="$(EMIT=undo NAME="$DEPLOY" NAMESPACE="$NAMESPACE" \
          APP_IMAGE= RESTORE_IMAGE="$restored_image" "${SCRIPT_DIR}/attach-lineage-proxy.sh")"
  # The server validates the FULLY MERGED object without persisting it, so every
  # rejection class — an invalid merged field, an admission webhook, RBAC missing
  # deployments/patch, and a cluster too old for native sidecars — fails here,
  # before the first write. This is the version guard too: on k8s < 1.29 the
  # server rejects the native-sidecar fields, so no version parsing is needed.
  local dryrun_err
  if ! dryrun_err="$(kubectl patch deploy "$DEPLOY" -n "$NAMESPACE" --type strategic \
        --patch "$patch" --dry-run=server -o name 2>&1)"; then
    echo "error: the server rejected the merged patch — nothing was applied:" >&2
    printf '%s\n' "$dryrun_err" >&2
    case "$dryrun_err" in
      *startupProbe*|*restartPolicy*|*"init container"*)
        echo "  hint: rejection of the native-sidecar fields (startupProbe / restartPolicy on an init" >&2
        echo "        container) likely means the cluster is older than k8s 1.29, which the proxy sidecar requires." >&2 ;;
    esac
    exit 1
  fi
  # On a re-attach the ConfigMap already exists and running pods project it:
  # the failure compensation below may delete only what THIS run created.
  cm_existed=0
  if kubectl get cm -n "$NAMESPACE" "authbridge-lineage-config-$DEPLOY" >/dev/null 2>&1; then
    cm_existed=1
  fi
  kubectl apply -f - <<<"$cm"
  kubectl patch deploy "$DEPLOY" -n "$NAMESPACE" --type strategic --patch "$patch" || {
    # Only a failure the dry-run could not predict lands here (e.g. a 409 from a
    # concurrent write). Nothing else was written this run except, possibly, the
    # ConfigMap — remove it only if this run created it.
    [ "$cm_existed" = "1" ] || kubectl delete cm -n "$NAMESPACE" "authbridge-lineage-config-$DEPLOY"
    exit 1
  }
  # Said before the wait: a rollout that never completes still needs this line.
  echo ">> back out: kubectl -n $NAMESPACE patch deploy/$DEPLOY --type strategic -p '$undo' && kubectl -n $NAMESPACE delete cm authbridge-lineage-config-$DEPLOY"
  [ -z "$restored_image" ] || \
    echo ">>   (the patch restores image $restored_image — drop its \"image\" field if the app is re-imaged after this attach)"
  # A rollout that times out is deliberately left patched (unlike the patch-apply
  # failure above, which auto-cleans): Kubernetes holds the blast — the back-out
  # line printed just above is the clean way out.
  kubectl rollout status -n "$NAMESPACE" "deploy/$DEPLOY" --timeout=180s
  echo ">> lineage proxy sidecar attached to deploy/$DEPLOY (self_id=$SELF_ID, ns=$NAMESPACE)"
}

preconditions() {  # read-only: each returns or exits — nothing is applied yet
  require_deployment
  refuse_name_collision
  refuse_port_collision
  refuse_volume_collision
  require_app_container
}

main() {
  [ $# -eq 0 ] || { echo "error: sidecar-patch-proxy.sh takes no arguments — every input is an environment variable (see the header)" >&2; exit 2; }
  read_inputs        # DEPLOY required; the rest defaulted or inherited
  preconditions      # read-only checks that can only stop the script
  note_capture_only  # no APP_CONTAINER → say what that means, once
  apply              # generate all three, then the only cluster writes: cm → patch → rollout
}
main "$@"
