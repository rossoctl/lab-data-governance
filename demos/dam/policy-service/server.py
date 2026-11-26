"""MCP policy service for DAM's experimental MCP policy enforcement.

DAM's agent gateway (an agent with ``mcpPolicyEnforcement``) sends every request on
a credentialed, non-model chain here as Envoy HTTP ext_authz: the original
method, ``/check/<agent id>`` + the original path, the original Host, and
the whole body. The gateway's controller sets the agent id; the agent's own
path only follows it, so the agent cannot name another agent.
A 2xx answer allows the request; any other status denies it, and its status,
body and ``x-mcp-policy-*`` headers reach the agent.

Each MCP JSON-RPC message is logged as one JSON line (host, JSON-RPC method,
tool name, argument keys, operations, the people the agent acts for; never
argument values). Each tools/call is decided by OPA's ``data.dam.mcp.decision``
on its operations (``operations.py``: ``issue_write`` as close, assign, edit,
...) and the people the agent acts for, which DAM's relay records
(``DAM_API_URL``, ``MCP_POLICY_SERVICE_TOKEN``). OPA holds what each agent's
description grants and the company security policy's role rules; when the
call is allowed and the company policy has data rules, data governance
(``dg_check.py``) judges the data against those rules. A deny answers 403 with
a JSON-RPC error that carries the reason, which names the agent's description
or the company policy. Anything unreachable, or answering nothing, is a deny:
the service fails closed.

Standard library only, so it runs from a ConfigMap on a stock python image.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import dg_check
from operations import operations_of

LOG = logging.getLogger("mcp-policy")
PORT = int(os.environ.get("PORT", "9191"))
CHECK_PREFIX = "/check"
OPA_URL = os.environ.get("OPA_URL", "").rstrip("/")
DECISION_PATH = "/v1/data/dam/mcp/decision"

# The hosts that speak MCP: a request to one of them must carry readable JSON-RPC or nothing.
MCP_HOSTS = {h.strip().lower() for h in os.environ.get("MCP_HOSTS", "api.githubcopilot.com").split(",") if h.strip()}


class Unreadable(ValueError):
    """A body the policy cannot read the way the MCP server would: it is refused, never passed."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise Unreadable("a JSON object repeats a key")
    return dict(pairs)


def _no_constants(name: str) -> Any:
    raise Unreadable(f"non-standard JSON constant {name}")


def _valid_message(m: Any) -> bool:
    """A JSON-RPC 2.0 request, notification or response (a client answers server requests too)."""
    if not isinstance(m, dict) or m.get("jsonrpc") != "2.0":
        return False
    if "method" in m:
        return isinstance(m["method"], str) and ("params" not in m or isinstance(m["params"], (dict, list)))
    return "id" in m and (("result" in m) != ("error" in m))


def mcp_messages(body: bytes) -> list[dict[str, Any]]:
    """The JSON-RPC messages in a body: one object, a batch, or none for an empty body.

    Strict, so that the policy reads a body exactly as any MCP server would: strict UTF-8, one
    standard JSON document, no repeated key in any object, and every element a well-formed
    JSON-RPC 2.0 message. Anything else raises ``Unreadable``.
    """
    if not body.strip():
        return []
    try:
        text = body.decode("utf-8", errors="strict")
        doc = json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constants)
    except UnicodeDecodeError as err:
        raise Unreadable("the body is not UTF-8") from err
    except json.JSONDecodeError as err:
        raise Unreadable(f"the body is not one JSON document ({err.msg})") from err
    items = doc if isinstance(doc, list) else [doc]
    if not items or not all(_valid_message(m) for m in items):
        raise Unreadable("the body is not JSON-RPC 2.0")
    return items


def describe(message: dict[str, Any]) -> dict[str, Any]:
    """What the policy decides on, without logging argument values."""
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    out: dict[str, Any] = {"rpc": message.get("method"), "id": message.get("id")}
    if message.get("method") == "tools/call":
        out["tool"] = params.get("name")
        args = params.get("arguments")
        out["arg_keys"] = sorted(args) if isinstance(args, dict) else []
        out["operations"] = operations_of(str(params.get("name", "")), args)
    return out


CONNECTIONS = {"api.githubcopilot.com": "github-mcp"}
DAM_API_URL = os.environ.get("DAM_API_URL", "").rstrip("/")
DAM_API_TOKEN = os.environ.get("MCP_POLICY_SERVICE_TOKEN", "")
ACTING_FOR_TTL_SECONDS = 1.0
_acting_lock = threading.Lock()
_acting_cache: dict[str, tuple[float, list[str]]] = {}


def acting_for(agent_id: str) -> list[str] | None:
    """The usernames of the people the agent acts for right now, from DAM's relay (cached for a
    second); ``None`` when DAM cannot say, which the policy treats as nobody it can allow."""
    with _acting_lock:
        hit = _acting_cache.get(agent_id)
        if hit and time.monotonic() - hit[0] < ACTING_FOR_TTL_SECONDS:
            return hit[1]
    if not DAM_API_URL:
        return None
    request = urllib.request.Request(
        f"{DAM_API_URL}/internal/mcp-policy/agents/{urllib.parse.quote(agent_id)}/acting-for",
        headers={"authorization": f"Bearer {DAM_API_TOKEN}", "accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            people = sorted({str(p["username"]) for p in json.load(response).get("people", [])})
    except (OSError, ValueError, KeyError, TypeError):
        return None
    with _acting_lock:
        _acting_cache[agent_id] = (time.monotonic(), people)
    return people


def agent_name(agent_id: str) -> str:
    """The agent's name, as the onboarder publishes it for every enforcing agent; else its id."""
    if not OPA_URL or not agent_id:
        return agent_id
    try:
        url = f"{OPA_URL}/v1/data/dam/names/{urllib.parse.quote(agent_id)}"
        with urllib.request.urlopen(url, timeout=2) as response:
            return str(json.load(response).get("result") or agent_id)
    except (OSError, ValueError):
        return agent_id


def split_check_path(raw: str) -> tuple[str, str]:
    """``/check/<agent id>/<original path>`` -> (agent id, original path). The gateway's controller
    writes the agent id; the agent's own path only follows it, so it cannot name another agent."""
    if not raw.startswith(CHECK_PREFIX + "/"):
        return "", raw
    agent, _, rest = raw[len(CHECK_PREFIX) + 1:].partition("/")
    return agent, "/" + rest


def opa_decision(agent: str, host: str, message: dict[str, Any], people: list[str] | None) -> dict[str, Any]:
    """OPA's decision for one JSON-RPC message, on the call's operations and the people the agent
    acts for; a failure is a deny. An allowed call names the company's data rules that apply
    (``data_rules``: dg rule id -> the policy sentence)."""
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    tool = str(params.get("name", ""))
    query = {"input": {"agent": agent, "host": host, "rpc": message.get("method"), "tool": tool,
                       "operations": operations_of(tool, params.get("arguments")), "people": people}}
    request = urllib.request.Request(
        OPA_URL + DECISION_PATH,
        data=json.dumps(query).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            result = json.load(response).get("result")
    except (OSError, ValueError) as err:
        return {"allow": False, "reason": f"policy decision point unavailable ({type(err).__name__}); denied"}
    if not isinstance(result, dict) or "allow" not in result:
        return {"allow": False, "reason": "policy decision point gave no decision; denied"}
    return result


def denial_body(message: dict[str, Any] | None, reason: str) -> bytes:  # noqa: D401
    """A JSON-RPC error for the denied message, so the MCP client shows the reason."""
    return json.dumps({
        "jsonrpc": "2.0",
        "id": (message or {}).get("id"),
        "error": {"code": -32001, "message": f"Denied by policy: {reason}"},
    }).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "mcp-policy/0.1"

    def _check(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        agent, path = split_check_path(self.path)
        host = self.headers.get("host", "")
        unreadable = ""
        try:
            messages = mcp_messages(body)
        except Unreadable as err:
            messages, unreadable = [], str(err)
        if unreadable and host.split(":")[0].lower() not in MCP_HOSTS:
            unreadable = ""  # not an MCP server: its bodies are not JSON-RPC, and the PEP has no say over them
        denied: tuple[dict[str, Any], str] | None = None
        reasons: list[str] = []
        if unreadable:
            denied = (None, f"the request to {host} is denied: the policy cannot read it ({unreadable}), so nothing allows it")
            reasons.append(denied[1])
        calls = [m for m in messages if m.get("method") == "tools/call"]
        name = agent_name(agent) if calls else agent
        people = acting_for(agent) if calls else []
        connection = CONNECTIONS.get(host.split(":")[0].lower(), host)
        checked: list[dict[str, Any]] = []
        for message in [] if unreadable else calls:
            verdict = opa_decision(agent, host, message, people) if OPA_URL else {
                "allow": False, "reason": "the policy has no decision point, so nothing allows it"}
            reasons.append(str(verdict.get("reason", "")))
            checked.append({"check": "access", "allow": bool(verdict.get("allow"))})
            if not verdict.get("allow"):
                denied = (message, str(verdict.get("reason", "denied")))
                break
            data_rules = verdict.get("data_rules") if isinstance(verdict.get("data_rules"), dict) else {}
            if data_rules:
                verdict = dg_check.judge(name, connection, host, self.command, path, body, message, data_rules)
                reasons.append(verdict["reason"])
                checked.append({"check": "data", "allow": verdict["allow"], **verdict.get("dg", {})})
                if not verdict["allow"]:
                    denied = (message, verdict["reason"])
                    break
        record: dict[str, Any] = {
            "decision": "deny" if denied else "allow",
            "agent": agent,
            "agent_name": name,
            "people": people,
            "checked": checked,
            "method": self.command,
            "host": host,
            "path": path,
            "connection": self.headers.get("x-platform-conn"),
            "body_bytes": len(body),
            "mcp": [describe(m) for m in messages],
            "reasons": reasons,
        }
        LOG.info(json.dumps(record))
        if denied:
            payload = denial_body(*denied)
            self.send_response(403)
            self.send_header("content-type", "application/json")
            self.send_header("x-mcp-policy-decision", "deny")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("x-mcp-policy-decision", "allow")
        self.send_header("content-length", "0")
        self.end_headers()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _check

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return  # one JSON line per check is logged in _check


def main() -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    LOG.info(json.dumps({"event": "listening", "port": PORT}))
    server.serve_forever()


if __name__ == "__main__":
    main()
