"""The Text projection rule (issue #78).

Projects a **Payload**'s JSONB ``content`` into its single **Classifiable text** —
the natural-language string P-classification feeds the NER model — with one branch
per **Content kind**. This is the read-side inverse of the P-interactions
**Payload extraction rule** (``processors/interactions/procedure.py``): that rule
canonicalizes span attributes into ``content`` per kind; this rule projects that
``content`` back out to the human-meaningful prose. Detected **Finding** offsets
are char positions into the *projected* string, not into the stored JSONB
(CONTEXT.md **Classifiable text**).

Like the extraction rule it mirrors, the branch set is a closed set enforced in
code (not a DB type) that churns as content kinds mature. ``unknown`` — and any
kind without a branch — falls back to serializing the whole ``content`` JSONB to a
canonical string: best-effort classification that also marks the
projection-coverage gap (filter ``content_kind`` where the fallback fired to
measure it), exactly as the extraction rule stores ``unknown`` payloads raw so
coverage stays measurable.
"""

from __future__ import annotations

import json
from typing import Any

# The message-body sub-key inside an LLM message dict. P-interactions splits
# ``llm.input_messages.N.message.content`` into a per-message dict keyed by the
# post-index remainder (``message.content``, ``message.role``, …), so the prose
# lives under this key.
_MESSAGE_CONTENT_KEY = "message.content"


def project(content_kind: str, content: Any) -> str:
    """Project one **Payload**'s ``content`` into its **Classifiable text**.

    Dispatches on *content_kind*. Known branches extract the human-meaningful
    prose; ``unknown`` and any unrecognised kind fall back to
    :func:`_serialize_whole` — the coverage-gap path.
    """
    branch = _BRANCHES.get(content_kind)
    if branch is None:
        return _serialize_whole(content)
    return branch(content)


def is_projectable(content_kind: str) -> bool:
    """Whether *content_kind* has a dedicated **Text projection rule** branch
    (rather than taking the whole-JSONB fallback). The single source of truth for
    the projection-coverage signal: the driver counts a ``projection_fallbacks_total``
    exactly when this is ``False`` (``unknown`` and any unbranched kind), so the
    metric can never drift from the branch set below — add a branch and both the
    projection and its coverage counter move together (issue #81 hook, #78)."""
    return content_kind in _BRANCHES


def _project_llm_messages(content: Any) -> str:
    """``llm_chat_prompt`` / ``llm_completion``: concatenate the messages'
    ``message.content`` bodies. Messages carrying no textual content (e.g. a pure
    tool-call turn) contribute nothing. Falls back to the whole-JSONB serialization
    if the content is not the expected ``{"messages": [...]}`` shape."""
    if not isinstance(content, dict) or not isinstance(content.get("messages"), list):
        return _serialize_whole(content)

    bodies: list[str] = []
    for message in content["messages"]:
        if not isinstance(message, dict):
            continue
        body = message.get(_MESSAGE_CONTENT_KEY)
        if isinstance(body, str) and body:
            bodies.append(body)
    return "\n".join(bodies)


def _project_tool_value(content: Any) -> str:
    """``tool_call_arguments`` / ``tool_call_result``: take the raw value as its
    string. A bare string is used verbatim; a JSON structure is canonically
    serialized so its textual content is still classifiable."""
    if isinstance(content, str):
        return content
    return _serialize_whole(content)


def _project_agent_message(content: Any) -> str:
    """``agent_request`` / ``agent_response``: an a2a message reduced by the
    AuthBridge lineage parser (``input.value`` / ``output.value``). Like a tool
    value it has no fixed sub-shape, so a bare string is used verbatim and a JSON
    structure is canonically serialized — its textual content stays classifiable.
    Added for the two-span sidecar derivation (ADR-0014: a new content kind is a
    code change to this closed branch set, not a migration)."""
    if isinstance(content, str):
        return content
    return _serialize_whole(content)


def _serialize_whole(content: Any) -> str:
    """The fallback: serialize the whole ``content`` to a canonical string
    (sorted keys, non-ASCII preserved) — deterministic so re-runs and dedup match.
    Best-effort; also the projection-coverage-gap signal for ``unknown`` and
    unbranched kinds."""
    if isinstance(content, str):
        return content
    return json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)


# The closed branch set, one per handled **Content kind**. Kinds absent here
# (``unknown``, ``agent_message``, ``http_request_body``, ``http_response_body``,
# …) take the whole-JSONB fallback until a branch is added — a code change, mirroring the
# Payload extraction rule. The ``agent_*`` pair is emitted by the two-span
# sidecar derivation; its content-kind parity test asserts every kind it can
# emit is present here.
_BRANCHES = {
    "llm_chat_prompt": _project_llm_messages,
    "llm_completion": _project_llm_messages,
    "tool_call_arguments": _project_tool_value,
    "tool_call_result": _project_tool_value,
    "agent_request": _project_agent_message,
    "agent_response": _project_agent_message,
    # MCP protocol plumbing (the classifier's (direction, protocol[, mcp.method])
    # term): same value shape as tool payloads — bare string verbatim, JSON
    # canonically serialized (ADR-0014 code-change branch additions).
    "mcp_lifecycle_request": _project_tool_value,
    "mcp_lifecycle_result": _project_tool_value,
    "tool_discovery_request": _project_tool_value,
    "tool_discovery_result": _project_tool_value,
}
