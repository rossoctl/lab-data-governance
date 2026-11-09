"""``dg.sh namespace <ns> gate-report`` and deploy/gate_report.py (#342 commit 3c): what the namespace's Sentry
gates did with the requests that got no verdict, read off each sidecar's session API.

The sandbox is test_dg_sh_enforce's ``gated`` one; a wrapper kubectl in front of it serves canned session API
pages from ``fixtures/sessions/<pod>/<path>.json`` to ``kubectl exec <pod> ... wget .../v1/sessions...``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import re
import stat

import pytest

from tests.deploy.test_dg_sh_enforce import (  # noqa: F401
    REPO_ROOT, ROWS, RUNNING, _adopt_written, document, fail_mode_cm, gated, mutations, revision_of, sandbox)

SPEC = importlib.util.spec_from_file_location("gate_report", REPO_ROOT / "deploy" / "gate_report.py")
gate_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate_report)

_SESSIONS = r"""#!/usr/bin/env bash
if [[ "$*" == *"exec"* && "$*" == *"/v1/sessions"* ]]; then
  printf '%s\n' "$*" >> "$KUBECTL_LOG"
  pod=""; prev=""
  for a in "$@"; do [[ "$prev" == "exec" ]] && pod="$a"; prev="$a"; done
  url="${*: -1}"
  key="$(printf '%s' "${url#http://127.0.0.1:9094}" | tr -c 'A-Za-z0-9_.-' '_')"
  f="$FIXDIR/sessions/$pod/$key.json"
  if [[ -f "$f" ]]; then cat "$f"; exit 0; fi
  printf '%s\n' "wget: server returned error: HTTP/1.1 404 Not Found" "command terminated with exit code 1" >&2
  exit 1
fi
exec "$(dirname "$0")/kubectl-gated" "$@"
"""

OLD = "1a2b3c4d5e6f" + "0" * 52  # a revision the namespace no longer runs


@pytest.fixture()
def reporting(gated):  # noqa: F811
    """Two workloads on a gated namespace (observe, the ROWS document), each pod's session API canned."""
    kubectl = gated.bindir / "kubectl"
    kubectl.rename(gated.bindir / "kubectl-gated")
    kubectl.write_text(_SESSIONS)
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    gated.set_namespace({"travel-advisor": RUNNING, "booking-agent": RUNNING})
    fail_mode_cm(gated, document(*ROWS))
    result = gated.run("namespace", "travel-advisor", "instrument", "--observe")
    assert result.returncode == 0, result.stderr
    _adopt_written(gated)
    return gated


def serve(box, pod: str, path: str, answer: dict) -> None:
    d = box.fixdir / "sessions" / pod
    d.mkdir(parents=True, exist_ok=True)
    (d / (re.sub(r"[^A-Za-z0-9_.-]", "_", path) + ".json")).write_text(json.dumps(answer))


def page_path(sid: str, before: int | None = None) -> str:
    return f"/v1/sessions/{sid}?limit=2000&view=summary" + (f"&before={before}" if before else "")


def event(seq: int, host: str, action: str, reason: str, *, shadow: bool = False, at: str = "", self_id: str = "",
          **details) -> dict:
    """An outbound request event carrying lineage's record (with ``self_id`` when given) and the gate's."""
    gate = {"plugin": "sentry-gate", "phase": "request", "action": action, "reason": reason, "details": details}
    if shadow:
        gate["shadow"] = True
    lineage = {"plugin": "lineage-telemetry", "phase": "request", "action": "modify", "reason": "stamped_tracestate"}
    if self_id:
        lineage["details"] = {"capture_io": "true", "self_id": self_id}
    return {"seq": seq, "at": at or f"2026-10-29T09:{seq:02d}:00.123456789Z", "direction": "outbound", "phase": "request",
            "host": host, "requestId": f"r{seq}", "invocations": {"outbound": [lineage, gate]}}


def travel_advisor_events(current: str) -> list[dict]:
    """A window across a mode switch and a document change: shadow records under observe, enforced ones after."""
    return [
        event(1, "peer.example:443", "deny", "fallback_deny", shadow=True, no_verdict="verdict_pending",
              waited_ms="100", fail_mode_destination="peer.example", fail_mode_revision=OLD),
        event(2, "PEER.example", "allow", "fallback_allow", no_verdict="verdict_busy", waited_ms="4",
              fail_mode_revision=OLD),
        event(3, "slow.example", "deny", "verdict_pending", shadow=True, body_cut="true", waited_ms="3000",
              fail_mode_revision=current),
        event(4, "peer.example", "deny", "no_lineage_stamp", waited_ms="0", fail_mode_revision=current),
        # a refusal by a verdict, a verdict let through, a pass host and a tunnel: not this report's
        event(5, "peer.example", "deny", "verdict_block", shadow=True, verdict="block", waited_ms="40"),
        event(6, "peer.example", "allow", "verdict_allow", verdict="allow", waited_ms="40"),
        event(7, "courier", "skip", "pass_host"),
        event(8, "10.96.0.1:443", "allow", "not_inspected", kind="tunnel"),
        event(9, "slow.example:8443", "deny", "fallback_deny", no_verdict="verdict_error", error_kind="transport",
              waited_ms="3000", fail_mode_destination="slow.example", fail_mode_revision=current),
        event(10, "slow.example", "deny", "verdict_busy", body_not_captured="true", waited_ms="5",
              fail_mode_revision=current),
        # a marked body under a fault's reason counts by its mark
        event(11, "peer.example", "deny", "verdict_error", body_cut="true", waited_ms="2000", fail_mode_revision=current),
    ]


def canned(box, current: str) -> None:
    """travel-advisor: one session in two pages (paged back with before=); booking-agent: no record of a request
    without a verdict."""
    events = travel_advisor_events(current)
    serve(box, "travel-advisor-abc123", "/v1/sessions", {"sessions": [{"id": "s/1"}]})
    serve(box, "travel-advisor-abc123", page_path("s%2F1"), {"id": "s/1", "events": events[4:], "oldestSeq": 1})
    serve(box, "travel-advisor-abc123", page_path("s%2F1", 5), {"id": "s/1", "events": events[:4]})
    serve(box, "booking-agent-abc123", "/v1/sessions", {"sessions": [{"id": "b"}]})
    serve(box, "booking-agent-abc123", page_path("b"), {"id": "b", "events": [
        event(1, "peer.example", "allow", "verdict_allow", verdict="allow", waited_ms="9")]})


def rows_of(out: str) -> list[list[str]]:
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("source "))
    end = lines.index("by reason:")
    return [line.split() for line in lines[start:end]]


def test_the_report_counts_per_source_and_destination(reporting) -> None:
    """D17: two pods, one paged back over two pages: the counts per source and destination (the host port-stripped
    and case-folded), the shadow records in would_refuse, the marked bodies by mark, the refusals by reason, the
    revisions seen against the current one on the header line; a pod with no records reads as 0; read-only."""
    current = revision_of(document(*ROWS))
    canned(reporting, current)
    result = reporting.run("namespace", "travel-advisor", "gate-report")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    header = out.splitlines()[0]
    assert re.fullmatch(r"gate-report namespace=travel-advisor since=all held until=\S+Z mode=observe "
                        rf"revision={current[:12]}", header), header
    assert "sidecar booking-agent pod=booking-agent-abc123 read=yes events=1 no_verdict_records=0" in out
    assert "sidecar travel-advisor pod=travel-advisor-abc123 read=yes events=11 no_verdict_records=7" in out
    assert rows_of(out) == [
        ["source", "destination", "refused", "fallback_allow", "would_refuse", "body_cut", "body_not_captured"],
        ["travel-advisor", "peer.example", "1", "1", "1", "1", "0"],
        ["travel-advisor", "slow.example", "1", "0", "1", "0", "1"],
    ]
    by_reason = out.split("by reason:\n", 1)[1].split("revisions seen:", 1)[0]
    assert [re.split(r"\s{2,}", line.strip()) for line in by_reason.splitlines()] == [
        ["travel-advisor", "peer.example", "fallback_deny shadow", "1"],
        ["travel-advisor", "peer.example", "no_lineage_stamp", "1"],
        ["travel-advisor", "peer.example", "verdict_error body_cut", "1"],
        ["travel-advisor", "slow.example", "fallback_deny", "1"],
        ["travel-advisor", "slow.example", "verdict_busy body_not_captured", "1"],
        ["travel-advisor", "slow.example", "verdict_pending body_cut shadow", "1"],
    ]
    seen = ", ".join(f"{rev} {n}" for rev, n in sorted({OLD[:12]: 2, current[:12]: 5}.items()))
    assert out.splitlines()[-1] == f"revisions seen: {seen} (current {current[:12]})"
    calls = reporting.kubectl_calls()
    assert any("before=5" in c for c in calls), "the second page is asked for with before= the first page's oldest seq"
    assert mutations(reporting) == []


def test_an_empty_window_and_a_pod_that_cannot_be_read(reporting) -> None:
    """D17: --since after every record prints the header with the window and says no request is in it, and stops
    paging at the window's start; a pod whose session API fails is named read=no and the rest still read."""
    canned(reporting, revision_of(document(*ROWS)))
    result = reporting.run("namespace", "travel-advisor", "gate-report", "--since", "2026-10-29T10:00:00Z")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0].startswith(
        "gate-report namespace=travel-advisor since=2026-10-29T10:00:00Z until="), result.stdout
    assert "sidecar travel-advisor pod=travel-advisor-abc123 read=yes events=7 no_verdict_records=0" in result.stdout
    assert result.stdout.splitlines()[-1] == "no request without a verdict in the window"
    assert not any("before=" in c for c in reporting.kubectl_calls()), "the first page already reaches back past --since"
    (reporting.fixdir / "sessions" / "booking-agent-abc123" / "_v1_sessions.json").unlink()
    result = reporting.run("namespace", "travel-advisor", "gate-report", "--since", "1h")
    assert result.returncode == 0, result.stderr
    assert "sidecar booking-agent pod=booking-agent-abc123 read=no (GET /v1/sessions: wget: server returned error" in result.stdout
    assert "sidecar travel-advisor pod=travel-advisor-abc123 read=yes" in result.stdout


def test_every_replica_of_a_workload_is_read(reporting) -> None:
    """Each Running pod of a workload keeps its own records: a second replica is read too, and the workload's counts
    are the sum of its pods'."""
    canned(reporting, revision_of(document(*ROWS)))
    pods = reporting.fixdir / "pods-booking-agent.json"
    listing = json.loads(pods.read_text())
    second = json.loads(json.dumps(listing["items"][0]))
    second["metadata"]["name"] = "booking-agent-def456"
    listing["items"].append(second)
    pods.write_text(json.dumps(listing))
    serve(reporting, "booking-agent-def456", "/v1/sessions", {"sessions": [{"id": "c"}]})
    serve(reporting, "booking-agent-def456", page_path("c"), {"id": "c", "events": [
        event(1, "peer.example", "deny", "fallback_deny", no_verdict="verdict_pending", waited_ms="2000")]})
    result = reporting.run("namespace", "travel-advisor", "gate-report")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "sidecar booking-agent pod=booking-agent-abc123 read=yes events=1 no_verdict_records=0" in out
    assert "sidecar booking-agent pod=booking-agent-def456 read=yes events=1 no_verdict_records=1" in out
    assert ["booking-agent", "peer.example", "1", "0", "0", "0", "0"] in rows_of(out)


def test_no_sidecar_read_is_not_an_empty_window(reporting) -> None:
    """When no sidecar can be read the report says so and fails, instead of reading as nothing refused."""
    canned(reporting, revision_of(document(*ROWS)))
    for pod in ("travel-advisor-abc123", "booking-agent-abc123"):
        (reporting.fixdir / "sessions" / pod / "_v1_sessions.json").unlink()
    result = reporting.run("namespace", "travel-advisor", "gate-report")
    assert result.returncode != 0 and "gate-report: failed" in result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 4 and lines[-1] == "no sidecar could be read", result.stdout
    assert sum(line.startswith("sidecar ") and "read=no" in line for line in lines) == 2, result.stdout
    assert "no request without a verdict" not in result.stdout


def test_the_source_is_lineage_s_self_id() -> None:
    """The source is the self_id lineage recorded beside the gate's record, the name a fail-mode row's source
    matches; without one, the workload the pod was read from."""
    named = event(1, "peer.example", "deny", "fallback_deny", self_id="desk")
    unnamed = event(2, "peer.example", "deny", "fallback_deny")
    assert [r["source"] for r in gate_report.no_verdict_records("desk-agent", [named, unnamed], None)] == \
        ["desk", "desk-agent"]


def test_gate_report_needs_a_gated_namespace_and_a_good_since(gated) -> None:  # noqa: F811
    """A namespace with no gate in its source is refused, naming the run that adds it; --since takes a time."""
    result = gated.run("namespace", "travel-advisor", "gate-report")
    assert result.returncode != 0 and "runs no Sentry gate" in result.stderr and "instrument --observe" in result.stderr
    result = gated.run("namespace", "travel-advisor", "gate-report", "--since")
    assert result.returncode == 2 and "--since takes a time" in result.stderr
    result = gated.run("namespace", "travel-advisor", "gate-report", "--save", "x")
    assert result.returncode == 2 and "unknown argument '--save'" in result.stderr


# --- the module: which record counts where --------------------------------------------------------


@pytest.mark.parametrize(("inv", "column"), [
    ({"action": "deny", "reason": "fallback_deny"}, "refused"),
    ({"action": "deny", "reason": "no_exchange_key"}, "refused"),
    ({"action": "deny", "reason": "verdict_unauthorized"}, "refused"),
    ({"action": "deny", "reason": "verdict_empty"}, "refused"),
    ({"action": "deny", "reason": "verdict_error"}, "refused"),
    ({"action": "deny", "reason": "no_lineage_stamp"}, "refused"),
    ({"action": "allow", "reason": "fallback_allow"}, "fallback_allow"),
    ({"action": "deny", "reason": "fallback_deny", "shadow": True}, "would_refuse"),
    ({"action": "deny", "reason": "verdict_pending", "details": {"body_cut": "true"}}, "body_cut"),
    ({"action": "deny", "reason": "verdict_pending", "details": {"body_not_captured": "true"}}, "body_not_captured"),
    ({"action": "deny", "reason": "verdict_pending", "shadow": True, "details": {"body_not_captured": "true"}}, "would_refuse"),
    ({"action": "deny", "reason": "verdict_block", "details": {"verdict": "block"}}, None),
    ({"action": "deny", "reason": "verdict_unjudged", "details": {"verdict": "allow"}}, None),
    ({"action": "allow", "reason": "unread_capture_off"}, None),
    ({"action": "allow", "reason": "not_inspected"}, None),
    ({"action": "skip", "reason": "pass_host"}, None),
])
def test_each_record_counts_in_one_column(inv, column) -> None:
    """D17: fallback_deny and the faults are refused, fallback_allow is delivered on fallback, a shadow refusal is
    would_refuse, a marked body counts by its mark; a verdict's records and the records that held nothing count nowhere."""
    assert gate_report.outcome(inv) == column


def test_hosts_as_the_gate_matches_them() -> None:
    assert gate_report.host_only("Peer.Example:443") == "peer.example"
    assert gate_report.host_only("Peer.Example.:443") == "peer.example"
    assert gate_report.host_only("peer.example.") == "peer.example"
    assert gate_report.host_only("[::1]:8000") == "::1"
    assert gate_report.host_only("courier") == "courier"


def test_times_as_the_session_api_writes_them() -> None:
    base = datetime(2026, 10, 29, 8, 57, 16, tzinfo=timezone.utc)
    assert gate_report.parse_at("2026-10-29T08:57:16.676907545Z") == base.replace(microsecond=676907)
    assert gate_report.parse_at("2026-10-29T10:57:16+02:00") == base
    assert gate_report.parse_time("90m", base) == base.replace(hour=7, minute=27)
    with pytest.raises(ValueError):
        gate_report.parse_at("yesterday")
