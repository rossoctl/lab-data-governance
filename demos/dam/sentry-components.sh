#!/usr/bin/env bash
# Turn Sentry's components on or off, for every agent or for one agent, live (about 5 s).
#
#   demos/dam/sentry-components.sh                         # show the config
#   demos/dam/sentry-components.sh default aiac=on dg=off  # for every agent
#   demos/dam/sentry-components.sh issue-triage-test aiac=off
#   demos/dam/sentry-components.sh issue-triage-test reset  # back to the default
#
# aiac: which tools an agent may use, from its description. dg: which data may go where.
set -euo pipefail
DAM="${DAM:-$HOME/development/dam}"
export KUBECONFIG="${KUBECONFIG:-$(cd "$DAM" && mise run -q cluster:kubeconfig 2>/dev/null | tail -1)}"
current="$(kubectl -n sentry-dam get configmap mcp-policy-config -o jsonpath='{.data.config\.json}')"
if [ $# -eq 0 ]; then printf '%s\n' "$current" | python3 -I -m json.tool; exit 0; fi
updated="$(python3 -I - "$current" "$@" <<'PY'
import json, sys
cfg = json.loads(sys.argv[1] or "{}")
target, changes = sys.argv[2], sys.argv[3:]
cfg.setdefault("default", {"aiac": "on", "dg": "on"}); cfg.setdefault("agents", {})
if changes == ["reset"]:
    if target == "default":
        sys.exit("reset applies to one agent")
    cfg["agents"].pop(target, None)
else:
    entry = cfg["default"] if target == "default" else cfg["agents"].setdefault(target, {})
    for change in changes:
        component, _, mode = change.partition("=")
        if component not in ("aiac", "dg") or mode not in ("on", "off"):
            sys.exit(f"bad change {change!r}: use aiac=on|off or dg=on|off")
        entry[component] = mode
print(json.dumps(cfg))
PY
)"
kubectl -n sentry-dam create configmap mcp-policy-config --from-literal=config.json="$updated" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
printf '%s\n' "$updated" | python3 -I -m json.tool
