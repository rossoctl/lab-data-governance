"""Reading a finding's value (``label_lineage.value``): the extent rule that makes
exact matching precise, and the bounded search every finder shares."""

from __future__ import annotations

import pytest

from data_governance.label_lineage.value import MAX_DIGITS, Value, find_all, is_whole_value, read_value

RECORD = '{"name": "Alice Moreno", "phone": "+1 212 555 0147", "ssn": "123-45-6789", "ssn_suffix": "6789"}'


def _value(entity_type: str, text: str, found: str, nth: int = 0) -> Value:
    at = -1
    for _ in range(nth + 1):
        at = text.index(found, at + 1)
    return read_value(entity_type, text, at, at + len(found))


def test_the_classifier_marks_123_and_the_value_is_the_whole_number() -> None:
    v = _value("SSN", RECORD, "123")
    assert v.canonical == "123-45-6789"
    assert RECORD[v.start:v.end] == "123-45-6789"


def test_a_suffix_field_is_its_own_number() -> None:
    """The quote bounds the run: ``"6789"`` in the suffix field is four digits."""
    assert _value("SSN", RECORD, "6789", nth=1).canonical == "6789"


def test_a_leading_plus_belongs_to_the_number_and_quotes_do_not() -> None:
    assert _value("PHONE", RECORD, "1 212 555 0147").canonical == "+1 212 555 0147"
    assert _value("PHONE", RECORD, '"+1 212 555 0147').canonical == "+1 212 555 0147"


def test_two_separators_in_a_row_end_the_number() -> None:
    text = "call 212 555 0147 / 999 then"
    assert _value("PHONE", text, "555").canonical == "212 555 0147"


def test_a_run_longer_than_max_digits_keeps_the_finding_as_marked() -> None:
    run = "-".join(["1234"] * (MAX_DIGITS // 4 + 1))
    text = f"id {run} end"
    assert sum(c.isdigit() for c in run) > MAX_DIGITS
    assert _value("SSN", text, "1234").canonical == "1234"
    exactly = "1234-5678-9012-3456-789"  # MAX_DIGITS digits: still one number
    assert _value("PCN", f"card {exactly}.", "5678").canonical == exactly


def test_a_finding_marking_only_the_plus_reads_the_number_it_opens() -> None:
    text = '{"phone": "+1 212 555 0147"}'
    assert _value("PHONE", text, '"+').canonical == "+1 212 555 0147"
    assert _value("PHONE", '"phone": \\"+1 212 555 0147\\"', '\\"+').canonical == "+1 212 555 0147"


def test_ndc_is_read_as_a_number() -> None:
    assert _value("NDC", '{"ndc": "0002-3227-30"}', "0002").canonical == "0002-3227-30"


def test_a_numeric_finding_without_a_digit_has_no_value() -> None:
    text = 'see \\"+ here'
    v = _value("PHONE", text, '\\"+')
    assert v.canonical == ""


@pytest.mark.parametrize("found", ["Alice Moreno", "alice.moreno@example.com", "Alice", "O'Brien"])
def test_a_text_finding_is_its_own_extent(found: str) -> None:
    text = f'{{"who": "{found} wrote"}}'
    v = _value("PN", text, found)
    assert v.canonical == found and text[v.start:v.end] == found


def test_a_text_type_is_never_widened_over_digits() -> None:
    text = "Moreno 123"
    assert _value("PN", text, "Moreno").canonical == "Moreno"


@pytest.mark.parametrize("found, canonical", [
    ('"Alice Moreno"', "Alice Moreno"),
    (" Alice Moreno ", "Alice Moreno"),
    ("\u201cAlice\u201d", "Alice"),
    ("'O'Brien'", "O'Brien"),
])
def test_a_text_finding_loses_edge_quotes_and_whitespace_only(found: str, canonical: str) -> None:
    text = f"to {found} today"
    v = _value("PN", text, found)
    assert v.canonical == canonical and text[v.start:v.end] == canonical


def test_an_ip_address_and_an_age_are_read_as_numbers() -> None:
    assert _value("IP", "from 10.244.0.17 on", "244").canonical == "10.244.0.17"
    assert _value("AGE", '{"age": "42"}', "42").canonical == "42"


def test_find_all_finds_every_place_bounded_or_not() -> None:
    assert find_all("6789", "6789 and 123-45-6789 and 67890") == [(0, 4), (16, 20)]
    assert find_all("6789", "6789 and 67890", bounded=False) == [(0, 4), (9, 13)]
    assert find_all("alice", "malice") == []
    assert find_all("", "anything") == []


def test_a_number_inside_a_longer_number_is_not_a_whole_value() -> None:
    for text in ("x 123-45-6789 y", '"123-45-6789"'):
        at = text.index("123")
        assert is_whole_value("SSN", text, at, at + 11)
    for text in ("x 7788-123-45-6789 y", "x 123-45-6789-01 y"):
        at = text.index("123-45")
        assert not is_whole_value("SSN", text, at, at + 11)
    assert is_whole_value("PN", "x Alice Moreno-Smith", 2, 14)
