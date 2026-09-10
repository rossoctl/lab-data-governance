"""Behavioural tests for ``dg.sh namespace <ns> status [<entity>]`` (issue #183).

The read-only namespace-inspection slice of ``dg.sh`` (design:
``docs/cli.md`` § ``dg.sh namespace <ns> status``; boundary in
``docs/adr/0031-non-reversible-namespace-lineage-activation.md``; the #183
re-scope comment records that this is ``dg.sh``'s own read model — it does NOT
delegate to the cortex #852 kit, which offers no status query).

For every agent/tool in ``<ns>`` — or just ``<entity>`` when named — ``status``
reports three facts per entity:

- **sidecar presence** — does the pod run an AuthBridge sidecar;
- **sidecar type** — ``proxy`` / ``envoy`` / ``none``;
- **plugin** — is ``lineage-telemetry`` wired into its pipeline.

Detection distinguishes the injected ``authbridge-proxy`` (proxy-sidecar)
container from ``envoy-proxy`` (envoy-sidecar), and inspects the *effective*
pipeline config — the ConfigMap the sidecar mounts at ``/etc/authbridge`` — for
the ``lineage-telemetry`` plugin entry. It reuses the #181 entity enumeration +
single-entity selection (loud error on a name that is not an agent/tool).

Like the rest of ``tests/deploy/``, these drive the real script as a subprocess
with a **fake ``kubectl``** on a synthetic ``PATH`` — no live cluster. The fake
serves:

  * ``kubectl version --client``                       → exit 0 (reachability)
  * ``kubectl get deployments -l <selector> -o json``  → an ``items`` list whose
    membership (and each item's container/volume shape) is programmed per-test
    from a small Python model of the namespace;
  * ``kubectl get configmap <name> -o json``           → that ConfigMap's data;

and logs every argv line to ``$KUBECTL_LOG`` so we can assert the command stays
read-only.
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
# A tiny model of a namespace, rendered into fixture JSON files the fake kubectl
# serves. Each entity is one Deployment (its app.kubernetes.io/name label) whose
# pod template carries an app container plus, optionally, a sidecar container:
#   * proxy-sidecar → container named `authbridge-proxy`
#   * envoy-sidecar → container named `envoy-proxy`
# The sidecar mounts /etc/authbridge from a volume backed by a per-app ConfigMap.
# `lineage_wired` controls whether that ConfigMap's config.yaml carries a
# `lineage-telemetry` plugin entry.
# ---------------------------------------------------------------------------


def _deployment(name: str, *, sidecar: str | None, cm_name: str | None) -> dict:
    """Build a Deployment doc for entity ``name``.

    ``sidecar`` is None | "proxy" | "envoy". When a sidecar is present it mounts
    /etc/authbridge from the ``authbridge-runtime`` volume, backed by the
    ConfigMap ``cm_name`` — the source of truth for plugin detection.
    """
    containers = [
        {
            "name": name,  # the app container (uninstrumented workload)
            "image": "agent-examples-snp:latest",
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
        volumes.append(
            {"name": "authbridge-runtime", "configMap": {"name": cm_name}}
        )
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
        volumes.append(
            {"name": "authbridge-runtime", "configMap": {"name": cm_name}}
        )
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": "travel-advisor",
            "labels": {
                "app.kubernetes.io/name": name,
                # component is agent for all of these; the actual value is
                # immaterial to detection — the selector match is enforced by
                # the fake serving these only for the component selector query.
                "app.kubernetes.io/component": "agent",
            },
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": containers,
                    "volumes": volumes,
                }
            }
        },
    }


# A per-app lineage ConfigMap (proxy-sidecar shape), with or without the plugin.
_CM_WITH_LINEAGE = """mode: proxy-sidecar
pipeline:
  inbound:
    plugins:
      - name: a2a-parser
      - name: mcp-parser
      - name: lineage-telemetry
        config:
          otel_endpoint: "otel-collector.rossoctl-system.svc.cluster.local:4317"
          self_id: "research-agent"
  outbound:
    plugins:
      - name: a2a-parser
      - name: lineage-telemetry
        config:
          otel_endpoint: "otel-collector.rossoctl-system.svc.cluster.local:4317"
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


@pytest.fixture()
def sandbox(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    sysdir = tmp_path / "sys"
    sysdir.mkdir()
    fixdir = tmp_path / "fixtures"
    fixdir.mkdir()
    log = tmp_path / "kubectl.log"

    _CONTROLLED = {"kubectl", "docker", "podman"}
    for tool in (
        "bash", "sh", "env", "basename", "dirname", "cat", "sed", "grep",
        "awk", "tr", "sort", "printf", "head", "tail", "cut", "uniq", "mktemp",
        "rm", "wc", "xargs", "python3", "jq",
    ):
        src = _which(tool)
        if src and Path(tool).name not in _CONTROLLED:
            (sysdir / tool).symlink_to(src)

    # The fake kubectl. It serves three query shapes from files written into
    # $FIXDIR by set_namespace(): the deployment listing (entities.json /
    # entity-<name>.json for the single-entity select) and per-ConfigMap data
    # (cm-<name>.json). Everything else is empty+success. It logs argv so tests
    # can assert the command is read-only.
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

# ---- ConfigMap fetch: `kubectl -n <ns> get configmap <name> -o jsonpath={.data}`
# Real kubectl renders `-o jsonpath={.data}` (a map) as a Go map[...] repr, NOT
# JSON — e.g. `map[config.yaml:mode: proxy-sidecar ...]`. dg.sh's detection
# relies on that (its json.loads throws, then substring-scans the raw text), so
# the fake MUST reproduce the Go-map repr, not dump the whole ConfigMap doc.
if [[ "$*" == *"get"* && ( "$*" == *"configmap"* || "$*" == *" cm "* || "$*" == *" cm"* ) ]]; then
  # find the CM name: the token after configmap/cm.
  cmname=""
  prev=""
  for a in "$@"; do
    case "$prev" in
      configmap|cm|configmaps) cmname="$a"; break ;;
    esac
    prev="$a"
  done
  # Deliberate NotFound signal for a fixed CM name, so the fail-loud test can
  # distinguish a legitimate NotFound (plugin absent) from a broken API.
  if [[ "$cmname" == "$CM_FORCE_NOTFOUND" ]]; then
    printf '%s\n' "Error from server (NotFound): configmaps \"${cmname}\" not found" >&2
    exit 1
  fi
  # A non-NotFound API failure during the CM read (apiserver 500 / RBAC-denied),
  # independent of GET_FAILS which only breaks the deployment listing above.
  if [[ "${CM_GET_FAILS:-0}" == "1" ]]; then
    printf '%s\n' "Error from server (InternalError): an error on the server (\"\") has prevented the request from succeeding" >&2
    exit 1
  fi
  f="$FIXDIR/cm-${cmname}.json"
  if [[ -n "$cmname" && -f "$f" ]]; then
    # Emit ONLY .data, in the Go map[...] repr real kubectl produces for
    # `-o jsonpath={.data}` (keys space-separated, `key:value`, no braces/quotes).
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

# ---- Pod fetch (injected-sidecar fallback) -----------------------------------
# When the Deployment TEMPLATE shows no sidecar, dg.sh falls back to the live Pod
# (`kubectl get pods ... --field-selector=status.phase=Running -o json`) to catch
# a webhook-injected sidecar. This model has NO injected sidecars — the pod
# mirrors the template — so an empty pod list makes the fallback re-detect `none`
# and leaves the template verdict standing. (The dedicated injected-sidecar
# suite, test_dg_sh_injected_sidecar.py, serves pods carrying a sidecar.)
if [[ "$*" == *"get"* && ( "$*" == *" pods"* || "$*" == *"pods "* || "$*" == *" pod "* ) ]]; then
  printf '%s' '{"items":[]}'
  exit 0
fi

# ---- Deployment listing ------------------------------------------------------
# Two output shapes are served, matching what dg.sh asks for:
#   * `-o json`      → the full Deployment doc(s) (namespace_status detection);
#   * `-o jsonpath=` → newline entity NAMES (enumerate_entities, #181).
# The single-entity select carries app.kubernetes.io/name=<entity> in -l.
if [[ "$*" == *"get"* && ( "$*" == *"deployment"* || "$*" == *"deploy"* ) ]]; then
  ent=""
  for a in "$@"; do
    case "$a" in
      *app.kubernetes.io/name=*)
        ent="${a##*app.kubernetes.io/name=}"
        ent="${ent%%,*}"     # strip any trailing comma-separated selector terms
        ;;
    esac
  done
  # Note: `-o json` is a substring of `-o jsonpath=`, so test jsonpath FIRST.
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

  # jsonpath name-listing: print the entity name(s). For a single-entity select
  # that does not resolve, print NOTHING (so enumerate_entities fails loud).
  if [[ -n "$ent" ]]; then
    if [[ -f "$FIXDIR/entity-${ent}.json" ]]; then printf '%s\n' "$ent"; fi
    exit 0
  fi
  # whole-namespace: one name per registered entity fixture.
  for f in "$FIXDIR"/entity-*.json; do
    [[ -e "$f" ]] || continue
    b="$(basename "$f")"; b="${b#entity-}"; b="${b%.json}"
    printf '%s\n' "$b"
  done
  exit 0
fi
exit 0
"""

    class _Sandbox:
        def __init__(self) -> None:
            self.bindir = bindir
            self.fixdir = fixdir
            self.log = log
            self.get_fails = False
            self.cm_get_fails = False
            self.cm_force_notfound = ""
            _make_bin(bindir, "docker", "exit 0\n")
            self._write_kubectl()
            self.set_namespace({})

        def _write_kubectl(self) -> None:
            body = ""
            if self.get_fails:
                body += 'export GET_FAILS=1\n'
            if self.cm_get_fails:
                body += 'export CM_GET_FAILS=1\n'
            body += f'export CM_FORCE_NOTFOUND={self.cm_force_notfound!r}\n'
            _make_bin(bindir, "kubectl", body + _FAKE_KUBECTL)

        def set_get_fails(self, val: bool) -> None:
            self.get_fails = val
            self._write_kubectl()

        def set_cm_get_fails(self, val: bool) -> None:
            """Break ONLY the ConfigMap read (non-NotFound), leaving the
            deployment listing healthy — exercises the CM-read fail-loud path."""
            self.cm_get_fails = val
            self._write_kubectl()

        def set_cm_force_notfound(self, cm_name: str) -> None:
            """Make the fake return a genuine NotFound for ``cm_name`` — the
            legitimate 'plugin absent' case, which must NOT die."""
            self.cm_force_notfound = cm_name
            self._write_kubectl()

        def set_namespace(self, entities: dict[str, dict]) -> None:
            """Program the namespace's entities.

            ``entities`` maps entity name → ``{"sidecar": None|"proxy"|"envoy",
            "lineage": bool}``. Writes the listing + single-entity + ConfigMap
            fixtures the fake kubectl serves.
            """
            items = []
            for name, spec in entities.items():
                sidecar = spec.get("sidecar")
                cm_name = None
                if sidecar is not None:
                    cm_name = f"authbridge-lineage-config-{name}"
                    data = (
                        _CM_WITH_LINEAGE if spec.get("lineage")
                        else _CM_WITHOUT_LINEAGE
                    )
                    cm_doc = {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {"name": cm_name, "namespace": "travel-advisor"},
                        "data": {"config.yaml": data},
                    }
                    (fixdir / f"cm-{cm_name}.json").write_text(json.dumps(cm_doc))
                dep = _deployment(name, sidecar=sidecar, cm_name=cm_name)
                items.append(dep)
                # single-entity select fixture.
                (fixdir / f"entity-{name}.json").write_text(
                    json.dumps({"items": [dep]})
                )
            (fixdir / "entities.json").write_text(json.dumps({"items": items}))

        def drop(self, name: str) -> None:
            p = bindir / name
            if p.exists() or p.is_symlink():
                p.unlink()

        def kubectl_calls(self) -> list[str]:
            if not log.exists():
                return []
            return [ln for ln in log.read_text().splitlines() if ln.strip()]

        def run(self, *args: str, **kw) -> subprocess.CompletedProcess:
            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{sysdir}"
            env["KUBECTL_LOG"] = str(log)
            env["FIXDIR"] = str(fixdir)
            env.setdefault("KIND_CLUSTER", "rossoctl")
            env.update(kw.pop("env", {}) or {})
            return subprocess.run(
                ["bash", str(DG_SH), *args],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
                **kw,
            )

    return _Sandbox()


# ---------------------------------------------------------------------------
# AC: lists each agent/tool with presence, type (proxy/envoy/none), plugin y/n
# ---------------------------------------------------------------------------


def test_status_lists_every_entity(sandbox) -> None:
    sandbox.set_namespace(
        {
            "research-agent": {"sidecar": "proxy", "lineage": True},
            "payment-agent": {"sidecar": "envoy", "lineage": True},
            "search-destinations": {"sidecar": None, "lineage": False},
        }
    )
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for ent in ("research-agent", "payment-agent", "search-destinations"):
        assert ent in out, f"status must list {ent}; got:\n{out}"


def test_status_reports_proxy_type(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": "proxy", "lineage": True}})
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "research-agent")
    assert "proxy" in line.lower(), f"proxy-sidecar must report type proxy; line={line!r}"
    assert "envoy" not in line.lower(), f"must not mislabel proxy as envoy; line={line!r}"


def test_status_reports_envoy_type(sandbox) -> None:
    sandbox.set_namespace({"payment-agent": {"sidecar": "envoy", "lineage": True}})
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "payment-agent")
    assert "envoy" in line.lower(), f"envoy-sidecar must report type envoy; line={line!r}"


def test_status_reports_none_type_and_no_sidecar(sandbox) -> None:
    sandbox.set_namespace({"search-destinations": {"sidecar": None, "lineage": False}})
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "search-destinations").lower()
    assert "none" in line, f"no sidecar must report type none; line={line!r}"


def test_status_reports_plugin_wired_when_present(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": "proxy", "lineage": True}})
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "research-agent").lower()
    # Match the plugin= VERDICT token specifically. Substrings like "wired" or
    # "present" are trivially true even when the verdict is wrong (both the
    # sidecar=present token and the "not wired" phrasing contain them), so a
    # 'not wired' misreport of this genuinely-wired fixture MUST fail here.
    assert "plugin=yes" in line, (
        f"a wired lineage-telemetry plugin must report plugin=yes; line={line!r}"
    )
    assert "plugin=no" not in line, (
        f"a wired plugin must NOT report plugin=no; line={line!r}"
    )


def test_status_reports_plugin_absent_when_not_wired(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": "proxy", "lineage": False}})
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "research-agent").lower()
    # Match the plugin= VERDICT token specifically (see the positive test).
    assert "plugin=no" in line, (
        f"an unwired plugin must report plugin=no; line={line!r}"
    )
    assert "plugin=yes" not in line, (
        f"an unwired plugin must NOT report plugin=yes; line={line!r}"
    )


def test_status_distinguishes_proxy_from_envoy_across_entities(sandbox) -> None:
    """proxy vs envoy is decided by the sidecar CONTAINER NAME, not guessed:
    authbridge-proxy → proxy, envoy-proxy → envoy — on the same listing."""
    sandbox.set_namespace(
        {
            "research-agent": {"sidecar": "proxy", "lineage": True},
            "payment-agent": {"sidecar": "envoy", "lineage": True},
        }
    )
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    assert "proxy" in _entity_line(r.stdout, "research-agent").lower()
    assert "envoy" in _entity_line(r.stdout, "payment-agent").lower()


def test_status_plugin_absent_on_no_sidecar_entity(sandbox) -> None:
    """A no-sidecar entity has no pipeline at all, so plugin is reported absent —
    never a crash, never a false 'wired'."""
    sandbox.set_namespace({"search-destinations": {"sidecar": None, "lineage": False}})
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, r.stderr
    line = _entity_line(r.stdout, "search-destinations").lower()
    assert "none" in line
    assert "plugin=no" in line, (
        f"no-sidecar entity must report plugin=no; line={line!r}"
    )


# ---------------------------------------------------------------------------
# AC: single-entity scope + loud error on an unresolved name
# ---------------------------------------------------------------------------


def test_status_single_entity_reports_only_that_entity(sandbox) -> None:
    sandbox.set_namespace(
        {
            "research-agent": {"sidecar": "proxy", "lineage": True},
            "payment-agent": {"sidecar": "envoy", "lineage": True},
        }
    )
    r = sandbox.run("namespace", "travel-advisor", "status", "research-agent")
    assert r.returncode == 0, r.stderr
    assert "research-agent" in r.stdout
    # payment-agent must NOT appear — the single-entity select scopes the report.
    assert "payment-agent" not in r.stdout, (
        f"single-entity status must report only the named entity; got:\n{r.stdout}"
    )


def test_status_single_entity_uses_name_label_selector(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": "proxy", "lineage": True}})
    sandbox.run("namespace", "travel-advisor", "status", "research-agent")
    calls = " ".join(sandbox.kubectl_calls())
    assert "app.kubernetes.io/name" in calls, (
        f"single-entity select must use app.kubernetes.io/name; calls={sandbox.kubectl_calls()!r}"
    )


def test_status_unknown_entity_is_loud_error(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": "proxy", "lineage": True}})
    r = sandbox.run("namespace", "travel-advisor", "status", "nope-agent")
    assert r.returncode != 0, "an entity that is not an agent/tool must fail loud"
    combined = (r.stdout + r.stderr).lower()
    assert "nope-agent" in combined, "error must name the entity that was not found"


# ---------------------------------------------------------------------------
# AC: read-only — mutates nothing
# ---------------------------------------------------------------------------


def test_status_is_read_only(sandbox) -> None:
    sandbox.set_namespace(
        {
            "research-agent": {"sidecar": "proxy", "lineage": True},
            "payment-agent": {"sidecar": "envoy", "lineage": False},
            "search-destinations": {"sidecar": None, "lineage": False},
        }
    )
    sandbox.run("namespace", "travel-advisor", "status")
    calls = " ".join(sandbox.kubectl_calls())
    for mutating in ("apply", "delete", "patch", "rollout restart", "create", "edit"):
        assert mutating not in calls, (
            f"status must be read-only; found {mutating!r} in kubectl calls: "
            f"{sandbox.kubectl_calls()!r}"
        )


def test_status_uses_component_selector_and_never_reads_reserved_type(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": "proxy", "lineage": True}})
    sandbox.run("namespace", "travel-advisor", "status")
    calls = " ".join(sandbox.kubectl_calls())
    assert "app.kubernetes.io/component" in calls
    assert "agent" in calls and "mcp-tool" in calls
    assert "rossoctl.io/type" not in calls, (
        "must NEVER read rossoctl.io/type (operator-reserved, VAP-protected)"
    )


# ---------------------------------------------------------------------------
# fail-loud on a broken API (a failed get is not an empty 'no entities')
# ---------------------------------------------------------------------------


def test_status_fails_loud_when_kubectl_get_errors(sandbox) -> None:
    sandbox.set_namespace({"research-agent": {"sidecar": "proxy", "lineage": True}})
    sandbox.set_get_fails(True)
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode != 0, "a failed kubectl get must not exit 0"
    combined = (r.stdout + r.stderr).lower()
    assert combined.strip(), "a failed kubectl get must not exit with EMPTY output"


def test_status_fails_loud_when_configmap_read_errors(sandbox) -> None:
    """A broken API DURING THE CONFIGMAP READ (sidecar present, deployment get
    succeeds, but the pipeline-config ConfigMap get fails non-NotFound) must die
    loud — never be silently rendered as an empty config → 'plugin=no', which is
    indistinguishable from a legitimate NotFound. Guards get_configmap_data's
    fail-loud (finding #2)."""
    sandbox.set_namespace({"research-agent": {"sidecar": "proxy", "lineage": True}})
    sandbox.set_cm_get_fails(True)  # breaks ONLY the CM read, not the deployment get
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode != 0, (
        f"a non-NotFound ConfigMap read failure must not exit 0; stdout={r.stdout!r}"
    )
    combined = (r.stdout + r.stderr).lower()
    assert combined.strip(), "a failed ConfigMap read must not exit with EMPTY output"
    # Must NOT masquerade as a benign 'plugin absent' verdict.
    assert "plugin=no" not in combined, (
        f"a broken CM read must die, not report plugin=no; got:\n{combined}"
    )


def test_status_notfound_configmap_reports_plugin_absent(sandbox) -> None:
    """A genuine NotFound on the pipeline-config ConfigMap (a dangling volume
    reference) is the legitimate 'plugin absent' case — reported plugin=no, NOT a
    crash. This is the boundary the fail-loud fix must preserve."""
    sandbox.set_namespace({"research-agent": {"sidecar": "proxy", "lineage": True}})
    sandbox.set_cm_force_notfound("authbridge-lineage-config-research-agent")
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, (
        f"a NotFound ConfigMap is a legitimate 'plugin absent', not an error; "
        f"stderr={r.stderr!r}"
    )
    line = _entity_line(r.stdout, "research-agent").lower()
    assert "plugin=no" in line, (
        f"a NotFound pipeline ConfigMap must report plugin=no; line={line!r}"
    )


def test_status_is_no_longer_a_stub(sandbox) -> None:
    """As of #183 `namespace <ns> status` is a REAL verb — it no longer announces
    'not implemented' / 'stub'."""
    sandbox.set_namespace({"research-agent": {"sidecar": "proxy", "lineage": True}})
    r = sandbox.run("namespace", "travel-advisor", "status")
    combined = (r.stdout + r.stderr).lower()
    assert "not implemented" not in combined and "stub" not in combined, (
        f"namespace status is a real verb as of #183; got:\n{combined}"
    )


# empty-namespace: zero agents/tools is a legitimate, non-error result.
def test_status_empty_namespace_is_not_an_error(sandbox) -> None:
    sandbox.set_namespace({})
    r = sandbox.run("namespace", "travel-advisor", "status")
    assert r.returncode == 0, (
        f"a namespace with zero agents/tools is a legitimate empty result, "
        f"not an error; stderr={r.stderr!r}"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _entity_line(stdout: str, entity: str) -> str:
    """Return the (first) output line mentioning ``entity``.

    ``status`` prints one line per entity; the per-entity facts (type, plugin)
    live on that entity's own line, so tests assert against it rather than the
    whole blob to avoid cross-entity bleed.
    """
    for ln in stdout.splitlines():
        if entity in ln:
            return ln
    raise AssertionError(f"no output line mentions {entity!r}; stdout:\n{stdout}")
