"""Sentry's dg (data governance) component: judges the data in an MCP tool call.

Sentry has two components, aiac (automated access control: which tools an agent
may use) and dg (data governance: which data may go where). This module is dg's
side of Sentry's MCP policy service, which plays the two parts the Cortex
sidecar plays for dg:
the ``lineage-telemetry`` producer, emitting the lineage wire contract's request
span (v1.8.1) with the tool call's arguments, and the dg gate, waiting for
the verdict service's answer on that exchange.

Standard library only: the OTLP span is encoded as protobuf by hand (the
receiver takes OTLP/HTTP protobuf, not JSON), with only the fields the
contract uses.
"""

from __future__ import annotations

import json
import os
import secrets
import struct
import time
import urllib.error
import urllib.request
from typing import Any

RECEIVER_URL = os.environ.get("DG_RECEIVER_URL", "http://data-governance-receiver.data-governance.svc:4318/v1/traces")
VERDICT_URL = os.environ.get("DG_VERDICT_URL", "http://data-governance-verdict.data-governance.svc:8080/enforce/v1")
VERDICT_SECRET = os.environ.get("DG_VERDICT_SECRET", "")
WAIT_MS = min(int(os.environ.get("DG_WAIT_MS", "3000")), 3000)
# Verdicts dg refuses (a dg gate's ``deny_on``).
DENY_ON = {v.strip() for v in os.environ.get("DG_DENY_ON", "block").split(",") if v.strip()}
# The agent's namespace as the lineage record names it (a DNS label).
SELF_NAMESPACE = os.environ.get("DG_SELF_NAMESPACE", "dam")
MAX_PAYLOAD_BYTES = int(os.environ.get("DG_MAX_PAYLOAD_BYTES", "4096"))

# How a label reads in a refusal: what kind of data the arguments carry.
LABEL_WORDS = {
    "person.govID": "a government ID or a person's name",
    "person.operationalID": "an operational identifier",
    "person.security": "credentials or a username",
    "person.contact": "personal contact details",
    "person.places": "an address",
    "person.employment": "employment information",
    "person.organization": "an organization's name",
    "person.personal": "personal information",
    "finance.pci": "payment card data",
    "finance.banking": "banking details",
    "finance.tax": "a tax identifier",
    "medical.identifiers": "medical identifiers",
    "medical.codes": "medical codes",
}
# Labels whose findings are only PI (no PII, PHI, PCI or CREDENTIALS tag): on their own they
# trigger no shipped rule, so a refusal does not name them unless nothing else is there.
CONTEXT_ONLY_LABELS = {
    "person.employment", "person.organization", "person.operationalID", "person.places",
    "person.dates", "person.demographics", "finance.data", "medical.codes", "medical.data",
}
# What the policy protects, by the rule that fired (the shipped catalog's ids).
RULE_GROUNDS = {
    "DG-001": "personal data",
    "DG-002": "health data",
    "DG-004": "restricted data",
}
# The company policy each shipped rule enforces, as the refusal states it.
RULE_POLICY = {
    "DG-001": "personal data must not be sent to an untrusted external destination",
    "DG-002": "health data must not be shared outside the company",
    "DG-004": "restricted data must not be sent to an untrusted external destination",
}


# --- protobuf, the few OTLP fields the contract uses ------------------------------------------


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _len(field: int, payload: bytes) -> bytes:
    return _varint(field << 3 | 2) + _varint(len(payload)) + payload


def _str(field: int, value: str) -> bytes:
    return _len(field, value.encode())


def _fixed64(field: int, value: int) -> bytes:
    return _varint(field << 3 | 1) + struct.pack("<Q", value)


def _uint(field: int, value: int) -> bytes:
    return _varint(field << 3 | 0) + _varint(value)


def _attr(key: str, value: Any) -> bytes:
    if isinstance(value, bool):
        any_value = _uint(2, int(value))
    elif isinstance(value, int):
        any_value = _uint(3, value & 0xFFFFFFFFFFFFFFFF)
    else:
        any_value = _str(1, str(value))
    return _str(1, key) + _len(2, any_value)


def _span(trace_id: bytes, span_id: bytes, name: str, kind: int, start: int, end: int, attrs: dict[str, Any],
          parent: bytes = b"") -> bytes:
    body = _len(1, trace_id) + _len(2, span_id)
    if parent:
        body += _len(4, parent)
    body += _str(5, name) + _uint(6, kind) + _fixed64(7, start) + _fixed64(8, end)
    for key, value in attrs.items():
        body += _len(9, _attr(key, value))
    return body


def export_request(spans: list[bytes]) -> bytes:
    """One ExportTraceServiceRequest with the contract's resource and scope."""
    resource = b"".join(_len(1, _attr(k, v)) for k, v in
                        {"service.name": "authbridge", "authbridge.component": "lineage-telemetry"}.items())
    scope = _str(1, "lineage-telemetry")
    scope_spans = _len(1, scope) + b"".join(_len(2, s) for s in spans)
    resource_spans = _len(1, resource) + _len(2, scope_spans)
    return _len(1, resource_spans)


# --- the exchange ------------------------------------------------------------------------------

SPAN_KIND_CLIENT = 3


def request_span(trace_id: bytes, span_id: bytes, agent: str, host: str, method: str, path: str, body: bytes,
                 tool: str, arguments: Any) -> bytes:
    now = time.time_ns()
    value = json.dumps(arguments if arguments is not None else {}, separators=(",", ":"), ensure_ascii=False)
    if len(value.encode()) > MAX_PAYLOAD_BYTES:
        value = value.encode()[:MAX_PAYLOAD_BYTES].decode("utf-8", errors="ignore") + "…[truncated]"
    exchange = span_id.hex()
    attrs = {
        "lineage.role": "request",
        "lineage.direction": "outbound",
        "lineage.exchange.id": exchange,
        "lineage.self.id": agent,
        "lineage.self.namespace": SELF_NAMESPACE,
        "lineage.protocol": "mcp",
        "lineage.peer.host": host,
        "lineage.parent.source": "none",
        "http.method": method,
        "url.path": path.split("?", 1)[0],
        "url.scheme": "https",
        "http.request.body.size": len(body),
        "mcp.method": "tools/call",
        "mcp.tool": tool,
        "input.value": value,
    }
    return _span(trace_id, span_id, f"{agent} mcp {tool}", SPAN_KIND_CLIENT, now, now + 1000, attrs)


def denied_response_span(trace_id: bytes, request_id: bytes, agent: str, host: str, tool: str) -> bytes:
    """The response half for an exchange this service refused: the row completes as denied."""
    now = time.time_ns()
    attrs = {
        "lineage.role": "response",
        "lineage.direction": "outbound",
        "lineage.exchange.id": request_id.hex(),
        "lineage.self.id": agent,
        "lineage.self.namespace": SELF_NAMESPACE,
        "lineage.protocol": "mcp",
        "lineage.peer.host": host,
        "lineage.outcome": "denied",
        "http.status_code": 403,
    }
    return _span(trace_id, secrets.token_bytes(8), f"{agent} mcp {tool} response", SPAN_KIND_CLIENT, now, now + 1000,
                 attrs, parent=request_id)


def _post_spans(spans: list[bytes]) -> None:
    request = urllib.request.Request(RECEIVER_URL, data=export_request(spans), method="POST",
                                     headers={"content-type": "application/x-protobuf"})
    with urllib.request.urlopen(request, timeout=3):
        pass


def _verdict(trace_hex: str, exchange_hex: str) -> dict[str, Any]:
    request = urllib.request.Request(f"{VERDICT_URL}/verdicts/{trace_hex}/{exchange_hex}?wait_ms={WAIT_MS}",
                                     headers={"authorization": f"Bearer {VERDICT_SECRET}"})
    with urllib.request.urlopen(request, timeout=WAIT_MS / 1000 + 3) as response:
        return json.load(response)


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _labels_in_words(labels: list[str]) -> str:
    words: list[str] = []
    decisive = [label for label in labels if label not in CONTEXT_ONLY_LABELS] or labels
    for label in decisive:
        word = LABEL_WORDS.get(label) or (label.split(".", 1)[1] + " information" if "." in label else label)
        if word not in words:
            words.append(word)
    return _join(words) if words else "sensitive data"


def _policy(rules: list[str], host: str) -> str:
    """What company policy states, for the rules that fired: one statement when they protect
    the same kind of destination ("personal data and restricted data must not be sent to an
    untrusted external destination such as <host>"), else each statement in turn."""
    statements = [RULE_POLICY[r] for r in rules if r in RULE_POLICY]
    if not statements:
        return f"sensitive data must not be sent to {host}"
    external = " must not be sent to an untrusted external destination"
    if all(st.endswith(external) for st in statements):
        kinds = [st[: -len(external)] for st in statements]
        return f"{_join(list(dict.fromkeys(kinds)))}{external} such as {host}"
    return _join(statements)


def _grounds(rules: list[str]) -> str:
    grounds: list[str] = []
    for rule in rules:
        ground = RULE_GROUNDS.get(rule, "sensitive data")
        if ground not in grounds:
            grounds.append(ground)
    return _join(grounds) if grounds else "sensitive data"


def judge(agent: str, connection: str, host: str, method: str, path: str, body: bytes,
          message: dict[str, Any]) -> dict[str, Any]:
    """dg's decision on one tools/call: {"allow": bool, "reason": str, "dg": {...}}.

    Fails closed: a span that cannot be sent, a verdict that does not come, or one Sentry's policy
    service cannot read, is a deny. The reason names the policy and the data, never a component.
    """
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    tool = str(params.get("name", ""))
    subject = f"{connection}.{tool} for agent {agent}"
    trace_id, span_id = secrets.token_bytes(16), secrets.token_bytes(8)
    key = {"trace_id": trace_id.hex(), "exchange_id": span_id.hex()}
    try:
        _post_spans([request_span(trace_id, span_id, agent, host, method, path, body, tool, params.get("arguments"))])
        answer = _verdict(key["trace_id"], key["exchange_id"])
    except (OSError, ValueError) as err:
        return {"allow": False, "dg": key,
                "reason": f"{subject} is denied: the data governance check is unavailable ({type(err).__name__}), so nothing allows it"}

    state, verdict = answer.get("state"), answer.get("verdict")
    info = {**key, "state": state, "verdict": verdict, "rules": answer.get("rules"), "labels": answer.get("labels")}
    if state == "decided" and verdict not in DENY_ON:
        return {"allow": True, "dg": info, "reason": f"{subject}: data governance found nothing the policy prohibits"}

    try:
        if state == "decided":
            what = _labels_in_words(answer.get("labels") or [])
            flagged = answer.get("redacted_payload")
            reason = (f"{subject} is denied: company policy states that {_policy(answer.get('rules') or [], host)}, "
                      f"and the request carries {what}" + (f": {flagged}" if flagged else ""))
        elif state == "unjudged":
            reason = f"{subject} is denied: data governance could not read the whole request ({answer.get('reason')})"
        else:
            reason = f"{subject} is denied: data governance gave no verdict in time, so nothing allows it"
        _post_spans([denied_response_span(trace_id, span_id, agent, host, tool)])
    except (OSError, ValueError):
        pass
    return {"allow": False, "dg": info, "reason": reason}
