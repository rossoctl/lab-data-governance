"""The sidecar wire-contract vocabulary — a pure facts table, no I/O.

One HTTP exchange through the AuthBridge sidecar emits TWO spans (request +
response) joined by ``lineage.exchange.id``; the request span's attributes carry
the classification facts (``docs/sidecar-wire-contract.md``, v1.1). This module
is the ONLY sidecar-vocabulary code: a static table over ``(direction,
protocol[, mcp.method])`` yielding caller/callee entity kinds and
request/response content kinds. It never reads a body: a bodyless exchange
(unparsed protocol, ``capture_io`` off, streamed response) still classifies to a
complete, first-class interaction with NULL payload hashes ("Interactions are
independent of payloads").

It is a leaf (stdlib-only) so both writers and readers share one meaning:

- the sidecar interactions algorithm
  (:mod:`data_governance.processors.interactions.sidecar`) classifies request
  spans at write time;
- the retrieval layer (:mod:`data_governance.retrieval.interactions`) re-derives
  kinds from the stored anchor span's attributes at read time, so content kinds
  follow this table's *current* vocabulary rather than a write-once snapshot.
"""

from __future__ import annotations

import dataclasses
from typing import Any

# (direction, protocol) -> (caller_kind, callee_kind, req_content_kind, resp_content_kind)
#
# The whole vocabulary of the derivation, on facts alone. `caller_kind` "user" is
# the inbound-entry default and is downgraded to "client" when the request has no
# validated principal (an anonymous caller — folded by peer ip in the caller
# derivation). Content kinds are None for `http` (no parser matched → no semantic
# body is ever produced → the payload columns stay NULL). Every non-None content
# kind here is projectable by processors/classification/projection.py — the
# parity test (test_content_kind_parity) pins that so this table cannot drift
# from the projector's branch set (ADR-0014: adding a content kind is a code
# change).
_KIND_TABLE: dict[tuple[str, str], tuple[str, str, str | None, str | None]] = {
    ("inbound", "a2a"): ("user", "agent", "agent_request", "agent_response"),
    ("inbound", "mcp"): ("user", "tool", "tool_call_arguments", "tool_call_result"),
    ("inbound", "inference"): ("user", "llm", "llm_chat_prompt", "llm_completion"),
    ("inbound", "http"): ("user", "agent", None, None),
    ("outbound", "a2a"): ("agent", "agent", "agent_request", "agent_response"),
    ("outbound", "mcp"): ("agent", "tool", "tool_call_arguments", "tool_call_result"),
    ("outbound", "inference"): ("agent", "llm", "llm_chat_prompt", "llm_completion"),
    ("outbound", "http"): ("agent", "service", None, None),
}

# The contract's optional third classify term — (direction, protocol[, mcp.method])
# — realized as content-kind overrides for MCP traffic that is protocol plumbing
# rather than a tool invocation. Overrides touch CONTENT KINDS ONLY: caller/callee
# kinds, entity derivation, and interaction ids are untouched, and every exchange
# still becomes a full first-class interaction (the UI hides these by default; it
# never drops them). Methods not listed here — tools/call, resources/read,
# sampling/*, anything unknown — keep the plain tool_call_* kinds: unknown traffic
# is never downgraded to noise.
_MCP_LIFECYCLE = ("mcp_lifecycle_request", "mcp_lifecycle_result")
_TOOL_DISCOVERY = ("tool_discovery_request", "tool_discovery_result")
_MCP_LIFECYCLE_METHODS = frozenset({"initialize", "ping"})
_MCP_LIFECYCLE_PREFIXES = ("notifications/", "logging/")
_TOOL_DISCOVERY_METHODS = frozenset(
    {"tools/list", "resources/list", "prompts/list", "resources/templates/list"}
)

# The content kinds this classifier can emit (for the projection parity test).
CONTENT_KINDS: frozenset[str] = frozenset(
    ck for row in _KIND_TABLE.values() for ck in row[2:4] if ck is not None
) | frozenset(_MCP_LIFECYCLE + _TOOL_DISCOVERY)


@dataclasses.dataclass(frozen=True)
class Kinds:
    """The static classification of one exchange (its request span)."""

    caller_kind: str
    callee_kind: str
    req_content_kind: str | None
    resp_content_kind: str | None


def _mcp_method_override(method: str) -> tuple[str, str] | None:
    if method in _MCP_LIFECYCLE_METHODS or method.startswith(_MCP_LIFECYCLE_PREFIXES):
        return _MCP_LIFECYCLE
    if method in _TOOL_DISCOVERY_METHODS:
        return _TOOL_DISCOVERY
    return None


def classify_attrs(attributes: dict[str, Any] | None) -> Kinds:
    """The classifier over a request span's bare attributes.

    The bodyless-/mcp branch is MCP transport plumbing the protocol parser
    didn't claim (SSE stream opens, session teardowns; live wire fact
    2026-07-26: those request spans carry no ``http.method`` at all).
    ``input.value`` is checked for *presence*, never read; with ``capture_io``
    off every /mcp http exchange matches — acceptable, since the override only
    re-labels content kinds and hides nothing at the data level.
    """
    a = attributes or {}
    direction = str(a.get("lineage.direction") or "").lower() or "inbound"
    proto = str(a.get("lineage.protocol") or "http").lower()
    protocol = proto if proto in ("a2a", "mcp", "inference") else "http"
    caller_kind, callee_kind, req_ck, resp_ck = _KIND_TABLE[(direction, protocol)]
    if protocol == "mcp":
        override = _mcp_method_override(str(a.get("mcp.method") or ""))
        if override is not None:
            req_ck, resp_ck = override
    elif protocol == "http" and (
        str(a.get("url.path") or "") in ("/mcp", "/sse")
        and a.get("input.value") is None
    ):
        req_ck, resp_ck = _MCP_LIFECYCLE
    if direction == "inbound" and not a.get("lineage.principal.sub"):
        caller_kind = "client"  # anonymous inbound caller; folded by peer ip
    return Kinds(caller_kind, callee_kind, req_ck, resp_ck)
