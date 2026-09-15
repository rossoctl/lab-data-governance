"""Behavioural tests for the ADR-0033 **proxy** lineage path (issue #243).

Step 4 of the one-trace epic (#239) adds two vendored, independently-runnable
scripts to ``deploy/lineage-attach/``:

* ``attach-lineage-proxy.sh`` — the **proxy injector**. A reviewed sibling of the
  envoy generator ``attach-lineage.sh`` that emits ONE of ``patch`` / ``cm`` /
  ``undo`` to attach an **auth-free, lineage-only** ``authbridge-proxy`` sidecar
  (only ``lineage-telemetry`` + the parsers — NO ``jwt-validation`` /
  ``token-exchange``, so it does not 401 the demo's unauthenticated MCP/A2A
  calls). The proxy captures the app's egress **transparently** — no reverse
  proxy, no app-port relocation, no ``HTTP_PROXY`` — via the include-only
  iptables path below. Its generated ConfigMap MUST supply ``namespace_file``
  (wire contract v1.7.0 §6: the ``lineage-telemetry`` producer refuses to start
  without a ``namespace`` / ``namespace_file``).

* ``init-iptables.sh`` — vendored **as-is** from cortex, plus a new **include-only
  ``OUTBOUND_PORTS_INCLUDE``** path in ``redirect`` mode: instead of the terminal
  catch-all REDIRECT, it REDIRECTs only the allowlisted dports (A2A ``8080`` +
  MCP ``8000`` by default) and RETURNs everything else DIRECT (fail-safe). The
  existing exclude/denylist path and ``enforce-redirect`` mode are untouched.

Like the rest of ``tests/deploy/``, these drive the REAL scripts as subprocesses
— no live cluster. The generator is pure stdout; the iptables script is run
under a **fake ``iptables`` command** (``IPTABLES_CMD`` → a shim that logs its
argv) with a seeded ``PROC_MODULES`` so backend detection is deterministic.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
KIT = REPO_ROOT / "deploy" / "lineage-attach"
PROXY_GEN = KIT / "attach-lineage-proxy.sh"
INIT_IPTABLES = KIT / "init-iptables.sh"

# The namespace path the wire contract v1.7.0 §6 names for `namespace_file`.
SA_NAMESPACE_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"


# ---------------------------------------------------------------------------
# attach-lineage-proxy.sh — subprocess helper
# ---------------------------------------------------------------------------


def run_gen(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the proxy generator with ``env`` (no args — every input is env)."""
    return subprocess.run(
        ["bash", str(PROXY_GEN)],
        env={"PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
    )


def emit(emit_kind: str, **env: str) -> str:
    """Run the generator for one EMIT kind and return stdout, asserting success."""
    proc = run_gen({"EMIT": emit_kind, **env})
    assert proc.returncode == 0, f"generator failed:\n{proc.stderr}"
    return proc.stdout


def _strip_comments(yaml_text: str) -> str:
    """Drop YAML comment lines so assertions test real directives, not the prose
    in explanatory ``#`` comments (which legitimately names things like
    ``lineage-telemetry`` / ``mtls`` / ``envoy-config`` while explaining what the
    config does or omits)."""
    return "\n".join(
        ln for ln in yaml_text.splitlines() if not ln.lstrip().startswith("#")
    )


# ---------------------------------------------------------------------------
# init-iptables.sh — fake-iptables harness
# ---------------------------------------------------------------------------


def _make_bin(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


# A STATEFUL fake iptables. It logs every invocation's argv (one line,
# space-joined) to $IPT_LOG, and models just enough rule state to satisfy the
# script's two uses of `-C`:
#
#   * the guard before `-I`/`-A` (`-C ... 2>/dev/null || -I ...`) expects "absent"
#     (rc 1) the FIRST time, so the install branch is taken;
#   * `require_jump` re-runs the SAME `-C` AFTER the install to verify the jump
#     landed, and treats a non-zero rc as a hard "interception NOT active" error.
#
# So a `-C` must return rc 0 iff the identical rule (same tokens, `-C` swapped for
# `-A`/`-I` and any position number dropped) was already installed. We record each
# installed rule's canonical form in $IPT_STATE and look it up on `-C`.
_FAKE_IPTABLES = r"""
printf '%s\n' "$*" >> "${IPT_LOG}"
: "${IPT_STATE:?}"
[ -f "${IPT_STATE}" ] || : > "${IPT_STATE}"

# Canonicalize an argv into a stable key so `-C`, `-A` and `-I` forms of the SAME
# rule collapse together: normalize the -I/-A/-C verb to a marker, and drop the
# optional rule-position number in `-I <chain> <num>` (which -A/-C never carry).
# The position is the token immediately AFTER the chain name, which is itself the
# token right after `-I` — so we skip a bare integer two tokens past `-I`.
canon() {
  out=""
  after_verb=0   # 1 = just saw the verb (next tok is the chain)
  after_chain=0  # 1 = just saw the chain of an -I (next bare int is the position)
  for tok in "$@"; do
    case "$tok" in
      -I) out="${out} @J"; after_verb=1; _isI=1; continue ;;
      -A|-C) out="${out} @J"; after_verb=1; _isI=0; continue ;;
    esac
    if [ "$after_verb" = "1" ]; then
      after_verb=0
      out="${out} ${tok}"                    # the chain name — keep
      [ "$_isI" = "1" ] && after_chain=1
      continue
    fi
    if [ "$after_chain" = "1" ]; then
      after_chain=0
      case "$tok" in [0-9]*) continue ;; esac  # drop the -I position number
    fi
    out="${out} ${tok}"
  done
  printf '%s' "$out"
}

verb=""
for a in "$@"; do case "$a" in -I|-A|-C) verb="$a"; break ;; esac; done
key="$(canon "$@")"

case "$verb" in
  -C)
    grep -qxF "$key" "${IPT_STATE}" && exit 0 || exit 1 ;;
  -I|-A)
    grep -qxF "$key" "${IPT_STATE}" || printf '%s\n' "$key" >> "${IPT_STATE}" ;;
esac
exit 0
"""


def run_iptables(tmp_path: Path, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run init-iptables.sh under a fake iptables; return (proc, rule-log)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    _make_bin(bindir, "iptables", _FAKE_IPTABLES)
    log = tmp_path / "ipt.log"
    log.write_text("")
    state = tmp_path / "ipt.state"
    state.write_text("")
    # Seed PROC_MODULES WITHOUT iptable_nat so detect_iptables_cmd picks the
    # non-legacy `iptables` (which we shadow with the fake). IPTABLES_CMD would
    # also force it, but PROC_MODULES keeps the detection path exercised.
    proc_modules = tmp_path / "proc_modules"
    proc_modules.write_text("nf_tables 1 0 - Live 0x0\n")
    full_env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "IPTABLES_CMD": "iptables",
        "IP6TABLES_CMD": "iptables",  # same fake covers the v6 command
        "PROC_MODULES": str(proc_modules),
        "IPT_LOG": str(log),
        "IPT_STATE": str(state),
        "POD_IP": "10.244.1.7",
        "POD_IPS": "10.244.1.7",
        **env,
    }
    result = subprocess.run(
        ["sh", str(INIT_IPTABLES)],
        env=full_env,
        capture_output=True,
        text=True,
    )
    return result, log.read_text()


def _proxy_output_rules(rule_log: str) -> list[str]:
    """The rule log lines that program the nat PROXY_OUTPUT chain."""
    return [
        ln
        for ln in rule_log.splitlines()
        if "PROXY_OUTPUT" in ln and "-A" in ln
    ]


def _proxy_inbound_rules(rule_log: str) -> list[str]:
    """The rule log lines that program the nat PROXY_INBOUND chain (redirect-mode
    inbound interception)."""
    return [
        ln
        for ln in rule_log.splitlines()
        if "PROXY_INBOUND" in ln and "-A" in ln
    ]


# ===========================================================================
# init-iptables.sh — include-only OUTBOUND_PORTS_INCLUDE path
# ===========================================================================


def test_init_iptables_ships_and_is_executable() -> None:
    assert INIT_IPTABLES.is_file(), "init-iptables.sh must be vendored into deploy/lineage-attach/"
    assert os.access(INIT_IPTABLES, os.X_OK), "init-iptables.sh must be executable"


def test_include_mode_redirects_only_allowlisted_dports(tmp_path) -> None:
    """OUTBOUND_PORTS_INCLUDE=8080,8000 in redirect mode: a per-dport REDIRECT to
    PROXY_PORT for EACH listed port, and NOTHING else redirected — the terminal
    catch-all `-p tcp -j REDIRECT` must be gone."""
    proc, log = run_iptables(
        tmp_path,
        {"MODE": "redirect", "OUTBOUND_PORTS_INCLUDE": "8080,8000", "PROXY_PORT": "8082"},
    )
    assert proc.returncode == 0, f"init-iptables.sh failed:\n{proc.stderr}"
    rules = _proxy_output_rules(log)
    joined = "\n".join(rules)
    for port in ("8080", "8000"):
        assert any(
            f"--dport {port}" in r and "REDIRECT" in r and "8082" in r for r in rules
        ), f"expected a per-dport REDIRECT of {port} to 8082; PROXY_OUTPUT rules:\n{joined}"
    # No terminal catch-all: a `-p tcp -j REDIRECT` with no --dport would send
    # every port to the proxy, which is exactly the denylist behavior the
    # allowlist inverts.
    catchall = [
        r for r in rules
        if "REDIRECT" in r and "--dport" not in r
    ]
    assert not catchall, f"include mode must not emit a catch-all REDIRECT; got:\n{catchall}"


def test_include_mode_ends_with_terminal_return(tmp_path) -> None:
    """The allowlist is fail-safe: after the per-dport REDIRECTs, a terminal
    RETURN lets every other (non-allowlisted) port pass DIRECT."""
    proc, log = run_iptables(
        tmp_path,
        {"MODE": "redirect", "OUTBOUND_PORTS_INCLUDE": "8080,8000", "PROXY_PORT": "8082"},
    )
    assert proc.returncode == 0, proc.stderr
    rules = _proxy_output_rules(log)
    # A terminal RETURN with no port/uid/mark qualifier — the blanket fall-through.
    blanket_return_idxs = [
        i
        for i, r in enumerate(rules)
        if r.strip().endswith("-j RETURN")
        and "--dport" not in r
        and "uid-owner" not in r
        and "mark" not in r
        and "127.0.0" not in r
    ]
    assert blanket_return_idxs, (
        f"include mode must end PROXY_OUTPUT with a blanket RETURN; rules:\n{chr(10).join(rules)}"
    )
    # ORDER MATTERS: the blanket RETURN must come AFTER the per-dport REDIRECTs, or
    # every packet RETURNs before reaching the allowlist and NOTHING is captured.
    redirect_idxs = [
        i for i, r in enumerate(rules) if "REDIRECT" in r and "--dport" in r
    ]
    assert redirect_idxs, "expected per-dport REDIRECT rules before the terminal RETURN"
    assert max(redirect_idxs) < min(blanket_return_idxs), (
        "the blanket RETURN must come AFTER all per-dport REDIRECTs (else nothing is captured); "
        f"rules:\n{chr(10).join(rules)}"
    )


def test_default_denylist_mode_still_has_catchall_redirect(tmp_path) -> None:
    """Regression guard: with OUTBOUND_PORTS_INCLUDE unset the chain is exactly
    today's — a terminal catch-all `-p tcp -j REDIRECT` and NO blanket RETURN."""
    proc, log = run_iptables(tmp_path, {"MODE": "redirect", "PROXY_PORT": "15123"})
    assert proc.returncode == 0, proc.stderr
    rules = _proxy_output_rules(log)
    assert any(
        "REDIRECT" in r and "--dport" not in r and "15123" in r for r in rules
    ), f"default mode must keep its catch-all REDIRECT; rules:\n{chr(10).join(rules)}"


def test_include_mode_does_not_intercept_inbound(tmp_path) -> None:
    """SHIP-BLOCKING guard: the include-only path is EGRESS-ONLY. redirect mode's
    default inbound interception REDIRECTs every inbound app port to
    INBOUND_PROXY_PORT (15124) — the ENVOY inbound listener, which the auth-free
    proxy-sidecar does NOT bind. So in include mode the PROXY_INBOUND chain must
    NOT be installed at all: leaving it would make the app unreachable inbound
    (every inbound request DNATed/REDIRECTed to a dead port)."""
    proc, log = run_iptables(
        tmp_path,
        {"MODE": "redirect", "OUTBOUND_PORTS_INCLUDE": "8080,8000", "PROXY_PORT": "8082"},
    )
    assert proc.returncode == 0, f"init-iptables.sh failed:\n{proc.stderr}"
    assert not _proxy_inbound_rules(log), (
        "include (egress-only) mode must NOT install the inbound PROXY_INBOUND chain; "
        f"got:\n{chr(10).join(_proxy_inbound_rules(log))}"
    )
    # And it must NOT DNAT ambient inbound to the (absent) inbound listener port
    # in the PROXY_OUTPUT chain (Rule 2). The default INBOUND_PROXY_PORT is 15124.
    assert not any("DNAT" in r and "15124" in r for r in _proxy_output_rules(log)), (
        "include mode must not DNAT ambient inbound to the absent 15124 listener"
    )


def test_default_mode_does_intercept_inbound(tmp_path) -> None:
    """Regression guard the other way: the default (envoy) redirect mode STILL
    installs PROXY_INBOUND redirecting to the inbound listener — only the
    egress-only include path suppresses it."""
    proc, log = run_iptables(tmp_path, {"MODE": "redirect", "PROXY_PORT": "15123"})
    assert proc.returncode == 0, proc.stderr
    inbound = _proxy_inbound_rules(log)
    assert inbound, "default redirect mode must still install the inbound PROXY_INBOUND chain"
    assert any("REDIRECT" in r and "15124" in r for r in inbound), (
        "default redirect mode's inbound catch-all redirects to the envoy inbound listener (15124)"
    )


def test_include_mode_does_not_require_pod_ip(tmp_path) -> None:
    """The egress-only allowlist path skips the only redirect-mode consumer of
    POD_IP (the ambient inbound DNAT), so it must not fail when POD_IP is unset."""
    env = {"MODE": "redirect", "OUTBOUND_PORTS_INCLUDE": "8080,8000", "PROXY_PORT": "8082"}
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_bin(bindir, "iptables", _FAKE_IPTABLES)
    log = tmp_path / "ipt.log"; log.write_text("")
    state = tmp_path / "ipt.state"; state.write_text("")
    proc_modules = tmp_path / "proc_modules"; proc_modules.write_text("nf_tables 1 0 - Live 0x0\n")
    # Deliberately DO NOT pass POD_IP / POD_IPS.
    proc = subprocess.run(
        ["sh", str(INIT_IPTABLES)],
        env={
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "IPTABLES_CMD": "iptables", "IP6TABLES_CMD": "iptables",
            "PROC_MODULES": str(proc_modules), "IPT_LOG": str(log), "IPT_STATE": str(state),
            **env,
        },
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"egress-only must not require POD_IP; stderr:\n{proc.stderr}"


@pytest.mark.parametrize("bad", ["8080 -j ACCEPT", "0080", "70000", "8080,", ",8080", "8080,,8000", "abc"])
def test_init_iptables_validates_include_in_script(tmp_path, bad: str) -> None:
    """Defense-in-depth: init-iptables.sh validates OUTBOUND_PORTS_INCLUDE itself,
    not only via the generator — the script is independently runnable and each
    token is interpolated straight into `--dport`."""
    proc, _ = run_iptables(tmp_path, {"MODE": "redirect", "OUTBOUND_PORTS_INCLUDE": bad})
    assert proc.returncode != 0, f"OUTBOUND_PORTS_INCLUDE={bad!r} must be refused in-script"


def test_include_and_exclude_together_is_refused(tmp_path) -> None:
    """The include allowlist and exclude denylist are opposite models; setting
    both is a config error, refused loudly."""
    proc, _ = run_iptables(
        tmp_path,
        {
            "MODE": "redirect",
            "OUTBOUND_PORTS_INCLUDE": "8080",
            "OUTBOUND_PORTS_EXCLUDE": "5432",
        },
    )
    assert proc.returncode != 0, "setting both INCLUDE and EXCLUDE must be refused"
    assert "INCLUDE" in proc.stderr and "EXCLUDE" in proc.stderr, (
        f"the refusal must name both knobs; stderr:\n{proc.stderr}"
    )


def test_enforce_redirect_mode_ignores_include_knob(tmp_path) -> None:
    """The include-only path is additive to REDIRECT mode only; enforce-redirect
    (the fail-closed egress guard) is unaffected and still installs AB_REDIRECT."""
    # A resolver so enforce-redirect does not refuse on zero nameservers.
    (tmp_path / "resolv.conf").write_text("nameserver 10.96.0.10\n")
    proc, log = run_iptables(
        tmp_path,
        {
            "MODE": "enforce-redirect",
            "OUTBOUND_PORTS_INCLUDE": "8080,8000",
            "TRANSPARENT_PORT": "8082",
            # enforce-redirect derives DNS exemptions from resolv.conf.
            "RESOLV_CONF": str(tmp_path / "resolv.conf"),
        },
    )
    assert proc.returncode == 0, f"enforce-redirect failed:\n{proc.stderr}"
    assert "AB_REDIRECT" in log, "enforce-redirect must still install its AB_REDIRECT chain"
    # It must NOT grow the redirect-mode PROXY_OUTPUT chain.
    assert not _proxy_output_rules(log), (
        "enforce-redirect must not touch the redirect-mode PROXY_OUTPUT chain"
    )


# ===========================================================================
# attach-lineage-proxy.sh — the proxy generator
# ===========================================================================


def test_proxy_gen_ships_and_is_executable() -> None:
    assert PROXY_GEN.is_file(), "attach-lineage-proxy.sh must be vendored into deploy/lineage-attach/"
    assert os.access(PROXY_GEN, os.X_OK), "attach-lineage-proxy.sh must be executable"


def test_gen_takes_no_positional_args() -> None:
    proc = run_gen({"EMIT": "cm", "NAME": "research-agent"})
    assert proc.returncode == 0
    proc2 = subprocess.run(
        ["bash", str(PROXY_GEN), "research-agent"],
        env={"PATH": os.environ["PATH"], "EMIT": "cm", "NAME": "research-agent"},
        capture_output=True,
        text=True,
    )
    assert proc2.returncode != 0, "positional args must be refused (every input is env)"


def test_cm_is_proxy_sidecar_mode() -> None:
    out = emit("cm", NAME="research-agent", NAMESPACE="travel-advisor")
    assert "mode: proxy-sidecar" in out, f"the CM must be proxy-sidecar mode; got:\n{out}"
    # NOT envoy — no envoy-only config keys.
    assert "mode: envoy-sidecar" not in out


def test_cm_supplies_namespace_file() -> None:
    """The ticket's headline constraint (wire contract v1.7.0 §6): the
    lineage-telemetry producer refuses to start without namespace/namespace_file.
    dg.sh sets namespace_file to the pod's projected SA namespace."""
    out = emit("cm", NAME="research-agent", NAMESPACE="travel-advisor")
    assert SA_NAMESPACE_PATH in out, (
        f"the CM's lineage-telemetry config must set namespace_file={SA_NAMESPACE_PATH}; got:\n{out}"
    )
    assert "namespace_file" in out
    # It must appear for BOTH directions (inbound + outbound pipelines).
    assert out.count("namespace_file") >= 2, (
        "namespace_file must be present in both inbound and outbound lineage-telemetry configs"
    )


def test_cm_has_parser_chain_and_lineage_plugin() -> None:
    out = emit("cm", NAME="research-agent")
    for plugin in ("a2a-parser", "mcp-parser", "inference-parser", "lineage-telemetry"):
        assert plugin in out, f"the CM must carry the {plugin} plugin; got:\n{out}"


def test_cm_is_auth_free() -> None:
    """Auth-free by construction: no jwt-validation / token-exchange / mtls, which
    would 401 the demo's unauthenticated MCP/A2A calls. Checked against real
    directives (comments explaining the omission are stripped)."""
    directives = _strip_comments(emit("cm", NAME="research-agent"))
    for forbidden in ("jwt-validation", "token-exchange", "mtls"):
        assert forbidden not in directives, (
            f"the auth-free proxy CM must not carry {forbidden}; directives:\n{directives}"
        )


def test_cm_wires_transparent_and_forward_listener() -> None:
    out = emit("cm", NAME="research-agent")
    assert "transparent_proxy_addr" in out, "the transparent outbound listener must be wired (:8082)"
    assert '":8082"' in out or ":8082" in out


def test_cm_disables_session_api() -> None:
    """The egress-only proxy installs NO inbound gate, so the unauthenticated
    session-events API (:9094, raw captured content) must be turned off."""
    import yaml

    cm = yaml.safe_load(emit("cm", NAME="research-agent"))
    inner = yaml.safe_load(cm["data"]["config.yaml"])
    assert inner.get("session", {}).get("enabled") is False, (
        "the auth-free proxy CM must disable the session store/API (session.enabled: false)"
    )


def test_cm_no_reverse_proxy_relocation() -> None:
    """ADR-0033 rejected the relocation approach: no reverse proxy, no backend
    relocation of the app."""
    out = emit("cm", NAME="research-agent")
    assert "reverse_proxy_backend" not in out, (
        "the ADR-0033 proxy path is transparent egress capture, NOT reverse-proxy relocation"
    )


def _load_patch(**env: str) -> dict:
    """Render EMIT=patch and parse it as a k8s object."""
    import yaml

    return yaml.safe_load(
        emit("patch", SIDECAR_IMAGE="ghcr.io/rossoctl/cortex/authbridge:lineage-test", **env)
    )


def test_patch_adds_authbridge_proxy_container_and_proxy_init() -> None:
    out = emit(
        "patch",
        NAME="research-agent",
        NAMESPACE="travel-advisor",
        SIDECAR_IMAGE="ghcr.io/rossoctl/cortex/authbridge:lineage-test",
    )
    assert "authbridge-proxy" in out, "the patch must add the authbridge-proxy container"
    assert "proxy-init" in out, "the patch must add the proxy-init initContainer"
    assert "authbridge-runtime" in out, "the patch must add the runtime-config volume"
    # No envoy-config volume — this is the proxy path, not the envoy path.
    # Checked against real directives (a comment explains the omission).
    assert "envoy-config" not in _strip_comments(out), (
        "the proxy patch must not reference the envoy-config volume"
    )


def test_authbridge_proxy_is_a_native_sidecar() -> None:
    """The proxy is a NATIVE sidecar (initContainer + restartPolicy: Always) so the
    kubelet holds the app until the proxy's startupProbe passes — otherwise
    proxy-init redirects egress to :8082 before the proxy binds and the demo
    agents (retry-less peer resolution at boot) race it to 0 peers."""
    spec = _load_patch(NAME="research-agent")["spec"]["template"]["spec"]
    inits = {c["name"]: c for c in spec.get("initContainers", [])}
    assert "proxy-init" in inits and "authbridge-proxy" in inits, (
        "both proxy-init and authbridge-proxy must be initContainers"
    )
    proxy = inits["authbridge-proxy"]
    assert proxy.get("restartPolicy") == "Always", (
        "authbridge-proxy must be a native sidecar (restartPolicy: Always)"
    )
    assert "startupProbe" in proxy, "a native sidecar must carry a startupProbe (gates the app)"
    # No readinessProbe: it would fold this observational capture sidecar into the
    # pod's overall readiness and drop the pod from Service endpoints if it flapped.
    assert "readinessProbe" not in proxy, (
        "a lineage-only capture sidecar must not carry a readinessProbe (couples app availability)"
    )
    # It must NOT be a regular container (that would not gate the app's start).
    reg = {c["name"] for c in spec.get("containers", [])}
    assert "authbridge-proxy" not in reg, "authbridge-proxy must not be a regular container"
    # proxy-init must precede authbridge-proxy so iptables is programmed first.
    order = [c["name"] for c in spec["initContainers"]]
    assert order.index("proxy-init") < order.index("authbridge-proxy")


def test_patch_proxy_init_uses_include_allowlist_default() -> None:
    """proxy-init runs redirect mode with the include-only allowlist defaulting to
    A2A 8080 + MCP 8000, REDIRECTing to the transparent listener :8082."""
    out = emit(
        "patch",
        NAME="research-agent",
        SIDECAR_IMAGE="ghcr.io/rossoctl/cortex/authbridge:lineage-test",
    )
    assert "OUTBOUND_PORTS_INCLUDE" in out, "proxy-init must set OUTBOUND_PORTS_INCLUDE"
    assert "8080,8000" in out or ("8080" in out and "8000" in out), (
        f"the allowlist must default to A2A 8080 + MCP 8000; got:\n{out}"
    )


def test_patch_app_container_untouched_without_app_container() -> None:
    """Without APP_CONTAINER the app is neither relocated nor env-patched."""
    out = emit(
        "patch",
        NAME="research-agent",
        SIDECAR_IMAGE="ghcr.io/rossoctl/cortex/authbridge:lineage-test",
    )
    assert "LINEAGE_PROPAGATE" not in out, (
        "without APP_CONTAINER the patch must not set the propagation switch"
    )


def test_patch_app_container_gets_propagation_switch() -> None:
    out = emit(
        "patch",
        NAME="research-agent",
        APP_CONTAINER="agent",
        APP_IMAGE="docker.io/library/research-agent-otel:latest",
        SIDECAR_IMAGE="ghcr.io/rossoctl/cortex/authbridge:lineage-test",
    )
    assert "LINEAGE_PROPAGATE" in out, "APP_CONTAINER must switch propagation on"
    assert "research-agent-otel:latest" in out, "APP_IMAGE must land on the app container"


def test_undo_is_the_inverse_of_patch() -> None:
    import json

    out = emit("undo", NAME="research-agent", NAMESPACE="travel-advisor")
    d = json.loads(out)
    spec = d["spec"]["template"]["spec"]
    # Both proxy-init and authbridge-proxy are native initContainers, so both
    # un-merge from initContainers via `$patch: delete`.
    init_deletes = {c["name"]: c for c in spec["initContainers"]}
    assert init_deletes.keys() == {"proxy-init", "authbridge-proxy"}, (
        f"undo must delete both native initContainers; got {list(init_deletes)}"
    )
    for name, c in init_deletes.items():
        assert c.get("$patch") == "delete", f"{name} must be a $patch:delete"
    # The runtime volume too.
    vol_deletes = {v["name"]: v for v in spec["volumes"]}
    assert vol_deletes["authbridge-runtime"].get("$patch") == "delete"
    # Without APP_CONTAINER, no containers list is touched.
    assert "containers" not in spec, "undo must not touch containers when no app container was attached"


def test_no_emit_drops_the_lineage_plugin() -> None:
    out = emit("cm", NAME="research-agent", NO_EMIT="1")
    directives = _strip_comments(out)
    # No `- name: lineage-telemetry` plugin entry (the term still appears in the
    # RequiresAny comment, which is stripped here).
    assert "lineage-telemetry" not in directives, "NO_EMIT=1 must drop the lineage-telemetry entry"
    assert SA_NAMESPACE_PATH not in out, "NO_EMIT=1 must not emit namespace_file either"
    # The parsers stay — a pure-proxy A/B baseline.
    assert "a2a-parser" in directives


# ---- input validation (mirrors attach-lineage.sh's refusals) -------------


def test_missing_name_is_refused() -> None:
    proc = run_gen({"EMIT": "cm"})
    assert proc.returncode != 0, "NAME is required"


@pytest.mark.parametrize("bad_name", ["Has-Upper", "under_score", "-leading", "trailing-", "a/b"])
def test_bad_name_is_refused(bad_name: str) -> None:
    proc = run_gen({"EMIT": "cm", "NAME": bad_name})
    assert proc.returncode != 0, f"NAME={bad_name!r} is not an RFC 1123 subdomain — must be refused"


def test_bad_namespace_is_refused() -> None:
    proc = run_gen({"EMIT": "cm", "NAME": "research-agent", "NAMESPACE": "Bad_NS"})
    assert proc.returncode != 0


def test_bad_emit_is_refused() -> None:
    proc = run_gen({"EMIT": "bogus", "NAME": "research-agent"})
    assert proc.returncode != 0


def test_published_default_sidecar_image_is_refused_on_patch() -> None:
    """The published :latest proxy image predates lineage-telemetry; a patch that
    pins it would crashloop. Refuse it (mirrors attach-lineage.sh)."""
    proc = run_gen({"EMIT": "patch", "NAME": "research-agent"})
    assert proc.returncode != 0, (
        "EMIT=patch with the crashloop-y published default SIDECAR_IMAGE must be refused"
    )


def test_app_image_needs_app_container() -> None:
    proc = run_gen(
        {
            "EMIT": "patch",
            "NAME": "research-agent",
            "APP_IMAGE": "docker.io/library/x-otel:latest",
            "SIDECAR_IMAGE": "ghcr.io/rossoctl/cortex/authbridge:lineage-test",
        }
    )
    assert proc.returncode != 0, "APP_IMAGE without APP_CONTAINER must be refused"


def test_restore_image_only_under_undo() -> None:
    proc = run_gen(
        {
            "EMIT": "patch",
            "NAME": "research-agent",
            "APP_CONTAINER": "agent",
            "RESTORE_IMAGE": "docker.io/library/x:latest",
            "SIDECAR_IMAGE": "ghcr.io/rossoctl/cortex/authbridge:lineage-test",
        }
    )
    assert proc.returncode != 0, "RESTORE_IMAGE is an EMIT=undo-only knob"


@pytest.mark.parametrize("bad_ports", ["0080", "70000", "abc", "8080,", "8080,,8000"])
def test_bad_include_ports_refused(bad_ports: str) -> None:
    proc = run_gen(
        {
            "EMIT": "patch",
            "NAME": "research-agent",
            "OUTBOUND_PORTS_INCLUDE": bad_ports,
            "SIDECAR_IMAGE": "ghcr.io/rossoctl/cortex/authbridge:lineage-test",
        }
    )
    assert proc.returncode != 0, f"OUTBOUND_PORTS_INCLUDE={bad_ports!r} must be refused"


def test_yaml_unsafe_value_refused() -> None:
    proc = run_gen(
        {
            "EMIT": "cm",
            "NAME": "research-agent",
            "SELF_ID": 'has"quote',
        }
    )
    assert proc.returncode != 0, "a value carrying a double-quote must be refused (yaml_safe)"
