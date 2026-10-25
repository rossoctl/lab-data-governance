"""Behavioural tests for existing-proxy lineage activation.

The real script runs against fake kubectl and shim-builder commands. Tests pin
the namespace-wide preflight, application shim rollout, admission-generated
ConfigMap reconciliation, and fail-closed handling of non-Rossoctl workloads.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from tests.deploy.fake_configmap_kubectl import (
    CONFIGMAP_KUBECTL,
    RUNTIME_CONFIG_WITH_LINEAGE,
    RUNTIME_CONFIG_WITHOUT_LINEAGE,
)


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
    trusted_type: str | None = None,
) -> dict:
    app_port = 8001 if trusted_type == "tool" else 8081 if trusted_type == "agent" else 8080
    reverse_proxy_port = 8000 if trusted_type == "tool" else 8080
    forward_proxy_port = 8081 if trusted_type == "tool" else 8084
    containers = [
        {
            "name": name,  # the app container
            "image": image,
            "ports": [{"name": "http", "containerPort": app_port}],
            "env": ([
                {"name": "HTTP_PROXY", "value": f"http://127.0.0.1:{forward_proxy_port}"},
                {"name": "HTTPS_PROXY", "value": f"http://127.0.0.1:{forward_proxy_port}"},
                {"name": "NO_PROXY", "value": "127.0.0.1,localhost"},
            ] if trusted_type else []),
        }
    ]
    volumes: list[dict] = []
    if sidecar == "proxy":
        containers.append(
            {
                "name": "authbridge-proxy",
                "image": "ghcr.io/rossoctl/cortex/authbridge:lineage",
                "args": ["--config", "/etc/authbridge/config.yaml"],
                "ports": [
                    {"name": "reverse-proxy", "containerPort": reverse_proxy_port},
                    {"name": "forward-proxy", "containerPort": forward_proxy_port},
                ],
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
    labels = {
        "app.kubernetes.io/name": name,
        "app.kubernetes.io/component": "agent",
    }
    if trusted_type:
        labels["rossoctl.io/type"] = trusted_type
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": "travel-advisor",
            "labels": labels,
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
listener:
  forward_proxy_addr: :8084
  reverse_proxy_addr: :8080
  reverse_proxy_backend: http://127.0.0.1:8081
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

_CM_TOOL_ENFORCING_NO_LINEAGE = """mode: proxy-sidecar
listener:
  forward_proxy_addr: :8081
  reverse_proxy_addr: :8000
  reverse_proxy_backend: http://127.0.0.1:8001
pipeline:
  inbound:
    plugins:
      - name: jwt-validation
  outbound:
    plugins:
      - name: token-exchange
"""

# ---------------------------------------------------------------------------
_STUB_BUILD_SHIM = r"""
if [[ -n "${KIT_LOG:-}" ]]; then
  printf 'build-otel-shim.sh argv: %s\n' "$*" >> "$KIT_LOG"
  # Record the cluster name dg.sh forwarded (the kit's container-runtime.sh
  # reads KIND_CLUSTER_NAME; a bare default here would be `rossoctl`). #244:
  # dg.sh must forward the operator's cluster, not silently default.
  printf 'build-otel-shim.sh env: KIND_CLUSTER_NAME=%s\n' "${KIND_CLUSTER_NAME:-<unset>}" >> "$KIT_LOG"
fi
base="${1:-}"
if [[ "${base}" == "--attest-existing" ]]; then
  base="${2:-}"
  if [[ "${SHIM_ATTEST_FAILS:-0}" == "1" || "${base}" != *-otel:* && "${base}" != *-otel@* ]]; then
    echo "ATTESTATION FAILED for ${base}: gate on, but the hook did not come up." >&2
    exit 4
  fi
  echo ">> attested existing ${base}"
  exit 0
fi
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

# ---- AuthBridge admin API ---------------------------------------------------
if [[ "$*" == *"exec"* && "$*" == *" propagates"* ]]; then
  cat >/dev/null
  [[ "${LIVE_SHIM_ATTEST_FAILS:-0}" != "1" ]]
  exit
fi
if [[ "$*" == *"exec"* && "$*" == *"/v1/plugins"* ]]; then
  if [[ "${PLUGIN_CATALOG_MISSING:-0}" == "1" ]]; then
    printf '%s' '{"plugins":[{"name":"a2a-parser"},{"name":"mcp-parser"},{"name":"inference-parser"}]}'
  else
    printf '%s' '{"plugins":[{"name":"a2a-parser"},{"name":"mcp-parser"},{"name":"inference-parser"},{"name":"lineage-telemetry"}]}'
  fi
  exit 0
fi
# A pod runs the namespace lineage pipeline once admitted after the namespace
# ConfigMap carried it: from the start, or after a `rollout restart`.
if [[ "$*" == *"exec"* && "$*" == *"/v1/pipeline"* ]]; then
  ent=""
  for a in "$@"; do
    case "$a" in
      *-abc123) ent="${a%-abc123}"
        if [[ -f "$FIXDIR/restarted-$ent" ]]; then
          printf '%s\n' "Error from server (NotFound): pods \"$a\" not found" >&2; exit 1
        fi ;;
      *-rolled) ent="${a%-rolled}" ;;
    esac
  done
  if ! grep -q lineage-telemetry "$FIXDIR/cm-authbridge-runtime-config.json" && [[ ! -f "$FIXDIR/restarted-$ent" ]]; then
    printf '%s' '{"inbound":[{"name":"jwt-validation"}],"outbound":[{"name":"token-exchange"}]}'
    exit 0
  fi
  body='{"inbound":[{"name":"jwt-validation"},{"name":"a2a-parser"},{"name":"mcp-parser"},{"name":"inference-parser"},{"name":"lineage-telemetry","config":{"otel_endpoint":"otel-collector.rossoctl-system.svc.cluster.local:4317","capture_io":false,"namespace_file":"/var/run/secrets/kubernetes.io/serviceaccount/namespace"}}],"outbound":[{"name":"token-exchange"},{"name":"a2a-parser"},{"name":"mcp-parser"},{"name":"inference-parser"},{"name":"lineage-telemetry","config":{"otel_endpoint":"otel-collector.rossoctl-system.svc.cluster.local:4317","capture_io":false,"namespace_file":"/var/run/secrets/kubernetes.io/serviceaccount/namespace"}}]}'
  printf '%s' "${body}"
  exit 0
fi

if [[ "$*" == *"rollout restart deployment/"* ]]; then
  for a in "$@"; do
    case "$a" in deployment/*) : > "$FIXDIR/restarted-${a#deployment/}" ;; esac
  done
  exit 0
fi
if [[ "$*" == *"get agentruntimes"* ]]; then
  cat "$FIXDIR/agentruntimes.json"
  exit 0
fi
if [[ "$1" == "replace" && "${REPLACE_FAILS:-0}" == "1" ]]; then
  printf '%s\n' 'Error from server (Conflict): the object has been modified; please apply your changes to the latest version and try again' >&2
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
  if [[ -n "$ent" && -f "$f" ]]; then
    # A rollout restart replaces the pod, which comes back under a new name.
    if [[ -f "$FIXDIR/restarted-$ent" ]]; then sed "s/${ent}-abc123/${ent}-rolled/g" "$f"; else cat "$f"; fi
  else printf '%s' '{"items":[]}'; fi
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

""" + CONFIGMAP_KUBECTL + r"""
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
            # The shim builder is the only executable driven by dg.sh.
            _make_bin(self.kitdir, "build-otel-shim.sh", _STUB_BUILD_SHIM)
            # ...plus the sourced / build-input companions require_vendored_kit
            # also insists on (build-otel-shim.sh sources container-runtime.sh and
            # its docker build reads the Dockerfile + BOTH shims — the propagate
            # hook and the turn-span shim, ADR-0033 D4). A real vendored kit ships
            # them; the stub must too, or the integrity check refuses.
            for f in ("container-runtime.sh", "Dockerfile.otel-shim",
                      "otel-instrumentors.txt",
                      "lineage-propagate-hook.py", "rossoctl_turnspan.py",
                      "rossoctl_turnspan.pth", "attest-otel-shim.py"):
                (self.kitdir / f).write_text("# stub\n")
            (self.kitdir / "reconcile-existing-proxy.py").write_text(
                (REPO_ROOT / "deploy" / "lineage-attach" / "reconcile-existing-proxy.py").read_text()
            )

        def remove_kit_file(self, name: str) -> None:
            """Delete one kit file so the vendored-kit integrity check refuses."""
            p = self.kitdir / name
            if p.exists():
                p.unlink()

        def remove_kit(self) -> None:
            """Delete the driven scripts so the vendored-kit integrity check refuses."""
            self.remove_kit_file("build-otel-shim.sh")

        def set_flag(self, **kw: str) -> None:
            self.envflags.update({k: str(v) for k, v in kw.items()})

        def set_namespace(
            self,
            entities: dict[str, dict],
            *,
            runtime_lineage: bool = False,
            extra_runtimes: tuple[dict, ...] = (),
        ) -> None:
            runtimes = [
                {
                    "spec": {
                        "targetRef": {"kind": "Deployment", "name": name},
                        "pluginPreset": spec.get("preset", ""),
                    }
                }
                for name, spec in entities.items()
                if spec.get("trusted_type")
            ] + [{"spec": {"targetRef": target}} for target in extra_runtimes]
            (fixdir / "agentruntimes.json").write_text(json.dumps({"items": runtimes}))
            runtime = {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "authbridge-runtime-config",
                    "namespace": "travel-advisor",
                    "resourceVersion": "42",
                },
                "data": {
                    "config.yaml": RUNTIME_CONFIG_WITH_LINEAGE
                    if runtime_lineage
                    else RUNTIME_CONFIG_WITHOUT_LINEAGE
                },
            }
            (fixdir / "cm-authbridge-runtime-config.json").write_text(json.dumps(runtime))
            items = []
            for name, spec in entities.items():
                sidecar = spec.get("sidecar")
                cm_name = None
                if sidecar is not None:
                    cm_name = f"authbridge-lineage-config-{name}"
                    if spec.get("lineage"):
                        data = _CM_WITH_LINEAGE
                    elif spec.get("enforcing"):
                        data = (
                            _CM_TOOL_ENFORCING_NO_LINEAGE
                            if spec.get("trusted_type") == "tool"
                            else _CM_ENFORCING_NO_LINEAGE
                        )
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
                dep = _deployment(
                    name,
                    sidecar=sidecar,
                    cm_name=cm_name,
                    image=image,
                    trusted_type=spec.get("trusted_type"),
                )
                if spec.get("activated"):
                    dep["spec"]["template"]["spec"]["containers"][0]["env"].append(
                        {"name": "LINEAGE_PROPAGATE", "value": "1"}
                    )
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
                    "status": {
                        "phase": "Running",
                        "conditions": [{"type": "Ready", "status": "True"}],
                        "containerStatuses": [
                            {"name": container["name"], "ready": True}
                            for container in dep["spec"]["template"]["spec"]["containers"]
                        ],
                    },
                }
                pod_items = [pod]
                if spec.get("stale_pod"):
                    stale = json.loads(json.dumps(pod))
                    stale["metadata"]["name"] = f"{name}-old123"
                    stale["metadata"]["deletionTimestamp"] = "2026-09-23T00:00:00Z"
                    stale["metadata"]["creationTimestamp"] = "2026-09-22T00:00:00Z"
                    stale["spec"]["containers"][0]["env"] = []
                    pod["metadata"]["creationTimestamp"] = "2026-09-23T00:00:00Z"
                    pod_items.insert(0, stale)
                (fixdir / f"pods-{name}.json").write_text(json.dumps({"items": pod_items}))
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
            # A pipeline that never converges fails in seconds, not 180 s, and the
            # canonical endpoint is dg.sh's own, whatever the caller's shell exports.
            env["DG_PIPELINE_VERIFY_ATTEMPTS"] = "3"
            env.pop("OTEL_ENDPOINT", None)
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
    ["container-runtime.sh", "Dockerfile.otel-shim", "otel-instrumentors.txt",
     "lineage-propagate-hook.py",
     "rossoctl_turnspan.py", "rossoctl_turnspan.pth", "attest-otel-shim.py"],
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


@pytest.mark.parametrize(
    "missing",
    ["build-otel-shim.sh"],
)
def test_instrument_refuses_when_a_driven_script_absent(sandbox, missing) -> None:
    """The integrity preflight requires the shim builder before mutation."""
    sandbox.set_namespace({"research-agent": {"sidecar": None}})
    sandbox.remove_kit_file(missing)
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode != 0, f"a kit missing {missing} must refuse"
    combined = (r.stdout + r.stderr).lower()
    assert "incomplete" in combined and missing.lower() in combined, (
        f"refusal must name the missing driven script {missing}; got:\n{combined}"
    )
    assert sandbox.kit_log() == "", "no kit script may run when the kit is incomplete"
    calls = " ".join(sandbox.kubectl_calls())
    # Crucially, nothing was built/loaded/applied — the refuse happens up front.
    for m in ("apply", "patch", "rollout restart", "create configmap"):
        assert m not in calls, f"a refused instrument must not {m!r}; calls={calls!r}"


# ===========================================================================
# AC (#241): the kit ships in-repo and `instrument` resolves it with NO cortex
# checkout and NO env override — the whole point of vendoring.
# ===========================================================================

_VENDORED_KIT = REPO_ROOT / "deploy" / "lineage-attach"


def test_vendored_kit_ships_in_repo_and_is_executable() -> None:
    """The lineage-attach kit is vendored into deploy/lineage-attach/ (ADR-0033);
    its driven shim builder is present and executable. This is the filesystem
    invariant require_vendored_kit checks — a broken checkout fails it loud."""
    assert _VENDORED_KIT.is_dir(), f"vendored kit dir must exist: {_VENDORED_KIT}"
    for s in ("build-otel-shim.sh",):
        p = _VENDORED_KIT / s
        assert p.is_file(), f"vendored kit is missing {s}"
        assert os.access(p, os.X_OK), f"vendored kit script {s} is not executable"


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


_TRUSTED_AGENT = {
    "sidecar": "proxy",
    "lineage": False,
    "enforcing": True,
    "trusted_type": "agent",
}


def test_trusted_proxy_writes_namespace_lineage_before_rolling(sandbox) -> None:
    sandbox.set_namespace(
        {"travel-advisor": _TRUSTED_AGENT, "demo-client": {"sidecar": None}}
    )

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode == 0, result.stderr
    calls = sandbox.kubectl_calls()
    # The shim first: the namespace write may roll every workload in <ns>.
    patch_i = next(i for i, call in enumerate(calls) if "patch deployment/travel-advisor" in call)
    replace_i = next(i for i, call in enumerate(calls) if call.startswith("replace -f -"))
    restart_i = next(i for i, call in enumerate(calls) if "rollout restart deployment/travel-advisor" in call)
    assert patch_i < replace_i < restart_i
    # A rollout Rossoctl starts for the new ConfigMap is waited out before the probe.
    after = calls[replace_i:]
    status_i = next(i for i, call in enumerate(after) if "rollout status deployment/travel-advisor" in call)
    probe_i = next(i for i, call in enumerate(after) if "/v1/pipeline" in call)
    assert status_i < probe_i
    # Admission rebuilds the per-workload ConfigMap; dg.sh never writes it.
    assert not any(call.startswith("apply -f -") for call in calls)
    written = json.loads((sandbox.fixdir / "cm-authbridge-runtime-config-wired.json").read_text())
    assert written["metadata"]["resourceVersion"] == "42"
    assert written["data"]["config.yaml"] == RUNTIME_CONFIG_WITH_LINEAGE
    assert not any("demo-client" in call and "patch deployment" in call for call in calls)
    assert sandbox.kit_log().count("build-otel-shim.sh argv:") == 2


def test_rerun_neither_rewrites_nor_rolls_a_live_workload(sandbox) -> None:
    sandbox.set_namespace({"travel-advisor": _TRUSTED_AGENT}, runtime_lineage=True)

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode == 0, result.stderr
    calls = sandbox.kubectl_calls()
    assert not any(call.startswith(("replace", "apply")) for call in calls)
    assert not any("rollout restart" in call for call in calls)
    assert "already runs the namespace lineage pipeline" in result.stderr


def test_a_failed_namespace_write_rolls_nothing_onto_lineage(sandbox) -> None:
    sandbox.set_namespace({"travel-advisor": _TRUSTED_AGENT})
    sandbox.set_flag(REPLACE_FAILS="1")

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode != 0
    assert "changed since it was read" in result.stderr
    assert not any("rollout restart" in call for call in sandbox.kubectl_calls())


def test_instrument_refuses_a_namespace_configmap_outside_the_envelope(sandbox) -> None:
    sandbox.set_namespace({"travel-advisor": _TRUSTED_AGENT})
    runtime = sandbox.fixdir / "cm-authbridge-runtime-config.json"
    document = json.loads(runtime.read_text())
    document["data"]["config.yaml"] = "pipeline: {}\n"
    runtime.write_text(json.dumps(document))

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode != 0
    assert "outside the safe reconciliation envelope. Mutating nothing." in result.stderr
    assert not any("patch deployment/" in call for call in sandbox.kubectl_calls())
    assert sandbox.kit_log() == ""


@pytest.mark.parametrize(
    ("entity", "extra", "expected"),
    [
        ({**_TRUSTED_AGENT, "preset": "full"}, (), "sets pluginPreset 'full'"),
        (_TRUSTED_AGENT, ({"kind": "Deployment", "name": "ledger"},), "Deployment/ledger"),
        (
            _TRUSTED_AGENT,
            ({"kind": "StatefulSet", "name": "travel-advisor"},),
            "StatefulSet/travel-advisor",
        ),
    ],
)
def test_instrument_refuses_agentruntimes_the_namespace_pipeline_cannot_serve(
    sandbox, entity, extra, expected
) -> None:
    sandbox.set_namespace({"travel-advisor": entity}, extra_runtimes=extra)

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode != 0
    assert expected in result.stderr
    assert "Mutating nothing" in result.stderr
    assert not any("patch deployment/" in call for call in sandbox.kubectl_calls())
    assert sandbox.kit_log() == ""


def test_entity_instrument_waits_for_a_namespace_wide_first_run(sandbox) -> None:
    tool = {**_TRUSTED_AGENT, "trusted_type": "tool"}
    sandbox.set_namespace({"travel-advisor": _TRUSTED_AGENT, "charge-card": tool})

    refused = sandbox.run("namespace", "travel-advisor", "instrument", "charge-card")

    assert refused.returncode != 0
    assert "Run 'dg.sh namespace travel-advisor instrument' first" in refused.stderr
    assert "Mutating nothing" in refused.stderr
    calls = "\n".join(sandbox.kubectl_calls())
    assert "patch deployment/" not in calls
    assert "replace -f -" not in calls
    assert sandbox.kit_log() == ""

    sandbox.set_namespace(
        {"travel-advisor": _TRUSTED_AGENT, "charge-card": tool}, runtime_lineage=True
    )
    result = sandbox.run("namespace", "travel-advisor", "instrument", "charge-card")

    assert result.returncode == 0, result.stderr
    patches = [call for call in sandbox.kubectl_calls() if "patch deployment/" in call]
    assert len(patches) == 1 and "patch deployment/charge-card" in patches[0]
    assert not any(call.startswith("replace") for call in sandbox.kubectl_calls())


def test_instrument_refuses_without_the_namespace_runtime_configmap(sandbox) -> None:
    sandbox.set_namespace({"travel-advisor": _TRUSTED_AGENT})
    sandbox.set_flag(CM_FORCE_NOTFOUND="authbridge-runtime-config")

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode != 0
    assert "authbridge-runtime-config" in result.stderr
    assert "Mutating nothing" in result.stderr
    assert not any("patch deployment/" in call for call in sandbox.kubectl_calls())
    assert sandbox.kit_log() == ""


def test_status_reports_a_namespace_configmap_without_lineage(sandbox) -> None:
    # The next admission renders the namespace ConfigMap, whatever the pod runs now.
    running = {
        "sidecar": "proxy",
        "lineage": True,
        "trusted_type": "agent",
        "image": "travel-advisor-otel:latest",
        "activated": True,
    }
    sandbox.set_namespace({"travel-advisor": running})

    lost = sandbox.run("namespace", "travel-advisor", "status")

    assert lost.returncode == 0, lost.stderr
    assert (
        "live=no (namespace authbridge-runtime-config differs from the canonical lineage pipeline"
        in lost.stdout
    )

    sandbox.set_namespace({"travel-advisor": running}, runtime_lineage=True)
    kept = sandbox.run("namespace", "travel-advisor", "status")

    assert kept.returncode == 0, kept.stderr
    assert "live=yes (live)" in kept.stdout


def test_namespace_instrument_rolls_tools_before_agents(sandbox) -> None:
    sandbox.set_namespace(
        {
            "a-agent": {
                "sidecar": "proxy", "enforcing": True, "trusted_type": "agent",
            },
            "b-tool": {
                "sidecar": "proxy", "enforcing": True, "trusted_type": "tool",
            },
            "c-agent": {
                "sidecar": "proxy", "enforcing": True, "trusted_type": "agent",
            },
            "d-tool": {
                "sidecar": "proxy", "enforcing": True, "trusted_type": "tool",
            },
        }
    )

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode == 0, result.stderr
    patches = [
        call.split("patch deployment/", 1)[1].split()[0]
        for call in sandbox.kubectl_calls()
        if "patch deployment/" in call
    ]
    assert patches == ["b-tool", "d-tool", "a-agent", "c-agent"]


def test_mixed_namespace_instruments_trusted_and_reports_legacy(sandbox) -> None:
    sandbox.set_namespace(
        {
            "travel-advisor": {
                "sidecar": "proxy",
                "lineage": False,
                "enforcing": True,
                "trusted_type": "agent",
            },
            "research-agent": {"sidecar": None},
        }
    )

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode == 0, result.stderr
    calls = sandbox.kubectl_calls()
    assert any("patch deployment/travel-advisor" in call for call in calls)
    assert not any("patch deployment/research-agent" in call for call in calls)
    assert not any("get pods" in call and "research-agent" in call for call in calls)
    assert sandbox.kit_log().count("build-otel-shim.sh argv:") == 2

    status = sandbox.run("namespace", "travel-advisor", "status")
    assert status.returncode == 0, status.stderr
    assert "travel-advisor\tsidecar=present" in status.stdout
    assert "research-agent\tsidecar=none" in status.stdout


def test_trusted_tool_accepts_the_platform_tool_proxy_port_contract(sandbox) -> None:
    sandbox.set_namespace(
        {
            "charge-card": {
                "sidecar": "proxy",
                "lineage": False,
                "enforcing": True,
                "trusted_type": "tool",
            }
        }
    )

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode == 0, result.stderr
    assert any(
        "patch deployment/charge-card" in call for call in sandbox.kubectl_calls()
    )
    patch_call = next(
        call for call in sandbox.kubectl_calls() if "patch deployment/charge-card" in call
    )
    assert '"imagePullPolicy": "IfNotPresent"' in patch_call


def test_trusted_proxy_ignores_a_terminating_old_pod(sandbox) -> None:
    sandbox.set_namespace(
        {
            "travel-advisor": {
                "sidecar": "proxy",
                "lineage": False,
                "enforcing": True,
                "trusted_type": "agent",
                "stale_pod": True,
            }
        }
    )

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode == 0, result.stderr


def test_trusted_proxy_missing_producer_plugin_fails_before_mutation(sandbox) -> None:
    sandbox.set_namespace(
        {
            "travel-advisor": {
                "sidecar": "proxy",
                "lineage": False,
                "enforcing": True,
                "trusted_type": "agent",
            }
        }
    )
    sandbox.set_flag(PLUGIN_CATALOG_MISSING="1")

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode != 0
    assert "lineage-capable" in result.stderr
    calls = "\n".join(sandbox.kubectl_calls())
    assert "patch deployment/" not in calls
    assert "apply -f -" not in calls
    assert "replace -f -" not in calls


def test_trusted_proxy_existing_otel_image_is_re_attested(sandbox) -> None:
    sandbox.set_namespace(
        {
            "travel-advisor": {
                "sidecar": "proxy",
                "lineage": False,
                "enforcing": True,
                "trusted_type": "agent",
                "image": "travel-advisor-otel:latest",
            }
        }
    )

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode == 0, result.stderr
    assert (
        "build-otel-shim.sh argv: --attest-existing travel-advisor-otel:latest"
        in sandbox.kit_log()
    )


def test_trusted_proxy_existing_otel_image_refuses_failed_attestation(sandbox) -> None:
    sandbox.set_namespace(
        {
            "travel-advisor": {
                "sidecar": "proxy",
                "lineage": False,
                "enforcing": True,
                "trusted_type": "agent",
                "image": "misnamed-otel:latest",
            }
        }
    )
    sandbox.set_flag(SHIM_ATTEST_FAILS="1")

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode != 0
    assert "failed preflight" in result.stderr
    assert not any("patch deployment/" in call for call in sandbox.kubectl_calls())


def test_status_explains_trusted_proxy_activation_drift(sandbox) -> None:
    sandbox.set_namespace(
        {
            "travel-advisor": {
                "sidecar": "proxy",
                "lineage": True,
                "trusted_type": "agent",
            }
        }
    )

    result = sandbox.run("namespace", "travel-advisor", "status")

    assert result.returncode == 0, result.stderr
    assert "live=no" in result.stdout
    assert "application shim or LINEAGE_PROPAGATE missing" in result.stdout


def test_status_attests_the_running_application_not_the_local_image(sandbox) -> None:
    sandbox.set_namespace(
        {
            "travel-advisor": {
                "sidecar": "proxy",
                "lineage": True,
                "trusted_type": "agent",
                "image": "opaque-registry.example/app:any-tag",
                "activated": True,
            }
        }
    )
    sandbox.set_flag(LIVE_SHIM_ATTEST_FAILS="1")

    result = sandbox.run("namespace", "travel-advisor", "status")

    assert result.returncode == 0, result.stderr
    assert "live=no (running application failed two-shim attestation)" in result.stdout
    assert "--attest-existing" not in sandbox.kit_log()


def test_instrument_refuses_non_rossoctl_workload_before_mutation(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": None}})

    result = sandbox.run("namespace", "travel-advisor", "instrument", "research-agent")

    assert result.returncode != 0
    assert "not a trusted Rossoctl agent/tool" in result.stderr
    assert "Deploy or import agents/tools through Rossoctl" in result.stderr
    calls = "\n".join(sandbox.kubectl_calls())
    assert "patch deployment/" not in calls
    assert "apply -f -" not in calls
    assert sandbox.kit_log() == ""


def test_legacy_only_namespace_has_no_trusted_instrumentation_targets(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": None}})

    result = sandbox.run("namespace", "travel-advisor", "instrument")

    assert result.returncode == 0, result.stderr
    assert "no trusted Rossoctl agents/tools found" in result.stderr
    assert not any("patch deployment/" in call for call in sandbox.kubectl_calls())
    assert sandbox.kit_log() == ""
