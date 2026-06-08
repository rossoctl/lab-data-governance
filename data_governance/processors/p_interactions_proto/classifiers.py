"""Agentic-scope classifiers for the graph prototype. THROWAWAY.

Per ADR-0007, the current algorithm covers the **openinference** agentic
scope only. a2a and mcp are deferred — their classifiers are not used and
their spans remain White at this stage.

The classifier decides:

  - whether each openinference span is a protocol boundary (Black) or just
    plumbing that should remain Gray;
  - whether a boundary span represents BOTH the source and the target of a
    call (e.g. ``ClaudeAgentSDK.query`` carries both the request and the
    response in one span — Step 2.b duplicates the node);
  - the entity label to pool onto the resulting entity node.

Non-agentic scopes (httpx, starlette, …) are NOT classified at this stage.
Their spans remain White in the base graph and contribute no entities; their
attributes will be used later for enrichment.

Attribute sources are validated against:
  - asgi_telemetry_spans.md  (starlette/httpx — not used here)
  - openinference_telemetry_spans.md  (openinference layer)
"""

from __future__ import annotations

import dataclasses
from typing import Any

from data_governance.retrieval import Span


@dataclasses.dataclass
class AgenticClassification:
    """Result of classifying an agentic span.

    is_boundary:  True iff this span is a protocol boundary (caller or callee
                  side of an agentic protocol call) — Step 2.a colors the
                  node Black. False means the span is agentic plumbing —
                  the node stays Gray.
    is_combined:  True iff the boundary span represents both the source AND
                  the target of the same call (e.g. ClaudeAgentSDK.query).
                  Triggers Step 2.b duplication. Implies is_boundary.
    label:        Entity label to attach to the node. May be None.
    target_label: For combined spans, the label for the duplicate (target)
                  node. Ignored if is_combined is False.
    """

    is_boundary: bool
    is_combined: bool = False
    label: str | None = None
    target_label: str | None = None


def _attr(span: Span, key: str) -> Any:
    return (span.attributes or {}).get(key)


def _service(span: Span) -> str | None:
    return span.service_name or None


# ---------------------------------------------------------------------------
# openinference classifier
# ---------------------------------------------------------------------------


def _oi_kind(span: Span) -> str | None:
    return _attr(span, "openinference.span.kind")


def _llm_model(span: Span) -> str | None:
    raw = _attr(span, "llm.model_name") or _attr(span, "gen_ai.request.model")
    if not isinstance(raw, str):
        return None
    if "/" in raw:
        raw = raw.split("/", 1)[1]
    return raw or None


# Combined source-AND-target spans — Step 2.b will duplicate these nodes and
# add request+response Black edges between the original (source) and the
# duplicate (target).
_COMBINED_PREFIXES = (
    "openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query",
)


def classify_openinference(span: Span) -> AgenticClassification:
    """Classify an openinference span. Returns is_boundary + optional
    combined-span flag and labels."""

    name = span.name or ""
    svc = _service(span)

    # Combined source-and-target span (e.g. ClaudeAgentSDK.query).
    if any(name.startswith(p) for p in _COMBINED_PREFIXES):
        oi = _oi_kind(span)
        if oi == "LLM":
            model = _llm_model(span)
            target = f"llm:{model}" if model else "llm:?"
        elif oi == "TOOL":
            target = f"tool:{span.name}" if span.name else "tool:?"
        else:
            target = None
        return AgenticClassification(
            is_boundary=True, is_combined=True, label=svc, target_label=target,
        )

    # OpenInference LLM calls — caller-side boundary; the LLM endpoint is the
    # callee but not directly observed. Step 2.c synthesizes the unobserved
    # peer.
    oi = _oi_kind(span)
    if oi == "LLM":
        model = _llm_model(span)
        return AgenticClassification(is_boundary=True, label=svc or (f"llm:{model}" if model else None))

    # OpenInference TOOL calls — caller-side boundary for in-process tool
    # invocations.
    if oi == "TOOL":
        tool_name = span.name or None
        return AgenticClassification(
            is_boundary=True, label=svc or (f"tool:{tool_name}" if tool_name else None),
        )

    # Other openinference spans — plumbing. Gray-but-not-Black.
    return AgenticClassification(is_boundary=False, label=svc)


# ---------------------------------------------------------------------------
# Scope dispatch
# ---------------------------------------------------------------------------


# Per ADR-0007, only the openinference scope is recognised as agentic at this
# stage. a2a and mcp scope support is deferred.
_AGENTIC_PREFIXES: tuple[str, ...] = (
    "openinference.instrumentation.",
)


def is_agentic_scope(scope_name: str) -> bool:
    """Is this scope considered agentic? Only agentic scopes drive boundary
    detection in Step 2.a; other scopes' spans stay White."""
    return any(scope_name.startswith(p) for p in _AGENTIC_PREFIXES)


def get_agentic_classifier(scope_name: str):
    """Return the agentic classifier for a scope, or None if non-agentic."""
    if not is_agentic_scope(scope_name):
        return None
    if scope_name.startswith("openinference.instrumentation."):
        return classify_openinference
    return None
