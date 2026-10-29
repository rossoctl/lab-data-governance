"""The matcher registry and the exact finder (``label_lineage.find``): byte equality
of the value's extent, bounded on both sides, every place it occurs. Nothing else."""

from __future__ import annotations

import dataclasses

import pytest

from data_governance.label_lineage import find
from data_governance.label_lineage.units import ProjectedPayload, Site, at_origin, units_of

ORIGIN = Site("T", "ix-store", "response", "h-in")
OUT = Site("T", "ix-out", "request", "h-out")


def _occ(entity_type: str, value: str, text: str | None = None):
    text = text or f'{{"v": "{value}"}}'
    at = text.index(value)
    (unit,) = units_of([{"entity_type": entity_type, "text": value, "start": at, "end": at + len(value),
                         "regulatory_tags": ["PII"], "sensitivity_level": "RESTRICTED"}], text, ORIGIN)
    return at_origin(unit)


def _found(occ, text: str) -> list[tuple[int, int]]:
    return [o.span for o in find.exact([occ], ProjectedPayload(OUT, text))]


def test_the_value_found_byte_for_byte_is_whole_with_score_one() -> None:
    occ = _occ("SSN", "123-45-6789")
    (found,) = find.exact([occ], ProjectedPayload(OUT, 'send {"ssn": "123-45-6789"}'))
    assert found.match_kind == "whole" and found.score == 1.0
    assert found.matched_text == "123-45-6789" and found.at == OUT
    assert found.unit is occ.unit and found.source is occ
    assert (found.held_from, found.held_to) == (0, len("123-45-6789"))


@pytest.mark.parametrize("output", [
    "ssn 123 45 6789",       # re-spaced: a structural transformation
    "ssn 123456789",         # separators dropped
    "ssn 123-45-6789"[:-1],  # a digit short
])
def test_a_reformatted_number_is_not_found(output: str) -> None:
    assert _found(_occ("SSN", "123-45-6789"), output) == []


@pytest.mark.parametrize("output", ["ALICE MORENO", "alice moreno", "Alice  Moreno"])
def test_a_re_cased_or_re_spaced_name_is_not_found(output: str) -> None:
    assert _found(_occ("PN", "Alice Moreno"), output) == []


def test_a_value_glued_to_a_digit_is_not_found() -> None:
    occ = _occ("SSN", "6789")
    assert _found(occ, "card 16789 and 67890") == []
    assert _found(occ, "card 1-6789") == []  # the tail of the number 1-6789
    assert _found(occ, "card 6789.") == [(5, 9)]


@pytest.mark.parametrize("output", ["ref 7788-123-45-6789", "ref 123-45-6789-01", "ref 9 123-45-6789"])
def test_a_number_found_inside_a_longer_number_is_not_the_unit(output: str) -> None:
    """The SSN is not found as the tail or the head of a longer number: the value
    is read at the output with the same extent rule as at its origin."""
    assert _found(_occ("SSN", "123-45-6789"), output) == []


def test_a_number_beside_punctuation_is_still_whole() -> None:
    occ = _occ("SSN", "123-45-6789")
    assert _found(occ, "ssn: 123-45-6789.") == [(5, 16)]
    assert _found(occ, '["123-45-6789", "x"]') == [(2, 13)]


def test_boundaries_are_letters_as_well_as_digits() -> None:
    occ = _occ("PN", "alice")
    assert _found(occ, "the malice is great") == []
    assert _found(occ, "alice.moreno@example.com") == [(0, 5)]
    assert _found(occ, '{"key": "alice"}') == [(9, 14)]
    assert _found(occ, "alice, moshe") == [(0, 5)]


def test_every_place_the_value_occurs_is_one_occurrence() -> None:
    occ = _occ("SSN", "123-45-6789")
    text = '{"a": "123-45-6789", "b": "x", "c": "123-45-6789"}'
    found = find.exact([occ], ProjectedPayload(OUT, text))
    assert [o.span for o in found] == [(7, 18), (37, 48)]
    assert all(o.unit is occ.unit for o in found)


def test_the_finder_searches_the_held_run_not_the_whole_value() -> None:
    """A carrier passes on only what it holds. In one conversation every
    occurrence holds the whole value; an occurrence holding a shorter run is
    searched for that run."""
    occ = dataclasses.replace(_occ("SSN", "123-45-6789"), held_from=7, held_to=11)
    assert occ.held == "6789"
    (found,) = find.exact([occ], ProjectedPayload(OUT, "last four 6789"))
    assert (found.held_from, found.held_to) == (7, 11) and found.matched_text == "6789"


def test_a_value_with_nothing_to_hold_is_found_nowhere() -> None:
    occ = _occ("PHONE", '"+', text='x "+ y')
    assert occ.held == ""
    assert _found(occ, 'x "+ y') == []


def test_the_registry_defaults_to_exact_and_refuses_an_unknown_name(monkeypatch) -> None:
    monkeypatch.delenv(find.LABEL_MATCHER_ENV_VAR, raising=False)
    assert find.configured_name() == "exact" and find.get_finder() is find.exact
    monkeypatch.setenv(find.LABEL_MATCHER_ENV_VAR, "exact")
    assert find.get_finder() is find.exact
    monkeypatch.setenv(find.LABEL_MATCHER_ENV_VAR, " Exact ")  # folded like RISK_INHERITED_LABELS
    assert find.get_finder() is find.exact
    monkeypatch.setenv(find.LABEL_MATCHER_ENV_VAR, "fuzzy")
    with pytest.raises(find.UnknownFinder, match="fuzzy"):
        find.get_finder()
    with pytest.raises(find.UnknownFinder):
        find.get_finder("verbatim")
