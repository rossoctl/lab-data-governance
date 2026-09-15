"""Behavioural tests for the vendored PROXY live applier ``sidecar-patch-proxy.sh``
(ADR-0033 Decision 2, issue #245).

The proxy generator ``attach-lineage-proxy.sh`` (issue #243) is **stdout-only** —
it prints ``EMIT=cm`` / ``EMIT=patch`` / ``EMIT=undo`` and never touches the
cluster. The envoy path already had a live applier (``sidecar-patch.sh``) that
generates those objects, dry-runs the merged patch, applies the ConfigMap + the
patch, prints its reverse-patch back-out line, and waits for the rollout. The
proxy path needs the SAME applier shape so ``dg.sh`` can drive it exactly like
the envoy path (the #245 owner-split routes a no-sidecar / non-envoy-configured
namespace to the proxy).

``sidecar-patch-proxy.sh`` is that applier — the proxy sibling of
``sidecar-patch.sh``. It differs only where the proxy path differs:

  * it does **not** require the platform ``envoy-config`` ConfigMap (the auth-free
    proxy mounts none);
  * its collision preconditions guard the proxy's own container names
    (``proxy-init`` / ``authbridge-proxy``), volume (``authbridge-runtime``) and
    ports (``8081`` / ``8082`` / ``9091``);
  * it forwards ``OUTBOUND_PORTS_INCLUDE`` (the include-only allowlist), not the
    envoy path's ``OUTBOUND_PORTS_EXCLUDE`` denylist.

Like the rest of ``tests/deploy/``, these drive the REAL script as a subprocess
with a **fake ``kubectl``** on a synthetic ``PATH`` — no live cluster. The fake
serves the reads the applier makes (deployment jsonpath probes for the
preconditions, the current app image, the CM existence check) and logs every
argv so tests can assert what was applied.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
KIT = REPO_ROOT / "deploy" / "lineage-attach"
PROXY_APPLIER = KIT / "sidecar-patch-proxy.sh"

# A locally-built proxy image tag carrying lineage-telemetry (the published
# default is refused by the generator until a release carries the plugin).
SIDECAR_IMAGE = "docker.io/library/authbridge:lineage-test"


def _make_bin(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


# A fake kubectl that models one Deployment (`$DEPLOY`) with a clean template
# (app container only, on port 8080) and answers exactly the reads the applier
# makes. Behaviour knobs come from the environment:
#   NAMES_COLLIDE=<name>   → the deploy reports a container/init named <name>
#   PORTS_COLLIDE=<port>   → the deploy declares containerPort <port>
#   VOLS_COLLIDE=<name>    → the deploy reports a volume named <name>
#   NO_APP_CONTAINER=1     → the deploy has NO container matching APP_CONTAINER
#   CM_EXISTS=1            → the per-app ConfigMap already exists (re-attach)
#   DRYRUN_FAILS=1         → `kubectl patch --dry-run=server` rejects the merge
#   ROLLOUT_FAILS=1        → `kubectl rollout status` fails (crash-loop symptom)
_FAKE_KUBECTL = r"""
# Log a COMPACT one-line summary per invocation — never the raw argv, which for a
# `patch --patch <multi-line YAML>` would smear one call across dozens of log
# lines and break line-oriented assertions. The summary keeps the verb, the
# object kind, and the flags/fragments tests assert on (--dry-run=server, the
# include allowlist token, the propagation switch), collapsed to one line.
summary="$1"
[[ -n "${2:-}" ]] && summary="$summary $2"
case "$*" in *"--dry-run=server"*) summary="$summary --dry-run=server" ;; esac
case "$*" in *"OUTBOUND_PORTS_INCLUDE"*) summary="$summary HAS:OUTBOUND_PORTS_INCLUDE" ;; esac
case "$*" in *"OUTBOUND_PORTS_EXCLUDE"*) summary="$summary HAS:OUTBOUND_PORTS_EXCLUDE" ;; esac
case "$*" in *"LINEAGE_PROPAGATE"*) summary="$summary HAS:LINEAGE_PROPAGATE" ;; esac
# Only a `get ... envoy-config` (a READ/require of the platform CM) is marked —
# NOT the word appearing in the patch body's own explanatory comments.
if [[ "$1" == "get" && "$*" == *"envoy-config"* ]]; then summary="$summary READS:envoy-config"; fi
printf '%s\n' "$summary" >> "${KUBECTL_LOG}"
case "$1" in version) exit 0 ;; esac

# ---- deployment existence (require_deployment: `get deploy -n <ns> <name>`) ---
# A jsonpath read is one of the precondition probes below; a plain existence
# check (no -o) just needs exit 0.
if [[ "$1" == "get" && ( "$2" == "deploy" || "$2" == "deployment" || "$2" == "deployments" ) ]]; then
  # The applier's precondition probes are jsonpath reads whose expressions look
  # like `{range .spec.template.spec.<list>[*]...}{.name|.containerPort}{" "}{end}`
  # — match on the LIST name, which is the unambiguous discriminator.
  # ports probe (refuse_port_collision) — check before the name probes.
  if [[ "$*" == *"containerPort"* ]]; then
    ports="8080"
    [[ -n "${PORTS_COLLIDE:-}" ]] && ports="8080 ${PORTS_COLLIDE}"
    printf '%s ' $ports; exit 0
  fi
  # image probe (apply captures restored_image via a name-filtered jsonpath).
  if [[ "$*" == *".image"* || "$*" == *"@.name=="* ]]; then
    printf 'docker.io/library/research-agent:pre-attach'; exit 0
  fi
  # volumes name probe (refuse_volume_collision) — ranges .volumes[*].
  if [[ "$*" == *"volumes[*]"* ]]; then
    vols=""
    [[ -n "${VOLS_COLLIDE:-}" ]] && vols="${VOLS_COLLIDE}"
    printf '%s ' $vols; exit 0
  fi
  # initContainers+containers name probe (refuse_name_collision) — ranges BOTH.
  if [[ "$*" == *"initContainers[*]"* ]]; then
    names="app-container"
    [[ -n "${NAMES_COLLIDE:-}" ]] && names="app-container ${NAMES_COLLIDE}"
    printf '%s ' $names; exit 0
  fi
  # app-container names probe (require_app_container) — ranges .containers[*] only.
  if [[ "$*" == *"containers[*]"* ]]; then
    if [[ "${NO_APP_CONTAINER:-0}" == "1" ]]; then printf 'app-container '; else printf 'agent '; fi
    exit 0
  fi
  # bare existence check (require_deployment).
  exit 0
fi

# ---- per-app ConfigMap existence (apply: cm_existed guard) ------------------
if [[ "$1" == "get" && ( "$2" == "cm" || "$2" == "configmap" || "$2" == "configmaps" ) ]]; then
  if [[ "${CM_EXISTS:-0}" == "1" ]]; then printf 'exists'; exit 0; fi
  printf '%s\n' 'Error from server (NotFound): configmaps not found' >&2; exit 1
fi

# ---- writes -----------------------------------------------------------------
if [[ "$1" == "apply" ]]; then exit 0; fi
if [[ "$1" == "patch" ]]; then
  if [[ "$*" == *"--dry-run=server"* ]]; then
    if [[ "${DRYRUN_FAILS:-0}" == "1" ]]; then
      printf '%s\n' 'Error from server: admission webhook denied the request' >&2; exit 1
    fi
    printf 'deployment.apps/research-agent\n'; exit 0
  fi
  exit 0
fi
if [[ "$1" == "delete" ]]; then exit 0; fi
if [[ "$1" == "rollout" ]]; then
  if [[ "${ROLLOUT_FAILS:-0}" == "1" ]]; then
    printf '%s\n' 'error: deployment "research-agent" exceeded its progress deadline' >&2; exit 1
  fi
  printf '%s\n' 'deployment "research-agent" successfully rolled out'; exit 0
fi
exit 0
"""


def _wrote_anything(log: str) -> bool:
    """True if the kubectl log shows any MUTATING call — a real (non-dry-run)
    patch, an apply, or a delete. A dry-run patch is read-only, so it does not
    count as a write."""
    for c in log.splitlines():
        if c.startswith("apply") or c.startswith("delete"):
            return True
        if c.startswith("patch") and "--dry-run=server" not in c:
            return True
    return False


def run_applier(tmp_path: Path, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run sidecar-patch-proxy.sh under a fake kubectl; return (proc, kubectl-log)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    _make_bin(bindir, "kubectl", _FAKE_KUBECTL)
    log = tmp_path / "kubectl.log"
    log.write_text("")
    full = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "KUBECTL_LOG": str(log),
        "SIDECAR_IMAGE": SIDECAR_IMAGE,
        **env,
    }
    proc = subprocess.run(
        ["bash", str(PROXY_APPLIER)], env=full, capture_output=True, text=True, timeout=60
    )
    return proc, log.read_text()


# ===========================================================================
# ships & is executable
# ===========================================================================


def test_proxy_applier_ships_and_is_executable() -> None:
    assert PROXY_APPLIER.is_file(), "sidecar-patch-proxy.sh must be vendored into deploy/lineage-attach/"
    assert os.access(PROXY_APPLIER, os.X_OK), "sidecar-patch-proxy.sh must be executable"


def test_deploy_is_required(tmp_path) -> None:
    """DEPLOY is the one required input (mirrors sidecar-patch.sh's read_inputs)."""
    proc, log = run_applier(tmp_path, {})  # DEPLOY unset
    assert proc.returncode != 0, "DEPLOY unset must be refused"
    assert not _wrote_anything(log), "a missing DEPLOY must apply nothing"


# ===========================================================================
# happy path: generate → dry-run → apply cm → patch → back-out → rollout
# ===========================================================================


def test_applier_applies_cm_then_patch_then_waits_rollout(tmp_path) -> None:
    proc, log = run_applier(tmp_path, {"DEPLOY": "research-agent", "NAMESPACE": "travel-advisor"})
    assert proc.returncode == 0, f"applier failed:\n{proc.stderr}"
    calls = log.splitlines()
    # A dry-run guard precedes any real write.
    dryrun_i = next(i for i, c in enumerate(calls) if c.startswith("patch") and "--dry-run=server" in c)
    apply_i = next(i for i, c in enumerate(calls) if c.startswith("apply"))
    patch_i = next(i for i, c in enumerate(calls) if c.startswith("patch") and "--dry-run" not in c)
    rollout_i = next(i for i, c in enumerate(calls) if c.startswith("rollout"))
    assert dryrun_i < apply_i < patch_i < rollout_i, f"order wrong; calls:\n{log}"


def test_applier_prints_reverse_patch_backout_line(tmp_path) -> None:
    proc, _ = run_applier(tmp_path, {"DEPLOY": "research-agent", "NAMESPACE": "travel-advisor"})
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert ">> back out:" in out, f"the applier must print its reverse-patch back-out line; got:\n{out}"
    assert "--type strategic" in out and "delete cm authbridge-lineage-config-research-agent" in out, (
        f"the back-out must be the generator's reverse-patch line; got:\n{out}"
    )
    assert "rollout undo" not in out.lower(), "the back-out is a reverse-patch, NOT a rollout undo"


def test_applier_forwards_include_allowlist_not_exclude(tmp_path) -> None:
    """The proxy path uses the include-only allowlist; a dry-run/patch of the
    generated object carries OUTBOUND_PORTS_INCLUDE (default 8080,8000), never the
    envoy path's OUTBOUND_PORTS_EXCLUDE denylist."""
    proc, log = run_applier(
        tmp_path,
        {"DEPLOY": "research-agent", "NAMESPACE": "travel-advisor",
         "OUTBOUND_PORTS_INCLUDE": "8080,8000,9000"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "HAS:OUTBOUND_PORTS_INCLUDE" in log, "the applied patch must carry the include allowlist"
    assert "HAS:OUTBOUND_PORTS_EXCLUDE" not in log, "the proxy path must not use the exclude denylist"


def test_applier_app_container_sets_propagation(tmp_path) -> None:
    proc, log = run_applier(
        tmp_path,
        {"DEPLOY": "research-agent", "NAMESPACE": "travel-advisor",
         "APP_CONTAINER": "agent", "APP_IMAGE": "docker.io/library/research-agent-otel:latest"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "HAS:LINEAGE_PROPAGATE" in log, "APP_CONTAINER must switch propagation on in the applied patch"


# ===========================================================================
# proxy path needs NO envoy-config (the auth-free proxy mounts none)
# ===========================================================================


def test_applier_does_not_require_envoy_config(tmp_path) -> None:
    """Unlike sidecar-patch.sh, the proxy applier must NOT probe for / require the
    platform envoy-config ConfigMap — the auth-free proxy mounts none."""
    proc, log = run_applier(tmp_path, {"DEPLOY": "research-agent", "NAMESPACE": "travel-advisor"})
    assert proc.returncode == 0, proc.stderr
    assert "READS:envoy-config" not in log, (
        f"the proxy applier must not read/require envoy-config; kubectl calls:\n{log}"
    )


# ===========================================================================
# preconditions (read-only refusals — mirror sidecar-patch.sh)
# ===========================================================================


@pytest.mark.parametrize("collide", ["proxy-init", "authbridge-proxy"])
def test_applier_refuses_name_collision(tmp_path, collide) -> None:
    proc, log = run_applier(
        tmp_path, {"DEPLOY": "research-agent", "NAMESPACE": "travel-advisor", "NAMES_COLLIDE": collide}
    )
    assert proc.returncode != 0, f"a target already carrying {collide} must be refused"
    assert not _wrote_anything(log), "a refused precondition must apply nothing"


@pytest.mark.parametrize("port", ["8081", "8082", "9091"])
def test_applier_refuses_port_collision(tmp_path, port) -> None:
    proc, log = run_applier(
        tmp_path, {"DEPLOY": "research-agent", "NAMESPACE": "travel-advisor", "PORTS_COLLIDE": port}
    )
    assert proc.returncode != 0, f"a target declaring the proxy port {port} must be refused"
    assert not _wrote_anything(log), "a refused precondition must apply nothing"


def test_applier_refuses_volume_collision(tmp_path) -> None:
    proc, log = run_applier(
        tmp_path,
        {"DEPLOY": "research-agent", "NAMESPACE": "travel-advisor", "VOLS_COLLIDE": "authbridge-runtime"},
    )
    assert proc.returncode != 0, "a target already carrying authbridge-runtime must be refused"
    assert not _wrote_anything(log)


def test_applier_refuses_unknown_app_container(tmp_path) -> None:
    proc, log = run_applier(
        tmp_path,
        {"DEPLOY": "research-agent", "NAMESPACE": "travel-advisor",
         "APP_CONTAINER": "agent", "NO_APP_CONTAINER": "1"},
    )
    assert proc.returncode != 0, "an APP_CONTAINER that names no real container must be refused"
    assert not _wrote_anything(log), "a refused app-container precondition must not apply a real patch"


# ===========================================================================
# post-apply failures fail loud
# ===========================================================================


def test_applier_dry_run_rejection_applies_nothing(tmp_path) -> None:
    proc, log = run_applier(
        tmp_path, {"DEPLOY": "research-agent", "NAMESPACE": "travel-advisor", "DRYRUN_FAILS": "1"}
    )
    assert proc.returncode != 0, "a dry-run rejection must fail loud"
    assert not _wrote_anything(log), f"a dry-run rejection must apply nothing; calls:\n{log}"


def test_applier_rollout_failure_fails_loud(tmp_path) -> None:
    proc, _ = run_applier(
        tmp_path, {"DEPLOY": "research-agent", "NAMESPACE": "travel-advisor", "ROLLOUT_FAILS": "1"}
    )
    assert proc.returncode != 0, "a failed rollout must fail loud"


def test_applier_takes_no_positional_args(tmp_path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_bin(bindir, "kubectl", _FAKE_KUBECTL)
    proc = subprocess.run(
        ["bash", str(PROXY_APPLIER), "research-agent"],
        env={"PATH": f"{bindir}:{os.environ['PATH']}", "KUBECTL_LOG": str(tmp_path / "k.log"),
             "DEPLOY": "research-agent", "SIDECAR_IMAGE": SIDECAR_IMAGE},
        capture_output=True, text=True,
    )
    assert proc.returncode != 0, "positional args must be refused (every input is env)"
