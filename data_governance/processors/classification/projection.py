"""The Text projection rule (issue #78, reshaped by #286).

Projects a **Payload**'s JSONB ``content`` into its single **Classifiable text** —
the natural-language string P-classification feeds the NER model. Detected
**Finding** offsets are char positions into the *projected* string, not into the
stored JSONB (CONTEXT.md **Classifiable text**).

The projection is a function of the content alone. A **Payload** is one row per
distinct bytes, referenced from every leg that carries them and classified once
(ADR-0024); the **Content kind** is a property of the reference — the role the
bytes played on one leg — and lives on ``interaction_legs`` (migration 0022). The
same bytes can be ``llm_completion`` on one leg and ``agent_response`` on
another, so a projection that dispatched on the kind would make "classified
once" depend on which leg happened to be written first. Instead the rule
recognises the shapes it knows structurally:

- a bare string is the text, verbatim (a completion, an a2a artifact, a tool
  result, a tool's argument string);
- the message-list envelope ``{"messages": [{"message.content": …}, …]}`` that
  the graph extractor writes for LLM exchanges — exactly that one key — projects
  to its message bodies joined by newlines: the prose, not the envelope. An
  envelope whose messages carry no text is serialised whole instead (an empty
  projection would classify nothing and hide the content; the old rule
  projected it to ``""``), and a dict with any other key beside ``messages`` is
  not the envelope and is serialised whole, so no sibling field can hide
  behind the message bodies;
- anything else is serialised whole to a canonical string — best-effort
  classification that also marks the projection-coverage gap
  (``projection_fallbacks_total``), exactly as before.
"""

from __future__ import annotations

import json
from typing import Any

# The message-body sub-key inside an LLM message dict. P-interactions splits
# ``llm.input_messages.N.message.content`` into a per-message dict keyed by the
# post-index remainder (``message.content``, ``message.role``, …), so the prose
# lives under this key.
_MESSAGE_CONTENT_KEY = "message.content"


def project(content: Any) -> str:
    """Project one **Payload**'s ``content`` into its **Classifiable text**.

    A function of the content alone (see the module docstring): the same bytes
    always project to the same text, whichever leg they are classified for.
    """
    if isinstance(content, str):
        return content
    bodies = _message_bodies(content)
    if bodies:
        return "\n".join(bodies)
    return _serialize_whole(content)


def is_projectable(content: Any) -> bool:
    """Whether *content* has a dedicated prose projection (a bare string or the
    message-list envelope) rather than taking the whole-JSONB fallback. The
    single source of truth for the projection-coverage signal: the driver counts
    a ``projection_fallbacks_total`` exactly when this is ``False``, so the
    metric can never drift from the rule — add a shape and both the projection
    and its coverage counter move together (issue #81 hook, #78)."""
    return isinstance(content, str) or bool(_message_bodies(content))


def _message_bodies(content: Any) -> list[str]:
    """The textual ``message.content`` bodies of the LLM message-list envelope
    ``{"messages": [...]}``; empty when *content* is not that envelope or none
    of its messages carries text (e.g. a pure tool-call turn)."""
    if not isinstance(content, dict) or set(content) != {"messages"}:
        return []
    if not isinstance(content["messages"], list):
        return []
    bodies: list[str] = []
    for message in content["messages"]:
        if not isinstance(message, dict):
            continue
        body = message.get(_MESSAGE_CONTENT_KEY)
        if isinstance(body, str) and body:
            bodies.append(body)
    return bodies


def _serialize_whole(content: Any) -> str:
    """The fallback: serialize the whole ``content`` to a canonical string
    (sorted keys, non-ASCII preserved) — deterministic so re-runs and dedup match.
    Best-effort; also the projection-coverage-gap signal."""
    return json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
