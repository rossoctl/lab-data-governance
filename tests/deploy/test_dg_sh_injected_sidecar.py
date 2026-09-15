"""Behavioural tests for dg.sh detecting a WEBHOOK-INJECTED pod-level sidecar.

The platform injection WEBHOOK can attach the AuthBridge `authbridge-proxy`
sidecar (e.g. when the rossoctl UI adds an agent). The webhook injects at the
POD level: the admitted Pod gets the sidecar container, but the owning
Deployment's `.spec.template.spec.containers` stays clean (app container only).

dg.sh's original sidecar detection was ENTIRELY Deployment-template based, so for
a webhook-injected sidecar it saw the clean template, returned `none`, and
reported `sidecar=none` for a workload whose live Pod is in fact running
`authbridge-proxy`. These tests pin the fix: detection FALLS BACK to the live Pod
when the Deployment template shows no sidecar, and resolves the sidecar's
pipeline ConfigMap from the Pod spec in that case. This detection matters for
`instrument` too — under the ADR-0033 owner-split (#245) its role is to SEE an
injected sidecar so `instrument` routes the entity correctly: an injected sidecar
WITHOUT lineage is APPENDED to in place (best-effort, verify-after-roll), one
already carrying lineage is a NO-OP — never a SECOND sidecar injected onto an
entity that already has one.

Same harness as ``test_dg_sh_namespace_status.py`` /
``test_dg_sh_instrument.py``: the real script runs as a subprocess with a **fake
``kubectl``** on a synthetic ``PATH`` — no live cluster. The fake here ALSO
serves ``kubectl get pods ... -o json`` so a workload can carry a sidecar in its
live Pod but not in its Deployment template (the injected case).
"""

from __future__ import annotations

import json
import os
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
# Container / volume shapes shared by the Deployment-template and Pod models.
# A proxy sidecar container named `authbridge-proxy` mounts /etc/authbridge from
# the `authbridge-runtime` volume, backed by ConfigMap ``cm_name`` — the source
# of truth for plugin detection. `lineage` toggles whether that CM carries the
# lineage-telemetry plugin entry.
# ---------------------------------------------------------------------------


def _app_container(name: str) -> dict:
    return {
        "name": name,
        "image": "agent-examples-snp:latest",
        "ports": [{"containerPort": 8080}],
    }


def _proxy_container() -> dict:
    return {
        "name": "authbridge-proxy",
        "image": "ghcr.io/rossoctl/cortex/authbridge:lineage",
        "args": ["--config", "/etc/authbridge/config.yaml"],
        "volumeMounts": [
            {"name": "authbridge-runtime", "mountPath": "/etc/authbridge"}
        ],
    }


def _envoy_container() -> dict:
    return {
        "name": "envoy-proxy",
        "image": "ghcr.io/rossoctl/cortex/authbridge-envoy:latest",
        "args": ["--config", "/etc/authbridge/config.yaml"],
        "volumeMounts": [
            {"name": "envoy-config", "mountPath": "/etc/envoy"},
            {"name": "authbridge-runtime", "mountPath": "/etc/authbridge"},
        ],
    }


def _runtime_volume(cm_name: str) -> dict:
    return {"name": "authbridge-runtime", "configMap": {"name": cm_name}}


def _sidecar_container(sidecar: str) -> dict:
    return _proxy_container() if sidecar == "proxy" else _envoy_container()


def _native_sidecar_container(sidecar: str) -> dict:
    """A sidecar shaped as a NATIVE sidecar (an initContainer with
    ``restartPolicy: Always``, k8s >= 1.29). Same container body as the ordinary
    sidecar — it is the PLACEMENT (in ``initContainers``, not ``containers``) that
    differs. This is exactly how the #852 kit attaches ``envoy-proxy`` (dg.sh
    comment ~line 761), and how a live instrumented pod carries it."""
    c = _sidecar_container(sidecar)
    c["restartPolicy"] = "Always"
    return c


def _proxy_init_container() -> dict:
    """The kit's iptables-redirect init step (an ordinary, run-to-completion
    init container that is NOT the sidecar). Present in the real instrumented
    pod alongside the native ``envoy-proxy`` sidecar; included so detection must
    pick the sidecar out of an initContainers list that also holds a non-sidecar
    init container (never misread ``proxy-init`` as the sidecar)."""
    return {
        "name": "proxy-init",
        "image": "ghcr.io/rossoctl/cortex/proxy-init:latest",
    }


def _deployment(
    name: str,
    *,
    template_sidecar: str | None,
    cm_name: str | None,
    native: bool = False,
) -> dict:
    """Build a Deployment doc for entity ``name``.

    ``template_sidecar`` is the sidecar embedded in the DEPLOYMENT TEMPLATE
    (None | "proxy" | "envoy") — the manual-attach / #852-kit shape. The
    webhook-injected shape leaves this None (clean template) and carries the
    sidecar in the live Pod only (see ``_pod``).

    ``native`` places that template sidecar in ``initContainers`` as a native
    sidecar (the #852-kit shape) instead of an ordinary ``containers`` entry.
    """
    containers = [_app_container(name)]
    init_containers: list[dict] = []
    volumes: list[dict] = []
    if template_sidecar is not None:
        if native:
            init_containers.append(_proxy_init_container())
            init_containers.append(_native_sidecar_container(template_sidecar))
        else:
            containers.append(_sidecar_container(template_sidecar))
        volumes.append(_runtime_volume(cm_name))
    pod_spec: dict = {"containers": containers, "volumes": volumes}
    if init_containers:
        pod_spec["initContainers"] = init_containers
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
        "spec": {"template": {"spec": pod_spec}},
    }


def _pod(
    name: str,
    *,
    pod_sidecar: str | None,
    cm_name: str | None,
    native: bool = False,
) -> dict:
    """Build a Running Pod doc for entity ``name``.

    A Pod's containers/volumes live at ``.spec.containers`` / ``.spec.volumes``
    DIRECTLY (no ``.template``). ``pod_sidecar`` is the sidecar the webhook
    injected into the admitted Pod (None | "proxy" | "envoy").

    ``native`` places that sidecar in ``.spec.initContainers`` (the kit / native
    shape) instead of ``.spec.containers``.
    """
    containers = [_app_container(name)]
    init_containers: list[dict] = []
    volumes: list[dict] = []
    if pod_sidecar is not None:
        if native:
            init_containers.append(_proxy_init_container())
            init_containers.append(_native_sidecar_container(pod_sidecar))
        else:
            containers.append(_sidecar_container(pod_sidecar))
        volumes.append(_runtime_volume(cm_name))
    spec: dict = {"containers": containers, "volumes": volumes}
    if init_containers:
        spec["initContainers"] = init_containers
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"{name}-abc123",
            "namespace": "travel-advisor",
            "labels": {
                "app.kubernetes.io/name": name,
                "app.kubernetes.io/component": "agent",
            },
        },
        "spec": spec,
        "status": {"phase": "Running"},
    }


_CM_WITH_LINEAGE = """mode: proxy-sidecar
pipeline:
  inbound:
    plugins:
      - name: a2a-parser
      - name: mcp-parser
      - name: lineage-telemetry
        config:
          otel_endpoint: "otel-collector.rossoctl-system.svc.cluster.local:4317"
  outbound:
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


# The fake kubectl. Serves, keyed off argv:
#   * version                                    → exit 0
#   * get configmap <name> -o jsonpath={.data}   → Go-map[...] repr of .data
#   * get deployments ... -o json                → entities.json / entity-<n>.json
#   * get deployments ... -o jsonpath            → entity NAMES
#   * get pods ... -o json                       → pods-<entity>.json (INJECTED)
# and logs argv to $KUBECTL_LOG so tests can assert what was queried / mutated.
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

# ---- component / tee / egress preflight probes (instrument path) ------------
if [[ "$*" == *"get"* && "$*" == *"namespace"* && "$*" == *"data-governance"* ]]; then
  printf '%s\n' "data-governance"; exit 0
fi
if [[ "$*" == *"get"* && "$*" == *"otel-collector-config"* ]]; then
  printf '%s' "traces/data_governance"; exit 0
fi
if [[ "$*" == *"get"* && "$*" == *"data-governance-receiver"* ]]; then
  printf '%s\n' "1"; exit 0
fi
if [[ "$*" == *"get"* && "$*" == *"authbridge-runtime-config"* ]]; then
  printf '%s' "${EGRESS_ENFORCEMENT:-enforce}"; exit 0
fi

# ---- ConfigMap fetch ---------------------------------------------------------
if [[ "$*" == *"get"* && ( "$*" == *"configmap"* || "$*" == *" cm "* || "$*" == *" cm"* ) ]]; then
  cmname=""; prev=""
  for a in "$@"; do
    case "$prev" in configmap|cm|configmaps) cmname="$a"; break ;; esac
    prev="$a"
  done
  if [[ "$cmname" == "$CM_FORCE_NOTFOUND" ]]; then
    printf '%s\n' "Error from server (NotFound): configmaps \"${cmname}\" not found" >&2
    exit 1
  fi
  if [[ "${CM_GET_FAILS:-0}" == "1" ]]; then
    printf '%s\n' "Error from server (InternalError): an error on the server (\"\") has prevented the request from succeeding" >&2
    exit 1
  fi
  f="$FIXDIR/cm-${cmname}.json"
  # After an in-place append writes the CM, an `appended-<cm>` marker records that
  # the wired body is now live — unless POD_CLOBBER=1 (the operator reverted it).
  if [[ -f "$FIXDIR/appended-${cmname}" && "${POD_CLOBBER:-0}" != "1" ]]; then
    f="$FIXDIR/cm-${cmname}-wired.json"
  fi
  if [[ -n "$cmname" && -f "$f" ]]; then
    # A `{.data.config\.yaml}` read wants the raw config.yaml body (append reads +
    # verifies); a `{.data}` read wants the Go-map repr (status/detection).
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
if [[ "$*" == *"create configmap"* && "$*" == *"--dry-run=client"* ]]; then
  cmn=""; for a in "$@"; do case "$a" in authbridge-lineage-config-*) cmn="$a" ;; esac; done
  body="$(cat)"
  {
    printf 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: %s\n  namespace: travel-advisor\ndata:\n  config.yaml: |\n' "$cmn"
    printf '%s\n' "$body" | sed 's/^/    /'
  }
  exit 0
fi

# ---- apply -f - (the append applies the amended CM; capture it as wired) -----
if [[ "$1" == "apply" && ( "$*" == *"-f -"* || "$*" == *"-f-"* ) ]]; then
  applied_f="$(mktemp)"; cat > "$applied_f"
  python3 - "$FIXDIR" "$applied_f" <<'PY'
import json, sys, re, os
fixdir, applied_f = sys.argv[1], sys.argv[2]
raw = open(applied_f).read()
m = re.search(r"authbridge-lineage-config-[a-z0-9-]+", raw)
if not m:
    sys.exit(0)
name = m.group(0)
bm = re.search(r"config\.yaml:\s*\|?\s*\n(.*)$", raw, re.S)
body = bm.group(1) if bm else raw
open(os.path.join(fixdir, f"appended-{name}"), "w").close()
json.dump({"data": {"config.yaml": body}}, open(os.path.join(fixdir, f"cm-{name}-wired.json"), "w"))
PY
  rm -f "$applied_f"
  exit 0
fi

# ---- Pod fetch (INJECTED-sidecar detection) ----------------------------------
# `kubectl get pods -n <ns> -l app.kubernetes.io/name=<entity> --field-selector=status.phase=Running -o json`
# Tested BEFORE the deployment branch because "pods" and "deploy" are distinct
# tokens; a query naming pods is served here.
# Gated on the -o json output flag so the plain-text crash-loop jsonpath probe
# (which also names `pods`, but with no -o json) falls THROUGH to the plain-text
# `Running` branch below — faithfully modelling real kubectl, which returns
# newline-delimited jsonpath text there, never the list JSON.
if [[ "$*" == *"get"* && ( "$*" == *" pods"* || "$*" == *" pod "* || "$*" == *"pods "* ) && ( "$*" == *"-o json"* || "$*" == *"-ojson"* ) ]]; then
  # Break ONLY the pod read (leaving the deployment + CM reads healthy) so we can
  # exercise the pod-fetch failure path independently: status treats it as
  # inconclusive (keeps the template verdict, continues), instrument dies loud.
  if [[ "${POD_GET_FAILS:-0}" == "1" ]]; then
    printf '%s\n' "Error from server (Forbidden): pods is forbidden: RBAC-denied" >&2
    exit 1
  fi
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

# ---- crash-loop watch: pod state (jsonpath) — kept AFTER the -o json pod ----
# branch above (this one has no -o json). Never crash-loops in these tests.
if [[ "$*" == *"get"* && "$*" == *"pod"* ]]; then
  printf '%s\n' "Running"; exit 0
fi
if [[ "$*" == *"logs"* ]]; then
  printf '%s\n' 'error: unknown plugin "lineage-telemetry"'; exit 0
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
    # A FAKE lineage-attach kit dir dg.sh is pointed at via DG_LINEAGE_ATTACH_DIR
    # (the seam that replaced --cortex-local-path when ADR-0033 vendored the kit).
    kitdir = tmp_path / "lineage-attach"
    kitdir.mkdir(parents=True)
    log = tmp_path / "kubectl.log"

    _CONTROLLED = {"kubectl", "docker", "podman"}
    for tool in (
        "bash", "sh", "env", "basename", "dirname", "cat", "sed", "grep",
        "awk", "tr", "sort", "printf", "head", "tail", "cut", "uniq", "mktemp",
        "rm", "wc", "xargs", "python3", "jq", "sleep", "date",
    ):
        src = _which(tool)
        if src and Path(tool).name not in _CONTROLLED:
            (sysdir / tool).symlink_to(src)

    # A stub kit so the instrument-path integrity check resolves. Under the
    # ADR-0033 owner-split (#245) an entity that ALREADY has a sidecar is NOT
    # injected — it is APPENDED to in place (dg.sh's own edit), so neither applier
    # runs for the injected cases here; the stubs shout if they wrongly run.
    _STUB_SIDECAR_PATCH = 'echo "sidecar-patch.sh stub should not run for an existing sidecar" >&2\nexit 0\n'
    _STUB_SIDECAR_PATCH_PROXY = 'echo "sidecar-patch-proxy.sh stub should not run for an existing sidecar" >&2\nexit 0\n'
    _STUB_BUILD_SHIM = 'echo "build-otel-shim.sh stub" >&2\nexit 0\n'
    _STUB_ATTACH = 'echo "attach-lineage.sh stub" >&2\nexit 0\n'

    class _Sandbox:
        def __init__(self) -> None:
            self.bindir = bindir
            self.fixdir = fixdir
            self.kitdir = kitdir
            self.log = log
            self.get_fails = False
            self.cm_get_fails = False
            self.pod_get_fails = False
            self.cm_force_notfound = ""
            self.envflags: dict[str, str] = {}
            _make_bin(bindir, "docker", "exit 0\n")
            _make_bin(bindir, "podman", "exit 0\n")
            _make_bin(kitdir, "sidecar-patch.sh", _STUB_SIDECAR_PATCH)
            _make_bin(kitdir, "sidecar-patch-proxy.sh", _STUB_SIDECAR_PATCH_PROXY)
            _make_bin(kitdir, "build-otel-shim.sh", _STUB_BUILD_SHIM)
            _make_bin(kitdir, "attach-lineage.sh", _STUB_ATTACH)
            # The sourced / build-input companions require_vendored_kit checks for
            # (both shims are build inputs; ADR-0033 D4).
            for f in ("container-runtime.sh", "Dockerfile.otel-shim",
                      "lineage-propagate-hook.py", "rossoctl_turnspan.py",
                      "rossoctl_turnspan.pth"):
                (kitdir / f).write_text("# stub\n")
            self._write_kubectl()
            self.set_namespace({})

        def _write_kubectl(self) -> None:
            body = ""
            if self.get_fails:
                body += "export GET_FAILS=1\n"
            if self.cm_get_fails:
                body += "export CM_GET_FAILS=1\n"
            if self.pod_get_fails:
                body += "export POD_GET_FAILS=1\n"
            body += f"export CM_FORCE_NOTFOUND={self.cm_force_notfound!r}\n"
            _make_bin(bindir, "kubectl", body + _FAKE_KUBECTL)

        def set_get_fails(self, val: bool) -> None:
            self.get_fails = val
            self._write_kubectl()

        def set_pod_get_fails(self, val: bool) -> None:
            """Break ONLY the pod read (deployment + CM reads stay healthy) —
            exercises the pod-fetch failure path in isolation."""
            self.pod_get_fails = val
            self._write_kubectl()

        def set_flag(self, **kw: str) -> None:
            self.envflags.update({k: str(v) for k, v in kw.items()})

        def set_namespace(self, entities: dict[str, dict]) -> None:
            """Program the namespace.

            ``entities`` maps entity name → a spec dict:
              * ``template_sidecar``: None|"proxy"|"envoy" — sidecar embedded in
                the DEPLOYMENT template (manual-attach / kit shape);
              * ``pod_sidecar``: None|"proxy"|"envoy" — sidecar the webhook
                injected into the live POD only (defaults to the same as
                ``template_sidecar`` so a normal workload's pod mirrors its
                template);
              * ``native``: bool — place the sidecar in ``initContainers`` as a
                native sidecar (the #852-kit shape), not an ordinary
                ``containers`` entry (defaults False);
              * ``lineage``: bool — whether the sidecar's pipeline CM carries the
                lineage-telemetry plugin.
              * ``no_pod``: bool — write NO ``pods-{name}.json`` fixture, so the
                fake kubectl serves ``{"items":[]}`` for the pod read (models a
                Deployment scaled to 0 / just-applied, no Running pod yet).
                Defaults False.
            """
            items = []
            for name, spec in entities.items():
                template_sidecar = spec.get("template_sidecar")
                if "pod_sidecar" in spec:
                    pod_sidecar = spec["pod_sidecar"]
                else:
                    pod_sidecar = template_sidecar
                lineage = spec.get("lineage", False)
                native = spec.get("native", False)

                cm_name = None
                effective = template_sidecar or pod_sidecar
                if effective is not None:
                    cm_name = f"authbridge-lineage-config-{name}"
                    data = _CM_WITH_LINEAGE if lineage else _CM_WITHOUT_LINEAGE
                    cm_doc = {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {"name": cm_name, "namespace": "travel-advisor"},
                        "data": {"config.yaml": data},
                    }
                    (fixdir / f"cm-{cm_name}.json").write_text(json.dumps(cm_doc))

                dep = _deployment(
                    name, template_sidecar=template_sidecar, cm_name=cm_name, native=native
                )
                items.append(dep)
                (fixdir / f"entity-{name}.json").write_text(json.dumps({"items": [dep]}))

                if spec.get("no_pod", False):
                    # No Running pod: leave the pods-{name}.json fixture absent so
                    # the fake kubectl serves {"items":[]} for the pod read.
                    continue
                pod = _pod(name, pod_sidecar=pod_sidecar, cm_name=cm_name, native=native)
                (fixdir / f"pods-{name}.json").write_text(json.dumps({"items": [pod]}))
            (fixdir / "entities.json").write_text(json.dumps({"items": items}))

        def kubectl_calls(self) -> list[str]:
            if not log.exists():
                return []
            return [ln for ln in log.read_text().splitlines() if ln.strip()]

        def run(self, *args: str, kit_dir: str | None = "__default__", **kw):
            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{sysdir}"
            env["KUBECTL_LOG"] = str(log)
            env["FIXDIR"] = str(fixdir)
            env.setdefault("KIND_CLUSTER", "rossoctl")
            # Point dg.sh at the FAKE stub kit via the DG_LINEAGE_ATTACH_DIR test
            # seam (replaces the retired --cortex-local-path flag; ADR-0033).
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


def _entity_line(stdout: str, entity: str) -> str:
    for ln in stdout.splitlines():
        if entity in ln:
            return ln
    raise AssertionError(f"no output line mentions {entity!r}; stdout:\n{stdout}")


# ===========================================================================
# status: a webhook-injected sidecar (Pod-only, clean Deployment template)
# ===========================================================================


def test_status_detects_injected_proxy_sidecar_from_pod(sandbox) -> None:
    """The bug: a workload with NO sidecar in the Deployment template but an
    injected ``authbridge-proxy`` in the live Pod was reported sidecar=none.
    It must now report sidecar=present type=proxy (detected from the Pod)."""
    sandbox.set_namespace(
        {"research-agent": {"template_sidecar": None, "pod_sidecar": "proxy", "lineage": True}}
    )
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "research-agent").lower()
    assert "sidecar=present" in line, (
        f"an injected proxy sidecar (pod-only) must report sidecar=present; line={line!r}"
    )
    assert "type=proxy" in line, (
        f"an injected authbridge-proxy must report type=proxy; line={line!r}"
    )
    assert "sidecar=none" not in line, (
        f"the injected case must NOT report sidecar=none (was the bug); line={line!r}"
    )


def test_status_injected_lineage_wired_via_pod_resolved_cm(sandbox) -> None:
    """Plugin detection must still work for the injected case: the pipeline CM is
    resolved from the POD's volume→configMap.name, then scanned for the plugin.
    A wired CM → lineage=yes."""
    sandbox.set_namespace(
        {"research-agent": {"template_sidecar": None, "pod_sidecar": "proxy", "lineage": True}}
    )
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "research-agent").lower()
    assert "lineage=yes" in line, (
        f"a wired plugin (resolved via the pod CM) must report lineage=yes; line={line!r}"
    )
    assert "lineage=no" not in line, f"must not misreport a wired plugin; line={line!r}"


def test_status_injected_lineage_absent_via_pod_resolved_cm(sandbox) -> None:
    """An injected sidecar whose pod-resolved CM lacks the plugin → lineage=no."""
    sandbox.set_namespace(
        {"research-agent": {"template_sidecar": None, "pod_sidecar": "proxy", "lineage": False}}
    )
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "research-agent").lower()
    assert "sidecar=present" in line and "type=proxy" in line, line
    assert "lineage=no" in line, (
        f"an injected sidecar with an unwired CM must report lineage=no; line={line!r}"
    )
    assert "lineage=yes" not in line, line


def test_status_detects_injected_envoy_sidecar_from_pod(sandbox) -> None:
    """The pod-fallback is shape-agnostic: an injected envoy-proxy is type=envoy."""
    sandbox.set_namespace(
        {"payment-agent": {"template_sidecar": None, "pod_sidecar": "envoy", "lineage": True}}
    )
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "payment-agent").lower()
    assert "sidecar=present" in line and "type=envoy" in line, (
        f"an injected envoy-proxy must report type=envoy; line={line!r}"
    )


# ===========================================================================
# status: NO regression — the template-embedded path still works unchanged
# ===========================================================================


def test_status_template_embedded_proxy_still_detected(sandbox) -> None:
    """The manual-attach / #852-kit shape (sidecar IN the Deployment template)
    must still be detected exactly as before — no fallback needed, and the pod
    query must not override a template-detected sidecar."""
    sandbox.set_namespace({"research-agent": {"template_sidecar": "proxy", "lineage": True}})
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "research-agent").lower()
    assert "sidecar=present" in line and "type=proxy" in line, line
    assert "lineage=yes" in line, line


def test_status_template_embedded_envoy_still_detected(sandbox) -> None:
    sandbox.set_namespace({"payment-agent": {"template_sidecar": "envoy", "lineage": True}})
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "payment-agent").lower()
    assert "sidecar=present" in line and "type=envoy" in line, line


def test_status_no_sidecar_anywhere_reports_none(sandbox) -> None:
    """No sidecar in the template AND none in the live Pod → sidecar=none (the
    fallback looked at the pod, found nothing, and correctly reports none)."""
    sandbox.set_namespace(
        {"search-destinations": {"template_sidecar": None, "pod_sidecar": None}}
    )
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "search-destinations").lower()
    assert "sidecar=none" in line and "type=none" in line, line
    assert "lineage=no" in line, line


# ===========================================================================
# status: a pod-read failure in the fallback is INCONCLUSIVE (read-only verb)
# ===========================================================================


def test_status_pod_read_failure_keeps_template_verdict_and_continues(sandbox) -> None:
    """`status` is a read-only, best-effort diagnostic. When the Deployment
    template shows no sidecar, it falls back to the live Pod — but a
    transient/RBAC failure of THAT pod read must be treated as inconclusive: keep
    the template's `none` verdict and continue (exit 0), the way status behaved
    before pod-level detection was added. It must NOT hard-die the whole run
    (regression the finding pins: get_pod_json's loud die was reachable here)."""
    sandbox.set_namespace(
        {"search-destinations": {"template_sidecar": None, "pod_sidecar": None}}
    )
    sandbox.set_pod_get_fails(True)  # breaks ONLY the pod read
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, (
        f"a pod-read failure in the status fallback is inconclusive, not fatal; "
        f"stderr={r.stderr!r}"
    )
    line = _entity_line(r.stdout, "search-destinations").lower()
    assert "sidecar=none" in line and "type=none" in line, (
        f"an inconclusive pod probe must leave the template's none verdict standing; "
        f"line={line!r}"
    )


def test_status_pod_read_failure_does_not_abort_other_entities(sandbox) -> None:
    """The soft pod probe must not abort the whole status run: even with the pod
    read broken, every enumerated entity is still reported (each falls back to its
    template verdict)."""
    sandbox.set_namespace(
        {
            "research-agent": {"template_sidecar": None, "pod_sidecar": None},
            "payment-agent": {"template_sidecar": None, "pod_sidecar": None},
        }
    )
    sandbox.set_pod_get_fails(True)
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    for ent in ("research-agent", "payment-agent"):
        assert ent in r.stdout, (
            f"status must still report {ent} despite the pod-read failure; got:\n{r.stdout}"
        )


# ===========================================================================
# instrument: an injected-only sidecar is DETECTED (pod fallback) and, under the
# ADR-0033 owner-split (#245), APPENDED to in place when it lacks lineage (or a
# no-op when it is already wired). The pod fallback exists so an injected sidecar
# is SEEN — never so a SECOND sidecar is injected onto an entity that has one.
# ===========================================================================


def _cm_applied(sandbox, entity: str) -> bool:
    """True if dg.sh applied the amended per-app CM for <entity> (the in-place
    append). The fake kubectl records an `appended-<cm>` marker on `apply -f -`."""
    return (sandbox.fixdir / f"appended-authbridge-lineage-config-{entity}").exists()


def test_instrument_injected_proxy_no_lineage_appends_in_place(sandbox) -> None:
    """An entity whose proxy sidecar is ONLY in the live Pod (webhook-injected,
    clean Deployment template) and lacks lineage-telemetry must be DETECTED via
    the pod fallback and APPENDED to in place (ADR-0033 Decision 3): the CM is
    re-applied with the plugin and the workload rolled. Neither kit APPLIER runs
    (this is dg.sh's own in-place edit); the run exits 0."""
    sandbox.set_namespace(
        {"legacy-agent": {"template_sidecar": None, "pod_sidecar": "proxy", "lineage": False}}
    )
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    # Neither applier ran — the append is dg.sh's own edit.
    assert "stub should not run" not in (r.stdout + r.stderr), (
        f"an injected sidecar must be appended in place, not driven through an applier; "
        f"got:\n{r.stdout}\n{r.stderr}"
    )
    assert _cm_applied(sandbox, "legacy-agent"), (
        f"the injected proxy without lineage must be appended in place; calls={sandbox.kubectl_calls()!r}"
    )
    combined = (r.stdout + r.stderr).lower()
    assert "append" in combined or "in place" in combined or "in-place" in combined, (
        f"the in-place append must be reported; got:\n{combined}"
    )


def test_instrument_injected_proxy_lineage_wired_is_noop(sandbox) -> None:
    """An injected proxy sidecar that ALREADY has lineage-telemetry wired → no-op
    (idempotent, origin-agnostic). Nothing is mutated; the run exits 0."""
    sandbox.set_namespace(
        {"legacy-agent": {"template_sidecar": None, "pod_sidecar": "proxy", "lineage": True}}
    )
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    calls = " ".join(sandbox.kubectl_calls())
    for m in ("apply", "rollout restart", "edit", "replace", "delete"):
        assert m not in calls, (
            f"an already-wired injected sidecar must mutate nothing; found {m!r} in calls={calls!r}"
        )
    assert not _cm_applied(sandbox, "legacy-agent"), "an already-wired sidecar must not re-apply the CM"
    combined = (r.stdout + r.stderr).lower()
    assert "no-op" in combined or ("already" in combined and ("wired" in combined or "lineage" in combined)), (
        f"an already-wired injected sidecar must be reported as an idempotent no-op; got:\n{combined}"
    )


def test_instrument_injected_proxy_pod_read_failure_dies_loud(sandbox) -> None:
    """The mutating `instrument` verb must NOT proceed on a pod-read failure: the
    injected-sidecar fallback there uses the loud-die get_pod_json, so a
    transient/RBAC failure of the pod probe halts loud rather than silently
    misclassifying the entity as no-sidecar and WRONGLY instrumenting it (the
    kit would inject a second sidecar onto an entity that already has one). The
    deployment template shows no sidecar, so the fallback IS reached."""
    sandbox.set_namespace(
        {"legacy-agent": {"template_sidecar": None, "pod_sidecar": "proxy", "lineage": False}}
    )
    sandbox.set_pod_get_fails(True)  # breaks ONLY the pod read
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode != 0, (
        f"a pod-read failure in the instrument fallback must die loud; stdout={r.stdout!r}"
    )
    combined = (r.stdout + r.stderr).lower()
    assert combined.strip(), "a failed pod read must not exit with EMPTY output"


def test_instrument_injected_proxy_does_not_drive_an_applier(sandbox) -> None:
    """An injected-proxy entity already has a sidecar → it is appended to in place,
    NOT injected. NEITHER applier (envoy sidecar-patch.sh nor proxy
    sidecar-patch-proxy.sh) may be invoked for it (guards against the pod-fallback
    misclassifying it as no-sidecar → injecting a SECOND sidecar)."""
    sandbox.set_namespace(
        {"legacy-agent": {"template_sidecar": None, "pod_sidecar": "proxy", "lineage": False}}
    )
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    # Either applier stub prints to stderr if it ever wrongly runs.
    assert "stub should not run" not in (r.stdout + r.stderr), (
        f"an injected sidecar must NOT drive either applier; got:\n{r.stdout}\n{r.stderr}"
    )


def test_instrument_no_running_pod_refuses_on_unconfirmed_none(sandbox) -> None:
    """A template that shows NO sidecar and has NO Running pod to confirm it must
    make `instrument` refuse and mutate nothing. get_pod_json returns exit 0 with
    {"items":[]} when the Deployment is scaled to 0 (or just-applied), and
    detect_sidecar_type prints 'none' for an empty listing too — so trusting that
    'none' would attach a SECOND sidecar onto an entity that actually carries a
    webhook-injected one (visible only once a Pod is admitted). The instrument
    path must distinguish an EMPTY pod read from a CONFIRMED no-sidecar read and
    die loud on the former."""
    sandbox.set_namespace(
        {"legacy-agent": {"template_sidecar": None, "no_pod": True}}
    )
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode != 0, (
        f"instrument must refuse on an unconfirmed 'none' (no Running pod); stdout={r.stdout!r}"
    )
    combined = (r.stdout + r.stderr).lower()
    assert "no running pod" in combined or "no pod" in combined, (
        f"the refusal must explain there is no Running pod to confirm from; got:\n{combined}"
    )
    # Nothing mutated, and the kit was never driven.
    calls = " ".join(sandbox.kubectl_calls())
    for m in ("apply", "patch", "rollout restart", "edit", "replace", "delete"):
        assert m not in calls, (
            f"a refused (unconfirmed-none) entity must mutate nothing; found {m!r} in calls={calls!r}"
        )
    assert "stub should not run" not in (r.stdout + r.stderr) and (
        "build-otel-shim.sh stub" not in (r.stdout + r.stderr)
    ), f"the kit must NOT be driven on an unconfirmed 'none'; got:\n{r.stdout}\n{r.stderr}"


# ===========================================================================
# status: a NATIVE sidecar — an initContainer (restartPolicy: Always), the
# shape the #852 kit attaches (dg.sh ~line 761). The bug (task 1): detection
# inspected `.spec[.template.spec].containers` ONLY, never `initContainers`, so
# an instrumented pod running `envoy-proxy` as a native sidecar was reported
# sidecar=none/type=none/lineage=no. Detection must union the two container lists.
# ===========================================================================


def test_status_detects_native_envoy_sidecar_in_template(sandbox) -> None:
    """The kit attaches ``envoy-proxy`` as a native sidecar — an initContainer in
    the DEPLOYMENT template. It must be detected as sidecar=present type=envoy,
    NOT sidecar=none (the task-1 bug: initContainers were never inspected)."""
    sandbox.set_namespace(
        {"research-agent": {"template_sidecar": "envoy", "native": True, "lineage": True}}
    )
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "research-agent").lower()
    assert "sidecar=present" in line, (
        f"a native envoy sidecar (initContainer) must report sidecar=present; line={line!r}"
    )
    assert "type=envoy" in line, (
        f"a native envoy-proxy must report type=envoy; line={line!r}"
    )
    assert "sidecar=none" not in line, (
        f"the native-sidecar case must NOT report sidecar=none (was the bug); line={line!r}"
    )


def test_status_native_envoy_lineage_wired_via_init_resolved_cm(sandbox) -> None:
    """Plugin detection must work for the native shape too: the pipeline CM is
    resolved from the initContainer sidecar's volume→configMap.name, then scanned
    for the plugin. A wired CM → lineage=yes."""
    sandbox.set_namespace(
        {"research-agent": {"template_sidecar": "envoy", "native": True, "lineage": True}}
    )
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "research-agent").lower()
    assert "lineage=yes" in line, (
        f"a wired plugin (CM resolved via the init-container sidecar) must report "
        f"lineage=yes; line={line!r}"
    )
    assert "lineage=no" not in line, f"must not misreport a wired plugin; line={line!r}"


def test_status_native_envoy_lineage_absent_via_init_resolved_cm(sandbox) -> None:
    """A native envoy sidecar whose init-resolved CM lacks the plugin → lineage=no
    (still sidecar=present type=envoy — the sidecar is seen, the plugin is not)."""
    sandbox.set_namespace(
        {"research-agent": {"template_sidecar": "envoy", "native": True, "lineage": False}}
    )
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "research-agent").lower()
    assert "sidecar=present" in line and "type=envoy" in line, line
    assert "lineage=no" in line, (
        f"a native sidecar with an unwired CM must report lineage=no; line={line!r}"
    )
    assert "lineage=yes" not in line, line


def test_status_detects_native_proxy_sidecar_in_template(sandbox) -> None:
    """The union is shape-agnostic: a hypothetical init-shaped ``authbridge-proxy``
    is detected as type=proxy too, not misread as the non-sidecar ``proxy-init``
    init container that sits beside it."""
    sandbox.set_namespace(
        {"payment-agent": {"template_sidecar": "proxy", "native": True, "lineage": True}}
    )
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "payment-agent").lower()
    assert "sidecar=present" in line and "type=proxy" in line, (
        f"a native authbridge-proxy must report type=proxy (not misread proxy-init "
        f"as the sidecar); line={line!r}"
    )


def test_status_proxy_init_alone_is_not_a_sidecar(sandbox) -> None:
    """A pod that carries ONLY the ``proxy-init`` init container (never happens
    from the kit without the sidecar, but pins the negative) must report
    sidecar=none — ``proxy-init`` is not ``envoy-proxy``/``authbridge-proxy``."""
    dep = _deployment(
        "search-destinations", template_sidecar=None, cm_name=None, native=False
    )
    dep["spec"]["template"]["spec"]["initContainers"] = [_proxy_init_container()]
    (sandbox.fixdir / "entity-search-destinations.json").write_text(
        json.dumps({"items": [dep]})
    )
    (sandbox.fixdir / "entities.json").write_text(json.dumps({"items": [dep]}))
    pod = _pod("search-destinations", pod_sidecar=None, cm_name=None, native=False)
    pod["spec"]["initContainers"] = [_proxy_init_container()]
    (sandbox.fixdir / "pods-search-destinations.json").write_text(
        json.dumps({"items": [pod]})
    )
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "search-destinations").lower()
    assert "sidecar=none" in line and "type=none" in line, (
        f"a lone proxy-init init container is NOT a sidecar; line={line!r}"
    )


# ===========================================================================
# instrument: a native envoy sidecar ALREADY carrying lineage is DETECTED (via
# the initContainers union) and treated as an idempotent NO-OP — instrument must
# not re-inject or re-append onto an already-wired, already-instrumented entity
# (ADR-0033 Decision 5). Guards the idempotency of a re-run.
# ===========================================================================


def test_instrument_native_envoy_wired_is_noop(sandbox) -> None:
    """An entity already carrying a native envoy sidecar (kit-attached, in the
    template's initContainers) WITH lineage-telemetry wired must be DETECTED and
    treated as a NO-OP — no applier is driven and nothing is mutated. Guards the
    idempotency of a re-run of `instrument` on an already-instrumented namespace."""
    sandbox.set_namespace(
        {"research-agent": {"template_sidecar": "envoy", "native": True, "lineage": True}}
    )
    r = sandbox.run("namespace", "travel-advisor", "instrument")
    assert r.returncode == 0, r.stderr
    assert "stub should not run" not in (r.stdout + r.stderr), (
        f"an already-wired (native envoy) entity must NOT be re-driven through an "
        f"applier; got:\n{r.stdout}\n{r.stderr}"
    )
    assert not _cm_applied(sandbox, "research-agent"), "an already-wired entity must not re-apply the CM"
    combined = (r.stdout + r.stderr).lower()
    assert "no-op" in combined or ("already" in combined and ("wired" in combined or "lineage" in combined)), (
        f"a wired native-envoy entity must be reported as an idempotent no-op; got:\n{combined}"
    )
