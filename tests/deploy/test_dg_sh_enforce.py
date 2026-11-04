"""dg.sh's ``instrument --enforce|--observe`` against a fake kubectl (#342).

The sandbox is #334's (``test_dg_sh_instrument``); a wrapper kubectl in front of
its fake adds what the gate needs: a catalog whose ``sentry-gate`` takes the
keys instrument writes, the verdict service's Secret, and a ``/v1/pipeline``
read from the runtime ConfigMap the pod was admitted with (the original one, the
written one once the workload was rolled, or ``pod-cm-<entity>.json`` to model a
pod admitted before the namespace source changed).
"""

from __future__ import annotations

import base64
import json
import stat
import sys

from pathlib import Path

import pytest
import yaml

from tests.deploy.test_dg_sh_instrument import _TRUSTED_AGENT, REPO_ROOT, sandbox  # noqa: F401

SECRET = "0123abcd" * 8

_WRAPPER = r"""#!/usr/bin/env bash
if [[ "$*" == *"exec"* && "$*" == *"/v1/plugins"* ]]; then
  [[ -n "${KUBECTL_LOG:-}" ]] && printf '%s\n' "$*" >> "$KUBECTL_LOG"
  gate=',{"name":"sentry-gate","fields":[{"name":"verdict_url"},{"name":"pass_hosts"},{"name":"pass_single_label"},{"name":"wait_ms"},{"name":"deny_on"},{"name":"verdict_secret"},{"name":"verdict_secret_file"},{"name":"fallback"},{"name":"fail_mode"},{"name":"sources"}]}'
  case "${GATE_CATALOG:-full}" in
    none) gate="" ;;
    no-secret) gate=',{"name":"sentry-gate","fields":[{"name":"verdict_url"},{"name":"pass_hosts"},{"name":"wait_ms"},{"name":"fallback"},{"name":"fail_mode"},{"name":"sources"}]}' ;;
    no-fallback) gate=',{"name":"sentry-gate","fields":[{"name":"verdict_url"},{"name":"pass_hosts"},{"name":"wait_ms"},{"name":"verdict_secret"},{"name":"fail_mode"},{"name":"sources"}]}' ;;
    no-fail-mode) gate=',{"name":"sentry-gate","fields":[{"name":"verdict_url"},{"name":"pass_hosts"},{"name":"wait_ms"},{"name":"verdict_secret"},{"name":"fallback"},{"name":"sources"}]}' ;;
    no-sources) gate=',{"name":"sentry-gate","fields":[{"name":"verdict_url"},{"name":"pass_hosts"},{"name":"wait_ms"},{"name":"verdict_secret"},{"name":"fallback"},{"name":"fail_mode"}]}' ;;
  esac
  printf '%s' '{"plugins":[{"name":"a2a-parser"},{"name":"mcp-parser"},{"name":"inference-parser"},{"name":"lineage-telemetry"}'"$gate"']}'
  exit 0
fi
if [[ "$*" == *"exec"* && "$*" == *"/v1/pipeline"* ]]; then
  [[ -n "${KUBECTL_LOG:-}" ]] && printf '%s\n' "$*" >> "$KUBECTL_LOG"
  ent=""
  for a in "$@"; do
    case "$a" in *-abc123) ent="${a%-abc123}" ;; *-rolled) ent="${a%-rolled}" ;; esac
  done
  cm="$FIXDIR/cm-authbridge-runtime-config.json"
  [[ -f "$FIXDIR/pod-cm-$ent.json" ]] && cm="$FIXDIR/pod-cm-$ent.json"
  if [[ -f "$FIXDIR/restarted-$ent" ]]; then
    cm="$FIXDIR/cm-authbridge-runtime-config.json"
    [[ -f "$FIXDIR/cm-authbridge-runtime-config-wired.json" ]] && cm="$FIXDIR/cm-authbridge-runtime-config-wired.json"
  fi
  exec "__PY__" - "$cm" <<'PY'
import json, sys, yaml
config = yaml.safe_load(json.load(open(sys.argv[1]))["data"]["config.yaml"])
out = {}
for direction in ("inbound", "outbound"):
    out[direction] = []
    for p in config["pipeline"][direction]["plugins"]:
        entry = {"name": p["name"]}
        if p.get("config"):
            entry["config"] = {k: ("[REDACTED]" if k.endswith("secret") else v) for k, v in p["config"].items()}
        if p.get("on_error"):
            entry["onError"] = p["on_error"]
        out[direction].append(entry)
print(json.dumps(out))
PY
fi
if [[ "$*" == *"get secret dg-verdict-secret"* ]]; then
  printf '%s\n' "$*" >> "$KUBECTL_LOG"
  printf '%s' "${GATE_SECRET_B64-__SECRET_B64__}"; exit 0
fi
exec "$(dirname "$0")/kubectl-base" "$@"
"""

# Two workloads already on the shim and activated, so only the gate decides a roll.
RUNNING = {"sidecar": "proxy", "lineage": True, "trusted_type": "agent", "image": "travel-advisor-otel:latest",
           "activated": True}


@pytest.fixture()
def gated(sandbox):  # noqa: F811
    kubectl = sandbox.bindir / "kubectl"
    kubectl.rename(sandbox.bindir / "kubectl-base")
    body = _WRAPPER.replace("__PY__", sys.executable).replace(
        "__SECRET_B64__", base64.b64encode(SECRET.encode()).decode())
    kubectl.write_text(body)
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    sandbox.set_namespace({"travel-advisor": _TRUSTED_AGENT, "demo-client": {"sidecar": None}})
    return sandbox


def written(box) -> dict:
    return yaml.safe_load(json.loads((box.fixdir / "cm-authbridge-runtime-config-wired.json").read_text())["data"]["config.yaml"])


def mutations(box) -> list[str]:
    return [c for c in box.kubectl_calls() if c.split(" ")[0] in ("replace", "apply", "patch", "create")
            or "rollout restart" in c]


def restarts(box) -> list[str]:
    return [c for c in box.kubectl_calls() if "rollout restart" in c]


def plugin(config: dict, direction: str, name: str) -> dict:
    return next(p for p in config["pipeline"][direction]["plugins"] if p["name"] == name)


def _adopt_written(box) -> None:
    """The written source becomes the namespace's; the pods were admitted with it."""
    wired = box.fixdir / "cm-authbridge-runtime-config-wired.json"
    (box.fixdir / "cm-authbridge-runtime-config.json").write_text(wired.read_text())
    wired.unlink()
    (box.fixdir / "appended-authbridge-runtime-config").unlink()
    for f in box.fixdir.glob("restarted-*"):
        f.unlink()
    box.log.unlink()


# --- what --enforce and --observe write ---------------------------------------------------


def test_enforce_writes_the_gate_into_the_namespace_source(gated) -> None:
    """--enforce writes sentry-gate last on outbound, after lineage (capture_io on, export_delay_ms 10), with URL, secret, pass hosts, and rolls."""
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--pass-host", "host.containers.internal")
    assert result.returncode == 0, result.stderr
    config = written(gated)
    outbound = [p["name"] for p in config["pipeline"]["outbound"]["plugins"]]
    assert outbound[-2:] == ["lineage-telemetry", "sentry-gate"]
    for direction in ("inbound", "outbound"):
        lineage = plugin(config, direction, "lineage-telemetry")["config"]
        assert lineage["capture_io"] is True and lineage["export_delay_ms"] == 10
        assert lineage["namespace_file"].endswith("/namespace") and "self_id" not in lineage
    assert "sentry-gate" not in [p["name"] for p in config["pipeline"]["inbound"]["plugins"]]
    gate = plugin(config, "outbound", "sentry-gate")
    assert "on_error" not in gate
    assert gate["config"] == {"verdict_url": "http://data-governance-verdict.data-governance.svc:8080/enforce/v1",
                              "verdict_secret": SECRET, "pass_hosts": ["host.containers.internal"]}
    calls = gated.kubectl_calls()
    # Nothing is written to a per-workload render; the workload is rolled and verified.
    assert not any(c.startswith("apply -f -") for c in calls)
    assert any("rollout restart deployment/travel-advisor" in c for c in calls)
    assert SECRET not in result.stderr and SECRET not in result.stdout
    assert not any(SECRET in c for c in calls)


def test_no_pass_host_writes_no_pass_list(gated) -> None:
    """Without --pass-host on an ungated namespace the gate entry carries no pass_hosts key."""
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode == 0, result.stderr
    assert "pass_hosts" not in plugin(written(gated), "outbound", "sentry-gate")["config"]


def test_an_existing_gate_before_lineage_is_moved_after_it(gated) -> None:
    """A gate entry already in the source, before lineage, is replaced by one after lineage."""
    cm = json.loads((gated.fixdir / "cm-authbridge-runtime-config.json").read_text())
    cm["data"]["config.yaml"] = cm["data"]["config.yaml"].replace(
        "    - name: token-exchange\n", "    - name: token-exchange\n    - name: sentry-gate\n      config:\n        wait_ms: 100\n")
    (gated.fixdir / "cm-authbridge-runtime-config.json").write_text(json.dumps(cm))
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode == 0, result.stderr
    names = [p["name"] for p in written(gated)["pipeline"]["outbound"]["plugins"]]
    assert names.count("sentry-gate") == 1 and names[-1] == "sentry-gate"


def test_observe_writes_on_error_observe_and_enforce_switches_it_back(gated) -> None:
    """--observe writes the framework's on_error: observe on the gate; --enforce removes it and rolls."""
    assert gated.run("namespace", "travel-advisor", "instrument", "--observe").returncode == 0
    gate = plugin(written(gated), "outbound", "sentry-gate")
    assert gate["on_error"] == "observe" and gate["config"]["verdict_secret"] == SECRET
    _adopt_written(gated)
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode == 0, result.stderr
    assert "on_error" not in plugin(written(gated), "outbound", "sentry-gate")
    assert any("rollout restart deployment/travel-advisor" in c for c in gated.kubectl_calls())


# --- convergence: write once, roll only what is not on the chain ---------------------------


def test_a_second_enforce_neither_writes_nor_rolls(gated) -> None:
    """A re-run on a namespace whose source and pods already carry the gate writes nothing and rolls nothing."""
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--pass-host", "a.example").returncode == 0
    _adopt_written(gated)
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--pass-host", "a.example")
    assert result.returncode == 0, result.stderr
    assert not any(c.startswith(("replace", "apply")) or "rollout restart" in c for c in gated.kubectl_calls())


def test_a_workload_not_on_the_gated_pipeline_is_rolled(gated) -> None:
    """With the source already gated, only the workload whose live pipeline lacks the gate is rolled; nothing is written."""
    gated.set_namespace({"travel-advisor": RUNNING, "booking-agent": RUNNING})
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce").returncode == 0
    _adopt_written(gated)
    # booking-agent's pod was admitted before the gate: it still runs the ungated pipeline.
    ungated = json.loads((gated.fixdir / "cm-authbridge-runtime-config.json").read_text())
    ungated["data"]["config.yaml"] = ungated["data"]["config.yaml"].split("    - name: sentry-gate\n")[0]
    (gated.fixdir / "pod-cm-booking-agent.json").write_text(json.dumps(ungated))
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode == 0, result.stderr
    assert not any(c.startswith(("replace", "apply")) for c in gated.kubectl_calls())
    rolled = restarts(gated)
    assert any("deployment/booking-agent" in c for c in rolled), rolled
    assert not any("deployment/travel-advisor" in c for c in rolled), rolled


def test_a_re_run_without_pass_host_keeps_the_namespace_pass_list(gated) -> None:
    """--enforce/--observe without --pass-host keeps the gate's pass list; --pass-host replaces it."""
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce",
                     "--pass-host", "a.example", "--pass-host", "*.b.example").returncode == 0
    _adopt_written(gated)
    result = gated.run("namespace", "travel-advisor", "instrument", "--observe")  # a mode change rewrites the source
    assert result.returncode == 0, result.stderr
    assert "pass list kept from the namespace's gate: a.example *.b.example" in result.stderr
    assert plugin(written(gated), "outbound", "sentry-gate")["config"]["pass_hosts"] == ["a.example", "*.b.example"]
    _adopt_written(gated)
    result = gated.run("namespace", "travel-advisor", "instrument", "--observe", "--pass-host", "c.example")
    assert result.returncode == 0, result.stderr
    assert plugin(written(gated), "outbound", "sentry-gate")["config"]["pass_hosts"] == ["c.example"]


# --- the fallback: --fallback deny|allow ------------------------------------------------------


def test_fallback_allow_needs_allow_open_default(gated) -> None:
    """D1: --fallback allow without --allow-open-default refuses before any write and says why; with it,
    fallback: allow lands in the gate's block; without --fallback the block has no fallback key."""
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--fallback", "allow")
    assert result.returncode != 0
    assert "unjudged while the verdict service is slow or down" in result.stderr
    assert "--allow-open-default" in result.stderr and "Mutating nothing" in result.stderr
    assert mutations(gated) == []
    assert not (gated.fixdir / "cm-authbridge-runtime-config-wired.json").exists()
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--fallback", "allow",
                       "--allow-open-default")
    assert result.returncode == 0, result.stderr
    assert plugin(written(gated), "outbound", "sentry-gate")["config"]["fallback"] == "allow"


def test_no_fallback_flag_writes_no_fallback_key(gated) -> None:
    """Without --fallback on an ungated namespace the gate's block has no fallback key: the gate's default, deny."""
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--allow-open-default")
    assert result.returncode == 0, result.stderr
    assert "fallback" not in plugin(written(gated), "outbound", "sentry-gate")["config"]
    assert "fallback kept" not in result.stderr


def test_fallback_deny_is_written(gated) -> None:
    """--fallback deny needs no other flag and writes fallback: deny into the gate's block."""
    result = gated.run("namespace", "travel-advisor", "instrument", "--observe", "--fallback", "deny")
    assert result.returncode == 0, result.stderr
    gate = plugin(written(gated), "outbound", "sentry-gate")
    assert gate["config"]["fallback"] == "deny" and gate["on_error"] == "observe"


def test_a_re_run_without_fallback_keeps_the_namespace_fallback(gated) -> None:
    """A re-run without --fallback keeps the gate's fallback and says so; status prints it; --fallback replaces it."""
    gated.set_namespace({"travel-advisor": RUNNING})
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--fallback", "allow",
                     "--allow-open-default").returncode == 0
    _adopt_written(gated)
    status = gated.run("namespace", "travel-advisor", "status")
    assert "sentry-gate mode=enforce pass_hosts=(none) wait_ms=2000 fallback=allow" in status.stdout, status.stdout
    assert "live=yes" in status.stdout, status.stdout
    result = gated.run("namespace", "travel-advisor", "instrument", "--observe")  # a mode change rewrites the source
    assert result.returncode == 0, result.stderr
    assert "fallback kept from the namespace's gate: allow (--fallback replaces it)" in result.stderr
    assert plugin(written(gated), "outbound", "sentry-gate")["config"]["fallback"] == "allow"
    _adopt_written(gated)
    result = gated.run("namespace", "travel-advisor", "instrument", "--observe", "--fallback", "deny")
    assert result.returncode == 0, result.stderr
    assert plugin(written(gated), "outbound", "sentry-gate")["config"]["fallback"] == "deny"


def test_a_workload_on_another_fallback_is_rolled(gated) -> None:
    """With the source at fallback: deny, a pod still running fallback: allow is not on the gated pipeline: it is rolled."""
    gated.set_namespace({"travel-advisor": RUNNING})
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--fallback", "deny").returncode == 0
    _adopt_written(gated)
    stale = json.loads((gated.fixdir / "cm-authbridge-runtime-config.json").read_text())
    stale["data"]["config.yaml"] = stale["data"]["config.yaml"].replace("fallback: deny", "fallback: allow")
    (gated.fixdir / "pod-cm-travel-advisor.json").write_text(json.dumps(stale))
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode == 0, result.stderr
    assert not any(c.startswith(("replace", "apply")) for c in gated.kubectl_calls())
    assert any("deployment/travel-advisor" in c for c in restarts(gated))


# --- the wait: --wait-ms <n> ----------------------------------------------------------------


def test_wait_ms_is_written_before_the_fallback(gated) -> None:
    """--wait-ms writes wait_ms into the gate's block, as a number, before fallback."""
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--wait-ms", "500", "--fallback", "deny")
    assert result.returncode == 0, result.stderr
    config = plugin(written(gated), "outbound", "sentry-gate")["config"]
    assert config["wait_ms"] == 500
    assert list(config) == ["verdict_url", "verdict_secret", "wait_ms", "fallback"]


def test_no_wait_ms_flag_writes_no_wait_ms_key(gated) -> None:
    """Without --wait-ms on an ungated namespace the gate's block has no wait_ms key: the gate's default, 2000."""
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode == 0, result.stderr
    assert "wait_ms" not in plugin(written(gated), "outbound", "sentry-gate")["config"]
    assert "wait_ms kept" not in result.stderr


def test_a_re_run_without_wait_ms_keeps_the_namespace_wait(gated) -> None:
    """A re-run without --wait-ms keeps the gate's wait_ms and says so; status prints it; --wait-ms replaces it."""
    gated.set_namespace({"travel-advisor": RUNNING})
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--wait-ms", "500").returncode == 0
    _adopt_written(gated)
    status = gated.run("namespace", "travel-advisor", "status")
    assert "sentry-gate mode=enforce pass_hosts=(none) wait_ms=500 fallback=deny" in status.stdout, status.stdout
    assert "live=yes" in status.stdout, status.stdout
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode == 0, result.stderr
    assert "wait_ms kept from the namespace's gate: 500 (--wait-ms replaces it)" in result.stderr
    assert not any(c.startswith(("replace", "apply")) or "rollout restart" in c for c in gated.kubectl_calls())
    result = gated.run("namespace", "travel-advisor", "instrument", "--observe")  # a mode change rewrites the source
    assert result.returncode == 0, result.stderr
    assert plugin(written(gated), "outbound", "sentry-gate")["config"]["wait_ms"] == 500
    _adopt_written(gated)
    result = gated.run("namespace", "travel-advisor", "instrument", "--observe", "--wait-ms", "2000")
    assert result.returncode == 0, result.stderr
    assert plugin(written(gated), "outbound", "sentry-gate")["config"]["wait_ms"] == 2000


def test_a_workload_on_another_wait_is_rolled(gated) -> None:
    """With the source at wait_ms: 500, a pod still running wait_ms: 2000 is not on the gated pipeline: it is rolled."""
    gated.set_namespace({"travel-advisor": RUNNING})
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--wait-ms", "500").returncode == 0
    _adopt_written(gated)
    stale = json.loads((gated.fixdir / "cm-authbridge-runtime-config.json").read_text())
    stale["data"]["config.yaml"] = stale["data"]["config.yaml"].replace("wait_ms: 500", "wait_ms: 2000")
    (gated.fixdir / "pod-cm-travel-advisor.json").write_text(json.dumps(stale))
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode == 0, result.stderr
    assert not any(c.startswith(("replace", "apply")) for c in gated.kubectl_calls())
    assert any("deployment/travel-advisor" in c for c in restarts(gated))


# --- refusals: nothing written ----------------------------------------------------------------


def test_a_plain_instrument_on_a_gated_namespace_refuses(gated) -> None:
    """Plain instrument (namespace or entity) on a gated source refuses, names the mode to re-run with, and mutates nothing."""
    assert gated.run("namespace", "travel-advisor", "instrument", "--observe", "--pass-host", "h.example").returncode == 0
    _adopt_written(gated)
    for args in (("instrument",), ("instrument", "travel-advisor")):
        result = gated.run("namespace", "travel-advisor", *args)
        assert result.returncode != 0, args
        assert "carries the Sentry gate (observe)" in result.stderr and "instrument --observe" in result.stderr
        assert mutations(gated) == []


@pytest.mark.parametrize("catalog", ["none", "no-secret", "no-fallback", "no-fail-mode", "no-sources"])
def test_an_image_whose_gate_cannot_take_the_config_refuses_before_any_write(gated, catalog) -> None:
    """D7 (no-fail-mode), D13 (no-sources): an image with no sentry-gate, or one lacking a key instrument writes
    (verdict_secret, fallback, fail_mode, sources), is refused before any write."""
    gated.set_flag(GATE_CATALOG=catalog)
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode != 0
    assert "verdict_url, verdict_secret, pass_hosts, fallback, fail_mode, sources" in result.stderr and "mutating nothing" in result.stderr
    assert mutations(gated) == []


def test_an_empty_secret_refuses_before_any_write(gated) -> None:
    """An empty verdict Secret refuses the run before anything is written."""
    gated.set_flag(GATE_SECRET_B64="")
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode != 0 and "is empty" in result.stderr
    assert mutations(gated) == []


def test_the_secret_is_stripped_as_the_service_strips_it(gated) -> None:
    """A Secret whose value ends in a newline is written without it, as the verdict service reads it; one that is only
    whitespace is empty."""
    gated.set_flag(GATE_SECRET_B64=base64.b64encode((SECRET + "\n").encode()).decode())
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce").returncode == 0
    assert plugin(written(gated), "outbound", "sentry-gate")["config"]["verdict_secret"] == SECRET
    gated.set_flag(GATE_SECRET_B64=base64.b64encode(b" \n").decode())
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--pass-host", "other.example")
    assert result.returncode != 0 and "is empty" in result.stderr


@pytest.mark.parametrize("value", ["123", "no", "1e3", "true", "0x1f", "2026-01-01", "null", "~", "-.inf", "a: b", "plain.example"])
def test_a_scalar_loads_back_as_the_string_it_was(value) -> None:
    """scalar() quotes a string YAML would read as another type, so a source named 123 or no stays a string key."""
    assert yaml.safe_load(f"k: {gate_config.scalar(value)}") == {"k": value}


def test_the_gate_is_a_namespace_setting(gated) -> None:
    """--enforce with an <entity> is refused: the gate is set per namespace."""
    result = gated.run("namespace", "travel-advisor", "instrument", "travel-advisor", "--enforce")
    assert result.returncode != 0 and "namespace setting" in result.stderr
    assert mutations(gated) == []


@pytest.mark.parametrize(("args", "message"), [
    (("--pass-host", "x.example"), "apply to --enforce and --observe only"),
    (("--enforce", "--pass-host", ""), "needs a host glob"),
    (("--enforce", "--observe"), "give one of --enforce, --observe"),
    (("--enforce", "--pass-host", "["), "a glob the gate refuses"),
    (("--enforce", "--pass-host", " "), "a glob the gate refuses: an empty glob"),
    (("--enforce", "--pass-host", "x\\"), "a glob the gate refuses: a trailing"),
    (("--enforce", "--fallback", "open"), "--fallback takes deny or allow"),
    (("--enforce", "--fallback", "Allow"), "--fallback takes deny or allow"),
    (("--enforce", "--fallback"), "--fallback takes deny or allow"),
    (("--fallback", "deny"), "apply to --enforce and --observe only"),
    (("--enforce", "--wait-ms", "0"), "--wait-ms takes a whole number of milliseconds, 1..3000"),
    (("--enforce", "--wait-ms", "3001"), "--wait-ms takes a whole number of milliseconds, 1..3000"),
    (("--enforce", "--wait-ms", "abc"), "--wait-ms takes a whole number of milliseconds, 1..3000"),
    (("--enforce", "--wait-ms"), "--wait-ms takes a whole number of milliseconds, 1..3000"),
    (("--wait-ms", "500"), "apply to --enforce and --observe only"),
    (("--allow-open-default",), "apply to --enforce and --observe only"),
])
def test_gate_flags_are_checked(gated, args, message) -> None:
    """--pass-host needs a mode and a non-empty glob; --enforce and --observe exclude each other;
    --wait-ms takes 1..3000 and --fallback deny or allow, and they and --allow-open-default need a mode."""
    result = gated.run("namespace", "travel-advisor", "instrument", *args)
    assert result.returncode == 2 and message in result.stderr
    assert mutations(gated) == []


@pytest.mark.parametrize(("glob", "ok"), [
    ("*.svc", True), ("a?b", True), ("[ab].x", True), ("[^a].x", True), ("[a-c]x", True), ("\\[x", True), ("x]", True),
    ("[\\]]x", True), ("Peer.Example", True),
    ("", False), ("  ", False), ("[", False), ("[]", False), ("[a", False), ("[a-]x", False), ("[-a]x", False),
    ("[]a]", False), ("a\\", False), ("[a-b-c]", False), ("[^]x", False), ("x[", False), ("$HOME.x", False),
])
def test_a_pass_host_is_checked_as_the_gate_checks_it(glob, ok) -> None:
    """The gate trims and folds a pass_hosts entry and refuses an empty one or one path.Match calls malformed."""
    assert (gate_config.pass_host_problem(glob) == "") == ok, gate_config.pass_host_problem(glob)


def test_a_pass_host_starting_with_a_dash_is_a_glob(gated) -> None:
    """A glob that starts with - is the glob, not an option of the check: the gate takes it, so instrument writes it."""
    gated.set_namespace({"travel-advisor": RUNNING})
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--pass-host", "-x.example")
    assert result.returncode == 0, result.stderr
    assert plugin(written(gated), "outbound", "sentry-gate")["config"]["pass_hosts"] == ["-x.example"]


# --- the fail-mode document: fail-mode set|show|validate, and its rows in the gate ---------

ROWS = [{"destination": "peer.example", "wait_ms": 100}, {"destination": "slow.example", "fallback": "deny", "wait_ms": 3000}]
ALLOW_ROW = {"destination": "open.example", "fallback": "allow"}


def fail_mode_cm(box, text: str) -> None:
    """The namespace holds ConfigMap dg-fail-mode with ``text`` as its table."""
    (box.fixdir / "cm-dg-fail-mode.json").write_text(json.dumps({
        "apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "dg-fail-mode", "namespace": "travel-advisor"},
        "data": {"table": text}}))


def document(*rows: dict) -> str:
    return json.dumps({"rows": list(rows)}, indent=2) + "\n"


def test_fail_mode_validate_never_touches_the_cluster(gated, tmp_path) -> None:
    """D2 through dg.sh: validate exits 0 on a good document and non-zero naming the rule on a bad one, with no kubectl call."""
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(document(*ROWS))
    bad.write_text(document({"destination": "peer.example:8443", "fallback": "deny"}))
    assert gated.run("namespace", "travel-advisor", "fail-mode", "validate", str(good)).returncode == 0
    result = gated.run("namespace", "travel-advisor", "fail-mode", "validate", str(bad))
    assert result.returncode != 0 and "a destination has no port" in result.stderr
    assert gated.kubectl_calls() == []


def test_fail_mode_set_writes_the_document_verbatim_and_show_prints_it(gated, tmp_path) -> None:
    """D3: show with no ConfigMap says so; set writes dg-fail-mode/table byte for byte; show prints it; a bad document writes nothing."""
    result = gated.run("namespace", "travel-advisor", "fail-mode", "show")
    assert result.returncode == 0 and result.stdout == "" and "has no dg-fail-mode" in result.stderr
    text = '{"rows": [ {"destination": "Peer.example",\r\n "wait_ms": 100} ]}  \n\n'
    doc = tmp_path / "doc.json"
    doc.write_bytes(text.encode())
    result = gated.run("namespace", "travel-advisor", "fail-mode", "set", str(doc))
    assert result.returncode == 0, result.stderr
    assert "next 'dg.sh namespace travel-advisor instrument'" in result.stderr
    written_cm = json.loads((gated.fixdir / "cm-dg-fail-mode-wired.json").read_text())
    assert written_cm["metadata"] == {"name": "dg-fail-mode", "namespace": "travel-advisor"}
    assert written_cm["data"] == {"table": text}
    result = gated.run("namespace", "travel-advisor", "fail-mode", "show")
    assert result.returncode == 0 and result.stdout == text.replace("\r\n", "\n")  # the run reads stdout in text mode
    gated.log.unlink()
    bad = tmp_path / "bad.json"
    bad.write_text(document({"destination": "peer.example"}))
    result = gated.run("namespace", "travel-advisor", "fail-mode", "set", str(bad))
    assert result.returncode != 0 and "a row gives wait_ms or fallback" in result.stderr and "unchanged" in result.stderr
    assert gated.kubectl_calls() == []
    assert json.loads((gated.fixdir / "cm-dg-fail-mode-wired.json").read_text())["data"] == {"table": text}


def test_instrument_renders_the_rows_into_the_gate(gated) -> None:
    """D4: with dg-fail-mode, instrument --enforce writes fail_mode rows (same keys, document order) after fallback;
    without it the gate's block is commit 1's (no fail_mode key)."""
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce").returncode == 0
    assert "fail_mode" not in plugin(written(gated), "outbound", "sentry-gate")["config"]
    _adopt_written(gated)
    fail_mode_cm(gated, document(*ROWS))
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--fallback", "deny")
    assert result.returncode == 0, result.stderr
    assert "fail_mode rows from travel-advisor/dg-fail-mode: 2" in result.stderr
    config = plugin(written(gated), "outbound", "sentry-gate")["config"]
    assert config["fail_mode"] == [{"destination": "peer.example", "wait_ms": 100},
                                   {"destination": "slow.example", "wait_ms": 3000, "fallback": "deny"}]
    assert list(config) == ["verdict_url", "verdict_secret", "fallback", "fail_mode"]


def test_a_row_with_allow_needs_allow_open_default(gated) -> None:
    """D4: a row with fallback allow refuses instrument before any write unless --allow-open-default is given."""
    fail_mode_cm(gated, document(ROWS[0], ALLOW_ROW))
    result = gated.run("namespace", "travel-advisor", "instrument", "--observe")
    assert result.returncode != 0
    assert "has a row with fallback allow" in result.stderr and "--allow-open-default" in result.stderr
    assert mutations(gated) == []
    result = gated.run("namespace", "travel-advisor", "instrument", "--observe", "--allow-open-default")
    assert result.returncode == 0, result.stderr
    assert plugin(written(gated), "outbound", "sentry-gate")["config"]["fail_mode"][1] == ALLOW_ROW


def test_the_allow_row_check_fails_closed(gated, tmp_path) -> None:
    """When the check for allow rows cannot run, instrument refuses; it never reads as a document with no allow row."""
    fail_mode_cm(gated, document(ROWS[0], ALLOW_ROW))
    real = sys.executable
    shim = gated.bindir / "python3"
    shim.write_text('#!/bin/bash\nif [[ "$*" == *\'"fallback") == "allow"\'* ]]; then exit 3; fi\n'
                    f'exec "{real}" "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode != 0 and "could not check the fail-mode document" in result.stderr, result.stderr
    assert mutations(gated) == []


@pytest.mark.parametrize("table", [
    document({"destination": "peer.example", "fallback": "deny"}, {"destination": "peer.example", "wait_ms": 5}),
    "rows:\n- destination: peer.example\n",
    None,
])
def test_an_invalid_document_refuses_instrument_before_any_write(gated, table) -> None:
    """D5: a dg-fail-mode holding an invalid document (or none) refuses instrument before any write, naming fail-mode validate."""
    if table is None:
        (gated.fixdir / "cm-dg-fail-mode.json").write_text(json.dumps({"metadata": {"name": "dg-fail-mode"}, "data": {}}))
    else:
        fail_mode_cm(gated, table)
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode != 0
    assert "is not a valid fail-mode document" in result.stderr and "fail-mode validate" in result.stderr
    assert mutations(gated) == []
    assert not (gated.fixdir / "cm-authbridge-runtime-config-wired.json").exists()


def test_status_names_an_invalid_document_as_invalid(gated) -> None:
    """status on a gated namespace whose fail-mode document is readable but invalid says it is not valid and names
    fail-mode validate, not only that something cannot be read."""
    gated.set_namespace({"travel-advisor": RUNNING})
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce").returncode == 0
    _adopt_written(gated)
    fail_mode_cm(gated, "rows:\n- destination: peer.example\n")
    result = gated.run("namespace", "travel-advisor", "status")
    out = result.stdout + result.stderr
    assert "is not valid" in out and "fail-mode validate" in out, out
    assert mutations(gated) == []


def test_a_pod_on_other_rows_is_rolled_and_status_counts_the_rows(gated) -> None:
    """D6: status prints the row count and live=yes; a pod running other rows is rolled with nothing written;
    a document set but not yet instrumented shows as a drifted source."""
    gated.set_namespace({"travel-advisor": RUNNING})
    fail_mode_cm(gated, document(*ROWS))
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce").returncode == 0
    _adopt_written(gated)
    status = gated.run("namespace", "travel-advisor", "status")
    assert "sentry-gate mode=enforce pass_hosts=(none) wait_ms=2000 fallback=deny fail_mode_rows=2 sources=0" in status.stdout, status.stdout
    assert "live=yes" in status.stdout, status.stdout
    stale = json.loads((gated.fixdir / "cm-authbridge-runtime-config.json").read_text())
    stale["data"]["config.yaml"] = stale["data"]["config.yaml"].replace("wait_ms: 100", "wait_ms: 200")
    (gated.fixdir / "pod-cm-travel-advisor.json").write_text(json.dumps(stale))
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode == 0, result.stderr
    assert not any(c.startswith(("replace", "apply")) for c in gated.kubectl_calls())
    assert any("deployment/travel-advisor" in c for c in restarts(gated))
    for f in gated.fixdir.glob("restarted-*"):
        f.unlink()
    (gated.fixdir / "pod-cm-travel-advisor.json").unlink()
    gated.log.unlink()
    fail_mode_cm(gated, document(ROWS[0]))
    status = gated.run("namespace", "travel-advisor", "status")
    assert "fail_mode_rows=2" in status.stdout and "instrument --enforce" in status.stdout, status.stdout
    assert mutations(gated) == []


# --- slices per source: rows naming a source --------------------------------------------------

SLICED = (
    {"source": "desk", "wait_ms": 1234},
    {"destination": "peer.example", "wait_ms": 100},
    {"source": "desk", "destination": "peer.example", "fallback": "deny"},
    {"destination": "slow.example", "fallback": "deny", "wait_ms": 3000},
)


def gate_text(box) -> str:
    """The gate's entry in the written source, as text: from its name line to the end of the outbound chain."""
    config = json.loads((box.fixdir / "cm-authbridge-runtime-config-wired.json").read_text())["data"]["config.yaml"]
    return "    - name: sentry-gate\n" + config.split("    - name: sentry-gate\n", 1)[1]


def test_instrument_renders_the_slices_after_the_rows(gated) -> None:
    """D11: a document with a source row, two destination rows and a pair renders the destination rows as fail_mode
    and one slice under sources, after fail_mode: defaults from the source row, rows the pair over the destination row
    and the second destination as is; status reads one source."""
    fail_mode_cm(gated, document(*SLICED))
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--fallback", "deny")
    assert result.returncode == 0, result.stderr
    assert "fail_mode rows from travel-advisor/dg-fail-mode: 2, sources: 1" in result.stderr
    config = plugin(written(gated), "outbound", "sentry-gate")["config"]
    assert list(config) == ["verdict_url", "verdict_secret", "fallback", "fail_mode", "sources"]
    assert config["fail_mode"] == [SLICED[1], SLICED[3]]
    assert config["sources"] == {"desk": {"wait_ms": 1234, "fail_mode": [
        {"destination": "peer.example", "wait_ms": 100, "fallback": "deny"},
        {"destination": "slow.example", "wait_ms": 3000, "fallback": "deny"}]}}
    assert gate_text(gated).endswith(
        "        fail_mode:\n"
        "        - destination: peer.example\n"
        "          wait_ms: 100\n"
        "        - destination: slow.example\n"
        "          wait_ms: 3000\n"
        "          fallback: deny\n"
        "        sources:\n"
        "          desk:\n"
        "            wait_ms: 1234\n"
        "            fail_mode:\n"
        "            - destination: peer.example\n"
        "              wait_ms: 100\n"
        "              fallback: deny\n"
        "            - destination: slow.example\n"
        "              wait_ms: 3000\n"
        "              fallback: deny\n"), gate_text(gated)


def test_a_document_with_no_source_rows_renders_the_3a_block(gated) -> None:
    """D11: with no row naming a source the gate's entry is commit 3a's, byte for byte: no sources key."""
    fail_mode_cm(gated, document(*ROWS))
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--fallback", "deny")
    assert result.returncode == 0, result.stderr
    assert "fail_mode rows from travel-advisor/dg-fail-mode: 2, sources: 0" in result.stderr
    assert gate_text(gated) == (
        "    - name: sentry-gate\n"
        "      config:\n"
        "        verdict_url: http://data-governance-verdict.data-governance.svc:8080/enforce/v1\n"
        f"        verdict_secret: {SECRET}\n"
        "        fallback: deny\n"
        "        fail_mode:\n"
        "        - destination: peer.example\n"
        "          wait_ms: 100\n"
        "        - destination: slow.example\n"
        "          wait_ms: 3000\n"
        "          fallback: deny\n"), gate_text(gated)


@pytest.mark.parametrize("rows", [
    ({"source": "desk", "fallback": "allow"},),
    ({"destination": "peer.example", "wait_ms": 100}, {"source": "desk", "destination": "peer.example", "fallback": "allow"}),
], ids=["slice-default", "slice-row"])
def test_a_slice_with_allow_needs_allow_open_default(gated, rows) -> None:
    """D11: an allow in a slice's defaults or in a slice row refuses instrument before any write unless
    --allow-open-default is given; with it the allow is in the slice."""
    fail_mode_cm(gated, document(*rows))
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode != 0
    assert "has a row with fallback allow" in result.stderr and "--allow-open-default" in result.stderr
    assert mutations(gated) == []
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce", "--allow-open-default")
    assert result.returncode == 0, result.stderr
    assert "allow" in json.dumps(plugin(written(gated), "outbound", "sentry-gate")["config"]["sources"]["desk"])


def test_a_pod_on_other_slices_is_rolled_and_status_counts_the_sources(gated) -> None:
    """D12: status prints sources=1 and live=yes; a pod running another slice default is rolled with nothing written;
    a slice set in the document but not yet instrumented shows as a drifted source."""
    gated.set_namespace({"travel-advisor": RUNNING})
    fail_mode_cm(gated, document(*SLICED))
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce").returncode == 0
    _adopt_written(gated)
    status = gated.run("namespace", "travel-advisor", "status")
    assert "fail_mode_rows=2 sources=1" in status.stdout and "live=yes" in status.stdout, status.stdout
    stale = json.loads((gated.fixdir / "cm-authbridge-runtime-config.json").read_text())
    stale["data"]["config.yaml"] = stale["data"]["config.yaml"].replace("wait_ms: 1234", "wait_ms: 1235")
    (gated.fixdir / "pod-cm-travel-advisor.json").write_text(json.dumps(stale))
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode == 0, result.stderr
    assert not any(c.startswith(("replace", "apply")) for c in gated.kubectl_calls())
    assert any("deployment/travel-advisor" in c for c in restarts(gated))
    for f in gated.fixdir.glob("restarted-*"):
        f.unlink()
    (gated.fixdir / "pod-cm-travel-advisor.json").unlink()
    gated.log.unlink()
    fail_mode_cm(gated, document(*SLICED, {"source": "booking", "wait_ms": 5}))
    status = gated.run("namespace", "travel-advisor", "status")
    assert "sources=1" in status.stdout and "instrument --enforce" in status.stdout, status.stdout
    assert mutations(gated) == []


# --- status ---------------------------------------------------------------------------------


def test_status_reads_the_gate(gated) -> None:
    """status prints the gate's mode and pass hosts, reports a gated pod live, and a stale source advises the gated run."""
    gated.set_namespace({"travel-advisor": RUNNING})
    assert gated.run("namespace", "travel-advisor", "instrument", "--observe", "--pass-host", "h.example",
                     "--pass-host", "k.example").returncode == 0
    _adopt_written(gated)
    result = gated.run("namespace", "travel-advisor", "status")
    assert result.returncode == 0, result.stderr
    assert "namespace travel-advisor: sentry-gate mode=observe pass_hosts=h.example,k.example wait_ms=2000 fallback=deny fail_mode_rows=0 sources=0" in result.stdout
    line = next(l for l in result.stdout.splitlines() if l.startswith("travel-advisor"))
    assert "live=yes" in line, line
    # A source that no longer matches the gated render: the advice keeps the gate's mode.
    result = gated.run("namespace", "travel-advisor", "status", env={"DG_VERDICT_URL": "http://other/enforce/v1"})
    assert "instrument --observe" in result.stdout and "live=no" in result.stdout, result.stdout
    assert mutations(gated) == []


# --- the secret ------------------------------------------------------------------------------


def test_the_secret_never_reaches_a_command_line(gated, tmp_path) -> None:
    """No python3 started by instrument --enforce or status carries the secret in its argv."""
    real = sys.executable
    shim = gated.bindir / "python3"
    shim.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> "{tmp_path}/argv.log"\nexec "{real}" "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce").returncode == 0
    _adopt_written(gated)
    assert gated.run("namespace", "travel-advisor", "status").returncode == 0
    argv = (tmp_path / "argv.log").read_text()
    assert argv and SECRET not in argv


def test_the_secret_file_does_not_outlive_the_run(gated, tmp_path) -> None:
    """Every temp file instrument --enforce and status make holding the secret is gone when they exit."""
    made = tmp_path / "made.log"
    shim = gated.bindir / "mktemp"
    shim.write_text(f'#!/bin/bash\nf="$(/usr/bin/mktemp "$@")" || exit\nprintf "%s\\n" "$f" >> "{made}"\nprintf "%s\\n" "$f"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    assert gated.run("namespace", "travel-advisor", "instrument", "--enforce").returncode == 0
    _adopt_written(gated)
    gated.run("namespace", "travel-advisor", "status")
    paths = [Path(p) for p in made.read_text().split()]
    assert paths, "the run made no temp file"
    left = [p for p in paths if p.is_file() and SECRET in p.read_text(errors="ignore")]
    assert left == []


# --- the live check: every field it compares is compared ----------------------------------


sys.path.insert(0, str(REPO_ROOT / "deploy"))
import gate_config  # noqa: E402

KIT = gate_config.load_reconcile(str(REPO_ROOT / "deploy" / "lineage-attach"))
ENDPOINT = "otel-collector.rossoctl-system.svc.cluster.local:4317"
SPEC = {"verdict_url": "http://v/enforce/v1", "verdict_secret": SECRET, "pass_hosts": ["h.example"]}


def _live(**changes) -> tuple[dict, dict]:
    lineage = {"name": "lineage-telemetry", "config": {"otel_endpoint": ENDPOINT, "capture_io": True,
                                                       "export_delay_ms": 10, "namespace_file": KIT.NAMESPACE_FILE}}
    gate = {"name": "sentry-gate", "config": {"verdict_url": SPEC["verdict_url"], "verdict_secret": "[REDACTED]",
                                              "pass_hosts": ["h.example"]}}
    parsers = [{"name": n} for n in KIT.PARSER_PLUGINS]
    doc = {"inbound": [{"name": "jwt-validation"}, *parsers, json.loads(json.dumps(lineage))],
           "outbound": [{"name": "token-exchange"}, *parsers, lineage, gate]}
    for path, value in changes.items():
        direction, plugin_name, key = path.split(".")
        target = next(p for p in doc[direction] if p["name"] == plugin_name)
        target.setdefault("config", {})[key] = value
    expected = {d: [p["name"] for p in doc[d]] for d in ("inbound", "outbound")}
    return doc, expected


@pytest.mark.parametrize("change", [
    {}, {"outbound.sentry-gate.pass_hosts": []}, {"outbound.sentry-gate.verdict_url": "http://x"},
    {"outbound.sentry-gate.verdict_secret": ""}, {"outbound.lineage-telemetry.capture_io": False},
    {"inbound.lineage-telemetry.export_delay_ms": 5000}, {"outbound.lineage-telemetry.otel_endpoint": "x:1"},
    {"outbound.sentry-gate.fallback": "allow"}, {"outbound.sentry-gate.wait_ms": 500},
])
def test_the_live_check_compares_every_field(change) -> None:
    """The /v1/pipeline check fails on any one changed gate or lineage field, and on the wrong mode."""
    doc, expected = _live(**change)
    problems = gate_config.pipeline_problems(doc, expected, ENDPOINT, KIT.NAMESPACE_FILE, KIT.MANAGED_PLUGINS, SPEC, "enforce")
    assert bool(problems) == bool(change), problems
    if not change:
        assert gate_config.pipeline_problems(doc, expected, ENDPOINT, KIT.NAMESPACE_FILE, KIT.MANAGED_PLUGINS, SPEC, "observe")


def test_the_live_check_compares_the_rows_as_a_set_by_destination() -> None:
    """D6: fail_mode rows in another order, or a destination in another case or with a trailing dot, are the same; a changed
    or missing row differs."""
    rows = [{"destination": "a.example", "wait_ms": 100}, {"destination": "b.example", "fallback": "allow"}]
    spec = dict(SPEC, fail_mode=rows)
    for live, same in ((rows, True), (rows[::-1], True), ([dict(rows[0], destination="A.Example"), rows[1]], True),
                       ([dict(rows[0], destination="a.example."), rows[1]], True),
                       ([rows[0]], False), ([rows[0], dict(rows[1], fallback="deny")], False), (None, False)):
        doc, expected = _live() if live is None else _live(**{"outbound.sentry-gate.fail_mode": live})
        problems = gate_config.pipeline_problems(doc, expected, ENDPOINT, KIT.NAMESPACE_FILE, KIT.MANAGED_PLUGINS, spec, "enforce")
        assert (problems == []) == same, (live, problems)


def test_a_slice_the_pipeline_endpoint_masks_is_compared_by_presence() -> None:
    """/v1/pipeline masks a key named like a secret's, so a source such as chaos-monkey comes back as a string: it
    matches when the spec has that source, and the other slices are still compared."""
    sources = {"chaos-monkey": {"fallback": "allow"}, "desk": {"wait_ms": 100}}
    spec = dict(SPEC, sources=sources)
    for live, same in (({"chaos-monkey": "[REDACTED]", "desk": {"wait_ms": 100}}, True),
                       ({"chaos-monkey": "[REDACTED]", "desk": {"wait_ms": 200}}, False),
                       ({"auth-token": "[REDACTED]", "chaos-monkey": "[REDACTED]", "desk": {"wait_ms": 100}}, False),
                       ({"desk": {"wait_ms": 100}}, False)):
        doc, expected = _live(**{"outbound.sentry-gate.sources": live})
        problems = gate_config.pipeline_problems(doc, expected, ENDPOINT, KIT.NAMESPACE_FILE, KIT.MANAGED_PLUGINS, spec, "enforce")
        assert (problems == []) == same, (live, problems)


def test_the_live_check_compares_the_slices_by_source() -> None:
    """D12: slices in another order, or a slice's rows in another order or destination case, are the same; a changed
    slice default, a changed or missing slice row, a missing or an extra slice differ."""
    desk = {"wait_ms": 100, "fail_mode": [{"destination": "a.example", "fallback": "allow"}, {"destination": "b.example", "wait_ms": 5}]}
    sources = {"desk": desk, "booking": {"fallback": "deny"}}
    spec = dict(SPEC, sources=sources)
    for live, same in (
            (sources, True),
            ({"booking": sources["booking"], "desk": desk}, True),
            ({"desk": dict(desk, fail_mode=[desk["fail_mode"][1], dict(desk["fail_mode"][0], destination="A.Example")]),
              "booking": sources["booking"]}, True),
            ({"desk": dict(desk, wait_ms=200), "booking": sources["booking"]}, False),
            ({"desk": {"wait_ms": 100, "fail_mode": desk["fail_mode"][:1]}, "booking": sources["booking"]}, False),
            ({"desk": dict(desk, fail_mode=[dict(desk["fail_mode"][0], fallback="deny"), desk["fail_mode"][1]]),
              "booking": sources["booking"]}, False),
            ({"desk": desk}, False),
            (dict(sources, other={"wait_ms": 1}), False),
            (None, False)):
        doc, expected = _live() if live is None else _live(**{"outbound.sentry-gate.sources": live})
        problems = gate_config.pipeline_problems(doc, expected, ENDPOINT, KIT.NAMESPACE_FILE, KIT.MANAGED_PLUGINS, spec, "enforce")
        assert (problems == []) == same, (live, problems)
        assert same or "gate sources slices differ" in problems


def test_read_takes_the_gate_keys_not_a_row_s(tmp_path) -> None:
    """read counts the fail_mode rows and the sources slices, and a row's or a slice's wait_ms or fallback is not read as the gate's."""
    import subprocess

    lines = gate_config.gate_block(4, dict(SPEC, fail_mode=[{"destination": "a.example", "fallback": "allow"},
                                                             {"destination": "b.example", "wait_ms": 5}],
                                           sources={"desk": {"fallback": "allow", "fail_mode": [{"destination": "c.example", "wait_ms": 1}]},
                                                    "booking": {"wait_ms": 9}}), "enforce")
    config = "pipeline:\n  inbound:\n    plugins:\n    - name: jwt-validation\n  outbound:\n    plugins:\n" + "".join(lines)
    result = subprocess.run([sys.executable, str(REPO_ROOT / "deploy" / "gate_config.py"), "read", "--kit-dir",
                             str(REPO_ROOT / "deploy" / "lineage-attach")], input=json.dumps({"data": {"config.yaml": config}}),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"mode": "enforce", "pass_hosts": ["h.example"], "wait_ms": None, "fallback": None, "fail_mode_rows": 2,
                                         "sources": 2}


def test_the_live_check_wants_the_gate_after_lineage() -> None:
    """A live outbound chain with the gate before lineage-telemetry fails the check."""
    doc, _ = _live()
    outbound = doc["outbound"]
    outbound[-2], outbound[-1] = outbound[-1], outbound[-2]
    expected = {d: [p["name"] for p in doc[d]] for d in ("inbound", "outbound")}
    problems = gate_config.pipeline_problems(doc, expected, ENDPOINT, KIT.NAMESPACE_FILE, KIT.MANAGED_PLUGINS, SPEC, "enforce")
    assert "outbound: the gate is not after lineage-telemetry" in problems


@pytest.mark.parametrize(("want", "live", "same"), [
    (None, "deny", True), ("deny", None, True), ("allow", None, False), ("allow", "deny", False), ("allow", "allow", True),
])
def test_the_live_check_reads_an_absent_fallback_as_deny(want, live, same) -> None:
    """The live check compares fallback with absent = deny on both sides."""
    doc, expected = _live() if live is None else _live(**{"outbound.sentry-gate.fallback": live})
    spec = dict(SPEC, fallback=want) if want else SPEC
    problems = gate_config.pipeline_problems(doc, expected, ENDPOINT, KIT.NAMESPACE_FILE, KIT.MANAGED_PLUGINS, spec, "enforce")
    assert (problems == []) == same, problems


@pytest.mark.parametrize(("want", "live", "same"), [
    (None, 2000, True), (2000, None, True), (500, None, False), (500, 2000, False), (500, 500, True),
])
def test_the_live_check_reads_an_absent_wait_ms_as_2000(want, live, same) -> None:
    """The live check compares wait_ms with absent = 2000 on both sides."""
    doc, expected = _live() if live is None else _live(**{"outbound.sentry-gate.wait_ms": live})
    spec = dict(SPEC, wait_ms=want) if want else SPEC
    problems = gate_config.pipeline_problems(doc, expected, ENDPOINT, KIT.NAMESPACE_FILE, KIT.MANAGED_PLUGINS, spec, "enforce")
    assert (problems == []) == same, problems


def test_read_takes_the_gate_s_wait_ms(tmp_path) -> None:
    """read returns the gate's own wait_ms as a number."""
    import subprocess

    lines = gate_config.gate_block(4, dict(SPEC, wait_ms=500, fallback="deny"), "enforce")
    config = "pipeline:\n  inbound:\n    plugins:\n    - name: jwt-validation\n  outbound:\n    plugins:\n" + "".join(lines)
    result = subprocess.run([sys.executable, str(REPO_ROOT / "deploy" / "gate_config.py"), "read", "--kit-dir",
                             str(REPO_ROOT / "deploy" / "lineage-attach")], input=json.dumps({"data": {"config.yaml": config}}),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["wait_ms"] == 500 and json.loads(result.stdout)["fallback"] == "deny"
