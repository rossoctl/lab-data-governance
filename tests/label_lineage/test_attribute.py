"""The attribution pass (``fold.attribute``): one owner per span, the clauses in
order, one test each — plus the two cases where nothing is resolved: several
places in one output, and one span read as two types upstream."""

from __future__ import annotations

from data_governance.label_lineage import find, fold
from data_governance.label_lineage.summary import summarize
from data_governance.label_lineage.units import ProjectedPayload, Site, at_origin, units_of

STORE = Site("T", "ix-store", "response", "h-store")
CARRIER = Site("T", "ix-b", "request", "h-b")
OUT = Site("T", "ix-out", "request", "h-out")


def _findings(text: str, *marks: tuple[str, str, int]) -> list[dict]:
    out = []
    for entity_type, value, nth in marks:
        at = -1
        for _ in range(nth + 1):
            at = text.index(value, at + 1)
        out.append({"entity_type": entity_type, "text": value, "start": at, "end": at + len(value),
                    "regulatory_tags": ["PCI"] if entity_type == "CVV" else ["PII"],
                    "sensitivity_level": "RESTRICTED"})
    return out


def _held(site: Site, text: str, *marks: tuple[str, str, int]):
    return [at_origin(u) for u in units_of(_findings(text, *marks), text, site)]


def _attribute(inputs, text: str, *marks: tuple[str, str, int]):
    output = ProjectedPayload(OUT, text)
    candidates = [c for occs in inputs for c in find.exact(occs, output)]
    return fold.attribute(candidates, units_of(_findings(text, *marks), text, OUT))


RECORD = '{"name": "Alice Moreno", "ssn": "123-45-6789", "ssn_suffix": "6789"}'


def test_a_a_carriers_misread_finding_is_the_unit_confirmed() -> None:
    """The carrier's classifier marks only ``123`` of the number that reached it
    (inside the reached span), or only the suffix field (a value inside the value
    that reached): either way it is the store's SSN confirmed, not a new origin."""
    store = _held(STORE, RECORD, ("SSN", "123-45-6789", 0))
    for marks in ((("SSN", "123", 0),), (("SSN", "6789", 1),)):
        occs, edges = _attribute([store], RECORD, *marks)
        assert [(o.unit.origin, o.unit.value.canonical) for o in occs] == [(STORE, "123-45-6789")], marks
        assert len(edges) == 1


def test_a_a_finding_of_another_type_on_the_reached_span_is_not_claimed() -> None:
    store = _held(STORE, RECORD, ("SSN", "123-45-6789", 0))
    occs, _ = _attribute([store], RECORD, ("CVV", "6789", 1))
    assert sorted((o.unit.entity_type, o.unit.origin) for o in occs) == [("CVV", OUT), ("SSN", STORE)]


def test_b_among_nested_spans_of_one_type_the_longer_value_owns_the_span() -> None:
    text = "to Alice Moreno"
    inputs = [_held(STORE, "Alice Moreno", ("PN", "Alice Moreno", 0)),
              _held(CARRIER, "Alice", ("PN", "Alice", 0))]
    occs, edges = _attribute(inputs, text)
    assert [o.unit.value.canonical for o in occs] == ["Alice Moreno"]
    assert [e.came_from.at for e in edges] == [STORE]


def test_c_one_origin_through_two_carriers_is_two_edges_and_one_occurrence() -> None:
    store = _held(STORE, RECORD, ("SSN", "123-45-6789", 0))
    store_out = find.exact(store, ProjectedPayload(CARRIER, RECORD))
    carrier = [fold.attribute(store_out, ())[0][0]]
    occs, edges = _attribute([store, carrier], RECORD)
    assert len(occs) == 1
    assert sorted(e.from_key for e in edges) == sorted([STORE.leg, CARRIER.leg])
    assert {e.to.key for e in edges} == {occs[0].key}


def test_d_a_finding_nothing_reached_is_a_new_unit() -> None:
    store = _held(STORE, RECORD, ("PN", "Alice Moreno", 0))
    text = '{"name": "Alice Moreno", "ssn": "123-45-6789"}'
    occs, edges = _attribute([store], text, ("SSN", "123", 0))
    origins = sorted((o.unit.entity_type, o.unit.origin) for o in occs)
    assert origins == [("PN", STORE), ("SSN", OUT)]
    assert [e.unit.entity_type for e in edges] == ["PN"]


def test_a_number_larger_than_what_reached_is_a_new_unit() -> None:
    """A tool combines the halves of a number: the suffix that reached it is only
    the tail of the combined number, so exact match does not find it there; the
    classifier finds the combined number whole — a new origin."""
    suffix = _held(STORE, '{"b": "6789"}', ("SSN", "6789", 0))
    text = '{"ssn": "123-45-6789"}'
    occs, edges = _attribute([suffix], text, ("SSN", "123-45-6789", 0))
    assert [(o.unit.origin, o.unit.value.canonical) for o in occs] == [(OUT, "123-45-6789")]
    assert edges == []


def test_every_copy_in_the_output_is_its_own_occurrence_and_edge() -> None:
    store = _held(STORE, RECORD, ("SSN", "123-45-6789", 0))
    text = '{"to": "123-45-6789", "cc": "123-45-6789"}'
    occs, edges = _attribute([store], text)
    assert [o.span for o in occs] == [(8, 19), (29, 40)]
    assert len(edges) == 2 and {e.unit.origin for e in edges} == {STORE}


def test_one_span_read_as_two_types_upstream_keeps_both_labels() -> None:
    """The same four digits were read once as an SSN suffix and once as a card
    code in the input; the output holds them once. Two edges on one span, two
    labels on the leg — the ambiguity is recorded, not resolved."""
    upstream = '{"ssn_suffix": "6789", "cvv": "6789"}'
    inp = _held(CARRIER, upstream, ("SSN", "6789", 0), ("CVV", "6789", 1))
    occs, edges = _attribute([inp], '{"last4": "6789"}')
    assert {o.span for o in occs} == {(11, 15)}
    assert sorted(e.unit.entity_type for e in edges) == ["CVV", "SSN"]
    summary = summarize(OUT.leg, OUT.payload_hash, 1, classified=False, edges=edges)
    assert summary.inherited_tags_whole == ("PCI", "PII") and summary.origin_count == 2
