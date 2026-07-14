"""The Text projection rule (issue #78).

P-classification projects a **Payload**'s JSONB ``content`` into its single
**Classifiable text** — the natural-language string the NER model reads — one
branch per **Content kind**, mirroring the **Payload extraction rule** it inverts
(CONTEXT.md). ``llm_chat_prompt`` / ``llm_completion`` concatenate the message
bodies; ``tool_call_arguments`` / ``tool_call_result`` take the result string;
``unknown`` (and any kind without a branch) falls back to serializing the whole
``content`` JSONB to a canonical string — best-effort, and the coverage-gap signal.

The ``content`` shapes here are exactly what the P-interactions **Payload
extraction rule** writes into ``interaction_payloads.content`` (see
``processors/interactions/procedure.py``): LLM payloads are
``{"messages": [{"message.role": ..., "message.content": ...}, ...]}``; tool
payloads are the raw ``input.value`` / ``output.value`` attribute (a string or a
JSON structure).
"""

from __future__ import annotations

import json

import pytest

from data_governance.processors.classification import projection


def test_llm_chat_prompt_concatenates_message_contents() -> None:
    """``llm_chat_prompt`` projects to the concatenation of the messages'
    ``message.content`` bodies — the human-meaningful prose, not the JSON envelope."""
    content = {
        "messages": [
            {"message.role": "system", "message.content": "You are a helpful agent."},
            {"message.role": "user", "message.content": "My SSN is 123-45-6789."},
        ]
    }
    text = projection.project("llm_chat_prompt", content)
    assert "You are a helpful agent." in text
    assert "My SSN is 123-45-6789." in text
    # The JSON structure itself must not leak into the classifiable text.
    assert "message.role" not in text
    assert "{" not in text


def test_llm_completion_concatenates_message_contents() -> None:
    """``llm_completion`` uses the same message-body concatenation branch as
    ``llm_chat_prompt`` (both are ``{"messages": [...]}``-shaped)."""
    content = {"messages": [{"message.role": "assistant", "message.content": "Booking confirmed."}]}
    assert projection.project("llm_completion", content) == "Booking confirmed."


def test_llm_messages_without_content_are_skipped() -> None:
    """A message carrying no ``message.content`` (e.g. a pure tool-call turn)
    contributes nothing rather than a blank line — only real prose is projected."""
    content = {
        "messages": [
            {"message.role": "assistant", "message.tool_calls.0.tool_call.function.name": "search"},
            {"message.role": "user", "message.content": "Find flights to Tokyo."},
        ]
    }
    assert projection.project("llm_chat_prompt", content) == "Find flights to Tokyo."


def test_tool_call_result_takes_the_result_string() -> None:
    """``tool_call_result`` projects the raw result value as its string — when the
    stored ``output.value`` is already a string it is used verbatim."""
    assert (
        projection.project("tool_call_result", "Flight BA123 departs at 09:00")
        == "Flight BA123 departs at 09:00"
    )


def test_tool_call_arguments_takes_the_argument_string() -> None:
    """``tool_call_arguments`` projects the raw argument value as its string."""
    assert projection.project("tool_call_arguments", '{"city": "Tokyo"}') == '{"city": "Tokyo"}'


def test_tool_call_result_stringifies_structured_content() -> None:
    """When a tool payload's value is a JSON structure (not a bare string), it is
    canonically serialized so the classifier still sees its textual content."""
    text = projection.project("tool_call_result", {"passenger": "John Smith", "seat": "12A"})
    assert "John Smith" in text
    assert "12A" in text


def test_unknown_kind_serializes_whole_content_as_fallback() -> None:
    """``unknown`` has no dedicated branch: it falls back to serializing the whole
    ``content`` JSONB to a canonical string — best-effort classification that
    still surfaces the text, and the projection-coverage gap signal."""
    content = {"weird": {"nested": "SSN 123-45-6789"}}
    text = projection.project("unknown", content)
    assert "SSN 123-45-6789" in text
    # Canonical serialization: deterministic (sorted keys), so a re-run matches.
    assert text == projection.project("unknown", content)


def test_kind_without_a_branch_uses_the_same_fallback() -> None:
    """A Content kind with no projection branch (e.g. a not-yet-implemented
    ``http_request_body``) takes the whole-JSONB fallback, exactly like
    ``unknown`` — the open-world coverage-gap path."""
    content = {"body": "contact jane@doe.org"}
    fallback = projection.project("http_request_body", content)
    assert "jane@doe.org" in fallback
    assert fallback == projection.project("unknown", content) == json.dumps(
        content, sort_keys=True, ensure_ascii=False
    )


def test_is_projectable_reflects_the_branch_set() -> None:
    """``is_projectable`` is the single source of truth the driver's
    projection-coverage counter keys off: True for a kind with a real branch,
    False for ``unknown`` and any unbranched kind (so it can never drift from the
    fallback path; #81/#78)."""
    assert projection.is_projectable("llm_chat_prompt") is True
    assert projection.is_projectable("tool_call_result") is True
    # Recognised Content kinds without a projection branch yet → fallback.
    assert projection.is_projectable("http_request_body") is False
    assert projection.is_projectable("agent_message") is False
    # The open-world case.
    assert projection.is_projectable("unknown") is False
