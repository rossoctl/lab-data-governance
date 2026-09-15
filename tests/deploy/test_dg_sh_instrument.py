"""Behavioural tests for ``dg.sh namespace <ns> instrument [<entity>]`` (#184).

The activation slice of ``dg.sh`` — the substantial one. It switches on lineage
for the agents/tools in ``<ns>`` (all of them, or a single ``<entity>`` when
named). It is **non-reversible, additive-only, and never changes a namespace's
sidecar mode** (design: ``docs/cli.md`` § ``instrument``; ADR-0031
non-reversible / mode-preserving; ADR-0033 vendors-the-kit + proxy-default,
supersedes 0032).

**ADR-0033 owner-split (issue #245 — the behaviour flip).** ``dg.sh`` no longer
emits its own YAML; it drives the lineage-attach kit **VENDORED into this repo**
at ``deploy/lineage-attach/`` (``--cortex-local-path`` retired — the kit is
local). Each targeted entity is dispatched by its CURRENT sidecar state, all
auto-detected, no flag:

    | Entity's current state                     | Action                          | Owner                    |
    |--------------------------------------------|---------------------------------|--------------------------|
    | no sidecar, ns NOT envoy-configured        | inject lineage-only PROXY sidecar | dg.sh proxy applier (sidecar-patch-proxy.sh) |
    | no sidecar, ns already envoy-configured    | inject ENVOY lineage sidecar    | vendored envoy applier (sidecar-patch.sh)    |
    | sidecar present, no lineage-telemetry      | APPEND lineage-telemetry in place (best-effort, verify-after-roll) | dg.sh in-place edit |
    | sidecar present, lineage-telemetry wired   | no-op (idempotent)              | —                        |

"Already envoy-configured" is detected from the namespace: the presence of the
platform ``envoy-config`` ConfigMap (not a flag). A bare, ad-hoc namespace (the
travel_advisor demo) has none, so it takes the PROXY branch.

The injected proxy is AUTH-FREE (lineage-only) and captures egress via the
include-only iptables allowlist (``OUTBOUND_PORTS_INCLUDE``, A2A 8080 + MCP 8000).
The in-place append keeps the sidecar's auth exactly as-is (append, never strip),
verifies after the roll, and warns loudly on an operator clobber or an enforcing
pipeline (401 risk) — ADR-0033 Decision 3.

Kit ground truth (the UNIT tests here drive a FAKE kit — a stub
``lineage-attach/`` under a tmp path that ``DG_LINEAGE_ATTACH_DIR`` points dg.sh
at — so no cluster is needed):

  * both appliers (``sidecar-patch.sh`` envoy, ``sidecar-patch-proxy.sh`` proxy)
    read ``DEPLOY`` (required), ``NAMESPACE``, ``SELF_ID``, ``APP_CONTAINER`` and
    inherit the generator knobs; the proxy applier forwards
    ``OUTBOUND_PORTS_INCLUDE``, the envoy applier ``OUTBOUND_PORTS_EXCLUDE``;
  * each prints its **back-out line** on stdout, verbatim:
    ``>> back out: kubectl -n <ns> patch deploy/<d> --type strategic -p '<undo>'
    && kubectl -n <ns> delete cm authbridge-lineage-config-<d>`` — a
    reverse-patch, NOT a ``rollout undo``;
  * each runs ``kubectl patch --dry-run=server`` before any write (the pre-apply
    "version guard"), COMPLEMENTARY to dg.sh's post-apply crash-loop watch.

Like the rest of ``tests/deploy/``, these drive the real script as a subprocess
with a **fake ``kubectl``** and a **fake kit** on a synthetic ``PATH``. Nothing
here touches a cluster; the live "running the agents produces a UI-visible
trace" AC is out of scope for a unit run.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DG_SH = REPO_ROOT / "deploy" / "dg.sh"


def _make_bin(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


# ---------------------------------------------------------------------------
# Namespace model (shared with the #183 status tests' shape).
# ---------------------------------------------------------------------------


def _deployment(
    name: str,
    *,
    sidecar: str | None,
    cm_name: str | None,
    image: str = "agent-examples-snp:latest",
) -> dict:
    containers = [
        {
            "name": name,  # the app container
            "image": image,
            "ports": [{"containerPort": 8080}],
        }
    ]
    volumes: list[dict] = []
    if sidecar == "proxy":
        containers.append(
            {
                "name": "authbridge-proxy",
                "image": "ghcr.io/rossoctl/cortex/authbridge:lineage",
                "args": ["--config", "/etc/authbridge/config.yaml"],
                "volumeMounts": [
                    {"name": "authbridge-runtime", "mountPath": "/etc/authbridge"}
                ],
            }
        )
        volumes.append({"name": "authbridge-runtime", "configMap": {"name": cm_name}})
    elif sidecar == "envoy":
        containers.append(
            {
                "name": "envoy-proxy",
                "image": "ghcr.io/rossoctl/cortex/authbridge-envoy:latest",
                "args": ["--config", "/etc/authbridge/config.yaml"],
                "volumeMounts": [
                    {"name": "envoy-config", "mountPath": "/etc/envoy"},
                    {"name": "authbridge-runtime", "mountPath": "/etc/authbridge"},
                ],
            }
        )
        volumes.append({"name": "authbridge-runtime", "configMap": {"name": cm_name}})
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": "travel-advisor",
            "labels": {
                "app.kubernetes.io/name": name,
                "app.kubernetes.io/component": "agent",
            },
        },
        "spec": {"template": {"spec": {"containers": containers, "volumes": volumes}}},
    }


_CM_WITH_LINEAGE = """mode: proxy-sidecar
pipeline:
  inbound:
    plugins:
      - name: a2a-parser
      - name: lineage-telemetry
"""

_CM_WITHOUT_LINEAGE = """mode: proxy-sidecar
pipeline:
  inbound:
    plugins:
      - name: a2a-parser
      - name: mcp-parser
  outbound:
    plugins:
      - name: a2a-parser
"""

# An ENFORCING pipeline (jwt-validation / token-exchange present, no lineage):
# appending lineage here works, but lineage will record 401s for unauthenticated
# callers — dg.sh must warn (ADR-0033 Decision 3). No lineage-telemetry yet.
_CM_ENFORCING_NO_LINEAGE = """mode: proxy-sidecar
pipeline:
  inbound:
    plugins:
      - name: jwt-validation
      - name: a2a-parser
      - name: mcp-parser
  outbound:
    plugins:
      - name: token-exchange
      - name: a2a-parser
"""


# ---------------------------------------------------------------------------
# The fake kit. A stub lineage-attach/ dir with stub sidecar-patch.sh /
# build-otel-shim.sh / attach-lineage.sh scripts. Each stub logs its argv +
# environment to $KIT_LOG so tests can assert what dg.sh passed through the
# kit's env contract, and its behaviour (success / crash-loop) is programmed by
# env the test sets.
# ---------------------------------------------------------------------------

# sidecar-patch.sh stub: log DEPLOY/NAMESPACE/APP_CONTAINER/APP_IMAGE/etc, print
# the kit's real back-out line on stdout (before the wait), then either succeed
# or — when SP_ROLLOUT_FAILS is set — emulate the kit reaching the rollout wait
# and the rollout failing (the crash-loop symptom), exiting non-zero.
_STUB_SIDECAR_PATCH = r"""
if [[ -n "${KIT_LOG:-}" ]]; then
  {
    printf 'sidecar-patch.sh argv: %s\n' "$*"
    printf 'sidecar-patch.sh env: DEPLOY=%s NAMESPACE=%s SELF_ID=%s APP_CONTAINER=%s APP_IMAGE=%s OTEL_ENDPOINT=%s SIDECAR_IMAGE=%s PROXY_INIT_IMAGE=%s OUTBOUND_PORTS_EXCLUDE=%s\n' \
      "${DEPLOY:-}" "${NAMESPACE:-}" "${SELF_ID:-}" "${APP_CONTAINER:-}" "${APP_IMAGE:-}" "${OTEL_ENDPOINT:-}" "${SIDECAR_IMAGE:-}" "${PROXY_INIT_IMAGE:-}" "${OUTBOUND_PORTS_EXCLUDE:-}"
  } >> "$KIT_LOG"
fi
# The kit generates all three objects, dry-runs, applies CM+patch, THEN prints
# the back-out line before the rollout wait. Reproduce that ordering: the
# back-out line is on stdout even when the wait then fails.
undo='{"spec":{"template":{"spec":{"initContainers":[{"name":"envoy-proxy","$patch":"delete"}]}}}}'
echo "configmap/authbridge-lineage-config-${DEPLOY} created"
echo "deployment.apps/${DEPLOY} patched"
echo ">> back out: kubectl -n ${NAMESPACE} patch deploy/${DEPLOY} --type strategic -p '${undo}' && kubectl -n ${NAMESPACE} delete cm authbridge-lineage-config-${DEPLOY}"
if [[ "${SP_ROLLOUT_FAILS:-0}" == "1" ]]; then
  echo "error: deployment \"${DEPLOY}\" exceeded its progress deadline" >&2
  exit 1
fi
echo "deployment \"${DEPLOY}\" successfully rolled out"
echo ">> lineage sidecar attached to deploy/${DEPLOY} (self_id=${SELF_ID}, ns=${NAMESPACE})"
exit 0
"""

# build-otel-shim.sh stub: log the base-image arg, print the loaded wrapper tag,
# succeed — unless one of the failure knobs is set, matching the real kit's
# distinct exit codes (build-otel-shim.sh header line 26 + its exit sites):
#   SHIM_REFUSES=1 → exit 3, the bake INTERLOCK (self-instrumenting app / outside
#                    the shim envelope) — the sanctioned capture-only case.
#   SHIM_ATTEST_FAILS=1 → exit 4, ATTESTATION FAILED (verify_inert /
#                    verify_propagates) — the shim was built but does not work.
#   SHIM_BUILD_FAILS=1 → exit 1, a Docker build error — genuinely broken.
_STUB_BUILD_SHIM = r"""
if [[ -n "${KIT_LOG:-}" ]]; then
  printf 'build-otel-shim.sh argv: %s\n' "$*" >> "$KIT_LOG"
  # Record the cluster name dg.sh forwarded (the kit's container-runtime.sh
  # reads KIND_CLUSTER_NAME; a bare default here would be `rossoctl`). #244:
  # dg.sh must forward the operator's cluster, not silently default.
  printf 'build-otel-shim.sh env: KIND_CLUSTER_NAME=%s\n' "${KIND_CLUSTER_NAME:-<unset>}" >> "$KIT_LOG"
fi
base="${1:-}"
if [[ "${SHIM_REFUSES:-0}" == "1" ]]; then
  echo "REFUSING to bake ${base}: it already instruments httpx" >&2
  exit 3
fi
if [[ "${SHIM_ATTEST_FAILS:-0}" == "1" ]]; then
  echo "ATTESTATION FAILED for ${base}-otel:latest: gate on, but the hook did not come up." >&2
  exit 4
fi
if [[ "${SHIM_BUILD_FAILS:-0}" == "1" ]]; then
  echo "Error: error building at STEP: no such file" >&2
  exit 1
fi
short="${base##*/}"; name="${short%%[:@]*}"
echo ">> loaded docker.io/library/${name}-otel:latest into kind cluster rossoctl"
exit 0
"""

# sidecar-patch-proxy.sh stub: the PROXY live applier (ADR-0033 owner-split, the
# no-sidecar / NON-envoy-configured row). Logs the same env contract as the envoy
# applier PLUS the include-only allowlist OUTBOUND_PORTS_INCLUDE (the proxy path's
# knob, vs the envoy path's OUTBOUND_PORTS_EXCLUDE). Same back-out line + rollout
# behaviour as the envoy stub, keyed off SP_ROLLOUT_FAILS.
_STUB_SIDECAR_PATCH_PROXY = r"""
if [[ -n "${KIT_LOG:-}" ]]; then
  {
    printf 'sidecar-patch-proxy.sh argv: %s\n' "$*"
    printf 'sidecar-patch-proxy.sh env: DEPLOY=%s NAMESPACE=%s SELF_ID=%s APP_CONTAINER=%s APP_IMAGE=%s OTEL_ENDPOINT=%s SIDECAR_IMAGE=%s PROXY_INIT_IMAGE=%s OUTBOUND_PORTS_INCLUDE=%s\n' \
      "${DEPLOY:-}" "${NAMESPACE:-}" "${SELF_ID:-}" "${APP_CONTAINER:-}" "${APP_IMAGE:-}" "${OTEL_ENDPOINT:-}" "${SIDECAR_IMAGE:-}" "${PROXY_INIT_IMAGE:-}" "${OUTBOUND_PORTS_INCLUDE:-}"
  } >> "$KIT_LOG"
fi
undo='{"spec":{"template":{"spec":{"initContainers":[{"name":"proxy-init","$patch":"delete"},{"name":"authbridge-proxy","$patch":"delete"}]}}}}'
echo "configmap/authbridge-lineage-config-${DEPLOY} created"
echo "deployment.apps/${DEPLOY} patched"
echo ">> back out: kubectl -n ${NAMESPACE} patch deploy/${DEPLOY} --type strategic -p '${undo}' && kubectl -n ${NAMESPACE} delete cm authbridge-lineage-config-${DEPLOY}"
if [[ "${SP_ROLLOUT_FAILS:-0}" == "1" ]]; then
  echo "error: deployment \"${DEPLOY}\" exceeded its progress deadline" >&2
  exit 1
fi
echo "deployment \"${DEPLOY}\" successfully rolled out"
echo ">> lineage proxy sidecar attached to deploy/${DEPLOY} (self_id=${SELF_ID}, ns=${NAMESPACE})"
exit 0
"""

# attach-lineage.sh stub: present only so the kit-resolvable preflight (which
# checks all script names) passes. Never invoked directly by dg.sh.
_STUB_ATTACH = 'echo "attach-lineage.sh stub" >&2\nexit 0\n'


_FAKE_KUBECTL = r"""
if [[ -n "${KUBECTL_LOG:-}" ]]; then
  printf '%s\n' "$*" >> "$KUBECTL_LOG"
fi
case "$1" in
  version) exit 0 ;;
esac
if [[ "${GET_FAILS:-0}" == "1" && "$1" == "get" ]]; then
  printf '%s\n' "The connection to the server 127.0.0.1:6443 was refused" >&2
  exit 1
fi

# ---- component/tee preflight probes -----------------------------------------
# Namespace presence (component installed?).
if [[ "$*" == *"get"* && "$*" == *"namespace"* && "$*" == *"data-governance"* ]]; then
  if [[ "${NS_PRESENT:-1}" == "1" ]]; then
    printf '%s\n' "data-governance"; exit 0
  else
    printf '%s\n' 'Error from server (NotFound): namespaces "data-governance" not found' >&2
    exit 1
  fi
fi
# Collector-tee state.
if [[ "$*" == *"get"* && "$*" == *"otel-collector-config"* ]]; then
  if [[ "${TEE_WIRED:-1}" == "1" ]]; then printf '%s' "traces/data_governance"; else printf '%s' "traces/default"; fi
  exit 0
fi
# DG deployment readiness (component_installed helper may probe this).
if [[ "$*" == *"get"* && "$*" == *"data-governance-receiver"* ]]; then
  printf '%s\n' "1"; exit 0
fi

# ---- egressEnforcement probe (authbridge-runtime-config CM in the ns) --------
if [[ "$*" == *"get"* && "$*" == *"authbridge-runtime-config"* ]]; then
  printf '%s' "${EGRESS_ENFORCEMENT:-enforce}"
  exit 0
fi

# ---- Pod fetch (injected-sidecar fallback) -----------------------------------
# When the Deployment TEMPLATE shows no sidecar, dg.sh falls back to the live Pod
# (`kubectl get pods ... -o json`) to catch a webhook-injected sidecar. This
# model has NO injected sidecars, so the served Pod MIRRORS the template — a
# Running pod that CONFIRMS the template verdict (a no-sidecar template → a
# no-sidecar pod → an authoritative `none`). set_namespace writes pods-<ent>.json;
# a MISSING fixture serves {"items":[]} so a test can model a Deployment with no
# Running pod (scaled to 0 / just-applied), which the mutating path refuses on.
# Tested BEFORE the plain-text crash-loop probe below (which reads a jsonpath, NOT
# -o json), so only the -o json fallback query lands here.
if [[ "$*" == *"get"* && ( "$*" == *" pods"* || "$*" == *"pods "* || "$*" == *" pod "* ) && ( "$*" == *"-o json"* || "$*" == *"-ojson"* ) ]]; then
  ent=""
  for a in "$@"; do
    case "$a" in
      *app.kubernetes.io/name=*)
        ent="${a##*app.kubernetes.io/name=}"; ent="${ent%%,*}" ;;
    esac
  done
  f="$FIXDIR/pods-${ent}.json"
  if [[ -n "$ent" && -f "$f" ]]; then cat "$f"; else printf '%s' '{"items":[]}'; fi
  exit 0
fi
# ---- crash-loop watch: pod phase / restart probes ---------------------------
# dg.sh watches the rollout the attach triggers. We surface a crash-looping
# sidecar via a canned pod state when POD_CRASHLOOP=1.
if [[ "$*" == *"get"* && ( "$*" == *"pod"* || "$*" == *"pods"* ) ]]; then
  if [[ "${POD_CRASHLOOP:-0}" == "1" ]]; then
    printf '%s\n' "CrashLoopBackOff"
    exit 0
  fi
  printf '%s\n' "Running"
  exit 0
fi
# sidecar container log dump (dg.sh prints it on a crash-loop).
if [[ "$*" == *"logs"* ]]; then
  printf '%s\n' 'error: unknown plugin "lineage-telemetry"'
  exit 0
fi

# ---- envoy-config probe (dg.sh ns_is_envoy_configured — ADR-0033 owner-split) -
# The presence of the platform `envoy-config` ConfigMap in the TARGET namespace
# is how dg.sh decides a no-sidecar entity's owner: present → ENVOY branch (the
# vendored envoy applier); absent → PROXY branch (the auth-free proxy applier).
# ENVOY_CONFIG_TARGET DEFAULTS TO 0 (absent) — the demo reality: a bare, ad-hoc
# namespace has no envoy-config, so the DEFAULT no-sidecar path is the PROXY
# branch. A test that wants the envoy branch sets ENVOY_CONFIG_TARGET=1.
if [[ "$*" == *"get"* && "$*" == *"envoy-config"* ]]; then
  # extract the -n <ns> if present
  ns=""; prev=""
  for a in "$@"; do case "$prev" in -n|--namespace) ns="$a"; break ;; esac; prev="$a"; done
  if [[ "$*" == *"--all-namespaces"* || "$*" == *"-A"* ]]; then
    # (legacy source discovery — retained so an old copy path is inert, not an error)
    if [[ "${ENVOY_CONFIG_SOURCE:-0}" == "1" ]]; then printf '%s\n' "rossoctl-system"; fi
    exit 0
  fi
  if [[ "$ns" == "travel-advisor" ]]; then
    if [[ "${ENVOY_CONFIG_TARGET:-0}" == "1" ]]; then
      printf '%s' 'admin: {}'; exit 0    # present in the target ns → envoy branch
    fi
    printf '%s\n' 'Error from server (NotFound): configmaps "envoy-config" not found' >&2
    exit 1
  fi
  # any other (source) ns
  if [[ "${ENVOY_CONFIG_SOURCE:-0}" == "1" ]]; then printf '%s' 'admin: {}'; exit 0; fi
  printf '%s\n' 'Error from server (NotFound): configmaps "envoy-config" not found' >&2
  exit 1
fi

# ---- ConfigMap fetch (in-place append read + verify + status-style detection) -
if [[ "$*" == *"get"* && ( "$*" == *"configmap"* || "$*" == *" cm "* || "$*" == *" cm"* ) ]]; then
  cmname=""; prev=""
  for a in "$@"; do
    case "$prev" in configmap|cm|configmaps) cmname="$a"; break ;; esac
    prev="$a"
  done
  f="$FIXDIR/cm-${cmname}.json"
  # After an in-place append writes the CM, a "$FIXDIR/appended-${cmname}" marker
  # records that the wired body is now live — UNLESS POD_CLOBBER=1, which models
  # the operator reverting the operator-owned CM on the roll (verify then sees the
  # OLD, unwired body and dg.sh must warn on the clobber).
  wired_marker="$FIXDIR/appended-${cmname}"
  if [[ -f "$wired_marker" && "${POD_CLOBBER:-0}" != "1" ]]; then
    f="$FIXDIR/cm-${cmname}-wired.json"
  fi
  if [[ -n "$cmname" && -f "$f" ]]; then
    # A `{.data.config\.yaml}` read wants the raw config.yaml BODY (the append
    # path reads + verifies it); a `{.data}` read wants the Go-map repr (status).
    if [[ "$*" == *"config\.yaml"* || "$*" == *"config.yaml"* ]]; then
      python3 - "$f" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
sys.stdout.write((doc.get("data") or {}).get("config.yaml", ""))
PY
      exit 0
    fi
    python3 - "$f" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
data = doc.get("data") or {}
inner = " ".join(f"{k}:{v}" for k, v in data.items())
sys.stdout.write("map[" + inner + "]")
PY
    exit 0
  fi
  printf '%s\n' "Error from server (NotFound): configmaps \"${cmname}\" not found" >&2
  exit 1
fi

# ---- create configmap --dry-run=client -o yaml (the append renders the CM) ---
# dg.sh's in-place append builds the amended CM via
# `create configmap <n> --from-file=config.yaml=/dev/stdin --dry-run=client -o yaml`
# then pipes it to `apply -f -`. Model that render: read the stdin body and emit a
# ConfigMap YAML wrapping it, so the downstream apply sees a real manifest.
if [[ "$*" == *"create configmap"* && "$*" == *"--dry-run=client"* ]]; then
  cmn=""; for a in "$@"; do case "$a" in authbridge-lineage-config-*) cmn="$a" ;; esac; done
  body="$(cat)"
  {
    printf 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: %s\n  namespace: travel-advisor\ndata:\n  config.yaml: |\n' "$cmn"
    # indent the body by four spaces (under the |-block).
    printf '%s\n' "$body" | sed 's/^/    /'
  }
  exit 0
fi

# ---- ConfigMap write (in-place append: dg.sh applies the amended CM) ---------
# The append renders the amended per-app CM (lineage-telemetry inserted, auth
# left as-is) and pipes it to `kubectl apply -f -`. We CAPTURE that stdin as the
# new wired body — an honest echo of what dg.sh actually produced, not a
# re-synthesis — and record a marker so the verify re-read (above) reflects it.
if [[ "$1" == "apply" && ( "$*" == *"-f -"* || "$*" == *"-f-"* ) ]]; then
  # Stash the applied manifest to a temp FILE and pass its PATH to python as argv
  # — NOT via a pipe, which would collide with the heredoc that feeds python its
  # program on stdin (a heredoc `python3 - <<PY` already occupies stdin).
  applied_f="$(mktemp)"; cat > "$applied_f"
  python3 - "$FIXDIR" "$applied_f" <<'PY'
import json, sys, re
fixdir, applied_f = sys.argv[1], sys.argv[2]
raw = open(applied_f).read()
# The applied doc is YAML from `kubectl create configmap --dry-run=client -o yaml`.
# Pull the CM name and the config.yaml body without a YAML lib: name from the
# authbridge-lineage-config-<n> token, body from the `config.yaml: |`-block.
m = re.search(r"authbridge-lineage-config-[a-z0-9-]+", raw)
if not m:
    sys.exit(0)
name = m.group(0)
bm = re.search(r"config\.yaml:\s*\|?\s*\n(.*)$", raw, re.S)
body = bm.group(1) if bm else raw
import os
os.makedirs(fixdir, exist_ok=True)
open(os.path.join(fixdir, f"appended-{name}"), "w").close()
json.dump({"data": {"config.yaml": body}}, open(os.path.join(fixdir, f"cm-{name}-wired.json"), "w"))
PY
  rm -f "$applied_f"
  exit 0
fi

# ---- Deployment listing ------------------------------------------------------
if [[ "$*" == *"get"* && ( "$*" == *"deployment"* || "$*" == *"deploy"* ) ]]; then
  ent=""
  for a in "$@"; do
    case "$a" in
      *app.kubernetes.io/name=*)
        ent="${a##*app.kubernetes.io/name=}"; ent="${ent%%,*}" ;;
    esac
  done
  wants_json=0
  case "$*" in
    *"jsonpath"*) wants_json=0 ;;
    *"-o json"*|*"-ojson"*) wants_json=1 ;;
  esac
  if [[ "$wants_json" == "1" ]]; then
    if [[ -n "$ent" ]]; then
      f="$FIXDIR/entity-${ent}.json"
      if [[ -f "$f" ]]; then cat "$f"; else printf '%s' '{"items":[]}'; fi
    else
      cat "$FIXDIR/entities.json"
    fi
    exit 0
  fi
  if [[ -n "$ent" ]]; then
    if [[ -f "$FIXDIR/entity-${ent}.json" ]]; then printf '%s\n' "$ent"; fi
    exit 0
  fi
  for f in "$FIXDIR"/entity-*.json; do
    [[ -e "$f" ]] || continue
    b="$(basename "$f")"; b="${b#entity-}"; b="${b%.json}"
    printf '%s\n' "$b"
  done
  exit 0
fi
exit 0
"""


@pytest.fixture()
def sandbox(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    sysdir = tmp_path / "sys"
    sysdir.mkdir()
    fixdir = tmp_path / "fixtures"
    fixdir.mkdir()
    # A FAKE lineage-attach kit — stub scripts that log argv+env instead of the
    # real vendored ones. dg.sh is pointed here via DG_LINEAGE_ATTACH_DIR (the
    # test seam that replaced --cortex-local-path when ADR-0033 vendored the kit
    # into deploy/lineage-attach/).
    kitdir = tmp_path / "lineage-attach"
    kitdir.mkdir(parents=True)
    log = tmp_path / "kubectl.log"
    kitlog = tmp_path / "kit.log"

    _CONTROLLED = {"kubectl", "docker", "podman"}
    for tool in (
        "bash", "sh", "env", "basename", "dirname", "cat", "sed", "grep",
        "awk", "tr", "sort", "printf", "head", "tail", "cut", "uniq", "mktemp",
        "rm", "wc", "xargs", "python3", "jq", "sleep", "date",
    ):
        src = _which(tool)
        if src and Path(tool).name not in _CONTROLLED:
            (sysdir / tool).symlink_to(src)

    class _Sandbox:
        def __init__(self) -> None:
            self.bindir = bindir
            self.fixdir = fixdir
            self.kitdir = kitdir
            self.log = log
            self.kitlog = kitlog
            self.envflags: dict[str, str] = {}
            _make_bin(bindir, "docker", "exit 0\n")
            _make_bin(bindir, "podman", "exit 0\n")
            _make_bin(bindir, "kubectl", _FAKE_KUBECTL)
            self._write_kit()
            self.set_namespace({})

        def _write_kit(self) -> None:
            # The driven scripts (stubs that log argv+env), executable...
            _make_bin(self.kitdir, "sidecar-patch.sh", _STUB_SIDECAR_PATCH)
            _make_bin(self.kitdir, "sidecar-patch-proxy.sh", _STUB_SIDECAR_PATCH_PROXY)
            _make_bin(self.kitdir, "build-otel-shim.sh", _STUB_BUILD_SHIM)
            _make_bin(self.kitdir, "attach-lineage.sh", _STUB_ATTACH)
            # ...plus the sourced / build-input companions require_vendored_kit
            # also insists on (build-otel-shim.sh sources container-runtime.sh and
            # its docker build reads the Dockerfile + BOTH shims — the propagate
            # hook and the turn-span shim, ADR-0033 D4). A real vendored kit ships
            # them; the stub must too, or the integrity check refuses.
            for f in ("container-runtime.sh", "Dockerfile.otel-shim",
                      "lineage-propagate-hook.py", "rossoctl_turnspan.py",
                      "rossoctl_turnspan.pth"):
                (self.kitdir / f).write_text("# stub\n")

        def remove_kit_file(self, name: str) -> None:
            """Delete one kit file so the vendored-kit integrity check refuses."""
            p = self.kitdir / name
            if p.exists():
                p.unlink()

        def remove_kit(self) -> None:
            """Delete the driven scripts so the vendored-kit integrity check refuses."""
            for s in ("sidecar-patch.sh", "sidecar-patch-proxy.sh", "build-otel-shim.sh", "attach-lineage.sh"):
                self.remove_kit_file(s)

        def set_flag(self, **kw: str) -> None:
            self.envflags.update({k: str(v) for k, v in kw.items()})

        def set_namespace(self, entities: dict[str, dict]) -> None:
            items = []
            for name, spec in entities.items():
                sidecar = spec.get("sidecar")
                cm_name = None
                if sidecar is not None:
                    cm_name = f"authbridge-lineage-config-{name}"
                    if spec.get("lineage"):
                        data = _CM_WITH_LINEAGE
                    elif spec.get("enforcing"):
                        data = _CM_ENFORCING_NO_LINEAGE
                    else:
                        data = _CM_WITHOUT_LINEAGE
                    cm_doc = {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {"name": cm_name, "namespace": "travel-advisor"},
                        "data": {"config.yaml": data},
                    }
                    (fixdir / f"cm-{cm_name}.json").write_text(json.dumps(cm_doc))
                image = spec.get("image", "agent-examples-snp:latest")
                dep = _deployment(name, sidecar=sidecar, cm_name=cm_name, image=image)
                items.append(dep)
                (fixdir / f"entity-{name}.json").write_text(json.dumps({"items": [dep]}))

                # A live Pod mirroring the Deployment template (this model has NO
                # injected sidecars, so the admitted Pod matches the template). It
                # gives the instrument path a Running pod to CONFIRM a template
                # 'none' against — without one, get_pod_json returns {"items":[]}
                # and the mutating path refuses on an unconfirmed 'none' (a pod
                # scaled to 0 could hide a webhook-injected sidecar). A Pod carries
                # its containers/volumes at .spec directly, not .spec.template.spec.
                pod = {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        "name": f"{name}-abc123",
                        "namespace": "travel-advisor",
                        "labels": dep["metadata"]["labels"],
                    },
                    "spec": dep["spec"]["template"]["spec"],
                    "status": {"phase": "Running"},
                }
                (fixdir / f"pods-{name}.json").write_text(json.dumps({"items": [pod]}))
            (fixdir / "entities.json").write_text(json.dumps({"items": items}))

        def kubectl_calls(self) -> list[str]:
            if not log.exists():
                return []
            return [ln for ln in log.read_text().splitlines() if ln.strip()]

        def kit_log(self) -> str:
            return kitlog.read_text() if kitlog.exists() else ""

        def run(self, *args: str, kit_dir: str | None = "__default__", **kw):
            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{sysdir}"
            env["KUBECTL_LOG"] = str(log)
            env["KIT_LOG"] = str(kitlog)
            env["FIXDIR"] = str(fixdir)
            env.setdefault("KIND_CLUSTER", "rossoctl")
            # Point dg.sh at the FAKE stub kit (not the real vendored scripts,
            # which would kubectl-patch / build images) via the DG_LINEAGE_ATTACH_DIR
            # test seam — the same override pattern as DG_BUILD_AND_LOAD / DG_K8S_DIR.
            # (Replaces the retired --cortex-local-path flag; ADR-0033 vendored the
            # kit into deploy/lineage-attach/.)
            if kit_dir == "__default__":
                env["DG_LINEAGE_ATTACH_DIR"] = str(self.kitdir)
            elif kit_dir is not None:
                env["DG_LINEAGE_ATTACH_DIR"] = kit_dir
            env.update(self.envflags)
            env.update(kw.pop("env", {}) or {})
            argv = ["bash", str(DG_SH), *args]
            return subprocess.run(
                argv, capture_output=True, text=True, env=env, timeout=120, **kw
            )

    return _Sandbox()


# ===========================================================================
# AC: vendored-kit integrity check — refuse (mutate nothing) if the vendored
# kit is incomplete. ADR-0033 vendored the kit into deploy/lineage-attach/ and
# retired --cortex-local-path / CORTEX_LOCAL_PATH / resolve_kit_dir; the only
# surviving refusal is a broken-checkout invariant, not a missing-flag error.
# ===========================================================================


def test_instrument_refuses_when_vendored_kit_incomplete(sandbox) -> None:
    """A vendored kit missing its scripts is a broken checkout → refuse, mutate
    nothing (this is the integrity check that replaced resolve_kit_dir)."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.remove_kit()
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode != 0, "an incomplete vendored kit must refuse"
    combined = (r.stdout + r.stderr).lower()
    assert "lineage-attach" in combined or "kit" in combined, (
        f"refusal must name the missing kit; got:\n{combined}"
    )
    assert sandbox.kit_log() == "", "no kit script may run when scripts are absent"
    calls = " ".join(sandbox.kubectl_calls())
    for m in ("apply", "patch", "rollout restart"):
        assert m not in calls, f"a refused instrument must not {m!r}; calls={calls!r}"


@pytest.mark.parametrize(
    "missing",
    ["container-runtime.sh", "Dockerfile.otel-shim", "lineage-propagate-hook.py",
     "rossoctl_turnspan.py", "rossoctl_turnspan.pth"],
)
def test_instrument_refuses_when_kit_companion_file_absent(sandbox, missing) -> None:
    """The integrity check covers the WHOLE surface the drive path needs, not
    just the three entry scripts: build-otel-shim.sh sources container-runtime.sh
    and its docker build reads the Dockerfile + propagate hook. A checkout with
    the scripts but missing one of these must refuse BEFORE any mutation —
    otherwise the bake dies mid-run after ensure_envoy_config may have already
    written a ConfigMap, breaking the 'refuse, mutate nothing' promise."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.remove_kit_file(missing)
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode != 0, f"a kit missing {missing} must refuse"
    combined = (r.stdout + r.stderr).lower()
    assert "incomplete" in combined and missing.lower() in combined, (
        f"refusal must name the missing companion file {missing}; got:\n{combined}"
    )
    assert sandbox.kit_log() == "", "no kit script may run when the kit is incomplete"
    calls = " ".join(sandbox.kubectl_calls())
    for m in ("apply", "patch", "rollout restart", "create configmap"):
        assert m not in calls, f"a refused instrument must not {m!r}; calls={calls!r}"


# ===========================================================================
# AC (#241): the kit ships in-repo and `instrument` resolves it with NO cortex
# checkout and NO env override — the whole point of vendoring.
# ===========================================================================

_VENDORED_KIT = REPO_ROOT / "deploy" / "lineage-attach"


def test_vendored_kit_ships_in_repo_and_is_executable() -> None:
    """The lineage-attach kit is vendored into deploy/lineage-attach/ (ADR-0033);
    its three driven scripts are present and executable. This is the filesystem
    invariant require_vendored_kit checks — a broken checkout fails it loud."""
    assert _VENDORED_KIT.is_dir(), f"vendored kit dir must exist: {_VENDORED_KIT}"
    for s in ("sidecar-patch.sh", "sidecar-patch-proxy.sh", "build-otel-shim.sh", "attach-lineage.sh"):
        p = _VENDORED_KIT / s
        assert p.is_file(), f"vendored kit is missing {s}"
        assert os.access(p, os.X_OK), f"vendored kit script {s} is not executable"


def test_instrument_resolves_vendored_kit_without_cortex_checkout(sandbox) -> None:
    """With DG_LINEAGE_ATTACH_DIR unset and no cortex checkout on disk,
    `instrument` falls back to the vendored deploy/lineage-attach/ and clears the
    integrity preflight — proving the vendored kit is self-sufficient (#241 AC).

    Driven against an entity whose sidecar ALREADY has lineage wired so the run
    reaches the idempotent NO-OP decision (mutating nothing) before invoking the
    kit: we exercise the preflight/resolution against the REAL vendored kit
    without running its live-cluster attach against the fake kubectl."""
    sandbox.set_namespace({"payment-agent": {"sidecar": "envoy", "lineage": True}})
    # kit_dir=None → run() sets no DG_LINEAGE_ATTACH_DIR, so dg.sh uses its own
    # default (${SCRIPT_DIR}/lineage-attach), i.e. the real vendored kit.
    r = sandbox.run("namespace", "travel-advisor", "instrument", kit_dir=None)
    assert r.returncode == 0, r.stderr
    combined = (r.stdout + r.stderr).lower()
    # It got PAST the vendored-kit integrity check: no "missing/incomplete kit"
    # refusal, and it reached the per-entity idempotent no-op decision.
    assert "vendored lineage-attach kit is missing" not in combined
    assert "vendored lineage-attach kit is incomplete" not in combined
    assert ("already" in combined and ("wired" in combined or "lineage" in combined)) or "no-op" in combined, (
        f"instrument must reach the idempotent no-op decision via the vendored kit; got:\n{combined}"
    )


# ===========================================================================
# AC: component + tee preflight — refuse when the consumer side is not up
# ===========================================================================


def test_instrument_refuses_when_component_not_installed(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.set_flag(NS_PRESENT="0")
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode != 0, "instrument must refuse when the DG component is absent"
    assert sandbox.kit_log() == "", "kit must not run when the component is absent"


def test_instrument_refuses_when_tee_not_wired(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.set_flag(TEE_WIRED="0")
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode != 0, "instrument must refuse when the collector tee is absent"
    combined = (r.stdout + r.stderr).lower()
    assert "tee" in combined or "collector" in combined, (
        f"refusal must mention the missing tee; got:\n{combined}"
    )
    assert sandbox.kit_log() == "", "kit must not run when the tee is absent"


# ===========================================================================
# AC: decision table (ADR-0033 owner-split, #245). A no-sidecar entity is
# injected — PROXY by default (bare ns), ENVOY when the ns is envoy-configured.
# ===========================================================================


def _proxy_env_lines(kit_log: str) -> list[str]:
    return [ln for ln in kit_log.splitlines() if ln.startswith("sidecar-patch-proxy.sh env:")]


def _envoy_env_lines(kit_log: str) -> list[str]:
    # `sidecar-patch.sh` is a substring of `sidecar-patch-proxy.sh`, so match the
    # exact envoy stub prefix (which is NOT the proxy prefix).
    return [ln for ln in kit_log.splitlines() if ln.startswith("sidecar-patch.sh env:")]


def test_instrument_no_sidecar_bare_ns_drives_proxy_applier(sandbox) -> None:
    """A no-sidecar entity in a NON-envoy-configured namespace (the demo default)
    → the PROXY applier sidecar-patch-proxy.sh is driven with DEPLOY + NAMESPACE
    (ADR-0033 Decision 2). The envoy applier is NOT driven."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})  # ENVOY_CONFIG_TARGET defaults 0
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    kit = sandbox.kit_log()
    proxy = _proxy_env_lines(kit)
    assert proxy, f"a bare-ns no-sidecar row must drive the PROXY applier; kit log:\n{kit}"
    assert "DEPLOY=research-agent" in proxy[-1], f"applier must be told the deployment; kit:\n{kit}"
    assert "NAMESPACE=travel-advisor" in proxy[-1], f"applier must be told the namespace; kit:\n{kit}"
    assert not _envoy_env_lines(kit), (
        f"a bare namespace must NOT take the envoy branch; kit log:\n{kit}"
    )


def test_instrument_no_sidecar_envoy_configured_ns_drives_envoy_applier(sandbox) -> None:
    """A no-sidecar entity in an ALREADY envoy-configured namespace → the ENVOY
    applier sidecar-patch.sh is driven (ADR-0033 Decision 2: envoy only when the
    namespace already carries the platform envoy-config CM)."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.set_flag(ENVOY_CONFIG_TARGET="1")  # envoy-config present → envoy branch
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    kit = sandbox.kit_log()
    assert _envoy_env_lines(kit), f"an envoy-configured ns must drive the ENVOY applier; kit log:\n{kit}"
    assert "DEPLOY=research-agent" in _envoy_env_lines(kit)[-1]
    assert not _proxy_env_lines(kit), (
        f"an envoy-configured ns must NOT take the proxy branch; kit log:\n{kit}"
    )


def test_instrument_no_sidecar_python_bakes_shim(sandbox) -> None:
    """A no-sidecar PYTHON entity also drives the kit's build-otel-shim.sh and
    passes APP_CONTAINER/APP_IMAGE so LINEAGE_PROPAGATE=1 is set — the
    difference between one trace and N fragments (root CLAUDE.md §4). True on the
    default (proxy) branch."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    kit = sandbox.kit_log()
    assert "build-otel-shim.sh" in kit, f"no-sidecar row must bake the shim; kit log:\n{kit}"
    # and the attach must set the propagation switch on the app container.
    proxy = _proxy_env_lines(kit)
    assert proxy and "APP_CONTAINER=research-agent" in proxy[-1], (
        f"the attach must flip propagation on the app container; kit log:\n{kit}"
    )


def test_instrument_proxy_forwards_include_allowlist(sandbox) -> None:
    """The proxy applier is handed OUTBOUND_PORTS_INCLUDE (the include-only
    allowlist), the proxy path's egress knob — never the envoy path's EXCLUDE."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    r = sandbox.run(
        "namespace", "travel-advisor", "instrument",
        env={"OUTBOUND_PORTS_INCLUDE": "8080,8000,9000"},
    )
    assert r.returncode == 0, r.stderr
    proxy = _proxy_env_lines(sandbox.kit_log())
    assert proxy and "OUTBOUND_PORTS_INCLUDE=8080,8000,9000" in proxy[-1], (
        f"dg.sh must forward OUTBOUND_PORTS_INCLUDE to the proxy applier; env line={proxy[-1] if proxy else None!r}"
    )


# ---------------------------------------------------------------------------
# AC (#244): dg.sh forwards the operator's Kind cluster name into the bake.
#
# The bake `kind load`s the -otel image via the kit's container-runtime.sh,
# whose KIND_CLUSTER_NAME defaults to `rossoctl`. dg.sh drove the bake WITHOUT
# forwarding a cluster name, so on any cluster NOT named `rossoctl` the load
# targeted the wrong cluster (the #241-review gap, parked on #244). dg.sh must
# forward the operator's cluster name (aligning on KIND_CLUSTER_NAME, matching
# the kit) and bridge the DG-wide KIND_CLUSTER var too. The stub bake records
# the KIND_CLUSTER_NAME it received.
# ---------------------------------------------------------------------------


def _bake_cluster_name(kit_log: str) -> str | None:
    """The KIND_CLUSTER_NAME the bake stub recorded (or None if it never baked)."""
    m = re.search(r"build-otel-shim\.sh env: KIND_CLUSTER_NAME=(\S+)", kit_log)
    return m.group(1) if m else None


def test_instrument_forwards_operator_cluster_name_to_bake(sandbox) -> None:
    """KIND_CLUSTER_NAME set to a non-default cluster must reach the bake, so the
    -otel image lands in the operator's cluster rather than defaulting to
    `rossoctl` (#244; ADR-0033 D4)."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    r = sandbox.run(
        "namespace", "travel-advisor", "instrument",
        env={"KIND_CLUSTER_NAME": "my-lab", "KIND_CLUSTER": ""},
    )
    assert r.returncode == 0, r.stderr
    got = _bake_cluster_name(sandbox.kit_log())
    assert got == "my-lab", (
        f"dg.sh must forward KIND_CLUSTER_NAME to the bake; the bake saw {got!r} "
        f"(expected 'my-lab'). A silent default to 'rossoctl' loads the image into "
        f"the wrong cluster."
    )


def test_instrument_bridges_legacy_kind_cluster_var_to_bake(sandbox) -> None:
    """`build-and-load.sh` and the deploy docs use KIND_CLUSTER for the DG images.
    An operator who set only KIND_CLUSTER must still have it reach the bake, so
    the two image paths land on the SAME cluster rather than the bake defaulting
    to `rossoctl`."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    r = sandbox.run(
        "namespace", "travel-advisor", "instrument",
        env={"KIND_CLUSTER": "dev-cluster", "KIND_CLUSTER_NAME": ""},
    )
    assert r.returncode == 0, r.stderr
    got = _bake_cluster_name(sandbox.kit_log())
    assert got == "dev-cluster", (
        f"dg.sh must bridge the legacy KIND_CLUSTER var into the bake's "
        f"KIND_CLUSTER_NAME so both image paths hit one cluster; bake saw {got!r}"
    )


def test_instrument_bake_cluster_name_defaults_to_rossoctl(sandbox) -> None:
    """With neither cluster var set the historical default (`rossoctl`) is
    preserved — the fix forwards the operator's choice without changing the
    no-config default."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    r = sandbox.run(
        "namespace", "travel-advisor", "instrument",
        env={"KIND_CLUSTER": "", "KIND_CLUSTER_NAME": ""},
    )
    assert r.returncode == 0, r.stderr
    got = _bake_cluster_name(sandbox.kit_log())
    assert got == "rossoctl", (
        f"with no cluster var set the bake must still default to 'rossoctl'; saw {got!r}"
    )


def test_instrument_shim_refusal_falls_back_to_capture_only(sandbox) -> None:
    """A self-instrumenting app: the kit's bake interlock refuses the shim
    (exit 3). dg.sh must NOT abort the whole entity — it attaches capture only
    (the proxy applier without APP_CONTAINER)."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.set_flag(SHIM_REFUSES="1")
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, (
        f"a bake-interlock refusal is capture-only, not a failure; stderr={r.stderr!r}"
    )
    kit = sandbox.kit_log()
    proxy = _proxy_env_lines(kit)
    assert proxy, f"capture-only still attaches the sidecar (proxy applier); kit log:\n{kit}"
    # capture-only: APP_CONTAINER is NOT passed to the attach.
    assert "APP_CONTAINER= " in (proxy[-1] + " "), (
        f"a shim-refused entity must attach capture-only (no APP_CONTAINER); env line={proxy[-1]!r}"
    )


def test_instrument_shim_attestation_failure_dies_loud(sandbox) -> None:
    """A GENUINELY broken shim: the kit built it but attestation failed (exit 4,
    verify_inert / verify_propagates). This is NOT the sanctioned capture-only
    case — the shim does not propagate trace context, so dg.sh must fail loud
    (repeated hard AC "fail loud, never silently no-op") rather than silently
    attach capture-only and lose one-trace parenting."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.set_flag(SHIM_ATTEST_FAILS="1")
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode != 0, (
        f"an attestation-failed shim (exit 4) must halt, not downgrade to "
        f"capture-only; stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    combined = (r.stdout + r.stderr).lower()
    assert "attest" in combined or "shim" in combined, (
        f"the failure must name the broken shim; got:\n{combined}"
    )
    # It must NOT masquerade as an intentional capture-only choice.
    assert "capture-only" not in combined and "capture only" not in combined, (
        f"a broken bake must not be reported as capture-only; got:\n{combined}"
    )
    # And it must not have proceeded to attach the sidecar for this entity.
    assert not _proxy_env_lines(sandbox.kit_log()) and not _envoy_env_lines(sandbox.kit_log()), (
        "a broken shim must halt before the attach, not attach capture-only"
    )


def test_instrument_shim_build_error_dies_loud(sandbox) -> None:
    """A Docker build error (exit 1, not the exit-3 interlock) is a genuinely
    broken bake, not a sanctioned capture-only refusal → dg.sh must fail loud."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.set_flag(SHIM_BUILD_FAILS="1")
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode != 0, (
        f"a build-error bake (exit 1) must halt, not downgrade to capture-only; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    combined = (r.stdout + r.stderr).lower()
    assert "capture-only" not in combined and "capture only" not in combined, (
        f"a broken bake must not be reported as capture-only; got:\n{combined}"
    )
    assert not _proxy_env_lines(sandbox.kit_log()) and not _envoy_env_lines(sandbox.kit_log()), (
        "a broken shim must halt before the attach, not attach capture-only"
    )


def _assert_no_mutating_kubectl(sandbox, entity: str) -> None:
    """No entity that instrument SKIPS may cause a mutating kubectl call."""
    calls = " ".join(sandbox.kubectl_calls())
    for m in ("apply", "patch", "rollout restart", "edit", "replace", "delete"):
        assert m not in calls, (
            f"a skipped entity ({entity}) must mutate nothing; found {m!r} in calls={calls!r}"
        )


# ===========================================================================
# AC: sidecar present, no lineage-telemetry → APPEND in place (best-effort,
# verify-after-roll); sidecar present, lineage wired → no-op (ADR-0033 D3/D5).
# ===========================================================================


def _cm_applied(sandbox, entity: str) -> bool:
    """True if dg.sh applied the amended per-app CM for <entity> (the in-place
    append). The fake kubectl records an `appended-<cm>` marker on `apply -f -`."""
    return (sandbox.fixdir / f"appended-authbridge-lineage-config-{entity}").exists()


def test_instrument_envoy_sidecar_no_lineage_appends_in_place(sandbox) -> None:
    """An envoy-sidecar entity WITHOUT lineage-telemetry → dg.sh appends the
    plugin in place (ADR-0033 Decision 3), rolls, and verifies. The neither-
    applier is driven (this is dg.sh's own in-place edit, not a kit inject); the
    CM is re-applied with the plugin, and the run exits 0."""
    sandbox.set_namespace({"payment-agent": {"sidecar": "envoy", "lineage": False}})
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    kit = sandbox.kit_log()
    assert not _proxy_env_lines(kit) and not _envoy_env_lines(kit), (
        f"an in-place append must NOT drive either kit applier; kit log:\n{kit}"
    )
    assert _cm_applied(sandbox, "payment-agent"), (
        f"the append must re-apply the amended CM; kubectl calls:\n{sandbox.kubectl_calls()}"
    )
    combined = (r.stdout + r.stderr).lower()
    assert "append" in combined or "in place" in combined or "in-place" in combined, (
        f"the in-place append must be reported; got:\n{combined}"
    )


def test_instrument_proxy_sidecar_no_lineage_appends_in_place(sandbox) -> None:
    """A proxy-sidecar entity WITHOUT lineage-telemetry → in-place append too
    (origin-agnostic — any sidecar without the plugin gets it appended)."""
    sandbox.set_namespace({"legacy-agent": {"sidecar": "proxy", "lineage": False}})
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    assert _cm_applied(sandbox, "legacy-agent"), (
        f"the append must re-apply the amended proxy CM; calls:\n{sandbox.kubectl_calls()}"
    )
    # The appended CM body actually carries the plugin now.
    wired = sandbox.fixdir / "cm-authbridge-lineage-config-legacy-agent-wired.json"
    assert wired.exists() and "lineage-telemetry" in wired.read_text(), (
        "the applied CM must carry the appended lineage-telemetry plugin"
    )


def test_instrument_sidecar_lineage_wired_is_noop(sandbox) -> None:
    """A sidecar that ALREADY carries lineage-telemetry → no-op (idempotent, the
    no-marker re-run signal, ADR-0033 Decision 5). Nothing is mutated; exit 0."""
    sandbox.set_namespace({"payment-agent": {"sidecar": "envoy", "lineage": True}})
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    _assert_no_mutating_kubectl(sandbox, "payment-agent")
    assert not _cm_applied(sandbox, "payment-agent"), "an already-wired sidecar must not re-apply the CM"
    combined = (r.stdout + r.stderr).lower()
    assert "already" in combined and ("wired" in combined or "lineage" in combined) or "no-op" in combined, (
        f"an already-wired sidecar must be reported as an idempotent no-op; got:\n{combined}"
    )


def test_instrument_append_verify_clobber_warns_loud(sandbox) -> None:
    """Best-effort: after the append + roll, dg.sh re-reads the running sidecar's
    CM to VERIFY the plugin is live. When the operator clobbered it (the CM was
    regenerated without the plugin on the roll), dg.sh must WARN loudly rather
    than report a success it did not achieve (ADR-0033 Decision 3)."""
    sandbox.set_namespace({"legacy-agent": {"sidecar": "proxy", "lineage": False}})
    sandbox.set_flag(POD_CLOBBER="1")  # the verify re-read sees the OLD, unwired body
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    # A clobber is surfaced (a warning) — the run does not falsely claim success.
    combined = (r.stdout + r.stderr).lower()
    assert "clobber" in combined or "operator" in combined or "revert" in combined, (
        f"a verify-after-roll clobber must be warned about loudly; got:\n{combined}"
    )


def test_instrument_append_enforcing_pipeline_warns(sandbox) -> None:
    """If the existing sidecar carries auth plugins (jwt-validation /
    token-exchange), dg.sh must WARN that lineage will record 401s for
    unauthenticated callers — a property of that pipeline, not something the
    append can fix (ADR-0033 Decision 3)."""
    sandbox.set_namespace({"legacy-agent": {"sidecar": "proxy", "lineage": False, "enforcing": True}})
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    combined = (r.stdout + r.stderr).lower()
    assert "401" in combined or "enforc" in combined or "auth" in combined, (
        f"an enforcing pipeline must be warned about (401 risk); got:\n{combined}"
    )


# ===========================================================================
# AC: scoping — all entities, single entity, bad name is a loud error
# ===========================================================================


def test_instrument_all_entities_visits_each_by_owner_split(sandbox) -> None:
    """A whole-namespace instrument visits every entity and dispatches each by its
    own state: the no-sidecar one is proxy-injected (bare ns default), the
    sidecar-without-lineage one is appended in place — and the run continues past
    both, exiting 0."""
    sandbox.set_namespace(
        {
            "research-agent": {"sidecar": None},
            "payment-agent": {"sidecar": "envoy", "lineage": False},
        }
    )
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    kit = sandbox.kit_log()
    proxy = _proxy_env_lines(kit)
    assert proxy and "DEPLOY=research-agent" in proxy[-1], (
        f"the no-sidecar entity must be proxy-injected; kit:\n{kit}"
    )
    # the sidecar entity is NOT driven through an applier — it is appended in place.
    assert "DEPLOY=payment-agent" not in kit, (
        f"the sidecar entity must be appended in place, not driven through an applier; kit:\n{kit}"
    )
    assert _cm_applied(sandbox, "payment-agent"), "the sidecar entity's CM must be appended in place"


def test_instrument_single_entity_scopes_to_one(sandbox) -> None:
    sandbox.set_namespace(
        {
            "research-agent": {"sidecar": None},
            "payment-agent": {"sidecar": None},
        }
    )
    r = sandbox.run("namespace", "travel-advisor", "instrument", "research-agent")
    assert r.returncode == 0, r.stderr
    kit = sandbox.kit_log()
    assert "DEPLOY=research-agent" in kit, "must process the named entity"
    assert "DEPLOY=payment-agent" not in kit, (
        f"a single-entity instrument must not touch other entities; kit:\n{kit}"
    )


def test_instrument_unknown_entity_is_loud_error(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    r = sandbox.run("namespace", "travel-advisor", "instrument", "nope-agent")
    assert r.returncode != 0, "a name that is not an agent/tool must fail loud"
    combined = (r.stdout + r.stderr).lower()
    assert "nope-agent" in combined, "error must name the unresolved entity"
    assert sandbox.kit_log() == "", "a bad name must mutate nothing (kit never runs)"


# ===========================================================================
# AC: mixed namespace — each entity dispatched by its own state (owner-split):
# no-sidecar → proxy inject; sidecar-no-lineage → in-place append; sidecar-wired
# → no-op. The whole run exits 0.
# ===========================================================================


def test_instrument_mixed_namespace_dispatches_each_by_owner_split(sandbox) -> None:
    """One no-sidecar, one proxy-without-lineage, one envoy-already-wired entity
    in a bare namespace: the no-sidecar entity is proxy-injected (once), the
    proxy-without-lineage entity is appended in place, and the already-wired
    envoy entity is a no-op. The whole run exits 0."""
    sandbox.set_namespace(
        {
            "research-agent": {"sidecar": None},
            "legacy-agent": {"sidecar": "proxy", "lineage": False},
            "payment-agent": {"sidecar": "envoy", "lineage": True},
        }
    )
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    kit = sandbox.kit_log()
    # no-sidecar → proxy applier, exactly once (only research-agent).
    proxy = _proxy_env_lines(kit)
    assert len(proxy) == 1 and "DEPLOY=research-agent" in proxy[0], (
        f"only the no-sidecar entity drives the proxy applier, once; kit:\n{kit}"
    )
    # proxy-without-lineage → in-place append (CM re-applied, not an applier).
    assert "DEPLOY=legacy-agent" not in kit, "the sidecar entity must not drive an applier"
    assert _cm_applied(sandbox, "legacy-agent"), "the proxy-without-lineage entity must be appended in place"
    # envoy-already-wired → no-op (no CM re-apply).
    assert not _cm_applied(sandbox, "payment-agent"), "the already-wired entity must be a no-op"


# ===========================================================================
# AC: the presence of the platform envoy-config CM in the target ns is what
# routes a no-sidecar entity to the ENVOY branch (ADR-0033 Decision 2). Its
# absence — the demo default — routes to the PROXY branch. dg.sh detects it,
# it does not provision it (the copy-from-source path is retired: a bare ns just
# takes the proxy branch instead).
# ===========================================================================


def test_instrument_envoy_config_present_routes_to_envoy(sandbox) -> None:
    """envoy-config present in the target ns → the no-sidecar entity takes the
    ENVOY branch (the vendored envoy applier), NOT the proxy branch."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.set_flag(ENVOY_CONFIG_TARGET="1")
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    kit = sandbox.kit_log()
    assert _envoy_env_lines(kit), f"envoy-config present → envoy applier; kit:\n{kit}"
    assert not _proxy_env_lines(kit), f"envoy-config present must NOT take the proxy branch; kit:\n{kit}"


def test_instrument_envoy_config_absent_routes_to_proxy(sandbox) -> None:
    """envoy-config ABSENT in the target ns (the ad-hoc travel_advisor demo) → the
    no-sidecar entity takes the PROXY branch. dg.sh does NOT fail on the missing
    envoy-config and does NOT try to provision it — the proxy path needs none."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.set_flag(ENVOY_CONFIG_TARGET="0")
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    kit = sandbox.kit_log()
    assert _proxy_env_lines(kit), f"envoy-config absent → proxy applier; kit:\n{kit}"
    # It must NOT refuse for a missing envoy-config (that was the old always-envoy
    # behaviour), and must NOT try to CREATE one.
    combined = (r.stdout + r.stderr).lower()
    assert "missing" not in combined or "envoy-config" not in combined, (
        f"a bare ns must take the proxy branch, not refuse on missing envoy-config; got:\n{combined}"
    )
    creates = [c for c in sandbox.kubectl_calls() if "create" in c and "envoy-config" in c]
    assert not creates, f"the proxy branch must not provision envoy-config; calls={creates!r}"


# ===========================================================================
# AC: dg.sh forwards OUTBOUND_PORTS_EXCLUDE to the ENVOY applier (#184 gap) — the
# envoy sidecar's proxy-init would otherwise intercept the tools' plaintext
# non-HTTP egress (Postgres 5432 / SMTP 1025 / object store), breaking them. The
# proxy path uses the INCLUDE allowlist instead (tested above). Both only on the
# no-sidecar row; envoy needs an envoy-configured ns.
# ===========================================================================


def test_instrument_envoy_forwards_outbound_ports_exclude(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.set_flag(ENVOY_CONFIG_TARGET="1")  # envoy branch
    r = sandbox.run(
        "namespace", "travel-advisor", "instrument",
        env={"OUTBOUND_PORTS_EXCLUDE": "5432,1025,9000"},
    )
    assert r.returncode == 0, r.stderr
    sp_env = _envoy_env_lines(sandbox.kit_log())
    assert sp_env, f"the envoy applier must have run; kit log:\n{sandbox.kit_log()}"
    assert "OUTBOUND_PORTS_EXCLUDE=5432,1025,9000" in sp_env[-1], (
        f"dg.sh must forward OUTBOUND_PORTS_EXCLUDE to the envoy applier; env line={sp_env[-1]!r}"
    )


def test_instrument_envoy_no_ports_exclude_forwards_empty(sandbox) -> None:
    """Unset OUTBOUND_PORTS_EXCLUDE → dg.sh forwards nothing (kit default), never
    a fabricated port list (envoy branch)."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.set_flag(ENVOY_CONFIG_TARGET="1")
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    sp_env = _envoy_env_lines(sandbox.kit_log())
    assert sp_env, "the envoy applier must have run"
    assert "OUTBOUND_PORTS_EXCLUDE=\n" in (sp_env[-1] + "\n"), (
        f"unset → empty, not a fabricated list; env line={sp_env[-1]!r}"
    )


# ===========================================================================
# AC: never edits authbridge-runtime-config mode: (no sidecar-mode switch)
# ===========================================================================


def test_instrument_never_edits_runtime_config_mode(sandbox) -> None:
    """No verb path may write the namespace's authbridge-runtime-config mode:
    field (ADR-0031 decision 1). Reading it is fine; a mutating kubectl touching
    authbridge-runtime-config is not."""
    sandbox.set_namespace(
        {
            "research-agent": {"sidecar": None},
            "payment-agent": {"sidecar": "envoy", "lineage": False},
            "legacy-agent": {"sidecar": "proxy", "lineage": False},
        }
    )
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    for c in sandbox.kubectl_calls():
        if "authbridge-runtime-config" in c:
            assert not any(m in c for m in ("apply", "patch", "edit", "replace", "delete")), (
                f"instrument must never MUTATE authbridge-runtime-config (no mode switch); call={c!r}"
            )


# ===========================================================================
# AC: crash-loop guard — surface the kit's PRINTED back-out line, fail loud,
# do not proceed to the next entity
# ===========================================================================


def test_instrument_crashloop_surfaces_kit_backout_line(sandbox) -> None:
    """A crash-looping sidecar after the kit's attach: dg.sh must fail loud and
    surface the kit's PRINTED reverse-patch back-out line verbatim (captured
    from sidecar-patch.sh's stdout) — NOT synthesize a rollout-undo."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    # The kit's own rollout wait fails (crash-loop symptom).
    sandbox.set_flag(SP_ROLLOUT_FAILS="1")
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode != 0, "a failed rollout / crash-loop must fail loud"
    combined = r.stdout + r.stderr
    # The kit's reverse-patch back-out line, surfaced verbatim.
    assert ">> back out:" in combined, (
        f"dg.sh must surface the kit's printed back-out line; got:\n{combined}"
    )
    assert "--type strategic" in combined and "delete cm authbridge-lineage-config-research-agent" in combined, (
        f"the surfaced back-out must be the kit's reverse-patch line; got:\n{combined}"
    )
    # It must NOT be a synthesized rollout-undo (explicitly wrong per fbff6753).
    assert "rollout undo" not in combined.lower(), (
        f"the back-out is a reverse-patch, NOT a rollout undo; got:\n{combined}"
    )


def test_instrument_crashloop_stops_before_next_entity(sandbox) -> None:
    """On a crash-loop, dg.sh does NOT proceed to the next entity."""
    sandbox.set_namespace(
        {
            "research-agent": {"sidecar": None},
            "payment-agent": {"sidecar": None},
        }
    )
    sandbox.set_flag(SP_ROLLOUT_FAILS="1")
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode != 0
    # Exactly one entity was attempted before the loud stop — the failing applier
    # invocation. The second entity's attach must not have run.
    kit = sandbox.kit_log()
    deploys = _proxy_env_lines(kit)
    assert len(deploys) == 1, (
        f"a crash-loop must halt the loop, not roll on to the next entity; kit:\n{kit}"
    )


def test_instrument_crashloop_dumps_sidecar_log(sandbox) -> None:
    """On a crash-loop dg.sh prints the sidecar container log to help diagnose
    the unknown-plugin / config-skew boot crash."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.set_flag(SP_ROLLOUT_FAILS="1")
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode != 0
    combined = r.stdout + r.stderr
    assert "lineage-telemetry" in combined, (
        f"the sidecar log (carrying the unknown-plugin crash) must be surfaced; got:\n{combined}"
    )


# ===========================================================================
# AC: the kit is pointed at OUR receiver via the platform collector
# ===========================================================================


def test_instrument_points_kit_at_platform_collector(sandbox) -> None:
    """When dg.sh sets OTEL_ENDPOINT for the kit, it is the platform collector
    the component tee carries to the receiver (kit's own default too). Either
    dg.sh passes it explicitly or leaves the kit's default — but if it passes
    one, it must be the platform collector, never something else."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    kit = sandbox.kit_log()
    sp_env = _proxy_env_lines(kit)
    assert sp_env, f"the proxy applier must have run; kit:\n{kit}"
    line = sp_env[-1]
    # Extract OTEL_ENDPOINT=... token.
    tok = ""
    for t in line.split():
        if t.startswith("OTEL_ENDPOINT="):
            tok = t.split("=", 1)[1]
    if tok:  # dg.sh passed one explicitly
        assert "otel-collector.rossoctl-system" in tok, (
            f"an explicit OTEL_ENDPOINT must be the platform collector; got {tok!r}"
        )


# ===========================================================================
# AC: instrument is no longer the #181 stub
# ===========================================================================


def test_instrument_is_no_longer_a_stub(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    combined = (r.stdout + r.stderr).lower()
    assert "not implemented" not in combined and "stub" not in combined, (
        f"namespace instrument is a real verb as of #184; got:\n{combined}"
    )
