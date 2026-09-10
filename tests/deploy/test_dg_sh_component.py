"""Behavioural tests for ``dg.sh component`` — install / uninstall / status (#182).

This ticket lands the full **reversible** lifecycle of the data-governance
component (Postgres, receiver, UI, the three processors, the UI HTTPRoute +
ReferenceGrant, and the collector tee), reusing the existing ``deploy/`` scripts
(design: ``docs/cli.md`` § ``dg.sh component``; ADR-0031 records
that *component* is fully reversible, separate from the one-way namespace
activation).

- **install** (idempotent): build+kind-load via ``deploy/build-and-load.sh``
  (``--no-build`` to skip) → ``kubectl apply -f deploy/k8s/`` →
  ``deploy/patch-rossoctl-collector.sh`` (the tee) → ``rollout restart`` +
  ``rollout status`` of receiver/ui/interactions.
- **uninstall**: ``patch-rossoctl-collector.sh --revert`` → delete the
  ``rossoctl-system`` HTTPRoute + the ``data-governance`` ReferenceGrant →
  ``kubectl delete namespace data-governance`` (takes the PVC). ``--keep-data``
  deletes workloads individually and preserves the PVC.
- **status**: are the deployments present/ready, and is the collector tee wired?

The tests drive the real script as a subprocess with a **fake ``kubectl``** and
fake sibling scripts (``build-and-load.sh`` / ``patch-rossoctl-collector.sh``)
on a synthetic ``PATH`` / injected via env — the same no-live-cluster stance the
rest of ``tests/deploy/`` takes. The fakes record their argv so we assert both
*what* dg.sh drives and the *order* it drives them in, without a cluster.
"""

from __future__ import annotations

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


# Fake kubectl covering the component verbs' query + mutation shapes.
#   * `kubectl version --client`                -> exit 0 (reachability preflight)
#   * `kubectl get cm otel-collector-config ...`-> tee-state probe; TEE_WIRED
#     controls whether the traces/data_governance pipeline is reported present
#   * `kubectl get deploy ... ` (readiness)     -> DEPLOY_READY controls ready
#   * `kubectl get ns data-governance`          -> NS_PRESENT controls presence
#   * apply / delete / rollout                  -> logged, exit 0
# Every invocation's argv is appended (one line) to $KUBECTL_LOG.
_FAKE_KUBECTL_TMPL = r"""
if [[ -n "${{KUBECTL_LOG:-}}" ]]; then
  printf '%s\n' "$*" >> "$KUBECTL_LOG"
fi
case "$1" in
  version) exit 0 ;;
esac
if [[ "{GET_FAILS}" == "1" && "$1" == "get" ]]; then
  printf '%s\n' "The connection to the server 127.0.0.1:6443 was refused" >&2
  exit 1
fi
# Collector-config probe: dg.sh inspects the collector ConfigMap to decide
# whether the tee is wired. When TEE_WIRED=1 echo a config carrying the
# dedicated pipeline; else a config without it.
if [[ "$*" == *"get"* && "$*" == *"otel-collector-config"* ]]; then
  if [[ "{TEE_WIRED}" == "1" ]]; then
    printf '%s' "traces/data_governance"
  else
    printf '%s' "traces/default"
  fi
  exit 0
fi
# Namespace presence probe.
if [[ "$*" == *"get"* && "$*" == *"namespace"* && "$*" == *"data-governance"* ]]; then
  if [[ "{NS_PRESENT}" == "1" ]]; then
    printf '%s\n' "data-governance"
    exit 0
  else
    printf '%s\n' 'Error from server (NotFound): namespaces "data-governance" not found' >&2
    exit 1
  fi
fi
# Deployment readiness probe: report readyReplicas per deployment.
if [[ "$*" == *"get"* && ( "$*" == *"deploy"* || "$*" == *"deployment"* ) ]]; then
  if [[ "{DEPLOY_READY}" == "1" ]]; then
    printf '%s\n' "1"
  else
    printf '%s\n' "0"
  fi
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
    scriptsdir = tmp_path / "scripts"
    scriptsdir.mkdir()
    log = tmp_path / "kubectl.log"
    scriptlog = tmp_path / "scripts.log"

    _CONTROLLED = {"kubectl", "docker", "podman"}
    for tool in (
        "bash", "sh", "env", "basename", "dirname", "cat", "sed", "grep",
        "awk", "tr", "sort", "printf", "head", "tail", "cut", "uniq", "mktemp",
        "rm", "wc", "xargs",
    ):
        src = _which(tool)
        if src and Path(tool).name not in _CONTROLLED:
            (sysdir / tool).symlink_to(src)

    class _Sandbox:
        def __init__(self) -> None:
            self.bindir = bindir
            self.scriptsdir = scriptsdir
            self.log = log
            self.scriptlog = scriptlog
            self.set_container_tool("docker")
            self.set_kubectl()
            # Fake sibling scripts: record argv, succeed.
            self.set_scripts()

        def set_kubectl(
            self,
            get_fails: bool = False,
            tee_wired: bool = True,
            ns_present: bool = True,
            deploy_ready: bool = True,
        ) -> None:
            body = _FAKE_KUBECTL_TMPL.format(
                GET_FAILS="1" if get_fails else "0",
                TEE_WIRED="1" if tee_wired else "0",
                NS_PRESENT="1" if ns_present else "0",
                DEPLOY_READY="1" if deploy_ready else "0",
            )
            _make_bin(bindir, "kubectl", body)

        def set_container_tool(self, name: str) -> None:
            for t in ("docker", "podman"):
                p = bindir / t
                if p.exists() or p.is_symlink():
                    p.unlink()
            if name:
                _make_bin(bindir, name, "exit 0\n")

        def set_scripts(self, build_fails: bool = False, tee_fails: bool = False) -> None:
            """Install fake build-and-load.sh + patch-rossoctl-collector.sh.

            They log ``<name> <args>`` to $SCRIPTS_LOG so tests can assert dg.sh
            drove them (and, for the tee, with/without --revert)."""
            _make_bin(
                scriptsdir,
                "build-and-load.sh",
                'printf "build-and-load.sh %s\\n" "$*" >> "$SCRIPTS_LOG"\n'
                + ("exit 1\n" if build_fails else "exit 0\n"),
            )
            _make_bin(
                scriptsdir,
                "patch-rossoctl-collector.sh",
                'printf "patch-rossoctl-collector.sh %s\\n" "$*" >> "$SCRIPTS_LOG"\n'
                + ("exit 1\n" if tee_fails else "exit 0\n"),
            )

        def drop(self, name: str) -> None:
            p = bindir / name
            if p.exists() or p.is_symlink():
                p.unlink()

        def kubectl_calls(self) -> list[str]:
            if not log.exists():
                return []
            return [ln for ln in log.read_text().splitlines() if ln.strip()]

        def script_calls(self) -> list[str]:
            if not scriptlog.exists():
                return []
            return [ln for ln in scriptlog.read_text().splitlines() if ln.strip()]

        def run(self, *args: str, **kw) -> subprocess.CompletedProcess:
            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{sysdir}"
            env["KUBECTL_LOG"] = str(log)
            env["SCRIPTS_LOG"] = str(scriptlog)
            # Point dg.sh at the fake sibling scripts (override the real
            # deploy/*.sh it would otherwise resolve next to itself).
            env["DG_BUILD_AND_LOAD"] = str(scriptsdir / "build-and-load.sh")
            env["DG_PATCH_COLLECTOR"] = str(scriptsdir / "patch-rossoctl-collector.sh")
            env.setdefault("KIND_CLUSTER", "rossoctl")
            env.update(kw.pop("env", {}) or {})
            return subprocess.run(
                ["bash", str(DG_SH), *args],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
                **kw,
            )

    return _Sandbox()


# ---------------------------------------------------------------------------
# component install
# ---------------------------------------------------------------------------


def test_component_install_drives_full_pipeline(sandbox) -> None:
    """install: build+load → apply -f deploy/k8s → tee → rollout restart+status."""
    r = sandbox.run("component", "install")
    assert r.returncode == 0, r.stderr
    scripts = " ".join(sandbox.script_calls())
    kubectl = " ".join(sandbox.kubectl_calls())
    # build-and-load ran (default, no --no-build).
    assert "build-and-load.sh" in scripts, f"install must build+load; scripts={scripts!r}"
    # manifests applied.
    assert "apply" in kubectl and "deploy/k8s" in kubectl, (
        f"install must apply -f deploy/k8s/; kubectl={sandbox.kubectl_calls()!r}"
    )
    # tee wired (apply mode: not --revert).
    tee_calls = [c for c in sandbox.script_calls() if "patch-rossoctl-collector.sh" in c]
    assert tee_calls, "install must run the collector-tee patch"
    assert not any("--revert" in c for c in tee_calls), "install tees, does not revert"
    # rollout restart + status of the load-bearing deployments.
    assert "rollout restart" in kubectl, "install must rollout restart"
    assert "rollout status" in kubectl, "install must wait via rollout status"


def test_component_install_rollout_targets_receiver_ui_interactions(sandbox) -> None:
    r = sandbox.run("component", "install")
    assert r.returncode == 0, r.stderr
    restart_calls = [c for c in sandbox.kubectl_calls() if "rollout restart" in c]
    joined = " ".join(restart_calls)
    for dep in (
        "data-governance-receiver",
        "data-governance-ui",
        "data-governance-interactions",
    ):
        assert dep in joined, f"rollout restart must cycle {dep}; got {restart_calls!r}"


def test_component_install_no_build_skips_build(sandbox) -> None:
    r = sandbox.run("component", "install", "--no-build")
    assert r.returncode == 0, r.stderr
    scripts = sandbox.script_calls()
    assert not any("build-and-load.sh" in c for c in scripts), (
        f"--no-build must skip the image build; scripts={scripts!r}"
    )
    # …but still applies + tees + rolls.
    kubectl = " ".join(sandbox.kubectl_calls())
    assert "apply" in kubectl, "--no-build still applies manifests"
    assert any("patch-rossoctl-collector.sh" in c for c in scripts), (
        "--no-build still wires the tee"
    )
    assert "rollout restart" in kubectl, "--no-build still rolls"


def test_component_install_idempotent_no_duplicate_tee(sandbox) -> None:
    """Re-running install is not an error and does not double-wire the tee.

    (The tee script is itself idempotent; dg.sh must simply invoke it, not
    guard against re-running.)"""
    r1 = sandbox.run("component", "install", "--no-build")
    r2 = sandbox.run("component", "install", "--no-build")
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr


def test_component_install_fails_loud_on_build_failure(sandbox) -> None:
    sandbox.set_scripts(build_fails=True)
    r = sandbox.run("component", "install")
    assert r.returncode != 0, "a failed build must abort install loudly"
    # must not have proceeded to apply after the build failed.
    assert not any("apply" in c for c in sandbox.kubectl_calls()), (
        "install must not apply manifests after a failed build"
    )


def test_component_install_fails_loud_on_tee_failure(sandbox) -> None:
    sandbox.set_scripts(tee_fails=True)
    r = sandbox.run("component", "install", "--no-build")
    assert r.returncode != 0, "a failed collector-tee patch must abort install loudly"


# ---------------------------------------------------------------------------
# component uninstall
# ---------------------------------------------------------------------------


def test_component_uninstall_reverts_tee_and_deletes_route_grant_ns(sandbox) -> None:
    r = sandbox.run("component", "uninstall")
    assert r.returncode == 0, r.stderr
    scripts = sandbox.script_calls()
    kubectl = " ".join(sandbox.kubectl_calls())
    # tee reverted.
    tee_calls = [c for c in scripts if "patch-rossoctl-collector.sh" in c]
    assert tee_calls, "uninstall must run the collector-tee patch"
    assert all("--revert" in c for c in tee_calls), "uninstall must --revert the tee"
    # HTTPRoute deleted (in rossoctl-system).
    assert "httproute" in kubectl.lower() or "HTTPRoute" in kubectl, (
        f"uninstall must delete the HTTPRoute; kubectl={sandbox.kubectl_calls()!r}"
    )
    assert "rossoctl-system" in kubectl, "HTTPRoute lives in rossoctl-system"
    # ReferenceGrant deleted.
    assert "referencegrant" in kubectl.lower() or "ReferenceGrant" in kubectl, (
        "uninstall must delete the ReferenceGrant"
    )
    # namespace deleted (default: takes the PVC).
    assert "delete" in kubectl and "namespace" in kubectl and "data-governance" in kubectl, (
        "uninstall (default) must delete the data-governance namespace"
    )


def test_component_uninstall_default_deletes_namespace(sandbox) -> None:
    r = sandbox.run("component", "uninstall")
    assert r.returncode == 0, r.stderr
    del_ns = [
        c for c in sandbox.kubectl_calls()
        if "delete" in c and "namespace" in c and "data-governance" in c
    ]
    assert del_ns, f"default uninstall deletes the namespace; calls={sandbox.kubectl_calls()!r}"


def test_component_uninstall_keep_data_preserves_pvc(sandbox) -> None:
    """--keep-data deletes workloads individually and NEVER deletes the namespace
    (which would take the Postgres PVC with it)."""
    r = sandbox.run("component", "uninstall", "--keep-data")
    assert r.returncode == 0, r.stderr
    calls = sandbox.kubectl_calls()
    # must NOT delete the namespace.
    assert not any(
        "delete" in c and "namespace" in c and "data-governance" in c
        for c in calls
    ), f"--keep-data must NOT delete the namespace (takes the PVC); calls={calls!r}"
    # must still revert the tee + delete the route/grant.
    scripts = sandbox.script_calls()
    assert any(
        "patch-rossoctl-collector.sh" in c and "--revert" in c for c in scripts
    ), "--keep-data still reverts the tee"
    kubectl = " ".join(calls)
    assert "httproute" in kubectl.lower(), "--keep-data still deletes the HTTPRoute"


def test_component_uninstall_keep_data_deletes_workloads(sandbox) -> None:
    """--keep-data removes the deployments (workloads) individually while
    leaving the PVC (namespace) intact."""
    r = sandbox.run("component", "uninstall", "--keep-data")
    assert r.returncode == 0, r.stderr
    calls = " ".join(sandbox.kubectl_calls())
    # A delete of the receiver deployment is evidence of individual-workload teardown.
    assert "delete" in calls and "data-governance-receiver" in calls, (
        f"--keep-data must delete workloads individually; calls={sandbox.kubectl_calls()!r}"
    )
    # …but must NOT delete the postgres PVC.
    assert not (
        "delete" in calls and "pvc" in calls.lower()
    ), "--keep-data must not delete the Postgres PVC"


def test_component_uninstall_leaves_shared_collector_clean(sandbox) -> None:
    """The shared otel-collector must be left clean — i.e. the tee is reverted,
    not merely the DG namespace torn down."""
    r = sandbox.run("component", "uninstall")
    assert r.returncode == 0, r.stderr
    assert any(
        "patch-rossoctl-collector.sh" in c and "--revert" in c
        for c in sandbox.script_calls()
    ), "uninstall must revert the tee so the shared collector is left clean"


# ---------------------------------------------------------------------------
# component status  (and bare dg.sh)
# ---------------------------------------------------------------------------


def test_component_status_reports_ready_and_tee_wired(sandbox) -> None:
    sandbox.set_kubectl(ns_present=True, deploy_ready=True, tee_wired=True)
    r = sandbox.run("component", "status")
    assert r.returncode == 0, r.stderr
    out = (r.stdout + r.stderr).lower()
    # It reports readiness and tee state affirmatively.
    assert "ready" in out or "installed" in out, f"status must report readiness; out={out!r}"
    assert "tee" in out or "collector" in out or "pipeline" in out, (
        f"status must report the collector-tee state; out={out!r}"
    )


def test_component_status_reports_not_installed(sandbox) -> None:
    """When the namespace is absent and the tee is not wired, status says so —
    it does not falsely claim installed/ready."""
    sandbox.set_kubectl(ns_present=False, deploy_ready=False, tee_wired=False)
    r = sandbox.run("component", "status")
    out = (r.stdout + r.stderr).lower()
    # Must not falsely claim ready/wired.
    assert "not" in out or "absent" in out or "missing" in out or "no" in out, (
        f"status must report the not-installed/not-wired state; out={out!r}"
    )


def test_component_status_reports_tee_not_wired(sandbox) -> None:
    sandbox.set_kubectl(ns_present=True, deploy_ready=True, tee_wired=False)
    r = sandbox.run("component", "status")
    out = (r.stdout + r.stderr).lower()
    assert "tee" in out or "collector" in out or "pipeline" in out, (
        f"status must report the tee state; out={out!r}"
    )


def test_bare_dg_sh_is_component_status(sandbox) -> None:
    sandbox.set_kubectl(ns_present=True, deploy_ready=True, tee_wired=True)
    r = sandbox.run()
    assert r.returncode == 0, r.stderr
    out = (r.stdout + r.stderr).lower()
    assert "status" in out or "ready" in out or "installed" in out, (
        f"bare dg.sh must be component status; out={out!r}"
    )


def test_component_status_fails_loud_when_kubectl_get_errors(sandbox) -> None:
    """A failed `kubectl get` (unreachable/RBAC-denied API — version --client
    still succeeds) must be a LOUD non-zero exit, not a misleading OK."""
    sandbox.set_kubectl(get_fails=True)
    r = sandbox.run("component", "status")
    assert r.returncode != 0, "status must not exit 0 when the API is unreachable"
    combined = (r.stdout + r.stderr).lower()
    assert combined.strip(), "a failed kubectl get must not exit with EMPTY output"


def test_component_status_is_read_only(sandbox) -> None:
    """status must never mutate — no apply/delete/rollout/tee."""
    sandbox.set_kubectl(ns_present=True, deploy_ready=True, tee_wired=True)
    sandbox.run("component", "status")
    calls = " ".join(sandbox.kubectl_calls())
    assert "apply" not in calls, "status must not apply"
    assert "delete" not in calls, "status must not delete"
    assert "rollout restart" not in calls, "status must not roll"
    assert not any(
        "patch-rossoctl-collector.sh" in c and "--revert" not in c
        for c in sandbox.script_calls()
        if "--help" not in c
    ), "status must not wire the tee"
