#!/usr/bin/env bash
# attach-lineage-proxy.sh — the PROXY generator (ADR-0033 Decision 2). The
# proxy-sidecar sibling of attach-lineage.sh (the envoy generator): given a
# Deployment's NAME, print ONE of the two objects (or the reverse) that attach an
# AUTH-FREE, lineage-only proxy sidecar to it:
#
#   EMIT=patch (default)  strategic-merge patch adding the sidecar pieces —
#                         proxy-init as an initContainer + authbridge-proxy as a
#                         regular container (the proxy-sidecar is NOT a native
#                         initContainer — it is not gating the app the way envoy
#                         does), one runtime-config volume — and, with
#                         APP_CONTAINER, the propagation switch on the app's own
#                         container.
#   EMIT=cm               the per-app plugin ConfigMap the sidecar mounts
#                         (mode: proxy-sidecar, parser chain + lineage-telemetry).
#   EMIT=undo             the reverse of the patch: one line of strategic-merge
#                         JSON deleting, by name, exactly what the patch adds
#                         (proxy-init initContainer, authbridge-proxy container,
#                         the volume, and the app's LINEAGE_PROPAGATE env). The
#                         image is restored via RESTORE_IMAGE; APP_IMAGE is
#                         refused in this mode.
#
# Why a proxy sidecar and not envoy: the demo's entities are bare, and a
# lineage-only proxy captures their plaintext HTTP hops (A2A / MCP) without the
# platform's enforcing token-exchange/jwt proxy that would 401 the demo's
# unauthenticated calls, and without recreating pods into a namespace-wide envoy
# mode. It carries ONLY the lineage-telemetry plugin + the parsers — no
# jwt-validation, no token-exchange, no mTLS (so no SPIRE), no envoy-config.
#
# Egress capture is TRANSPARENT, via the vendored init-iptables.sh in its NEW
# include-only allowlist mode: proxy-init REDIRECTs ONLY the A2A (8080) + MCP
# (8000) egress into the proxy's transparent outbound listener (8082), and leaves
# every other port (Postgres, SMTP, the TLS LLM tunnel) direct — fail-safe. The
# app container is NOT relocated and needs no HTTP_PROXY: this is the ADR-0033
# transparent design, not the rejected reverse-proxy-relocation prototype.
#
# Propagation: capture alone cannot attribute an app's outbound calls to the
# inbound that caused them — only code inside the request can carry the trace
# context from the inbound to the outbound calls it makes. For an uninstrumented
# Python app, bake the shim first (build-otel-shim.sh), then pass APP_CONTAINER
# (+ APP_IMAGE): the patch sets LINEAGE_PROPAGATE=1 on that container, which
# wakes the baked hook. Without APP_CONTAINER the patch is capture-only and the
# app container is not touched.
#
# Usage:
#   NAME=research-agent ./attach-lineage-proxy.sh                    # the patch
#   NAME=research-agent EMIT=cm ./attach-lineage-proxy.sh            # the ConfigMap
#   NAME=research-agent APP_CONTAINER=agent \
#     APP_IMAGE=docker.io/library/research-agent-otel:latest ./attach-lineage-proxy.sh
#
# Variables:
#   NAME            (required) the target Deployment; also names the ConfigMap
#                   (authbridge-lineage-config-NAME) and defaults SELF_ID
#   NAMESPACE       default team1
#   SELF_ID         lineage identity on every span (default NAME)
#   APP_CONTAINER   the app container to set LINEAGE_PROPAGATE=1 on. MUST name an
#                   existing container: a strategic merge ADDS a stub for an
#                   unknown name. Checked live by the applier, not here.
#   APP_IMAGE       needs APP_CONTAINER: the -otel image to set on it
#                   (EMIT=patch only; EMIT=undo refuses it — see RESTORE_IMAGE)
#   RESTORE_IMAGE   EMIT=undo only, needs APP_CONTAINER: the pre-attach image ref
#                   the reverse patch sets back on the app container
#   OTEL_ENDPOINT   OTLP/gRPC target for the plugin's spans (default: the
#                   platform collector). Plain gRPC unless it starts with https://.
#   CAPTURE_IO      false (default) | true — whether the spans carry the parsed
#                   content (prompts, tool arguments, messages). PII-bearing.
#   MAX_PAYLOAD_BYTES  cap on a captured value (plugin default 4096 when unset;
#                   -1 = attach whole). Only meaningful with CAPTURE_IO=true.
#   OUTBOUND_PORTS_INCLUDE  the include-only egress allowlist proxy-init programs
#                   (default 8080,8000 — A2A + MCP). Only these dports are
#                   redirected into the proxy; everything else stays direct.
#   SIDECAR_IMAGE   the auth-free proxy-sidecar image (authbridge, proxy mode).
#                   UNTIL A RELEASE CARRIES lineage-telemetry AND the v1.7.0
#                   namespace key (cortex #761 + the v1.7.0 producer), EMIT=patch
#                   REFUSES the published default: the sidecar crashloops on the
#                   unknown plugin name and the app never attaches. Build from a
#                   tree that has the plugin and point this at your tag (RECIPE.md
#                   step 1); or NO_EMIT=1 for a parsers-only sidecar on a stock
#                   image (the parsers predate the plugin).
#   PROXY_INIT_IMAGE  default ghcr.io/rossoctl/cortex/proxy-init:latest
#   NO_EMIT=1       omit the plugin entry: the sidecar proxies, emits nothing
#                   (parsers alone are legal). The A/B baseline.
#   EMIT            patch (default) | cm | undo
#
# Structure mirrors attach-lineage.sh: parse_inputs validates EVERY knob (all
# refusals live there); build_* each assemble one optional fragment into a
# global; the fragment functions are the single source of the sidecar YAML;
# emit() dispatches. Stdout only; this script never touches the cluster.
set -euo pipefail

# The published default proxy image, refused by EMIT=patch until a release
# carries the plugin (see SIDECAR_IMAGE note). Named once so the guard and the
# default stay in step.
PUBLISHED_SIDECAR_DEFAULT="ghcr.io/rossoctl/cortex/authbridge:latest"

# Every free-form value lands in a double-quoted YAML or JSON scalar (only '"'
# and '\' are special) and, via EMIT=undo, inside a single-quoted shell line
# ("'" is). Refusing those three plus whitespace/control chars is exactly
# sufficient (identical to attach-lineage.sh's yaml_safe).
yaml_safe() {  # $1 = what it is (for the error), $2 = the value
  local unsafe=$'"\'\\'
  case "$2" in
    *["$unsafe"]*|*[[:space:][:cntrl:]]*)
      printf "error: %s '%s' contains whitespace, a control character or one of %s, which this script cannot quote safely\n" "$1" "$2" "$unsafe" >&2
      exit 2 ;;
  esac
}

# validate_port_list <name> <value> — a comma-separated port list (1-65535, no
# leading zeros), the shape proxy-init hands to iptables. Empty is allowed.
validate_port_list() {
  local name="$1" value="$2" port
  [[ "$value" =~ ^([1-9][0-9]{0,4}(,[1-9][0-9]{0,4})*)?$ ]] \
    || { echo "error: $name='$value' is not a comma-separated list of ports (1-65535, no leading zeros)" >&2; exit 2; }
  for port in ${value//,/ }; do
    if [ "$port" -gt 65535 ]; then
      echo "error: $name has '$port', which is not a port (1-65535)" >&2; exit 2
    fi
  done
}

parse_inputs() {
  # Knobs of the rejected reverse-proxy-relocation prototype (FRONT_PORT /
  # BACK_PORT / HTTP_PROXY): a caller passing one is running the scratch recipe,
  # not this ADR-0033 transparent generator — refuse loudly rather than ignore.
  local stale
  for stale in FRONT_PORT BACK_PORT FWD_PORT DROP_OPAQUE DROP_MCP_MGMT; do
    if [ -n "${!stale:-}" ]; then
      echo "error: $stale is not a knob — it belonged to the rejected reverse-proxy-relocation" >&2
      echo "       prototype. This generator captures egress TRANSPARENTLY via the include-only" >&2
      echo "       iptables allowlist (OUTBOUND_PORTS_INCLUDE); the app is never relocated." >&2
      exit 2
    fi
  done

  EMIT="${EMIT:-patch}"
  NAME="${NAME:?set NAME}"
  NAMESPACE="${NAMESPACE:-team1}"
  # NAME (RFC 1123 subdomain) and NAMESPACE (DNS label) are interpolated bare.
  # 227 = 253 minus the ConfigMap name's prefix (authbridge-lineage-config-).
  if [ "${#NAME}" -gt 227 ] \
     || ! [[ "$NAME" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$ ]]; then
    echo "error: NAME='$NAME' must be a lowercase RFC 1123 subdomain of at most 227 chars (253 minus the ConfigMap prefix)" >&2; exit 2
  fi
  if ! [[ "$NAMESPACE" =~ ^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$ ]]; then
    echo "error: NAMESPACE='$NAMESPACE' is not a DNS label (lowercase alphanumerics and '-', max 63)" >&2; exit 2
  fi
  case "$EMIT" in
    patch|cm|undo) ;;
    *) echo "error: EMIT must be patch|cm|undo (got '$EMIT')" >&2; exit 2 ;;
  esac
  SELF_ID="${SELF_ID:-$NAME}"
  APP_CONTAINER="${APP_CONTAINER:-}"
  APP_IMAGE="${APP_IMAGE:-}"
  RESTORE_IMAGE="${RESTORE_IMAGE:-}"
  # The two image knobs are mode-bound, and confusing them is destructive:
  # regenerating an undo with the attach line's APP_IMAGE would "restore" the
  # -otel image. Refuse the wrong knob loudly rather than reinterpret it.
  if [ "$EMIT" = "undo" ] && [ -n "$APP_IMAGE" ]; then
    echo "error: APP_IMAGE is the image to INSTALL (EMIT=patch) — under EMIT=undo pass the" >&2
    echo "       pre-attach ref to restore as RESTORE_IMAGE instead" >&2
    exit 2
  fi
  if [ "$EMIT" != "undo" ] && [ -n "$RESTORE_IMAGE" ]; then
    echo "error: RESTORE_IMAGE is an EMIT=undo knob (the pre-attach ref the reverse patch restores)" >&2
    exit 2
  fi
  OUTBOUND_PORTS_INCLUDE="${OUTBOUND_PORTS_INCLUDE:-8080,8000}"
  validate_port_list OUTBOUND_PORTS_INCLUDE "$OUTBOUND_PORTS_INCLUDE"
  OTEL_ENDPOINT="${OTEL_ENDPOINT:-otel-collector.rossoctl-system.svc.cluster.local:4317}"
  CAPTURE_IO="${CAPTURE_IO:-false}"
  case "$CAPTURE_IO" in
    true|false) ;;
    *) echo "error: CAPTURE_IO must be true|false (got '$CAPTURE_IO')" >&2; exit 2 ;;
  esac
  # Capture carries PII; the plugin sends it plain gRPC unless OTEL_ENDPOINT is
  # https://. Warn (not refuse) on stdout's sibling stderr, once (gated to cm).
  if [ "$EMIT" = "cm" ] && [ "$CAPTURE_IO" = "true" ]; then
    case "$OTEL_ENDPOINT" in
      https://*) ;;
      *) echo "NOTE: CAPTURE_IO=true sends parsed content (PII) to ${OTEL_ENDPOINT} over plain gRPC." >&2
         echo "      Use an https:// OTEL_ENDPOINT for TLS if that endpoint leaves the cluster." >&2 ;;
    esac
  fi
  MAX_PAYLOAD_BYTES="${MAX_PAYLOAD_BYTES:-}"
  [[ "$MAX_PAYLOAD_BYTES" =~ ^(-1|[1-9][0-9]*)?$ ]] \
    || { echo "error: MAX_PAYLOAD_BYTES must be a positive integer or -1 (got '$MAX_PAYLOAD_BYTES')" >&2; exit 2; }
  # Published images by default; point at local tags when building from source.
  SIDECAR_IMAGE="${SIDECAR_IMAGE:-$PUBLISHED_SIDECAR_DEFAULT}"
  PROXY_INIT_IMAGE="${PROXY_INIT_IMAGE:-ghcr.io/rossoctl/cortex/proxy-init:latest}"
  NO_EMIT="${NO_EMIT:-0}"
  case "$NO_EMIT" in
    0|1) ;;
    *) echo "error: NO_EMIT must be 0|1 (got '$NO_EMIT')" >&2; exit 2 ;;
  esac
  # The published default proxy image predates the plugin (cortex #761) and the
  # v1.7.0 namespace key: plugins.Build fails closed on the unknown name and the
  # sidecar crashloops. Emitting a patch that pins it is a foreseeable misuse —
  # refuse it. EMIT=patch only: the ConfigMap and the reverse patch carry no
  # image, and a back-out must never be blocked. Delete this guard when a release
  # image carries lineage-telemetry at wire contract v1.7.0.
  if [ "$EMIT" = "patch" ] && [ "$NO_EMIT" != "1" ] \
     && [ "$SIDECAR_IMAGE" = "$PUBLISHED_SIDECAR_DEFAULT" ]; then
    echo "error: SIDECAR_IMAGE is the published default, which does not carry lineage-telemetry" >&2
    echo "  at wire contract v1.7.0 yet (cortex #761) — the sidecar would crashloop on it. Build one" >&2
    echo "  from a tree that has the plugin and set SIDECAR_IMAGE (RECIPE.md step 1), or NO_EMIT=1" >&2
    echo "  for a parsers-only sidecar on the stock image." >&2
    exit 2
  fi

  local v
  for v in SELF_ID OTEL_ENDPOINT APP_IMAGE RESTORE_IMAGE SIDECAR_IMAGE PROXY_INIT_IMAGE; do
    yaml_safe "$v" "${!v}"
  done
  # A container name is a DNS label; it is interpolated bare.
  if [ -n "$APP_CONTAINER" ] && ! [[ "$APP_CONTAINER" =~ ^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$ ]]; then
    echo "error: APP_CONTAINER='$APP_CONTAINER' is not a valid container name (DNS label)" >&2; exit 2
  fi
  if [ -n "$APP_IMAGE" ] && [ -z "$APP_CONTAINER" ]; then
    echo "error: APP_IMAGE needs APP_CONTAINER — the image lands on a container the patch must name" >&2; exit 2
  fi
  if [ -n "$RESTORE_IMAGE" ] && [ -z "$APP_CONTAINER" ]; then
    echo "error: RESTORE_IMAGE needs APP_CONTAINER — the ref lands on a container the patch must name" >&2; exit 2
  fi
}

build_plugin_entry() {
  # The lineage-telemetry plugin entry, one variable so NO_EMIT is a single
  # point. Empty → the parsers stay and the sidecar is a pure proxy that emits
  # nothing. namespace_file is REQUIRED by wire contract v1.7.0 §6 — the producer
  # refuses to start without a namespace/namespace_file. We set namespace_file to
  # the pod's projected serviceaccount namespace, the one source that stays
  # correct in a config templated or copied across namespaces.
  lineage_plugin=""
  if [ "$NO_EMIT" != "1" ]; then
    lineage_plugin='
          - name: lineage-telemetry
            config:
              otel_endpoint: "'"${OTEL_ENDPOINT}"'"
              capture_io: '"${CAPTURE_IO}"'
              self_id: "'"${SELF_ID}"'"
              namespace_file: "/var/run/secrets/kubernetes.io/serviceaccount/namespace"'
    # The plugin's own default applies when unset; an explicit value is emitted.
    [ -z "$MAX_PAYLOAD_BYTES" ] || lineage_plugin="${lineage_plugin}
              max_payload_bytes: ${MAX_PAYLOAD_BYTES}"
  fi
}

build_app_patch() {
  # The propagation switch. `containers` and `env` both merge by name, so this
  # sets exactly LINEAGE_PROPAGATE (and the image, if given) on the owner's
  # container and nothing else. This is the ONLY thing the patch adds under
  # `containers:` — the sidecar is a native initContainer now (see emit_patch),
  # so the app stays the pod's sole regular container.
  app_patch=""
  if [ -n "$APP_CONTAINER" ]; then
    app_patch="
        - name: ${APP_CONTAINER}"
    if [ -n "$APP_IMAGE" ]; then
      app_patch="${app_patch}
          image: \"${APP_IMAGE}\""
    fi
    app_patch="${app_patch}
          env:
            - { name: LINEAGE_PROPAGATE, value: \"1\" }"
  fi
}

# ---- the sidecar fragments (the single source of every sidecar YAML byte) ----

sidecar_container() {  # the AUTH-FREE authbridge-proxy NATIVE sidecar (8-space list-item indent)
  cat <<EOF
        # authbridge-proxy in proxy-sidecar mode, carrying ONLY the parsers +
        # lineage-telemetry (auth-free — no jwt-validation / token-exchange /
        # mTLS, so it does not 401 the demo's unauthenticated MCP/A2A calls, and
        # needs no SPIRE). MUST run as UID 1337: proxy-init exempts that uid from
        # the outbound redirect so the proxy's own re-originated egress is not
        # captured into a loop. Egress reaches it transparently on 8082 (the
        # include-only iptables REDIRECT target); the forward proxy on 8081 is
        # distinct and idle here (no HTTP_PROXY). Mounts ONLY the runtime config
        # — no envoy-config, no shared-data/SPIRE volumes.
        #
        # A NATIVE sidecar (initContainer + restartPolicy: Always), for the same
        # reason the envoy path is: proxy-init has already REDIRECTed the app's
        # A2A/MCP egress to :8082 before the app starts, and an app that makes its
        # first peer/tool call at process start (peer discovery — the demo agents
        # do exactly this, with NO retry) would hit the redirect before the proxy
        # is listening: connection refused, agent boots with 0 peers, silently. As
        # a native sidecar with a startupProbe on the health port, the kubelet
        # holds the app container until this one is accepting, with no app change.
        # Needs Kubernetes >= 1.29 (native sidecars); an older cluster rejects the
        # startupProbe on an initContainer without restartPolicy: Always, loud.
        - name: authbridge-proxy
          image: "${SIDECAR_IMAGE}"
          imagePullPolicy: IfNotPresent
          restartPolicy: Always
          args: ["--config", "/etc/authbridge/config.yaml"]
          securityContext:
            runAsNonRoot: true
            runAsUser: 1337
            runAsGroup: 1337
            allowPrivilegeEscalation: false
            seccompProfile: { type: RuntimeDefault }
            capabilities:
              drop: ["ALL"]
          ports:
            - { containerPort: 8081, name: forward-proxy }
            - { containerPort: 8082, name: transparent }
            - { containerPort: 9091, name: ab-health }
          # startupProbe ONLY (no readinessProbe): as a native sidecar the
          # startupProbe gates the app's start until the proxy is accepting — the
          # one property we need. A readinessProbe would fold this
          # purely-observational capture sidecar into the pod's overall readiness,
          # so a proxy that flapped ready (e.g. a plugin reload) would drop the
          # whole pod out of its Service endpoints — an availability regression for
          # an app the sidecar only observes.
          startupProbe:
            httpGet: { path: /healthz, port: 9091 }
            periodSeconds: 1
            failureThreshold: 60
          resources:
            requests: { cpu: 50m, memory: 64Mi }
            limits: { cpu: 500m, memory: 512Mi }
          volumeMounts:
            - { name: authbridge-runtime, mountPath: /etc/authbridge, readOnly: true }
EOF
}

proxy_init_container() {  # iptables in include-only allowlist mode
  cat <<EOF
        # Programs the pod's iptables once and exits. Root + exactly
        # NET_ADMIN/NET_RAW. redirect mode with OUTBOUND_PORTS_INCLUDE: only the
        # allowlisted dports (A2A 8080 + MCP 8000 by default) are REDIRECTed to
        # the proxy's transparent outbound listener (PROXY_PORT=8082); everything
        # else passes direct (fail-safe). PROXY_UID 1337 is RETURNed to avoid a
        # self-loop. POD_IP + POD_IPS (Downward API): proxy-init prefers the
        # plural for full dual-stack redirect and falls back to the singular.
        - name: proxy-init
          image: "${PROXY_INIT_IMAGE}"
          imagePullPolicy: IfNotPresent
          securityContext:
            runAsNonRoot: false
            runAsUser: 0
            allowPrivilegeEscalation: false
            seccompProfile: { type: RuntimeDefault }
            capabilities:
              drop: ["ALL"]
              add: ["NET_ADMIN", "NET_RAW"]
          resources:
            requests: { cpu: 10m, memory: 16Mi }
            limits: { cpu: 100m, memory: 64Mi }
          env:
            - { name: MODE, value: "redirect" }
            - { name: PROXY_UID, value: "1337" }
            - { name: PROXY_PORT, value: "8082" }
            - name: OUTBOUND_PORTS_INCLUDE
              value: "${OUTBOUND_PORTS_INCLUDE}"
            - name: POD_IP
              valueFrom:
                fieldRef:
                  fieldPath: status.podIP
            - name: POD_IPS
              valueFrom:
                fieldRef:
                  fieldPath: status.podIPs
EOF
}

sidecar_volumes() {  # ONLY the per-app runtime ConfigMap (no envoy-config, no auth vols)
  cat <<EOF
        - name: authbridge-runtime
          configMap:
            name: authbridge-lineage-config-${NAME}
            items:
              - { key: config.yaml, path: config.yaml }
EOF
}

# ---- the two emittable objects ----

emit_configmap() {
  cat <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: authbridge-lineage-config-${NAME}
  namespace: ${NAMESPACE}
data:
  config.yaml: |
    mode: proxy-sidecar
    # Only the forward role runs: this sidecar captures egress, it does not front
    # the app (no reverse proxy, no app-port relocation). The forward role's
    # preset binds the transparent outbound listener (8082) — the include-only
    # iptables REDIRECT target — sharing the forward proxy's outbound pipeline, so
    # the captured hops run the same plugin chain.
    listener:
      roles: [forward]
      forward_proxy_addr: ":8081"
      transparent_proxy_addr: ":8082"
    # Turn the session-events store (and its API) OFF. This is an egress-only
    # capture proxy: it installs NO inbound iptables chain, so nothing gates the
    # pod's inbound ports. The session API (default :9094) is UNAUTHENTICATED and
    # serves raw captured content (prompts/messages, PII under capture_io) — with
    # no inbound gate it would be readable by any pod that can reach this pod's IP.
    # The lineage flow does not use the session store (spans go to OTEL_ENDPOINT),
    # so disable it entirely; main.go skips the API server when the store is nil.
    session:
      enabled: false
    # Auth-free capture-only: NO jwt-validation / token-exchange, NO mtls block
    # (so no SPIRE needed). The same parser chain in both directions — an app's
    # entry protocol is not knowable from the attach side, and parsers are
    # content-gated and not mutually exclusive. Parsers MUST precede
    # lineage-telemetry (RequiresAny) or the binary fails at startup.
    pipeline:
      inbound:
        plugins:
          - name: a2a-parser
          - name: mcp-parser
          - name: inference-parser${lineage_plugin}
      outbound:
        plugins:
          - name: a2a-parser
          - name: mcp-parser
          - name: inference-parser${lineage_plugin}
EOF
}

emit_patch() {  # strategic merge: lists merge by name — the owner's spec is untouched
  # apiVersion/kind/metadata make it a complete resource, which kustomize
  # requires of a patch file; kubectl patch merges them harmlessly. proxy-init
  # (to completion) then authbridge-proxy (native sidecar) are BOTH initContainers
  # — the same shape as the envoy path — so the app stays the pod's sole regular
  # container (and the default for `kubectl logs/exec`). The app container is
  # touched only to switch propagation on. Emit `containers:` ONLY when there is
  # an app fragment — a bare `containers:` key is `containers: null` in a
  # strategic merge and would clobber the owner's container list.
  local containers_block=""
  [ -z "$app_patch" ] || containers_block="      containers:${app_patch}
"
  cat <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${NAME}
  namespace: ${NAMESPACE}
spec:
  template:
    spec:
      initContainers:
$(proxy_init_container)
$(sidecar_container)
${containers_block}      volumes:
$(sidecar_volumes)
EOF
}

emit_undo() {
  # The exact inverse of emit_patch, in one line of compact JSON so it can ride
  # inside a single-quoted back-out line. Lists that merge by name un-merge by
  # name: `$patch: delete` removes exactly the items the patch added. proxy-init
  # AND authbridge-proxy are BOTH initContainers now (the native sidecar), so both
  # are deleted from that list. The image is the one non-additive field — a
  # replaced value has no delete, only another value — so RESTORE_IMAGE is the
  # pre-attach ref to restore (APP_IMAGE is refused in this mode; see
  # parse_inputs). `containers` appears only when the attach touched one.
  local app_containers=""
  if [ -n "$APP_CONTAINER" ]; then
    local app='{"name":"'"${APP_CONTAINER}"'"'
    [ -z "$RESTORE_IMAGE" ] || app="${app},\"image\":\"${RESTORE_IMAGE}\""
    app="${app},\"env\":[{\"name\":\"LINEAGE_PROPAGATE\",\"\$patch\":\"delete\"}]}"
    app_containers=',"containers":['"${app}"']'
  fi
  printf '{"spec":{"template":{"spec":{"initContainers":[{"name":"proxy-init","$patch":"delete"},{"name":"authbridge-proxy","$patch":"delete"}]%s,"volumes":[{"name":"authbridge-runtime","$patch":"delete"}]}}}}\n' \
    "$app_containers"
}

emit() {
  case "$EMIT" in
    cm)    emit_configmap ;;
    patch) emit_patch ;;
    undo)  emit_undo ;;
  esac
}

main() {
  [ $# -eq 0 ] || { echo "error: attach-lineage-proxy.sh takes no arguments — every input is an environment variable (see the header)" >&2; exit 2; }
  parse_inputs        # every knob: read, default, validate — all refusals live here
  build_plugin_entry  # the lineage-telemetry pipeline entry (empty under NO_EMIT=1)
  build_app_patch     # optional propagation switch on the app's own container
  emit                # dispatch: patch | cm | undo
}
main "$@"
