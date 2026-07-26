#!/usr/bin/env bash
# Patch the kagenti-system otel-collector ConfigMap so traces fan out to the
# data-governance receiver (issue #42, cross-repo half).
#
# The kagenti collector ConfigMap is owned by the kagenti repo. This script
# additively patches the live in-cluster object: it adds an
# `otlp/data_governance` exporter and a `transform/lineage_display` processor
# (Phoenix display kinds AGENT/TOOL/LLM/CHAIN stamped from the sidecar's
# `lineage.protocol` fact — the wire contract puts display meaning in infra,
# not the producer) and wires both into the `traces/phoenix` pipeline. All
# edits are idempotent — the script is safe to re-run, and an upstream
# re-apply of the kagenti ConfigMap simply requires re-running this script to
# re-add the patch.
#
# Declarative alternative (survives helm upgrade instead of needing a re-run):
# deploy/kagenti-collector-dg-values.yaml wires the SAME otlp/data_governance
# exporter into the traces/phoenix pipeline as a kagenti-deps chart value. Use
# it where you own the kagenti-deps Helm release and the collector ConfigMap is
# Helm-managed (not operatorManaged). See that file's header for caveats.
#
# Run from anywhere; paths are resolved relative to this file.
#
# Usage:
#   ./deploy/patch-kagenti-collector.sh            # apply (default)
#   ./deploy/patch-kagenti-collector.sh --revert   # remove the patch
#
# Both modes are idempotent: re-running with the patch already applied (or
# already absent) is a no-op and does not roll the collector.
#
# Environment overrides:
#   COLLECTOR_NAMESPACE  Namespace of the kagenti collector (default: kagenti-system)
#   COLLECTOR_CONFIGMAP  ConfigMap name (default: otel-collector-config)
#   COLLECTOR_DEPLOY     Deployment name to roll after patching (default: otel-collector)
#   RECEIVER_ENDPOINT    Receiver gRPC endpoint to add as exporter target
#                        (default: data-governance-receiver.data-governance.svc.cluster.local:4317)

set -euo pipefail

MODE="apply"
for arg in "$@"; do
    case "${arg}" in
        --revert)
            MODE="revert"
            ;;
        -h|--help)
            sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "error: unknown argument: ${arg}" >&2
            echo "usage: $(basename "${BASH_SOURCE[0]}") [--revert]" >&2
            exit 2
            ;;
    esac
done

COLLECTOR_NAMESPACE="${COLLECTOR_NAMESPACE:-kagenti-system}"
COLLECTOR_CONFIGMAP="${COLLECTOR_CONFIGMAP:-otel-collector-config}"
COLLECTOR_DEPLOY="${COLLECTOR_DEPLOY:-otel-collector}"
RECEIVER_ENDPOINT="${RECEIVER_ENDPOINT:-data-governance-receiver.data-governance.svc.cluster.local:4317}"

if ! command -v kubectl >/dev/null 2>&1; then
    echo "error: 'kubectl' is not on PATH" >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "error: 'python3' is not on PATH (needed for YAML editing)" >&2
    exit 1
fi
if ! python3 -c "import yaml" >/dev/null 2>&1; then
    echo "error: python3 PyYAML module not installed; install with: pip install pyyaml" >&2
    exit 1
fi

if ! kubectl -n "${COLLECTOR_NAMESPACE}" get cm "${COLLECTOR_CONFIGMAP}" >/dev/null 2>&1; then
    echo "error: ConfigMap ${COLLECTOR_NAMESPACE}/${COLLECTOR_CONFIGMAP} not found" >&2
    echo "       Is the kagenti platform deployed on this cluster?" >&2
    exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT
ORIG="${WORK_DIR}/base.yaml"
EDITED="${WORK_DIR}/base.edited.yaml"

echo ">> Reading ${COLLECTOR_NAMESPACE}/${COLLECTOR_CONFIGMAP} (key base.yaml)"
kubectl -n "${COLLECTOR_NAMESPACE}" get cm "${COLLECTOR_CONFIGMAP}" \
    -o jsonpath='{.data.base\.yaml}' > "${ORIG}"

if [[ ! -s "${ORIG}" ]]; then
    echo "error: ConfigMap key 'base.yaml' is empty or missing" >&2
    exit 1
fi

# Edit the YAML in Python so we preserve structure and stay idempotent.
#
# Apply mode: add exporters['otlp/data_governance'] (if missing) and append
# it to service.pipelines['traces/phoenix'].exporters (if not already
# listed).
#
# Revert mode: remove exporters['otlp/data_governance'] (if present) and
# remove it from service.pipelines['traces/phoenix'].exporters (if listed).
# Other exporters and pipelines are left untouched.
CHANGE_STATE="$(
    MODE="${MODE}" \
    RECEIVER_ENDPOINT="${RECEIVER_ENDPOINT}" \
    ORIG_PATH="${ORIG}" \
    EDITED_PATH="${EDITED}" \
    python3 - <<'PY'
import os
import sys
import yaml

mode = os.environ["MODE"]
orig_path = os.environ["ORIG_PATH"]
edited_path = os.environ["EDITED_PATH"]
endpoint = os.environ["RECEIVER_ENDPOINT"]

with open(orig_path) as f:
    cfg = yaml.safe_load(f)

EXPORTER_NAME = "otlp/data_governance"
PIPELINE_NAME = "traces/phoenix"
PROCESSOR_NAME = "transform/lineage_display"
PROCESSOR_DEF = {
    "trace_statements": [
        {
            "context": "span",
            "statements": [
                'set(attributes["openinference.span.kind"], "AGENT") where attributes["lineage.protocol"] == "a2a"',
                'set(attributes["openinference.span.kind"], "TOOL") where attributes["lineage.protocol"] == "mcp"',
                'set(attributes["openinference.span.kind"], "LLM") where attributes["lineage.protocol"] == "inference"',
                'set(attributes["openinference.span.kind"], "CHAIN") where attributes["lineage.protocol"] == "http"',
            ],
        }
    ],
}

exporters = cfg.setdefault("exporters", {})
processors = cfg.setdefault("processors", {})
pipelines = cfg.get("service", {}).get("pipelines", {})
if PIPELINE_NAME not in pipelines:
    sys.stderr.write(
        f"pipeline '{PIPELINE_NAME}' not found in collector config\n"
    )
    sys.exit(2)
pipeline_exporters = pipelines[PIPELINE_NAME].setdefault("exporters", [])
pipeline_processors = pipelines[PIPELINE_NAME].setdefault("processors", [])

changed = False

if mode == "apply":
    if EXPORTER_NAME not in exporters:
        exporters[EXPORTER_NAME] = {
            "endpoint": endpoint,
            "tls": {"insecure": True},
        }
        changed = True
    if EXPORTER_NAME not in pipeline_exporters:
        pipeline_exporters.append(EXPORTER_NAME)
        changed = True
    if PROCESSOR_NAME not in processors:
        processors[PROCESSOR_NAME] = PROCESSOR_DEF
        changed = True
    if PROCESSOR_NAME not in pipeline_processors:
        # Before 'batch' (batch is terminal by collector convention).
        pos = (
            pipeline_processors.index("batch")
            if "batch" in pipeline_processors
            else len(pipeline_processors)
        )
        pipeline_processors.insert(pos, PROCESSOR_NAME)
        changed = True
elif mode == "revert":
    if EXPORTER_NAME in exporters:
        del exporters[EXPORTER_NAME]
        changed = True
    if EXPORTER_NAME in pipeline_exporters:
        pipeline_exporters.remove(EXPORTER_NAME)
        changed = True
    if PROCESSOR_NAME in processors:
        del processors[PROCESSOR_NAME]
        changed = True
    if PROCESSOR_NAME in pipeline_processors:
        pipeline_processors.remove(PROCESSOR_NAME)
        changed = True
else:
    sys.stderr.write(f"unknown MODE: {mode}\n")
    sys.exit(2)

with open(edited_path, "w") as f:
    yaml.safe_dump(cfg, f, sort_keys=False)

print("CHANGED" if changed else "UNCHANGED")
PY
)"

if [[ "${CHANGE_STATE}" != "CHANGED" && "${CHANGE_STATE}" != "UNCHANGED" ]]; then
    echo "error: edit step produced unexpected output: ${CHANGE_STATE}" >&2
    exit 1
fi

if [[ "${CHANGE_STATE}" == "UNCHANGED" ]]; then
    if [[ "${MODE}" == "apply" ]]; then
        echo ">> ConfigMap already patched (otlp/data_governance + transform/lineage_display present in traces/phoenix); nothing to do."
    else
        echo ">> ConfigMap already reverted (otlp/data_governance + transform/lineage_display absent); nothing to do."
    fi
    exit 0
fi

if [[ "${MODE}" == "apply" ]]; then
    echo ">> Applying patched ConfigMap"
else
    echo ">> Applying reverted ConfigMap (removing otlp/data_governance)"
fi
kubectl -n "${COLLECTOR_NAMESPACE}" create configmap "${COLLECTOR_CONFIGMAP}" \
    --from-file=base.yaml="${EDITED}" \
    --dry-run=client -o yaml | kubectl apply -f -

echo ">> Restarting deploy/${COLLECTOR_DEPLOY} to pick up new config"
kubectl -n "${COLLECTOR_NAMESPACE}" rollout restart "deploy/${COLLECTOR_DEPLOY}"
kubectl -n "${COLLECTOR_NAMESPACE}" rollout status "deploy/${COLLECTOR_DEPLOY}" --timeout=120s

if [[ "${MODE}" == "apply" ]]; then
    cat <<EOF

Done. The kagenti otel-collector now exports traces/phoenix-pipeline spans
to ${RECEIVER_ENDPOINT}, in addition to its existing phoenix target, and
stamps Phoenix display kinds (AGENT/TOOL/LLM/CHAIN) onto sidecar spans from
their lineage.protocol fact (transform/lineage_display).

This patch is NOT persisted in the kagenti repo. If the kagenti collector
ConfigMap is re-applied from upstream, re-run this script to re-add the
exporter. Until the patch lands in the kagenti repo (issue #42 cross-repo
half), this script is the durable way to restore the integration after a
cluster recreate or kagenti upgrade.

EOF
else
    cat <<EOF

Done. The otlp/data_governance exporter and the transform/lineage_display
processor have been removed from the kagenti otel-collector ConfigMap and
the collector restarted. Existing phoenix and mlflow exporters are
unchanged.

EOF
fi
