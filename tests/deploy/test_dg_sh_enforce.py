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
  gate=',{"name":"sentry-gate","fields":[{"name":"verdict_url"},{"name":"pass_hosts"},{"name":"pass_single_label"},{"name":"wait_ms"},{"name":"deny_on"},{"name":"verdict_secret"},{"name":"verdict_secret_file"}]}'
  case "${GATE_CATALOG:-full}" in
    none) gate="" ;;
    no-secret) gate=',{"name":"sentry-gate","fields":[{"name":"verdict_url"},{"name":"pass_hosts"},{"name":"wait_ms"}]}' ;;
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


@pytest.mark.parametrize("catalog", ["none", "no-secret"])
def test_an_image_whose_gate_cannot_take_the_config_refuses_before_any_write(gated, catalog) -> None:
    """An image with no sentry-gate, or one lacking a key instrument writes (verdict_secret), is refused before any write."""
    gated.set_flag(GATE_CATALOG=catalog)
    result = gated.run("namespace", "travel-advisor", "instrument", "--enforce")
    assert result.returncode != 0
    assert "verdict_url, verdict_secret, pass_hosts" in result.stderr and "mutating nothing" in result.stderr
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
    (("--pass-host", "x.example"), "applies to --enforce and --observe only"),
    (("--enforce", "--pass-host", ""), "needs a host glob"),
    (("--enforce", "--observe"), "give one of --enforce, --observe"),
    (("--enforce", "--pass-host", "["), "a glob the gate refuses"),
    (("--enforce", "--pass-host", " "), "a glob the gate refuses: an empty glob"),
    (("--enforce", "--pass-host", "x\\"), "a glob the gate refuses: a trailing"),
])
def test_gate_flags_are_checked(gated, args, message) -> None:
    """--pass-host needs a mode and a non-empty glob; --enforce and --observe exclude each other."""
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


# --- status ---------------------------------------------------------------------------------


def test_status_reads_the_gate(gated) -> None:
    """status prints the gate's mode and pass hosts, reports a gated pod live, and a stale source advises the gated run."""
    gated.set_namespace({"travel-advisor": RUNNING})
    assert gated.run("namespace", "travel-advisor", "instrument", "--observe", "--pass-host", "h.example",
                     "--pass-host", "k.example").returncode == 0
    _adopt_written(gated)
    result = gated.run("namespace", "travel-advisor", "status")
    assert result.returncode == 0, result.stderr
    assert "namespace travel-advisor: sentry-gate mode=observe pass_hosts=h.example,k.example" in result.stdout
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
])
def test_the_live_check_compares_every_field(change) -> None:
    """The /v1/pipeline check fails on any one changed gate or lineage field, and on the wrong mode."""
    doc, expected = _live(**change)
    problems = gate_config.pipeline_problems(doc, expected, ENDPOINT, KIT.NAMESPACE_FILE, KIT.MANAGED_PLUGINS, SPEC, "enforce")
    assert bool(problems) == bool(change), problems
    if not change:
        assert gate_config.pipeline_problems(doc, expected, ENDPOINT, KIT.NAMESPACE_FILE, KIT.MANAGED_PLUGINS, SPEC, "observe")


def test_the_live_check_wants_the_gate_after_lineage() -> None:
    """A live outbound chain with the gate before lineage-telemetry fails the check."""
    doc, _ = _live()
    outbound = doc["outbound"]
    outbound[-2], outbound[-1] = outbound[-1], outbound[-2]
    expected = {d: [p["name"] for p in doc[d]] for d in ("inbound", "outbound")}
    problems = gate_config.pipeline_problems(doc, expected, ENDPOINT, KIT.NAMESPACE_FILE, KIT.MANAGED_PLUGINS, SPEC, "enforce")
    assert "outbound: the gate is not after lineage-telemetry" in problems
