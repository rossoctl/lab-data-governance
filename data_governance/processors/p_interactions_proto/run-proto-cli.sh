#!/usr/bin/env bash
# Run the p_interactions_proto CLI inside the data-governance receiver pod
# against a given trace_id, hot-copying the prototype's Python files into
# the pod first.
#
# Usage:
#   run-proto-cli.sh <trace_id> [--scramble] [--rebuild-ui]
#
# Flags:
#   --scramble     Pass through to the CLI: torture-test the late-parent
#                  re-eval path by reversing span order.
#   --rebuild-ui   After running the CLI, rebuild the container image via
#                  ./deploy/build-and-load.sh and rollout-restart
#                  data-governance-ui so the UI picks up any unstaged
#                  changes to data_governance/api/. Use this when you've
#                  edited the API layer (e.g. adding `retracted_at IS
#                  NULL` filters) and need to see the change in the
#                  running UI. The receiver pod is NOT restarted because
#                  the CLI dev loop uses hot-copy, not the receiver
#                  process.
#
# Conventions:
#   - kubectl context is `kind-kagenti`, namespace is `data-governance`.
#   - Receiver pod label is `app.kubernetes.io/name=data-governance-receiver`.
#   - kubectl cp of a *directory* nests instead of overwriting; this
#     script copies individual files for that reason.
#   - The CLI module path is data_governance.processors.p_interactions_proto.cli.
#
# This script is part of the throwaway prototype. See README.md and
# CLAUDE.md in this directory.

set -euo pipefail

CTX="kind-kagenti"
NS="data-governance"
RECEIVER_LABEL="app.kubernetes.io/name=data-governance-receiver"
UI_LABEL="app.kubernetes.io/name=data-governance-ui"
PROC_REL="data_governance/processors/p_interactions_proto"
PROTO_FILES=(
    procedure.py
    anchor_rules.py
    caller_inference.py
    extractor.py
    cli.py
    __init__.py
)

# Resolve repo root from this script's location so caller cwd doesn't matter.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
PROC_ABS="${REPO_ROOT}/${PROC_REL}"

usage() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-2}"
}

# --- argument parsing -------------------------------------------------------

trace_id=""
scramble=0
rebuild_ui=0

while (($#)); do
    case "$1" in
        -h|--help) usage 0 ;;
        --scramble) scramble=1; shift ;;
        --rebuild-ui) rebuild_ui=1; shift ;;
        --) shift; break ;;
        -*) echo "error: unknown flag: $1" >&2; usage 2 ;;
        *)
            if [[ -z "$trace_id" ]]; then
                trace_id="$1"
                shift
            else
                echo "error: unexpected positional arg: $1" >&2
                usage 2
            fi
            ;;
    esac
done

if [[ -z "$trace_id" ]]; then
    echo "error: trace_id is required" >&2
    usage 2
fi

# --- pod lookup -------------------------------------------------------------

receiver_pod=$(kubectl --context "$CTX" -n "$NS" get pods \
    -l "$RECEIVER_LABEL" --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}')

if [[ -z "$receiver_pod" ]]; then
    echo "error: no running receiver pod with label $RECEIVER_LABEL in namespace $NS" >&2
    exit 1
fi

echo ">> receiver pod: $receiver_pod"

# --- hot-copy prototype files ----------------------------------------------

echo ">> copying prototype files to /app/${PROC_REL}"
for f in "${PROTO_FILES[@]}"; do
    src="${PROC_ABS}/${f}"
    if [[ ! -f "$src" ]]; then
        echo "  skip $f (not present locally)"
        continue
    fi
    kubectl --context "$CTX" -n "$NS" cp \
        "$src" "${receiver_pod}:/app/${PROC_REL}/${f}" \
        >/dev/null
    echo "  copied $f"
done

# --- run the CLI ------------------------------------------------------------

cli_args=("$trace_id")
if (( scramble )); then
    cli_args+=("--scramble")
fi

echo ">> running CLI: python -m ${PROC_REL//\//.}.cli ${cli_args[*]}"
echo
kubectl --context "$CTX" -n "$NS" exec "$receiver_pod" -c receiver -- \
    python -m "${PROC_REL//\//.}.cli" "${cli_args[@]}"
exit_code=$?

# --- optional UI rebuild ---------------------------------------------------

if (( rebuild_ui )); then
    echo
    echo ">> --rebuild-ui: building image + rolling UI deployment"
    "${REPO_ROOT}/deploy/build-and-load.sh"
    kubectl --context "$CTX" -n "$NS" rollout restart deploy/data-governance-ui
    kubectl --context "$CTX" -n "$NS" rollout status deploy/data-governance-ui \
        --timeout=120s
    new_ui=$(kubectl --context "$CTX" -n "$NS" get pods \
        -l "$UI_LABEL" --field-selector=status.phase=Running \
        -o jsonpath='{.items[0].metadata.name}')
    echo ">> UI ready: $new_ui"
fi

exit "$exit_code"
