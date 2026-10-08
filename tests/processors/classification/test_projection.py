"""The Text projection rule (issue #78; a function of the content alone since #286).

P-classification projects a **Payload**'s JSONB ``content`` into its single
**Classifiable text** — the natural-language string the NER model reads. The
**Content kind** is the referencing leg's, not the payload's (one row of bytes can
sit under two kinds on two legs), so the rule recognises shapes, not kinds: a bare
string is used verbatim; the LLM message-list envelope projects to its message
bodies; anything else falls back to serializing the whole ``content`` JSONB to a
canonical string — best-effort, and the coverage-gap signal.

The ``content`` shapes here are exactly what the two P-interactions algorithms
write into ``interaction_payloads.content``: the graph extractor's LLM envelope
``{"messages": [{"message.role": ..., "message.content": ...}, ...]}``, and the
sidecar derivation's raw ``input.value`` / ``output.value`` (a string or a JSON
structure).
"""

from __future__ import annotations

import json

from data_governance.processors.classification import projection


def test_message_list_concatenates_message_contents() -> None:
    """The LLM envelope projects to the concatenation of the messages'
    ``message.content`` bodies — the human-meaningful prose, not the JSON envelope."""
    content = {
        "messages": [
            {"message.role": "system", "message.content": "You are a helpful agent."},
            {"message.role": "user", "message.content": "My SSN is 123-45-6789."},
        ]
    }
    text = projection.project(content)
    assert "You are a helpful agent." in text
    assert "My SSN is 123-45-6789." in text
    # The JSON structure itself must not leak into the classifiable text.
    assert "message.role" not in text
    assert "{" not in text


def test_single_message_projects_to_its_body() -> None:
    content = {"messages": [{"message.role": "assistant", "message.content": "Booking confirmed."}]}
    assert projection.project(content) == "Booking confirmed."


def test_messages_without_content_are_skipped() -> None:
    """A message carrying no ``message.content`` (e.g. a pure tool-call turn)
    contributes nothing rather than a blank line — only real prose is projected."""
    content = {
        "messages": [
            {"message.role": "assistant", "message.tool_calls.0.tool_call.function.name": "search"},
            {"message.role": "user", "message.content": "Find flights to Tokyo."},
        ]
    }
    assert projection.project(content) == "Find flights to Tokyo."


def test_message_list_with_no_text_at_all_is_serialized_whole() -> None:
    """An envelope none of whose messages carries text would project to the
    empty string and classify nothing; it takes the whole-JSONB fallback instead
    so its content is still seen, and counts as a coverage gap."""
    content = {"messages": [{"message.role": "assistant", "tool": "search"}]}
    assert projection.project(content) == json.dumps(
        content, sort_keys=True, ensure_ascii=False
    )
    assert projection.is_projectable(content) is False


def test_bare_string_is_used_verbatim() -> None:
    """A completion, an a2a artifact, a tool result, an argument string: the
    raw value IS the text."""
    assert projection.project("Flight BA123 departs at 09:00") == "Flight BA123 departs at 09:00"
    assert projection.project('{"city": "Tokyo"}') == '{"city": "Tokyo"}'


def test_structured_content_is_serialized_canonically() -> None:
    """A JSON structure that is not the message envelope is canonically
    serialized so the classifier still sees its textual content; deterministic
    (sorted keys), so a re-run matches."""
    content = {"weird": {"nested": "SSN 123-45-6789"}, "passenger": "John Smith"}
    text = projection.project(content)
    assert "SSN 123-45-6789" in text
    assert "John Smith" in text
    assert text == projection.project(content) == json.dumps(
        content, sort_keys=True, ensure_ascii=False
    )


def test_the_sidecar_shapes_project_by_shape_alone() -> None:
    """The invariant behind one classification per ``content_hash`` (ADR-0024,
    #286): the projection takes no kind, so the same bytes referenced as an
    ``llm_completion`` on one leg and an ``agent_response`` on another project
    to one text. Pinned on the sidecar's real shapes — a bare string (completion
    = a2a artifact = tool result), a prompt list, a tool-calls list."""
    assert projection.project("The weather in Tokyo is sunny, 22C.") == (
        "The weather in Tokyo is sunny, 22C."
    )
    prompt = [{"role": "user", "content": "My SSN is 123-45-6789"}]
    assert projection.project(prompt) == json.dumps(prompt, sort_keys=True, ensure_ascii=False)
    calls = [{"name": "get_flights", "arguments": '{"to": "TLV"}'}]
    assert projection.project(calls) == json.dumps(calls, sort_keys=True, ensure_ascii=False)


def test_is_projectable_reflects_the_shapes_with_a_prose_projection() -> None:
    """``is_projectable`` is the single source of truth the driver's
    projection-coverage counter keys off: True for a shape with a real prose
    projection, False for everything that is serialized whole (so it can never
    drift from the fallback path; #81/#78)."""
    assert projection.is_projectable("plain text") is True
    assert projection.is_projectable({"messages": [{"message.content": "hi"}]}) is True
    assert projection.is_projectable({"body": "contact jane@doe.org"}) is False
    assert projection.is_projectable([{"role": "user", "content": "hi"}]) is False
    assert projection.is_projectable({}) is False
