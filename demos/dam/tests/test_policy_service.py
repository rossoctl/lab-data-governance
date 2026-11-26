"""Sentry's MCP policy service, through its HTTP interface, on the story of the Sentry on DAM demo.

The service runs in-process as DAM's gateway calls it (``POST /check/<agent id>/mcp/`` with the
JSON-RPC body). OPA is real: a local ``opa`` server loaded with ``opa/mcp.rego``, the two agents'
generated modules and the company policy. DAM's acting-for API is a fake HTTP server; dg's span
and verdict calls are faked, blocking a payload that carries an email address on rule DG-001.

Run: ``.venv/bin/pytest demos/dam/tests`` (skipped when no ``opa`` binary is on PATH).
"""

from __future__ import annotations

import importlib
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "policy-service"))

pytestmark = pytest.mark.skipif(shutil.which("opa") is None, reason="needs the opa binary")

HOST = "api.githubcopilot.com"

MODULES = {
    "agent-w": ("issue-workbench", {
        "issue_read": ("allow", "github-mcp.issue_read for agent issue-workbench is allowed: agent states that it reads and summarizes issues."),
        "add_issue_comment": ("allow", "github-mcp.add_issue_comment for agent issue-workbench is allowed: agent states that it posts comments."),
        "issue_write.assign": ("allow", "github-mcp.issue_write.assign for agent issue-workbench is allowed: agent states that it assigns an issue back and forth."),
        "issue_write.close": ("deny", "github-mcp.issue_write.close for agent issue-workbench is denied: the policy prohibits it, agent specifically states that it never closes issues."),
    }),
    "agent-q": ("qa-signoff", {
        "issue_read": ("allow", "github-mcp.issue_read for agent qa-signoff is allowed: agent states that it reads an issue and its history."),
        "add_issue_comment": ("allow", "github-mcp.add_issue_comment for agent qa-signoff is allowed: agent states that it posts the sign-off comment."),
        "issue_write.close": ("allow", "github-mcp.issue_write.close for agent qa-signoff is allowed: agent states that it closes the issue as completed."),
        "issue_write.assign": ("deny", "github-mcp.issue_write.assign for agent qa-signoff is denied: the policy prohibits it, agent specifically states that it never reassigns issues."),
    }),
}

COMPANY = {
    "version": 1,
    "status": "applied",
    "role_rules": [
        {"id": "R1", "text": "Developers and testers may read issues, comment on them and assign them.",
         "operations": ["github-mcp.issue_read", "github-mcp.add_issue_comment", "github-mcp.issue_write.assign"],
         "agents": [], "roles": ["developer", "tester"], "holders": ["alice", "bob"]},
        {"id": "R2", "text": "Only testers close an issue.", "operations": ["github-mcp.issue_write.close"],
         "agents": [], "roles": ["tester"], "holders": ["bob"]},
        {"id": "R3", "text": "The QA sign-off agent is used by testers only.", "operations": [],
         "agents": ["agent-q"], "roles": ["tester"], "holders": ["bob"]},
    ],
    "data_rules": [{"text": "Personal data and restricted data must not be sent to an untrusted external destination.",
                    "dg_rules": ["DG-001", "DG-004"]}],
}
NO_COMPANY = {"version": 0, "status": "applied", "role_rules": [], "data_rules": []}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def module_rego(agent_id: str, name: str, ops: dict) -> str:
    q = json.dumps
    return "\n".join([
        f"package dam.generated.{agent_id.replace('-', '_')}",
        "import rego.v1",
        f"name := {q(name)}", 'connection := "github-mcp"', f"host := {q(HOST)}",
        *[f"allow contains {q(op)}" for op, (eff, _) in ops.items() if eff == "allow"],
        "reasons := {", *[f"\t{q(op)}: {q(r)}," for op, (_, r) in ops.items()], "}", "",
    ])


def opa_put(base: str, path: str, body: bytes, content_type: str) -> None:
    req = urllib.request.Request(base + path, data=body, method="PUT", headers={"content-type": content_type})
    urllib.request.urlopen(req, timeout=5).close()


@pytest.fixture(scope="module")
def opa():
    port = free_port()
    proc = subprocess.Popen(["opa", "run", "--server", f"--addr=127.0.0.1:{port}", str(HERE / "opa/mcp.rego")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/health", timeout=1).close()
            break
        except OSError:
            time.sleep(0.05)
    agents = {i: {"name": n, "connection": "github-mcp", "host": HOST, "status": "decided"} for i, (n, _) in MODULES.items()}
    opa_put(base, "/v1/data/dam/agents", json.dumps(agents).encode(), "application/json")
    opa_put(base, "/v1/data/dam/names", json.dumps({i: n for i, (n, _) in MODULES.items()}).encode(), "application/json")
    for agent_id, (name, ops) in MODULES.items():
        opa_put(base, f"/v1/policies/aiac-{agent_id}", module_rego(agent_id, name, ops).encode(), "text/plain")
    yield base
    proc.terminate()
    proc.wait()


class World:
    """Whom each agent acts for (the fake DAM), and the service under test."""

    def __init__(self, opa: str, monkeypatch: pytest.MonkeyPatch):
        self.people: dict[str, list[str]] | None = {}
        self.opa = opa
        world = self

        class Dam(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib signature
                if self.headers.get("authorization") != "Bearer test-token" or world.people is None:
                    self.send_response(401 if world.people is not None else 503)
                    self.end_headers()
                    return
                agent = self.path.split("/")[-2]
                body = json.dumps({"people": [{"sub": f"{u}-sub", "username": u} for u in world.people.get(agent, [])]}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                return

        self.dam = ThreadingHTTPServer(("127.0.0.1", 0), Dam)
        threading.Thread(target=self.dam.serve_forever, daemon=True).start()
        monkeypatch.setenv("OPA_URL", opa)
        monkeypatch.setenv("DAM_API_URL", f"http://127.0.0.1:{self.dam.server_port}")
        monkeypatch.setenv("MCP_POLICY_SERVICE_TOKEN", "test-token")
        import dg_check
        import server
        self.server_mod = importlib.reload(server)
        self.dg = dg_check
        monkeypatch.setattr(dg_check, "_post_spans", lambda spans: None)
        self.sent: list[str] = []
        monkeypatch.setattr(server.dg_check, "_post_spans", lambda spans: None)
        monkeypatch.setattr(server.dg_check, "_verdict", self._verdict)
        self.last_body = b""
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), self.server_mod.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def _verdict(self, trace_id: str, exchange_id: str) -> dict:
        if b"@" in self.last_body:
            return {"state": "decided", "verdict": "block", "rules": ["DG-001"], "labels": ["person.name", "person.email"]}
        return {"state": "decided", "verdict": "allow", "rules": [], "labels": []}

    def company(self, doc: dict) -> None:
        opa_put(self.opa, "/v1/data/dam/company", json.dumps(doc).encode(), "application/json")

    def call(self, agent: str, tool: str, arguments: dict, people: list[str]) -> tuple[int, str]:
        assert self.people is not None
        self.people[agent] = people
        self.server_mod._acting_cache.clear()
        body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                           "params": {"name": tool, "arguments": arguments}}).encode()
        self.last_body = body
        req = urllib.request.Request(f"http://127.0.0.1:{self.http.server_port}/check/{agent}/mcp/", data=body,
                                     method="POST", headers={"host": HOST, "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, ""
        except urllib.error.HTTPError as err:
            return err.code, json.load(err)["error"]["message"]

    def close(self) -> None:
        self.http.shutdown()
        self.dam.shutdown()


@pytest.fixture
def world(opa, monkeypatch):
    w = World(opa, monkeypatch)
    yield w
    w.close()


ISSUE = {"owner": "s-and-p-team", "repo": "demo-public", "issue_number": 1}
CLOSE = {**ISSUE, "method": "update", "state": "closed", "state_reason": "completed"}
ASSIGN = {**ISSUE, "method": "update", "assignees": ["bob"]}
SIGN_OFF_WITH_EMAIL = {**ISSUE, "body": "Verified. Alice Mayer resolved it; reach Alice at alice.mayer@company.com."}
SIGN_OFF = {**ISSUE, "body": "Verified on Safari 17 and 18, fixed in PR #12."}


def test_act2_each_agent_keeps_to_its_description(world):
    world.company(NO_COMPANY)
    assert world.call("agent-w", "issue_write", ASSIGN, ["alice"])[0] == 200
    status, reason = world.call("agent-w", "issue_write", CLOSE, ["bob"])
    assert status == 403 and "never closes issues" in reason
    status, reason = world.call("agent-q", "issue_write", ASSIGN, ["bob"])
    assert status == 403 and "never reassigns" in reason


def test_act2_gaps_the_company_policy_closes(world):
    world.company(NO_COMPANY)
    assert world.call("agent-q", "issue_write", CLOSE, ["alice"])[0] == 200
    assert world.call("agent-q", "add_issue_comment", SIGN_OFF_WITH_EMAIL, ["bob"])[0] == 200


def test_act3_the_company_policy_decides_who_and_what_data(world):
    world.company(COMPANY)
    status, reason = world.call("agent-q", "issue_write", CLOSE, ["alice"])
    assert status == 403 and "asked by alice" in reason and '"The QA sign-off agent is used by testers only."' in reason
    status, reason = world.call("agent-q", "add_issue_comment", SIGN_OFF_WITH_EMAIL, ["bob"])
    assert status == 403
    assert '"Personal data and restricted data must not be sent to an untrusted external destination."' in reason
    assert world.call("agent-q", "add_issue_comment", SIGN_OFF, ["bob"])[0] == 200
    assert world.call("agent-q", "issue_write", CLOSE, ["bob"])[0] == 200
    assert world.call("agent-w", "issue_write", ASSIGN, ["alice"])[0] == 200


def test_two_people_get_only_what_both_may(world):
    world.company(COMPANY)
    assert world.call("agent-q", "issue_write", CLOSE, ["alice", "bob"])[0] == 403


def test_dam_unreachable_fails_closed(world):
    world.company(COMPANY)
    world.people = None
    world.server_mod._acting_cache.clear()
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "issue_read", "arguments": ISSUE}}).encode()
    world.last_body = body
    req = urllib.request.Request(f"http://127.0.0.1:{world.http.server_port}/check/agent-w/mcp/", data=body,
                                 method="POST", headers={"host": HOST})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=10)
    assert err.value.code == 403
    assert "who asked for it could not be told" in json.load(err.value)["error"]["message"]


def test_reasons_never_name_the_mechanism(world):
    world.company(COMPANY)
    for agent, tool, args, people in [("agent-q", "issue_write", CLOSE, ["alice"]),
                                      ("agent-w", "issue_write", CLOSE, ["bob"]),
                                      ("agent-q", "add_issue_comment", SIGN_OFF_WITH_EMAIL, ["bob"])]:
        _, reason = world.call(agent, tool, args, people)
        for word in ("aiac", "dg ", "OPA", "Rego", "data governance"):
            assert word not in reason, (word, reason)
