"""The fold over the replayed lab traces (``tests/fixtures/label_lineage``): per
catalogue row, what reaches the egress leg under the exact finder — the
``expected_label_propagation`` column of the live catalogue, as code. The findings
are the classifier's real ones (the SSN on the record is two findings, ``123`` and
``6789``; the redacted last four read as a card code)."""

from __future__ import annotations

import dataclasses

import pytest

from data_governance.label_lineage import find, fold
from tests.label_lineage.fixtures import FIXTURES, egress, legs_to, name, trace_input

ROWS = sorted(p.stem for p in FIXTURES.glob("R*.json"))
RECORD_UNITS = {("SSN", "123-45-6789"), ("PN", "Alice Moreno"), ("EMAIL", "alice.moreno@example.com"),
                ("PHONE", "+1 212 555 0147"), ("DOB", "1987-03-14")}


def derive(row: str) -> tuple[fold.TraceInput, fold.Derivation]:
    t = trace_input(row)
    return t, fold.derive(t, find.exact)


def into(d: fold.Derivation, leg: fold.WireLeg) -> list:
    return [e for e in d.edges if e.to_key == (leg.interaction_id, leg.leg_type)]


def origin_name(t: fold.TraceInput, edge) -> tuple[str, str]:
    """(callee of the origin's leg, origin leg type) — where the unit was found."""
    o = edge.unit.origin
    leg = next(x for x in t.legs if (x.interaction_id, x.leg_type) == (o.interaction_id, o.leg_type))
    return name(t, leg.callee_entity_id), o.leg_type


def from_store(t, edges) -> set[tuple[str, str]]:
    return {(e.unit.entity_type, e.unit.value.canonical) for e in edges
            if origin_name(t, e) == ("lab-store", "response") and e.match_kind == "whole"}


def own_tags(row: str, t: fold.TraceInput, leg: fold.WireLeg) -> set[str]:
    return {tag for f in t.findings[leg.payload_hash] for tag in f["regulatory_tags"]}


@pytest.mark.parametrize("row", ["R2", "R3", "R4", "R9"])
def test_the_record_reaches_the_egress_whole(row: str) -> None:
    t, d = derive(row)
    leg = egress(row, t)
    assert from_store(t, into(d, leg)) >= RECORD_UNITS
    summary = d.legs[(leg.interaction_id, "request")]
    assert "PII" in summary.inherited_tags_whole and summary.max_inherited_level == "RESTRICTED"


def test_R2_the_labels_travel_through_the_second_agent() -> None:
    """lab-a hands the record to lab-b (a2a), lab-b sends it: the egress's edges come
    from lab-b's inbound request, where the classifier read the suffix as a card
    code — the SSN still arrives because that request carried the unit."""
    t, d = derive("R2")
    edges = into(d, egress("R2", t))
    assert {name(t, next(x for x in t.legs if (x.interaction_id, x.leg_type) == e.from_key).callee_entity_id)
            for e in edges} == {"lab-b"}
    assert ("SSN", "123-45-6789") in from_store(t, edges)


def test_R5_the_combined_number_is_a_new_origin_and_reaches_the_egress_whole() -> None:
    t, d = derive("R5")
    (combined,) = legs_to("R5", t, "lab-tool-x", "response")
    # the suffix that reached the tool is only the tail of the combined number: not found there
    assert not [e for e in into(d, combined) if e.unit.entity_type == "SSN"]
    edges = [e for e in into(d, egress("R5", t)) if e.unit.entity_type == "SSN"]
    assert [(origin_name(t, e), e.unit.value.canonical, e.match_kind) for e in edges] == [
        (("lab-tool-x", "response"), "123-45-6789", "whole")]


def test_R9_the_llm_kept_every_identifier_and_the_record_reaches_the_egress() -> None:
    t, d = derive("R9")
    (completion,) = legs_to("R9", t, "qwen2.5:7b", "response")
    assert ("SSN", "123-45-6789") in from_store(t, into(d, completion))


def test_R7_the_re_spaced_number_inherits_nothing_from_the_store() -> None:
    """The tool re-spaces the SSN (``123 45 6789``): a structural transformation,
    which exact match does not read. Nothing from the store's SSN reaches the
    egress; the tool's own re-reading (the classifier's finding of ``123 45 6789``
    on the tool's answer) still reaches the egress. The spelled-out email likewise
    does not arrive; the unchanged fields do."""
    t, d = derive("R7")
    edges = into(d, egress("R7", t))
    reached = from_store(t, edges)
    assert ("SSN", "123-45-6789") not in reached and ("EMAIL", "alice.moreno@example.com") not in reached
    assert {("PN", "Alice Moreno"), ("DOB", "1987-03-14"), ("PHONE", "+1 212 555 0147")} <= reached
    respaced = [e for e in edges if e.unit.value.canonical == "123 45 6789"]
    assert respaced and {origin_name(t, e) for e in respaced} == {("lab-tool-x", "response")}


@pytest.mark.parametrize("row", ["R6b", "R8"])
def test_split_or_redacted_the_egress_inherits_nothing_the_classifier_did_not_see(row: str) -> None:
    """R6b splits the SSN across two fields, R8 keeps its last four: the store's
    SSN is a unit at its origin, and nothing from the store's SSN reaches the
    egress; the tool's own re-reading (the last four read as a card code) still
    reaches the egress — tags the egress's own classification already carries."""
    t, d = derive(row)
    store = next(leg for leg in legs_to(row, t, "lab-store", "response") if t.findings[leg.payload_hash])
    assert ("SSN", "123-45-6789") in {(o.unit.entity_type, o.unit.value.canonical)
                                      for o in d.occurrences[(store.interaction_id, "response")]}
    leg = egress(row, t)
    edges = into(d, leg)
    assert not [e for e in edges if e.unit.entity_type == "SSN"]
    summary = d.legs[(leg.interaction_id, "request")]
    assert "PII" not in summary.inherited_tags_whole and summary.inherited_tags_fragment == ()
    assert set(summary.inherited_tags_whole) <= own_tags(row, t, leg)
    assert summary.recombined is False and summary.recombined_units == ()


def test_R10_each_egress_carries_its_own_record_only() -> None:
    t, d = derive("R10")
    egresses = legs_to("R10", t, "api.partner.example:8000")
    ssns = sorted(tuple(sorted({e.unit.value.canonical for e in into(d, leg) if e.unit.entity_type == "SSN"}))
                  for leg in egresses)
    assert ssns == [("123-45-6789",), ("987-65-4321",)]


def test_R10_a_tools_answer_comes_from_its_own_request_under_two_callers() -> None:
    t, d = derive("R10")
    responses = legs_to("R10", t, "lab-tool-y", "response")
    assert len(responses) == 2
    for leg in responses:
        assert {e.from_key for e in into(d, leg)} == {(leg.interaction_id, "request")}


@pytest.mark.parametrize("row", ROWS)
def test_the_fold_is_deterministic_and_independent_of_input_order(row: str) -> None:
    t, d = derive(row)
    again = fold.derive(dataclasses.replace(t, legs=list(reversed(t.legs))), find.exact)
    assert again == d and d.status.status == "complete" and d.edges
    assert {e.match_kind for e in d.edges} == {"whole"}


def test_wire_order_not_seq_order_feeds_the_egress() -> None:
    """The egress leg's seq is LOWER than the store read that fed it (the
    interactions processor re-keys the tool entity after its echo); seq order would
    give the egress nothing."""
    t = trace_input("R6")
    leg = egress("R6", t)
    store = next(x for x in legs_to("R6", t, "lab-store", "response") if t.findings[x.payload_hash])
    assert leg.seq < store.seq and leg.occurred_at > store.occurred_at
    ordered = fold.wire_order(t.legs)
    assert ordered.index(store) < ordered.index(leg)


def test_a_leg_without_a_payload_is_routed_but_has_no_summary() -> None:
    t, d = derive("R6")
    without = {(x.interaction_id, x.leg_type) for x in t.legs if x.payload_hash is None}
    assert without and not (without & set(d.legs))
    assert d.status.derived_legs == len(t.legs) - len(without)


def test_an_unclassified_payload_makes_the_trace_partial_and_still_carries() -> None:
    t = trace_input("R4")
    (annotated,) = legs_to("R4", t, "lab-tool-x", "response")
    findings = {h: f for h, f in t.findings.items() if h != annotated.payload_hash}
    d = fold.derive(dataclasses.replace(t, findings=findings), find.exact)
    assert d.status.status == "partial" and d.status.unclassified_legs >= 1
    assert not d.legs[(annotated.interaction_id, "response")].classified
    assert ("SSN", "123-45-6789") in from_store(t, into(d, annotated))


def test_a_leg_without_a_wire_time_sorts_last() -> None:
    t = trace_input("R8")
    legs = [dataclasses.replace(x, occurred_at=None) if i == 3 else x for i, x in enumerate(t.legs)]
    assert fold.wire_order(legs)[-1].occurred_at is None
    assert fold.derive(dataclasses.replace(t, legs=legs), find.exact).status.status == "complete"


def test_priors_are_read_since_slice_two() -> None:
    """The slice-1 refusal is gone; what priors do is pinned in ``test_priors.py``."""
    t, d = derive("R8")
    assert fold.derive(dataclasses.replace(t, priors=()), find.exact) == d
