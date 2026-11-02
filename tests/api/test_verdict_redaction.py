"""The redacted payload's rendering, finding by finding, no service or database.

The route's end to end cases are in ``test_verdict_route.py``."""

from __future__ import annotations

import json

import pytest

from data_governance.processors.classification.projection import project
from data_governance.risk.api import verdict_routes
from data_governance.risk.api.verdict_routes import _labels, _redact, _redacted_payload, _Unrenderable


def _finding(text: str, value: str, category: str = "person.govID", **over) -> dict:
    start = text.index(value)
    return {"entity_type": "PN", "category": category, "start": start, "end": start + len(value), "text": value, **over}


def test_each_finding_is_replaced_by_its_category() -> None:
    text = "name Rosa, ssn 123-45-6789, drug Lisinopril"
    findings = [_finding(text, "Rosa", "person.name"), _finding(text, "123-45-6789")]
    assert _redact(text, findings) == "name [person.name], ssn [person.govID], drug Lisinopril"


def test_the_order_of_the_findings_does_not_matter() -> None:
    text = "name Rosa, ssn 123-45-6789"
    findings = [_finding(text, "123-45-6789"), _finding(text, "Rosa", "person.name")]
    assert _redact(text, findings) == "name [person.name], ssn [person.govID]"


def test_a_finding_at_each_edge_of_the_text() -> None:
    text = "Rosa has 123-45-6789"
    findings = [_finding(text, "Rosa", "person.name"), _finding(text, "123-45-6789")]
    assert _redact(text, findings) == "[person.name] has [person.govID]"


def test_a_finding_that_is_the_whole_text() -> None:
    assert _redact("123-45-6789", [_finding("123-45-6789", "123-45-6789")]) == "[person.govID]"


def test_adjacent_findings_stay_two() -> None:
    text = "RosaLee"
    findings = [_finding(text, "Rosa", "a"), _finding(text, "Lee", "b")]
    assert _redact(text, findings) == "[a][b]"


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("Rosa Lee", "Lee Ann", "x [a] y"),  # overlapping
        ("Rosa Lee Ann", "Lee", "x [a] y"),  # nested
        ("Rosa", "Rosa Lee Ann", "x [b] y"),  # the same start: the longest names it
    ],
)
def test_overlapping_findings_merge_into_one_naming_the_first(first, second, expected) -> None:
    text = "x Rosa Lee Ann y"
    redacted = _redact(text, [_finding(text, first, "a"), _finding(text, second, "b")])
    assert redacted == expected
    assert "Rosa" not in redacted and "Lee" not in redacted and "Ann" not in redacted


def test_no_findings_is_the_text_as_it_is() -> None:
    assert _redact("clean", []) == "clean"


def test_a_tool_call_is_the_classifiers_own_text() -> None:
    content = {"ssn": "123-45-6789", "drug": "Lisinopril"}
    text = project(content)
    redacted = _redact(content, [_finding(text, "123-45-6789")])
    assert redacted == text.replace("123-45-6789", "[person.govID]")
    assert json.loads(redacted)["ssn"] == "[person.govID]"


def test_text_outside_the_basic_plane_counts_in_characters() -> None:
    text = "😀😀 ssn 123-45-6789 é"
    assert _redact(text, [_finding(text, "123-45-6789")]) == "😀😀 ssn [person.govID] é"


def test_an_empty_category_names_the_entity_type() -> None:
    text = "ssn 123"
    assert _redact(text, [_finding(text, "123", "", entity_type="ID")]) == "ssn [ID]"


def test_only_a_finding_is_replaced() -> None:
    """The classifier is the only judge: what it did not flag stays, here the second copy."""
    text = "123-45-6789 and 123-45-6789"
    finding = {"category": "person.govID", "start": 0, "end": 11, "text": "123-45-6789"}
    assert _redact(text, [finding]) == "[person.govID] and 123-45-6789"


@pytest.mark.parametrize(
    "finding",
    [
        {"category": "c", "start": 0, "end": 99, "text": "abc"},  # past the end
        {"category": "c", "start": -1, "end": 2, "text": "ab"},  # before the start
        {"category": "c", "start": 2, "end": 2, "text": ""},  # empty
        {"category": "c", "start": 3, "end": 2, "text": ""},  # reversed
        {"category": "c", "start": "0", "end": 3, "text": "abc"},  # not a number
        {"category": "c", "start": True, "end": 3, "text": "bc"},  # a bool is an int to Python
        {"category": "c", "start": 0, "end": 3, "text": "xyz"},  # drifted
        {"category": "c", "start": 0, "end": 3},  # no text
        {"start": 0, "end": 3, "text": "abc"},  # no name
        "not an object",
    ],
)
def test_a_finding_that_does_not_fit_gives_nothing(finding, caplog) -> None:
    good = {"category": "c", "start": 4, "end": 7, "text": "SEC"}
    with pytest.raises(_Unrenderable):
        _redact("abc SEC", [good, finding])
    assert _redacted_payload("i-1", "abc SEC", [good, finding]) is None, "never a partial text"
    assert "SEC" not in caplog.text and "abc" not in caplog.text, "the log names offsets, never text"


@pytest.mark.parametrize("findings", [None, {"a": 1}, "[]", 3])
def test_findings_that_are_not_a_list_give_nothing(findings) -> None:
    assert _redacted_payload("i-1", "abc", findings) is None


@pytest.mark.parametrize(
    "finding",
    [
        {"category": "c", "start": 4, "end": 99, "text": "SEC"},  # past the end; the slice still reads SEC
        {"category": "c", "start": -3, "end": 7, "text": "SEC"},  # before the start; the slice still reads SEC
    ],
)
def test_a_span_outside_the_text_is_refused_though_its_slice_matches(finding) -> None:
    with pytest.raises(_Unrenderable, match="is outside"):
        _redact("abc SEC", [finding])


def test_the_message_envelope_is_redacted_in_its_projection() -> None:
    content = {"messages": [{"message.role": "user", "message.content": "ssn 123-45-6789"},
                            {"message.role": "assistant", "message.content": "noted"}]}
    text = project(content)
    assert _redact(content, [_finding(text, "123-45-6789")]) == "ssn [person.govID]\nnoted"


def test_the_cap_counts_bytes_not_characters(monkeypatch) -> None:
    monkeypatch.setattr(verdict_routes, "REDACTED_MAX_BYTES", 12)
    with pytest.raises(_Unrenderable):
        _redact("\u05d0" * 6, [])  # 14 bytes encoded, 8 characters


def test_the_cap_is_on_the_encoded_size(monkeypatch) -> None:
    """Quotes double when encoded: the answer's size, not the text's length."""
    monkeypatch.setattr(verdict_routes, "REDACTED_MAX_BYTES", 12)
    assert _redact("12345678", []) == "12345678"  # 10 encoded
    with pytest.raises(_Unrenderable):
        _redact('""""""', [])  # 6 long, 14 encoded


def test_an_unexpected_fault_does_not_quote_the_payload(monkeypatch, caplog) -> None:
    def boom(_content):
        raise RuntimeError("SECRET-VALUE")

    monkeypatch.setattr(verdict_routes, "project", boom)
    assert _redacted_payload("i-1", "SECRET-VALUE", []) is None
    assert "SECRET-VALUE" not in caplog.text and "RuntimeError" in caplog.text


def test_labels_are_sorted_distinct_and_total() -> None:
    findings = [
        {"category": "b"}, {"category": "a"}, {"category": "b"},
        {"category": "", "entity_type": "ID"},
        {"category": 7, "entity_type": "NUM"},
        {"category": None},
        {},
        "not an object",
    ]
    assert _labels(findings) == ["ID", "NUM", "a", "b"]
    assert _labels(None) == [] and _labels({"a": 1}) == []
