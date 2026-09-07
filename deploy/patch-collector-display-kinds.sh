#!/usr/bin/env bash
# OPTIONAL, DISPLAY-ONLY: teach a trace-display backend (Phoenix) to render
# AuthBridge sidecar lineage spans by kind instead of as "unknown".
#
# WHY THIS IS ITS OWN SCRIPT, AND NOT PART OF THE DATA-GOVERNANCE DEPLOY
# ---------------------------------------------------------------------
# The sidecar wire contract is explicit that `openinference.span.kind` is NOT a
# producer attribute: the producer emits facts, and display meaning belongs to
# infrastructure. This script is that seam, and nothing more.
#
# It deliberately does NOT touch data-governance's own pipeline. Our ingest path
# is `traces/data_governance` (created by patch-rossoctl-collector.sh), which
# runs `[batch]` only; the derivation never reads `openinference.*` and the
# `spans` table has no business storing a display attribute. Adding the
# transform there would put an attribute nobody reads into a facts-only store.
#
# So the transform goes on the DISPLAY pipeline — the one already exporting to
# Phoenix — where it is the only place it means anything. Data governance
# ingests the same spans without it. Running this script is entirely optional:
# skip it and lineage still works, Phoenix just shows sidecar spans as
# `unknown`. (Measured 2026-08-14 on a 152-span reference trace: 70 sidecar
# spans, all `unknown` without this, `tool`/`llm`/`agent` with it.)
#
# The mapping is the contract's protocol fact, one hop:
#     lineage.protocol=a2a       -> AGENT
#     lineage.protocol=mcp       -> TOOL
#     lineage.protocol=inference -> LLM
#     lineage.protocol=http      -> CHAIN
#
# Idempotent in both directions, like its sibling: re-applying is a no-op and
# does not roll the collector.
#
# Usage:
#   ./deploy/patch-collector-display-kinds.sh            # apply (default)
#   ./deploy/patch-collector-display-kinds.sh --revert   # remove
#
# Environment overrides:
#   COLLECTOR_NAMESPACE  Namespace of the platform collector (default: rossoctl-system)
#   COLLECTOR_CONFIGMAP  ConfigMap name (default: otel-collector-config)
#   COLLECTOR_DEPLOY     Deployment to roll after patching (default: otel-collector)
#   DISPLAY_PIPELINE     Which pipeline renders traces (default: traces/default;
#                        older platform builds name it traces/phoenix)

set -euo pipefail

MODE="apply"
for arg in "$@"; do
    case "${arg}" in
        --revert) MODE="revert" ;;
        -h|--help)
            sed -n '2,45p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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
DISPLAY_PIPELINE="${DISPLAY_PIPELINE:-traces/default}"

for tool in kubectl python3; do
    command -v "${tool}" >/dev/null 2>&1 || { echo "error: '${tool}' is not on PATH" >&2; exit 1; }
done
python3 -c "import yaml" >/dev/null 2>&1 || {
    echo "error: python3 PyYAML not installed; install with: pip install pyyaml" >&2; exit 1; }

if ! kubectl -n "${COLLECTOR_NAMESPACE}" get cm "${COLLECTOR_CONFIGMAP}" >/dev/null 2>&1; then
    echo "error: ConfigMap ${COLLECTOR_NAMESPACE}/${COLLECTOR_CONFIGMAP} not found" >&2
    exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT
ORIG="${WORK_DIR}/base.yaml"
EDITED="${WORK_DIR}/base.edited.yaml"

echo ">> Reading ${COLLECTOR_NAMESPACE}/${COLLECTOR_CONFIGMAP} (key base.yaml)"
kubectl -n "${COLLECTOR_NAMESPACE}" get cm "${COLLECTOR_CONFIGMAP}" \
    -o jsonpath='{.data.base\.yaml}' > "${ORIG}"
[[ -s "${ORIG}" ]] || { echo "error: ConfigMap key 'base.yaml' is empty or missing" >&2; exit 1; }

# APOSTROPHES BELOW ARE LOAD-BEARING — keep this heredoc free of them.
#
# macOS still ships bash 3.2 as /bin/bash, which `#!/usr/bin/env bash` resolves
# to on a stock Mac. Its parser scans a `$( … )` command substitution for
# balanced quotes and counts the ones inside a heredoc body too (fixed in
# bash 4). An odd number of apostrophes anywhere in the Python below — including
# in a comment — makes the whole script fail to parse with a misleading
# "unexpected EOF while looking for matching" pointing at the end of the file.
# `shellcheck` does not catch it, because it parses as modern bash.
#
# This is not hypothetical: the sibling `patch-rossoctl-collector.sh` has
# exactly one stray apostrophe in a Python comment and cannot run on macOS at all.
CHANGE_STATE="$(
    MODE="${MODE}" \
    DISPLAY_PIPELINE="${DISPLAY_PIPELINE}" \
    ORIG_PATH="${ORIG}" \
    EDITED_PATH="${EDITED}" \
    python3 - <<'PY'
import os
import sys
import yaml

mode = os.environ["MODE"]
pipeline_name = os.environ["DISPLAY_PIPELINE"]

with open(os.environ["ORIG_PATH"]) as f:
    cfg = yaml.safe_load(f)

PROCESSOR_NAME = "transform/lineage_display"

# One statement per protocol the contract defines. Kept as a literal list rather
# than generated, so the mapping is greppable from the wire contract vocabulary.
STATEMENTS = [
    'set(attributes["openinference.span.kind"], "AGENT") where attributes["lineage.protocol"] == "a2a"',
    'set(attributes["openinference.span.kind"], "TOOL") where attributes["lineage.protocol"] == "mcp"',
    'set(attributes["openinference.span.kind"], "LLM") where attributes["lineage.protocol"] == "inference"',
    'set(attributes["openinference.span.kind"], "CHAIN") where attributes["lineage.protocol"] == "http"',
]

processors = cfg.setdefault("processors", {})
pipelines = cfg.setdefault("service", {}).setdefault("pipelines", {})
pipeline = pipelines.get(pipeline_name)

if pipeline is None:
    # Fail loudly in apply mode rather than inventing a display pipeline: which
    # one renders traces is a platform decision, not ours. Revert still has
    # to be able to clean up, so it only warns.
    available = ", ".join(sorted(pipelines)) or "(none)"
    msg = (
        f"display pipeline {pipeline_name!r} not found in the collector config. "
        f"Available: {available}. "
        "Set DISPLAY_PIPELINE to the one that exports to your trace UI.\n"
    )
    if mode == "apply":
        sys.stderr.write(msg)
        sys.exit(2)
    sys.stderr.write("warning: " + msg)

changed = False

if mode == "apply":
    if processors.get(PROCESSOR_NAME) is None:
        processors[PROCESSOR_NAME] = {
            "trace_statements": [{"context": "span", "statements": STATEMENTS}]
        }
        changed = True
    procs = pipeline.setdefault("processors", [])
    if PROCESSOR_NAME not in procs:
        # Ahead of `batch`, which is terminal by convention.
        procs.insert(procs.index("batch") if "batch" in procs else len(procs), PROCESSOR_NAME)
        changed = True
elif mode == "revert":
    for pl in pipelines.values():
        procs = (pl or {}).get("processors") or []
        if PROCESSOR_NAME in procs:
            procs.remove(PROCESSOR_NAME)
            changed = True
    # Drop the definition only once nothing references it, so we never break a
    # pipeline someone else wired it into.
    if PROCESSOR_NAME in processors and not any(
        PROCESSOR_NAME in ((pl or {}).get("processors") or []) for pl in pipelines.values()
    ):
        del processors[PROCESSOR_NAME]
        changed = True
else:
    sys.stderr.write(f"unknown MODE: {mode}\n")
    sys.exit(2)

with open(os.environ["EDITED_PATH"], "w") as f:
    yaml.safe_dump(cfg, f, sort_keys=False)

print("CHANGED" if changed else "UNCHANGED")
PY
)"

if [[ "${CHANGE_STATE}" == "UNCHANGED" ]]; then
    echo ">> Nothing to do (${MODE} already in effect); collector not rolled."
    exit 0
fi

echo ">> Applying ${MODE}d ConfigMap (${PROCESSOR_NAME:-transform/lineage_display} on ${DISPLAY_PIPELINE})"
kubectl -n "${COLLECTOR_NAMESPACE}" create configmap "${COLLECTOR_CONFIGMAP}" \
    --from-file=base.yaml="${EDITED}" \
    --dry-run=client -o yaml | kubectl apply -f -

echo ">> Restarting deploy/${COLLECTOR_DEPLOY} to pick up new config"
kubectl -n "${COLLECTOR_NAMESPACE}" rollout restart "deploy/${COLLECTOR_DEPLOY}"
kubectl -n "${COLLECTOR_NAMESPACE}" rollout status "deploy/${COLLECTOR_DEPLOY}" --timeout=120s

if [[ "${MODE}" == "apply" ]]; then
    cat <<EOF

Done. Sidecar lineage spans on the '${DISPLAY_PIPELINE}' pipeline now carry
openinference.span.kind, so Phoenix renders them as AGENT / TOOL / LLM / CHAIN
instead of "unknown".

Data governance is unaffected either way: its own traces/data_governance
pipeline does not run this processor, and the derivation never reads the
attribute. Not persisted in the platform repo — re-run after a collector
ConfigMap re-apply.

EOF
else
    cat <<EOF

Done. The lineage display transform has been removed. Sidecar spans will render
as "unknown" in Phoenix; nothing else changes.

EOF
fi
