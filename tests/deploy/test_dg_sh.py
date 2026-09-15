"""Behavioural tests for the ``deploy/dg.sh`` cluster-management CLI (issue #181).

This ticket is the **skeleton + shared cluster helpers** slice of ``dg.sh``
(design: ``docs/cli.md``; ADR-0031 / ADR-0032). It lands:

- command-grammar dispatch (``dg.sh`` → component status stub; ``component``,
  ``namespace``, ``namespaces`` verbs; unknown/bad usage → usage + non-zero);
- ``namespaces list`` — the one verb that WORKS this ticket: prints exactly the
  user namespaces (labelled ``rossoctl-enabled=true``, minus ``kube-*`` and
  ``*-system``);
- the shared helpers the later verbs reuse — entity enumeration
  (``app.kubernetes.io/component in (agent, mcp-tool)``, optional single-entity
  select by ``app.kubernetes.io/name``, loud error on a name that does not
  resolve), never touching the operator-reserved ``rossoctl.io/type``;
- preflight primitives — ``kubectl`` reachable, a container tool detected
  (matching ``build-and-load.sh`` conventions), reported loudly when missing.

The tests drive the real script as a subprocess with a **fake ``kubectl``** (and
fake container tools) placed on a synthetic ``PATH`` — the same
no-live-cluster stance the rest of ``tests/deploy/`` takes. The fake records the
``kubectl`` argv it was called with and replays a canned response, so we assert
both the *selectors* ``dg.sh`` uses and the *output* it produces without a
cluster.

The ``--cortex-local-path`` global (issue #181 re-scope comment) was **retired**
when the lineage-attach kit was vendored into ``deploy/lineage-attach/`` (ADR-0033,
supersedes ADR-0032); we assert it is now rejected as an unknown option. The
(now-real, #184) ``instrument`` verb is pointed at a stub kit via the
``DG_LINEAGE_ATTACH_DIR`` test seam.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DG_SH = REPO_ROOT / "deploy" / "dg.sh"


# ---------------------------------------------------------------------------
# Fake-binary harness
# ---------------------------------------------------------------------------


def _make_bin(dir_: Path, name: str, body: str) -> Path:
    """Write an executable shim ``name`` into ``dir_`` with shell ``body``."""
    p = dir_ / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


# A fake kubectl that answers the two query shapes the skeleton needs:
#   * `kubectl get namespaces ...`  -> newline list of ns names
#   * `kubectl get deploy/pods ...` -> newline list of entity names
# It logs its full argv (one invocation per line) to $KUBECTL_LOG so tests can
# assert the selectors dg.sh used. Everything else returns empty + success.
_FAKE_KUBECTL_TMPL = r"""
if [[ -n "${{KUBECTL_LOG:-}}" ]]; then
  printf '%s\n' "$*" >> "$KUBECTL_LOG"
fi
# `kubectl version --client` etc. must succeed for the reachability preflight.
case "$1" in
  version) exit 0 ;;
esac
# Optional: make `kubectl get` fail like an unreachable/RBAC-denied API server,
# writing a real diagnostic to stderr and exiting non-zero — while `version`
# above still succeeds (mirrors the client-runnable/API-unreachable split).
if [[ "{GET_FAILS}" == "1" && "$1" == "get" ]]; then
  printf '%s\n' "The connection to the server 127.0.0.1:6443 was refused" >&2
  exit 1
fi
# instrument preflights (#184): the DG component namespace is present and the
# collector tee is wired, so enumeration is reached. These are read-only probes.
if [[ "$*" == *"get"* && "$*" == *"namespace"* && "$*" == *"data-governance"* ]]; then
  printf '%s\n' "data-governance"; exit 0
fi
if [[ "$*" == *"get"* && "$*" == *"otel-collector-config"* ]]; then
  printf '%s' "traces/data_governance"; exit 0
fi
# Namespace listing.
if [[ "$*" == *"get namespace"* || "$*" == *"get namespaces"* || "$*" == *"get ns"* ]]; then
  printf '%s' "{NS_OUT}"
  exit 0
fi
# Entity (agent/tool) listing. The component-selector query returns entity NAMES
# (jsonpath). instrument then does a per-entity `-o json` get for detection — for
# these skeleton-level selector-shape tests the entity has no sidecar, so a
# minimal Deployment doc (no sidecar container) is enough and the entity is
# classified `none` (which then tries the kit — harmless, the kit is a stub).
if [[ "$*" == *"app.kubernetes.io/component"* ]]; then
  # NB: `-o json` is a SUBSTRING of `-o jsonpath=`, so match jsonpath FIRST
  # (the enumeration name-listing uses jsonpath; per-entity detection uses json).
  case "$*" in
    *"jsonpath"*)
      printf '%s' "{ENTITY_OUT}"
      exit 0 ;;
    *"-o json"*|*"-ojson"*)
      # Build an items[] of minimal Deployments for each ENTITY_OUT name.
      # NB: literal braces are DOUBLED — this heredoc passes through str.format.
      python3 - "{ENTITY_OUT}" <<'PY'
import json, sys
names = [n for n in sys.argv[1].split("\n") if n.strip()]
# -o json here is only used post-enumeration (per-entity detection); the
# jsonpath name-listing branch below is what scopes and fails-loud on a bad name.
items = [{{
    "apiVersion": "apps/v1", "kind": "Deployment",
    "metadata": {{"name": n, "labels": {{"app.kubernetes.io/name": n,
                                        "app.kubernetes.io/component": "agent"}}}},
    "spec": {{"template": {{"spec": {{"containers": [{{"name": n, "image": "x:latest"}}],
                                    "volumes": []}}}}}},
}} for n in names]
sys.stdout.write(json.dumps({{"items": items}}))
PY
      exit 0 ;;
    *)
      printf '%s' "{ENTITY_OUT}"
      exit 0 ;;
  esac
fi
exit 0
"""


@pytest.fixture()
def sandbox(tmp_path: Path):
    """A synthetic PATH dir with a real bash + a fake kubectl/container tool.

    Returns a helper object exposing:
      * ``run(*args, **kw)`` — run dg.sh with the sandbox PATH,
      * ``set_kubectl(ns_out=..., entity_out=...)`` — install/replace the fake
        kubectl with canned newline output,
      * ``drop(name)`` — remove a binary (e.g. simulate missing kubectl),
      * ``kubectl_calls()`` — the recorded kubectl argv lines.
    """
    # Two dirs. `bindir` holds the CONTROLLED tools (fake kubectl / container
    # tools) tests turn on and off; `sysdir` holds symlinks to the real
    # coreutils + bash the script legitimately needs. bindir comes FIRST on
    # PATH so its fakes shadow anything, and sysdir carries NONE of
    # kubectl/docker/podman — so dropping one from bindir makes it genuinely
    # unavailable (the real system PATH, where those live, is not on PATH at
    # all). This keeps the missing-tool preflight tests honest.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    sysdir = tmp_path / "sys"
    sysdir.mkdir()
    log = tmp_path / "kubectl.log"

    _CONTROLLED = {"kubectl", "docker", "podman"}
    for tool in (
        "bash", "sh", "env", "basename", "dirname", "cat", "sed", "grep",
        "awk", "tr", "sort", "printf", "head", "tail", "cut", "uniq",
        # python3 (+ mktemp/rm) are needed once `instrument` is a real verb
        # (#184): its per-entity dispatch and the shared read model use them.
        "python3", "mktemp", "rm",
    ):
        src = _which(tool)
        if src and Path(tool).name not in _CONTROLLED:
            (sysdir / tool).symlink_to(src)

    # A stub lineage-attach kit, so the enumeration-shape tests can drive the
    # (now-real, #184) `instrument` verb past its vendored-kit integrity check.
    # The stubs succeed and no-op; these #181 tests assert only the shared
    # enumeration selector shape, not the kit's behaviour (that is #184's file).
    # dg.sh is pointed at it via DG_LINEAGE_ATTACH_DIR (the test seam that
    # replaced --cortex-local-path when ADR-0033 vendored the kit).
    kitdir = tmp_path / "lineage-attach"
    kitdir.mkdir(parents=True)
    for s in ("sidecar-patch.sh", "sidecar-patch-proxy.sh", "build-otel-shim.sh", "attach-lineage.sh"):
        _make_bin(kitdir, s, "exit 0\n")
    # The sourced / build-input companions require_vendored_kit also checks for
    # (both shims are build inputs; ADR-0033 D4).
    for f in ("container-runtime.sh", "Dockerfile.otel-shim", "lineage-propagate-hook.py",
              "rossoctl_turnspan.py", "rossoctl_turnspan.pth"):
        (kitdir / f).write_text("# stub\n")

    class _Sandbox:
        def __init__(self) -> None:
            self.bindir = bindir
            self.log = log
            self.kitdir = kitdir
            # default: docker present (podman absent) so the container-tool
            # preflight is satisfiable.
            self.set_container_tool("docker")
            self.set_kubectl()

        def set_kubectl(
            self, ns_out: str = "", entity_out: str = "", get_fails: bool = False
        ) -> None:
            body = _FAKE_KUBECTL_TMPL.format(
                NS_OUT=ns_out,
                ENTITY_OUT=entity_out,
                GET_FAILS="1" if get_fails else "0",
            )
            _make_bin(bindir, "kubectl", body)

        def set_container_tool(self, name: str) -> None:
            for t in ("docker", "podman"):
                p = bindir / t
                if p.exists() or p.is_symlink():
                    p.unlink()
            if name:
                _make_bin(bindir, name, "exit 0\n")

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
            env.setdefault("KIND_CLUSTER", "rossoctl")
            # Point the (now-real) `instrument` verb at the stub kit via the
            # DG_LINEAGE_ATTACH_DIR test seam (replaced --cortex-local-path;
            # ADR-0033). A test may override it through env= to exercise a
            # missing/incomplete kit.
            env.setdefault("DG_LINEAGE_ATTACH_DIR", str(kitdir))
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


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


# ---------------------------------------------------------------------------
# Presence / shape of the script itself
# ---------------------------------------------------------------------------


def test_dg_sh_exists_and_executable() -> None:
    assert DG_SH.is_file(), f"expected {DG_SH} to exist"
    mode = DG_SH.stat().st_mode
    assert mode & stat.S_IXUSR, f"{DG_SH} must be executable (chmod +x)"


def test_dg_sh_has_strict_mode() -> None:
    """Fail-loud posture starts with strict bash flags."""
    text = DG_SH.read_text()
    assert "set -euo pipefail" in text, "dg.sh must run under `set -euo pipefail`"


# ---------------------------------------------------------------------------
# Grammar dispatch
# ---------------------------------------------------------------------------


def test_no_args_dispatches_to_component_status(sandbox) -> None:
    """Bare `dg.sh` == `component status` — read-only, exits 0 against a reachable
    cluster and reports the component + collector-tee state.

    (`component status` became a real, read-only verb in #182; the full status
    behaviour is covered by tests/deploy/test_dg_sh_component.py — here we only
    guard the bare-dispatch + read-only contract with the skeleton harness.)"""
    r = sandbox.run()
    assert r.returncode == 0, r.stderr
    combined = (r.stdout + r.stderr).lower()
    # It reports the component and the collector-tee state.
    assert "component" in combined
    assert "tee" in combined or "collector" in combined or "pipeline" in combined
    # …and never mutates.
    calls = " ".join(sandbox.kubectl_calls())
    assert not any(tok in calls for tok in ("apply", "delete", "rollout restart")), (
        "component status must be read-only"
    )


def test_component_status_subcommand_recognised(sandbox) -> None:
    """`component status` is recognised and exits 0 against a reachable cluster.

    (`install`/`uninstall` are real, cluster-mutating verbs as of #182 and are
    driven with a richer fake-script harness in test_dg_sh_component.py; the
    skeleton harness here has no build/tee fakes, so it only exercises the
    read-only `status`.)"""
    r = sandbox.run("component", "status")
    assert r.returncode == 0, f"component status: {r.stderr}"


def test_component_install_uninstall_are_real_verbs(sandbox) -> None:
    """As of #182 install/uninstall are REAL verbs, not stubs — they no longer
    announce "not implemented" and they DO drive the cluster (install reaches
    the image build; uninstall reverts the tee + deletes resources).

    The skeleton harness has no fake sibling scripts, so `install` fails loud at
    the (real) build step — which itself proves it is no longer an inert stub.
    The full happy-path behaviour lives in test_dg_sh_component.py."""
    r_install = sandbox.run("component", "install")
    combined = (r_install.stdout + r_install.stderr).lower()
    assert "not implemented" not in combined and "stub" not in combined, (
        f"component install is a real verb as of #182, got: {combined!r}"
    )
    # It attempted real work (the build step), rather than silently no-opping.
    assert "build" in combined or "install" in combined


def test_namespace_verb_recognised(sandbox) -> None:
    """`namespace <ns> instrument` is a REAL verb as of #184 (its behaviour is
    covered in tests/deploy/test_dg_sh_instrument.py); `status` became real in
    #183 (tests/deploy/test_dg_sh_namespace_status.py). Here we only guard that
    the `instrument` verb is recognised (dispatched, not "unknown action") — it
    is no longer the #181 stub. It runs the real activation path (against the
    stub kit the sandbox points DG_LINEAGE_ATTACH_DIR at), which is a recognised
    dispatch, not an 'unknown namespace action'."""
    sandbox.set_kubectl(entity_out="research-agent\n")
    r = sandbox.run("namespace", "some-ns", "instrument")
    combined = (r.stdout + r.stderr).lower()
    # Recognised: it is dispatched (it reaches the real instrument path), not
    # rejected as an unknown action, and it is no longer a stub.
    assert "unknown namespace action" not in combined, (
        f"instrument must be a recognised verb; got:\n{combined}"
    )
    assert "not implemented" not in combined and "stub" not in combined, (
        f"instrument is a real verb as of #184, not a stub; got:\n{combined}"
    )


def test_namespace_requires_a_namespace_arg(sandbox) -> None:
    """`namespace` with no <ns> is bad usage."""
    r = sandbox.run("namespace")
    assert r.returncode != 0
    assert "usage" in (r.stdout + r.stderr).lower()


def test_unknown_subcommand_prints_usage_nonzero(sandbox) -> None:
    r = sandbox.run("frobnicate")
    assert r.returncode != 0, "unknown verb must exit non-zero"
    assert "usage" in (r.stdout + r.stderr).lower()


def test_unknown_component_action_is_bad_usage(sandbox) -> None:
    r = sandbox.run("component", "explode")
    assert r.returncode != 0
    assert "usage" in (r.stdout + r.stderr).lower()


# ---------------------------------------------------------------------------
# namespaces list — the verb that WORKS this ticket
# ---------------------------------------------------------------------------


# Canned kubectl namespace output: name<TAB>label-value pairs are avoided; the
# script asks for names only (it uses a label selector), so the fake returns the
# already-filtered-by-label set and dg.sh must additionally drop kube-*/*-system.
_NS_OUTPUT = (
    "default\n"
    "travel-advisor\n"
    "kube-system\n"          # excluded: kube-*
    "kube-public\n"          # excluded: kube-*
    "rossoctl-system\n"      # excluded: *-system
    "data-governance\n"
    "my-system\n"            # excluded: *-system
)


def test_namespaces_list_prints_user_namespaces(sandbox) -> None:
    sandbox.set_kubectl(ns_out=_NS_OUTPUT)
    r = sandbox.run("namespaces", "list")
    assert r.returncode == 0, r.stderr
    printed = {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    assert "default" in printed
    assert "travel-advisor" in printed
    assert "data-governance" in printed


def test_namespaces_list_excludes_system_and_kube(sandbox) -> None:
    sandbox.set_kubectl(ns_out=_NS_OUTPUT)
    r = sandbox.run("namespaces", "list")
    assert r.returncode == 0, r.stderr
    printed = {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    for excluded in ("kube-system", "kube-public", "rossoctl-system", "my-system"):
        assert excluded not in printed, f"{excluded} must be filtered out"


def test_namespaces_list_uses_rossoctl_enabled_label_selector(sandbox) -> None:
    sandbox.set_kubectl(ns_out=_NS_OUTPUT)
    sandbox.run("namespaces", "list")
    calls = " ".join(sandbox.kubectl_calls())
    assert "rossoctl-enabled" in calls, (
        "namespaces list must select on the rossoctl-enabled label; "
        f"kubectl calls were: {sandbox.kubectl_calls()!r}"
    )


def test_bare_namespaces_defaults_to_list(sandbox) -> None:
    """`dg.sh namespaces` with no action == `namespaces list`."""
    sandbox.set_kubectl(ns_out=_NS_OUTPUT)
    r = sandbox.run("namespaces")
    assert r.returncode == 0, r.stderr
    printed = {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    assert "travel-advisor" in printed
    assert "kube-system" not in printed


def test_namespaces_unknown_action_is_bad_usage(sandbox) -> None:
    r = sandbox.run("namespaces", "delete")
    assert r.returncode != 0
    assert "usage" in (r.stdout + r.stderr).lower()


def test_namespaces_list_fails_loud_when_kubectl_get_errors(sandbox) -> None:
    """A failed `kubectl get` (unreachable/RBAC-denied API — `version --client`
    still succeeds) must be a LOUD, non-zero exit with a diagnostic, NOT an
    empty exit-1 indistinguishable from the legitimate 'zero user namespaces'
    case. Regression guard for the `2>/dev/null` swallow (finding on #181)."""
    sandbox.set_kubectl(get_fails=True)
    r = sandbox.run("namespaces", "list")
    assert r.returncode != 0, "a failed kubectl get must not exit 0"
    combined = (r.stdout + r.stderr).lower()
    # It must name the failure — not leave the operator with a bare exit code.
    assert "namespace" in combined, (
        f"error must name the failed listing; got stdout={r.stdout!r} "
        f"stderr={r.stderr!r}"
    )
    assert combined.strip(), "a failed kubectl get must not exit with EMPTY output"
    # kubectl's own diagnostic must be surfaced, not swallowed by 2>/dev/null.
    assert "connection to the server" in combined or "refused" in combined, (
        f"kubectl's own stderr must be surfaced; got stderr={r.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Shared helper: entity enumeration selector shape
# ---------------------------------------------------------------------------


def test_entity_enumeration_uses_component_selector(sandbox) -> None:
    """The shared entity enumeration (driven here through the still-stubbed
    `instrument` verb) uses the platform's own
    `app.kubernetes.io/component in (agent, mcp-tool)` selector, and NEVER reads
    the operator-reserved `rossoctl.io/type`."""
    sandbox.set_kubectl(entity_out="research-agent\npayment-agent\n")
    sandbox.run("namespace", "travel-advisor", "instrument")
    calls = " ".join(sandbox.kubectl_calls())
    assert "app.kubernetes.io/component" in calls, (
        f"entity enumeration must use app.kubernetes.io/component; calls: "
        f"{sandbox.kubectl_calls()!r}"
    )
    assert "agent" in calls and "mcp-tool" in calls, (
        "selector must cover both agent and mcp-tool components"
    )
    assert "rossoctl.io/type" not in calls, (
        "must NEVER read rossoctl.io/type (operator-reserved, VAP-protected)"
    )


def test_single_entity_select_uses_name_label(sandbox) -> None:
    """A named <entity> is selected by `app.kubernetes.io/name` (driven here
    through the still-stubbed `instrument` verb)."""
    sandbox.set_kubectl(entity_out="research-agent\n")
    sandbox.run("namespace", "travel-advisor", "instrument", "research-agent")
    calls = " ".join(sandbox.kubectl_calls())
    assert "app.kubernetes.io/name" in calls, (
        f"single-entity select must use app.kubernetes.io/name; calls: "
        f"{sandbox.kubectl_calls()!r}"
    )


def test_named_entity_not_found_is_loud_error(sandbox) -> None:
    """A named entity that resolves to nothing is a loud error, not a no-op
    (driven here through the still-stubbed `instrument` verb, which enumerates
    up front so a bad <entity> fails loud even in the stub)."""
    sandbox.set_kubectl(entity_out="")  # nothing matches the name
    r = sandbox.run("namespace", "travel-advisor", "instrument", "nope-agent")
    assert r.returncode != 0, "unresolved named entity must fail loud"
    combined = (r.stdout + r.stderr).lower()
    assert "nope-agent" in combined, "error must name the entity that was not found"


# ---------------------------------------------------------------------------
# Preflight primitives — fail loud
# ---------------------------------------------------------------------------


def test_missing_kubectl_fails_loud(sandbox) -> None:
    sandbox.drop("kubectl")
    r = sandbox.run("namespaces", "list")
    assert r.returncode != 0, "missing kubectl must fail, not silently no-op"
    assert "kubectl" in (r.stdout + r.stderr).lower()


def test_missing_container_tool_fails_loud_for_component_status(sandbox) -> None:
    """The container-tool preflight matches build-and-load.sh (docker|podman).

    A verb that needs the container tool must report its absence loudly. The
    skeleton's `component status` is the natural surface for the preflight; if
    a verb does not need it, it simply must not crash — but where the tool is
    required, its absence is reported, never swallowed."""
    sandbox.set_container_tool("")  # neither docker nor podman
    r = sandbox.run("component", "status")
    combined = (r.stdout + r.stderr).lower()
    # Either it fails loud naming a container tool, or (if status does not need
    # the tool) it must at minimum still run — but it must never claim success
    # while silently ignoring a missing tool it needed. We assert the loud path.
    if r.returncode != 0:
        assert "docker" in combined or "podman" in combined or "container tool" in combined
    else:
        # status may legitimately not require a container tool; accept success
        # only when it did not pretend to build anything.
        assert "build" not in combined


def test_missing_kubectl_reported_by_component_status(sandbox) -> None:
    sandbox.drop("kubectl")
    r = sandbox.run("component", "status")
    assert r.returncode != 0
    assert "kubectl" in (r.stdout + r.stderr).lower()


# ---------------------------------------------------------------------------
# --cortex-local-path is RETIRED (ADR-0033 vendored the kit into
# deploy/lineage-attach/): it is no longer a recognised global option.
# ---------------------------------------------------------------------------


def test_retired_cortex_local_path_flag_is_rejected(sandbox) -> None:
    """`--cortex-local-path` was retired when the lineage-attach kit was vendored
    (ADR-0033). It is now an unknown global option — a loud usage error, not a
    silently-ignored no-op — so a runbook still passing it fails visibly."""
    sandbox.set_kubectl(ns_out=_NS_OUTPUT)
    r = sandbox.run("--cortex-local-path", "/some/cortex", "namespaces", "list")
    assert r.returncode != 0, "the retired flag must not be silently accepted"
    combined = (r.stdout + r.stderr).lower()
    assert "unknown option" in combined and "--cortex-local-path" in combined, (
        f"the retired flag must fail loud as an unknown option; got:\n{combined}"
    )
