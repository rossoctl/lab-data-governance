#!/usr/bin/env bash
# Wire the rossoctl otel-collector to the data-governance receiver via a
# DEDICATED traces pipeline (issue #42, cross-repo half).
#
# The rossoctl collector ConfigMap is owned by the rossoctl repo. This script
# additively patches the live in-cluster object: it adds an
# `otlp/data_governance` exporter and a dedicated `traces/data_governance`
# pipeline that fans the shared `otlp` receiver into that exporter. It does
# NOT piggyback on any other pipeline (e.g. Phoenix's) — data-governance gets
# its own isolated pipeline, so its span ingestion is independent of whatever
# other trace pipelines the platform ships.
#
# The dedicated pipeline is created as:
#     traces/data_governance:
#       receivers:  [otlp]              # reuse the collector's existing OTLP receiver
#       processors: [batch]             # reuse the existing batch processor
#       exporters:  [otlp/data_governance]
#
# The a2a-noise `filter/a2a_noise` processor is NOT added here — the
# agent-examples deploy (travel_advisor) adds it into this same pipeline when
# it wants a2a queue/event noise dropped before data-governance ingests. This
# script tolerates that processor being present: --revert removes the whole
# `traces/data_governance` pipeline (and the exporter) regardless.
#
# Both edits are idempotent — the script is safe to re-run, and an upstream
# re-apply of the rossoctl ConfigMap simply requires re-running this script to
# re-add the patch.
#
# Run from anywhere; paths are resolved relative to this file.
#
# Usage:
#   ./deploy/patch-rossoctl-collector.sh            # apply (default)
#   ./deploy/patch-rossoctl-collector.sh --revert   # remove the patch
#
# Both modes are idempotent: re-running with the patch already applied (or
# already absent) is a no-op and does not roll the collector.
#
# Environment overrides:
#   COLLECTOR_NAMESPACE  Namespace of the rossoctl collector (default: rossoctl-system)
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
            # Print the leading comment block (lines 2-43, ending at the last
            # comment line before `set -euo pipefail`), stripping the `# ` prefix.
            sed -n '2,43p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "error: unknown argument: ${arg}" >&2
            echo "usage: $(basename "${BASH_SOURCE[0]}") [--revert]" >&2
            exit 2
            ;;
    esac
done

COLLECTOR_NAMESPACE="${COLLECTOR_NAMESPACE:-rossoctl-system}"
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
    echo "       Is the rossoctl platform deployed on this cluster (--with-otel)?" >&2
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
# Apply mode: add exporters['otlp/data_governance'] (if missing) and create a
# dedicated service.pipelines['traces/data_governance'] pipeline (if missing)
# that reuses the existing `otlp` receiver and `batch` processor. The existing
# `traces/default` pipeline is never touched.
#
# Revert mode: remove exporters['otlp/data_governance'] (if present) and remove
# the whole service.pipelines['traces/data_governance'] pipeline (if present).
# Other exporters, processors, and pipelines are left untouched — this restores
# the collector to its pre-patch state.
#
# NOTE: the heredoc below sits inside $( ... ). bash 3.2 — still /bin/bash on
# macOS — lexes the whole substitution before the quoted heredoc takes effect,
# so a lone apostrophe anywhere inside it opens a quote state that is never
# closed, and the script fails to parse at EOF with the error reported far from
# its cause. Keep the Python comments free of them.
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
PIPELINE_NAME = "traces/data_governance"

exporters = cfg.setdefault("exporters", {})
service = cfg.setdefault("service", {})
pipelines = service.setdefault("pipelines", {})

# Sanity: the dedicated pipeline reuses the shared `otlp` receiver. If the
# collector config is shaped unexpectedly (no otlp receiver at all), fail loudly
# rather than wiring a pipeline with no input — but only in apply mode; revert
# must always be able to clean up regardless of receiver shape.
receivers = cfg.get("receivers", {})
if mode == "apply" and "otlp" not in receivers:
    sys.stderr.write(
        "receiver 'otlp' not found in collector config; cannot build the "
        "traces/data_governance pipeline (expected an OTLP receiver)\n"
    )
    sys.exit(2)

changed = False

if mode == "apply":
    if EXPORTER_NAME not in exporters:
        exporters[EXPORTER_NAME] = {
            "endpoint": endpoint,
            "tls": {"insecure": True},
        }
        changed = True
    if PIPELINE_NAME not in pipelines:
        pipelines[PIPELINE_NAME] = {
            "receivers": ["otlp"],
            "processors": ["batch"],
            "exporters": [EXPORTER_NAME],
        }
        changed = True
elif mode == "revert":
    if EXPORTER_NAME in exporters:
        del exporters[EXPORTER_NAME]
        changed = True
    if PIPELINE_NAME in pipelines:
        del pipelines[PIPELINE_NAME]
        changed = True
    # The agent-examples deploy may have added a `filter/a2a_noise` processor
    # into our traces/data_governance pipeline. Removing the pipeline above
    # orphans that processor definition (defined, referenced by nothing). The
    # OTel Collector tolerates an unreferenced processor, but leaving it means
    # revert does not fully restore the pre-patch config. Drop the definition
    # too — but ONLY if no surviving pipeline still references it, so we never
    # break another pipeline that legitimately uses the same processor.
    A2A_FILTER = "filter/a2a_noise"
    processors = cfg.get("processors", {})
    if A2A_FILTER in processors:
        still_referenced = any(
            A2A_FILTER in (p or {}).get("processors", [])
            for p in pipelines.values()
        )
        if not still_referenced:
            del processors[A2A_FILTER]
            changed = True
else:
    sys.stderr.write(f"unknown MODE: {mode}\n")
    sys.exit(2)

with open(edited_path, "w") as f:
    yaml.safe_dump(cfg, f, sort_keys=False)

print("CHANGED" if changed else "UNCHANGED")
PY
)"

# The Python guards (missing otlp receiver, unknown MODE) exit non-zero, which
# under `set -e` aborts the CHANGE_STATE assignment above with the Python
# stderr shown — so reaching here means the edit succeeded and CHANGE_STATE is
# exactly "CHANGED" or "UNCHANGED".
if [[ "${CHANGE_STATE}" == "UNCHANGED" ]]; then
    if [[ "${MODE}" == "apply" ]]; then
        echo ">> ConfigMap already patched (traces/data_governance pipeline present); nothing to do."
    else
        echo ">> ConfigMap already reverted (traces/data_governance pipeline absent); nothing to do."
    fi
    exit 0
fi

if [[ "${MODE}" == "apply" ]]; then
    echo ">> Applying patched ConfigMap (adding traces/data_governance pipeline)"
else
    echo ">> Applying reverted ConfigMap (removing traces/data_governance pipeline)"
fi
kubectl -n "${COLLECTOR_NAMESPACE}" create configmap "${COLLECTOR_CONFIGMAP}" \
    --from-file=base.yaml="${EDITED}" \
    --dry-run=client -o yaml | kubectl apply -f -

echo ">> Restarting deploy/${COLLECTOR_DEPLOY} to pick up new config"
kubectl -n "${COLLECTOR_NAMESPACE}" rollout restart "deploy/${COLLECTOR_DEPLOY}"
kubectl -n "${COLLECTOR_NAMESPACE}" rollout status "deploy/${COLLECTOR_DEPLOY}" --timeout=120s

if [[ "${MODE}" == "apply" ]]; then
    cat <<EOF

Done. The rossoctl otel-collector now runs a dedicated 'traces/data_governance'
pipeline (otlp -> batch -> otlp/data_governance) that exports to
${RECEIVER_ENDPOINT}. The existing 'traces/default' pipeline is unchanged.

This patch is NOT persisted in the rossoctl repo. If the rossoctl collector
ConfigMap is re-applied from upstream, re-run this script to re-add the
pipeline. Until the patch lands in the rossoctl repo (issue #42 cross-repo
half), this script is the durable way to restore the integration after a
cluster recreate or rossoctl upgrade.

EOF
else
    cat <<EOF

Done. The 'traces/data_governance' pipeline and the 'otlp/data_governance'
exporter have been removed from the rossoctl otel-collector ConfigMap and the
collector restarted. The 'traces/default' pipeline is unchanged.

EOF
fi
