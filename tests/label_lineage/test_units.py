"""The units of one payload (``label_lineage.units``): the containment fold and
anchoring, both drift branches included."""

from __future__ import annotations

from data_governance.label_lineage.units import Site, at_origin, units_of

SITE = Site("T", "ix", "response", "h")
RECORD = '{"name": "Alice Moreno", "ssn": "123-45-6789", "ssn_suffix": "6789"}'


def _f(entity_type: str, text: str, payload: str, nth: int = 0, **extra) -> dict:
    at = -1
    for _ in range(nth + 1):
        at = payload.index(text, at + 1)
    return {"entity_type": entity_type, "text": text, "start": at, "end": at + len(text),
            "regulatory_tags": ["PII"], "sensitivity_level": "RESTRICTED", **extra}


def test_the_record_holds_one_ssn_not_123_and_6789() -> None:
    units = units_of([_f("SSN", "123", RECORD), _f("SSN", "6789", RECORD, nth=1)], RECORD, SITE)
    (ssn,) = units
    assert ssn.value.canonical == "123-45-6789" and ssn.text == "123"


def test_equal_values_of_one_type_fold_into_one_unit() -> None:
    text = '{"a": "123-45-6789", "b": "123-45-6789"}'
    units = units_of([_f("SSN", "123-45-6789", text), _f("SSN", "123-45-6789", text, nth=1)], text, SITE)
    assert len(units) == 1 and units[0].value.start == text.index("123")


def test_different_types_never_fold() -> None:
    text = '{"ssn_suffix": "6789", "cvv": "6789"}'
    units = units_of([_f("SSN", "6789", text), _f("CVV", "6789", text, nth=1)], text, SITE)
    assert sorted(u.entity_type for u in units) == ["CVV", "SSN"]


def test_containment_is_bounded_so_a_shorter_number_glued_inside_stays_its_own() -> None:
    text = '{"a": "12345", "b": "234"}'
    units = units_of([_f("SSN", "12345", text), _f("SSN", "234", text, nth=1)], text, SITE)
    assert sorted(u.value.canonical for u in units) == ["12345", "234"]


def test_a_name_inside_a_longer_name_folds_into_it() -> None:
    text = "Alice Moreno, or Alice"
    units = units_of([_f("PN", "Alice Moreno", text), _f("PN", "Alice", text, nth=1)], text, SITE)
    assert [u.value.canonical for u in units] == ["Alice Moreno"]


def test_an_anchored_finding_keeps_its_offsets() -> None:
    (u,) = units_of([_f("PN", "Alice Moreno", RECORD)], RECORD, SITE)
    assert u.anchored and not u.relocated and RECORD[u.value.start:u.value.end] == "Alice Moreno"


def test_drift_relocated_when_the_text_occurs_exactly_once() -> None:
    finding = _f("PN", "Alice Moreno", RECORD)
    finding["start"] += 3
    finding["end"] += 3
    (u,) = units_of([finding], RECORD, SITE)
    assert u.anchored and u.relocated
    assert (u.value.start, u.value.end) == (RECORD.index("Alice"), RECORD.index("Alice") + 12)


def test_drift_unanchored_when_the_text_is_absent_or_ambiguous() -> None:
    absent = {"entity_type": "PN", "text": "Bob Okafor", "start": 0, "end": 10,
              "regulatory_tags": ["PII"], "sensitivity_level": "RESTRICTED"}
    text = "Alice and Alice"
    twice = {**_f("PN", "Alice", text), "start": 1, "end": 6}
    units = units_of([absent, twice], text, SITE)
    assert [u.anchored for u in units] == [False, False]
    assert all(not u.relocated for u in units)


def test_an_unanchored_unit_takes_no_part_in_the_fold() -> None:
    text = "Alice Moreno"
    stray = {**_f("PN", "Alice Moreno", text), "start": 2, "end": 14, "text": "Moreno Alice"}
    units = units_of([_f("PN", "Alice Moreno", text), stray], text, SITE)
    assert [(u.anchored, u.value.canonical) for u in units] == [(True, "Alice Moreno"), (False, "Moreno Alice")]


def test_a_finding_without_text_is_counted_unanchored() -> None:
    (u,) = units_of([{"entity_type": "PN", "text": "", "start": 0, "end": 0}], RECORD, SITE)
    assert not u.anchored and u.value.canonical == ""


def test_at_its_origin_a_unit_holds_its_whole_value_at_its_extent() -> None:
    (u,) = units_of([_f("SSN", "123", RECORD)], RECORD, SITE)
    occ = at_origin(u)
    assert occ.at == SITE and occ.span == (u.value.start, u.value.end)
    assert occ.held == "123-45-6789" and occ.source is None
