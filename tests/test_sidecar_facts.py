"""Unit tests for the sidecar wire-contract vocabulary (`sidecar_facts`):
the static classify table, its mcp.method overrides, and content-kind
projection parity. Pure — no DB, no span objects.
"""

from __future__ import annotations

from data_governance.processors.classification import projection
from data_governance.sidecar_facts import CONTENT_KINDS, classify_attrs


def _attrs(direction, protocol, **extra):
    a = {"lineage.role": "request", "lineage.direction": direction,
         "lineage.protocol": protocol}
    a.update(extra)
    return a


def _content_kinds(k):
    return (k.req_content_kind, k.resp_content_kind)


# --- classify: the static facts table -------------------------------------

def test_classify_outbound_protocols():
    assert classify_attrs(_attrs("outbound", "mcp")).callee_kind == "tool"
    assert classify_attrs(_attrs("outbound", "inference")).callee_kind == "llm"
    assert classify_attrs(_attrs("outbound", "a2a")).callee_kind == "agent"
    assert classify_attrs(_attrs("outbound", "http")).callee_kind == "service"
    k = classify_attrs(_attrs("outbound", "mcp"))
    assert _content_kinds(k) == ("tool_call_arguments", "tool_call_result")
    assert k.caller_kind == "agent"


def test_classify_inbound_principal_makes_user_else_client():
    assert classify_attrs(
        _attrs("inbound", "a2a", **{"lineage.principal.sub": "alice"})
    ).caller_kind == "user"
    assert classify_attrs(_attrs("inbound", "a2a")).caller_kind == "client"


def test_classify_never_reads_a_body():
    """A bodyless request still classifies to a complete kind set (no input.value)."""
    k = classify_attrs(_attrs("outbound", "inference"))
    assert k.callee_kind == "llm"
    assert _content_kinds(k) == ("llm_chat_prompt", "llm_completion")


def test_unknown_protocol_falls_back_to_http():
    k = classify_attrs(_attrs("outbound", "grpc-something"))
    assert k.callee_kind == "service"
    assert _content_kinds(k) == (None, None)


def test_classify_mcp_method_overrides_content_kinds_only():
    """The contract's (direction, protocol[, mcp.method]) term: lifecycle and
    discovery methods re-label content kinds; entity kinds are untouched."""
    lifecycle = ("mcp_lifecycle_request", "mcp_lifecycle_result")
    discovery = ("tool_discovery_request", "tool_discovery_result")
    for method in (
        "initialize",
        "ping",
        "notifications/initialized",
        "logging/setLevel",
        # mcp-parser synthetic events for transport machinery (SSE GET open,
        # session DELETE) — claimed as protocol=mcp since the parser's
        # $transport framing; without the override an MCP session derives
        # three tools/call-kind roots instead of one.
        "$transport/stream",
        "$transport/terminate",
    ):
        k = classify_attrs(_attrs("outbound", "mcp", **{"mcp.method": method}))
        assert _content_kinds(k) == lifecycle, method
        assert (k.caller_kind, k.callee_kind) == ("agent", "tool"), method
    for method in ("tools/list", "resources/list", "prompts/list", "resources/templates/list"):
        k = classify_attrs(_attrs("outbound", "mcp", **{"mcp.method": method}))
        assert _content_kinds(k) == discovery, method
        assert (k.caller_kind, k.callee_kind) == ("agent", "tool"), method
    # inbound gets the same override; caller downgrade to client still applies
    k = classify_attrs(_attrs("inbound", "mcp", **{"mcp.method": "initialize"}))
    assert _content_kinds(k) == lifecycle
    assert k.caller_kind == "client"


def test_classify_mcp_unlisted_methods_stay_tool_call():
    """Never downgrade unknown traffic: tools/call, resources/read, sampling/*,
    an unknown method, and an absent mcp.method all keep tool_call_* kinds."""
    for method in ("tools/call", "resources/read", "sampling/createMessage", "future/unknown"):
        k = classify_attrs(_attrs("outbound", "mcp", **{"mcp.method": method}))
        assert _content_kinds(k) == ("tool_call_arguments", "tool_call_result"), method
    k = classify_attrs(_attrs("outbound", "mcp"))  # mcp.method absent (framing drift)
    assert _content_kinds(k) == ("tool_call_arguments", "tool_call_result")


def test_classify_bodyless_http_on_mcp_endpoint_is_lifecycle():
    """MCP transport plumbing the parser didn't claim (SSE opens, teardowns):
    protocol http, path /mcp or /sse, no input.value → lifecycle kinds. Entity
    kinds keep the plain http row (callee stays service)."""
    for path in ("/mcp", "/sse"):
        k = classify_attrs(_attrs("outbound", "http", **{"url.path": path}))
        assert _content_kinds(k) == ("mcp_lifecycle_request", "mcp_lifecycle_result"), path
        assert k.callee_kind == "service", path
    # a body present → not plumbing → plain http kinds
    k = classify_attrs(_attrs("outbound", "http", **{"url.path": "/mcp", "input.value": "{}"}))
    assert _content_kinds(k) == (None, None)
    # other bodyless http paths are untouched
    k = classify_attrs(_attrs("outbound", "http", **{"url.path": "/api/things"}))
    assert _content_kinds(k) == (None, None)


# --- content-kind parity ----------------------------------------------------

def test_content_kind_parity_every_emitted_kind_is_projectable():
    """Every content kind classify_attrs() can emit must be projectable by
    P-classification (no whole-JSONB fallback). Pins the vocabulary to the
    projector's branch set so the two cannot drift (ADR-0014)."""
    assert CONTENT_KINDS  # non-empty
    for ck in CONTENT_KINDS:
        assert projection.is_projectable(ck), ck


def test_missing_or_garbled_direction_raises():
    """lineage.direction is contract-unconditional: classify never defaults it.

    A fabricated "inbound" here would silently diverge from the derivation's
    own _direction() on the same span (audit 2026-08-02, PY-3)."""
    import pytest

    for bad in ({}, _attrs("", "a2a"), _attrs("both", "a2a"), _attrs("Inbound ", "a2a")):
        with pytest.raises(ValueError):
            classify_attrs(bad)
