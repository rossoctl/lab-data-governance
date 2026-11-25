"""Label lineage across conversations (``label_lineage.cross`` and the fold with a
crossing): the configuration, the record of a value (R4), the match by text, the
interval (R1), the cap on edges per value (R2), the rule table and R7 — each by one small case
whose answer follows from the rule, never from a measurement."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json

import pytest

from data_governance.label_lineage import cross, find, fold
from data_governance.label_lineage.units import ProjectedPayload, Site, at_origin, units_of
from data_governance.processors.classification.projection import project
from data_governance.processors.data_lineage.traversal import Entity

SSN, DOB, MRN, OPID = "123-45-6789", "1961-03-14", "MRN-4471", "ext-99812"
T0 = dt.datetime(2026, 11, 13, 9, 0, tzinfo=dt.timezone.utc)
ON = cross.Crossing()


def _findings(text: str, *marks: tuple[str, str]) -> list[dict]:
    return [{"entity_type": t, "text": v, "start": text.index(v), "end": text.index(v) + len(v),
             "regulatory_tags": ["PII"], "sensitivity_level": "RESTRICTED"} for t, v in marks]


def _holdings(trace: str, content: object, *marks: tuple[str, str], at: dt.datetime = T0,
              entity: str = "v", leg: str = "ix-in") -> list[cross.Holding]:
    """What *entity* holds after receiving *content* classified with *marks* at *at*."""
    text = project(content)
    site = Site(trace, leg, "request", f"h-{trace}-{leg}")
    recs = cross.records(text) if isinstance(content, dict) else {}
    return [cross.Holding(entity, o, recs.get(o.span), at, at + ON.retention, "classified")
            for o in (at_origin(u) for u in units_of(_findings(text, *marks), text, site))]


def _cut(holdings, content: object, t: dt.datetime = T0 + dt.timedelta(minutes=5), *,
         trace: str = "T9", routed=(), crossing: cross.Crossing = ON, held: int = 0):
    """(crossings, capped values, the limits that cut the leg)."""
    output = ProjectedPayload(Site(trace, "ix-out", "response", "h-out"), project(content))
    found, capped, cuts = cross.leaving(cross.Memory(holdings), crossing, "v", output, t, routed, held)
    return sorted((o.matched_text, c.level, o.unit.origin.trace_id) for o, c in found), \
        [(m.value, m.conversations) for m in capped], cuts


def _leaving(holdings, content: object, t: dt.datetime = T0 + dt.timedelta(minutes=5), **kw):
    """(crossings, capped values) of a leg no limit cut."""
    found, capped, cuts = _cut(holdings, content, t, **kw)
    assert cuts == []
    return found, capped


# --- configuration -------------------------------------------------------------------------------------------------

def test_the_switch_is_off_unless_it_says_on():
    assert cross.configured({}) is None
    assert cross.configured({cross.SWITCH_ENV_VAR: "off"}) is None
    assert cross.configured({cross.SWITCH_ENV_VAR: "on"}) == cross.Crossing()


def test_the_defaults_are_30_edges_per_value_and_15_minutes():
    c = cross.Crossing()
    assert (c.edges_per_value, c.retention) == (30, dt.timedelta(minutes=15))
    assert {"SSN", "MRN", "IMEI", "MAC", "LPN", "CCA"} <= c.anchors
    assert not {"PN", "UN", "IP", "CN", "OPID", "DOB", "EMAIL", "PHONE"} & c.anchors


def test_the_configuration_reads_the_cap_retention_and_anchors():
    c = cross.configured({cross.SWITCH_ENV_VAR: "ON", cross.EDGES_PER_VALUE_ENV_VAR: "5",
                          cross.RETENTION_ENV_VAR: "60", cross.ANCHORS_ENV_VAR: "ssn, MRN"})
    assert c == cross.Crossing(anchors=frozenset({"SSN", "MRN"}), edges_per_value=5, retention=dt.timedelta(seconds=60))


def test_no_limit_unless_one_is_configured():
    assert (ON.holdings_limit, ON.payload_bytes, ON.candidates_limit) == (None, None, None)
    c = cross.configured({cross.SWITCH_ENV_VAR: "on", cross.HOLDINGS_LIMIT_ENV_VAR: "100",
                          cross.PAYLOAD_BYTES_ENV_VAR: "4096", cross.CANDIDATES_LIMIT_ENV_VAR: " ",})
    assert (c.holdings_limit, c.payload_bytes, c.candidates_limit) == (100, 4096, None)


@pytest.mark.parametrize("env", [
    {cross.SWITCH_ENV_VAR: "yes"},
    {cross.SWITCH_ENV_VAR: "on", cross.HOLDINGS_LIMIT_ENV_VAR: "0"},
    {cross.SWITCH_ENV_VAR: "on", cross.PAYLOAD_BYTES_ENV_VAR: "4k"},
    {cross.SWITCH_ENV_VAR: "on", cross.CANDIDATES_LIMIT_ENV_VAR: "-1"},
    {cross.SWITCH_ENV_VAR: "on", cross.EDGES_PER_VALUE_ENV_VAR: "0"},
    {cross.SWITCH_ENV_VAR: "on", cross.RETENTION_ENV_VAR: "15m"},
    {cross.SWITCH_ENV_VAR: "on", cross.ANCHORS_ENV_VAR: " , "},
    {cross.SWITCH_ENV_VAR: "on", cross.ANCHORS_ENV_VAR: "SSN, SNN"},
    {cross.SWITCH_ENV_VAR: "on", cross.RETENTION_ENV_VAR: "300000000000"},     # past the last date
    {cross.SWITCH_ENV_VAR: "on", cross.RETENTION_ENV_VAR: "100000000000000"},  # past the largest interval
    {cross.SWITCH_ENV_VAR: "on", cross.REMOVED_COMMON_LIMIT_ENV_VAR: "3"},      # the removed setting: refused, not ignored
    {cross.SWITCH_ENV_VAR: "off", cross.EDGES_PER_VALUE_ENV_VAR: "-1"},        # parsed with the switch off too
    {cross.ANCHORS_ENV_VAR: "SNN"},
    {cross.SWITCH_ENV_VAR: "off", cross.PAYLOAD_BYTES_ENV_VAR: "abc"},           # a limit, with the switch off too
])
def test_an_invalid_configuration_raises(env):
    with pytest.raises(cross.InvalidCrossing):
        cross.configured(env)


def test_an_unknown_anchor_is_named_with_where_the_label_types_are():
    with pytest.raises(cross.InvalidCrossing, match=r"'SNN', which the classifier does not label; a label type is "
                                                    r"one of the TAG column of .*EntityTypesForTokenClassification"):
        cross.configured({cross.SWITCH_ENV_VAR: "on", cross.ANCHORS_ENV_VAR: "ssn,snn"})
    with pytest.raises(cross.InvalidCrossing, match="leave it unset for the person identifiers"):
        cross.configured({cross.ANCHORS_ENV_VAR: ","})


def test_every_default_anchor_is_a_label_type_of_the_classifier():
    from data_governance.processors.classification.config import load_entity_metadata
    assert cross.DEFAULT_ANCHORS <= set(load_entity_metadata())


# --- records (R4) and words ----------------------------------------------------------------------------------------

def test_a_value_has_the_innermost_object_that_holds_it_as_a_member():
    text = project({"records": [{"ssn": SSN, "dob": DOB}, {"ssn": "987-65-4321", "dob": "1978-11-02"}], "n": 2})
    recs = cross.records(text)
    first, second = (recs[(text.index(v), text.index(v) + len(v))] for v in (SSN, "987-65-4321"))
    assert first != second
    assert recs[(text.index(DOB), text.index(DOB) + len(DOB))] == first
    assert text[first[0]] == "{" and text[first[1] - 1] == "}"


def test_a_key_an_array_element_or_part_of_a_string_has_no_record():
    inner = json.dumps({"ssn": SSN, "dob": DOB})
    text = project({"ssn": ["x", SSN], "content": [{"type": "text", "text": inner}], "note": f"ssn {SSN} on file"})
    recs = cross.records(text)
    assert all(text[s:e] != SSN for s, e in recs)
    assert all(text[s:e] != "ssn" for s, e in recs)


def test_a_number_member_has_a_record():
    text = project({"mrn": 4471, "x": "y"})
    assert (text.index("4471"), text.index("4471") + 4) in cross.records(text)


def test_the_core_runs_from_the_first_letter_or_digit_to_the_last_and_counts_its_words():
    assert cross.core("+1 555-0100") == ("1 555-0100", 3)
    assert cross.core('"Alice Moreno",') == ("Alice Moreno", 2)
    assert cross.core("123-45-6789") == ("123-45-6789", 3)
    assert cross.core("--") is None


def test_a_value_found_bounded_has_its_core_among_the_payload_windows():
    """A value the exact finder finds begins and ends at word boundaries of the payload, so its core is a run of
    its own number of consecutive payload words: hashing every run of 1..K words finds exactly those cores."""
    text = project({"phone": "+1 555-0100", "note": "Alice Moreno called; ssn 123-45-6789"})
    windows = cross.window_hashes(text, 3)
    for value in ("+1 555-0100", "Alice Moreno", "123-45-6789", "called; ssn"):
        assert cross.core_hash(cross.core(value)[0]) in windows, value
    for value in ("Alice", "555-0100", "123-45"):          # also windows: a held value is checked by found() after
        assert cross.core_hash(cross.core(value)[0]) in windows, value
    assert cross.core_hash("123-45-6789 x") not in windows
    assert cross.core_hash(cross.core("Alice Moreno called; ssn")[0]) not in windows      # four words: K is 3
    assert cross.core_hash("1961-03-14") not in windows
    assert cross.window_hashes("--", 3) == set()


# --- the match -----------------------------------------------------------------------------------------------------

def test_an_identifier_and_another_value_of_its_record_are_corroborated():
    held = _holdings("T1", {"ssn": SSN, "dob": DOB}, ("SSN", SSN), ("DOB", DOB))
    assert _leaving(held, {"subject": SSN, "since": DOB}) == (
        [(SSN, cross.CORROBORATED, "T1"), (DOB, cross.CORROBORATED, "T1")], [])


def test_an_identifier_alone_is_value_only():
    held = _holdings("T1", {"ssn": SSN, "dob": DOB}, ("SSN", SSN), ("DOB", DOB))
    assert _leaving(held, {"subject": SSN}) == ([(SSN, cross.VALUE_ONLY, "T1")], [])


def test_no_anchoring_identifier_draws_nothing():
    held = _holdings("T1", {"ssn": SSN, "dob": DOB, "ext": OPID}, ("SSN", SSN), ("DOB", DOB), ("OPID", OPID))
    assert _leaving(held, {"since": DOB, "ref": OPID}) == ([], [])


def test_values_from_two_records_do_not_corroborate():
    held = _holdings("T1", {"records": [{"ssn": SSN, "dob": DOB}, {"ssn": "987-65-4321", "dob": "1978-11-02"}]},
                     ("SSN", SSN), ("DOB", DOB), ("SSN", "987-65-4321"), ("DOB", "1978-11-02"))
    assert _leaving(held, {"subject": SSN, "since": "1978-11-02"}) == ([(SSN, cross.VALUE_ONLY, "T1")], [])


def test_without_a_record_an_identifier_is_value_only_and_nothing_else_crosses():
    held = _holdings("T1", f"patient {SSN} born {DOB}", ("SSN", SSN), ("DOB", DOB))
    assert all(h.record is None for h in held)
    assert _leaving(held, {"subject": SSN, "since": DOB}) == ([(SSN, cross.VALUE_ONLY, "T1")], [])


def test_the_label_type_at_the_received_leg_decides_the_anchor():
    held = _holdings("T1", {"external_id": MRN, "dob": DOB}, ("OPID", MRN), ("DOB", DOB))
    assert _leaving(held, {"account": MRN, "since": DOB}) == ([], [])


IBAN = "DE89370400440532013000"


def test_the_match_is_the_whole_bounded_value():
    held = _holdings("T1", {"ssn": SSN}, ("SSN", SSN))
    assert _leaving(held, {"subject": f"7788-{SSN}"}) == ([], [])
    assert _leaving(held, {"subject": f"x{SSN}"}) == ([], [])
    assert _leaving(held, {"subject": f"{SSN}x"}) == ([], [])          # bounded on the right too


def test_a_value_with_a_letter_or_digit_after_it_is_not_found():
    held = _holdings("T1", {"iban": IBAN}, ("IBAN", IBAN))
    assert _leaving(held, {"account": IBAN}) == ([(IBAN, cross.VALUE_ONLY, "T1")], [])
    assert _leaving(held, {"account": f"{IBAN}9"}) == ([], [])
    assert _leaving(held, {"account": f"{IBAN}x"}) == ([], [])


@pytest.mark.parametrize("offset, crosses", [
    (dt.timedelta(0), False), (dt.timedelta(seconds=1), True),
    (ON.retention - dt.timedelta(seconds=1), True), (ON.retention, False), (-dt.timedelta(seconds=1), False)])
def test_an_occurrence_is_valid_strictly_inside_its_interval(offset, crosses):
    held = _holdings("T1", {"ssn": SSN}, ("SSN", SSN))
    assert bool(_leaving(held, {"subject": SSN}, T0 + offset)[0]) is crosses


def test_one_value_from_two_conversations_is_two_edges():
    held = (_holdings("T1", {"ssn": SSN}, ("SSN", SSN))
            + _holdings("T2", {"ssn": SSN}, ("SSN", SSN), at=T0 + dt.timedelta(minutes=1)))
    assert _leaving(held, {"subject": SSN}) == ([(SSN, cross.VALUE_ONLY, "T1"), (SSN, cross.VALUE_ONLY, "T2")], [])


def _many(conversations: int) -> list[cross.Holding]:
    """The SSN held from *conversations* conversations, one second apart, three legs in the first."""
    return [h for i in range(conversations) for leg in (("a", "b", "c") if i == 0 else ("a",))
            for h in _holdings(f"T{i}", {"ssn": SSN}, ("SSN", SSN), at=T0 + dt.timedelta(seconds=i), leg=leg)]


def test_a_value_held_from_many_conversations_still_crosses():
    assert _leaving(_many(4), {"subject": SSN}) == (
        [(SSN, cross.VALUE_ONLY, "T0")] * 3 + [(SSN, cross.VALUE_ONLY, f"T{i}") for i in (1, 2, 3)], [])


def test_one_value_draws_at_most_the_cap_the_newest_holdings_first_and_says_so():
    two = dataclasses.replace(ON, edges_per_value=2)
    assert _leaving(_many(4), {"subject": SSN}, crossing=two) == (
        [(SSN, cross.VALUE_ONLY, "T2"), (SSN, cross.VALUE_ONLY, "T3")], [(SSN, 4)])
    six = dataclasses.replace(ON, edges_per_value=6)
    assert _leaving(_many(4), {"subject": SSN}, crossing=six)[1] == []        # exactly the cap: nothing cut
    assert _leaving(list(reversed(_many(4))), {"subject": SSN}, crossing=two) == _leaving(
        _many(4), {"subject": SSN}, crossing=two)                              # whatever order they are loaded in


def test_holdings_of_the_same_moment_are_cut_the_same_way_whatever_order_they_come_in():
    held = [h for t in ("Tb", "Ta", "Tc") for h in _holdings(t, {"ssn": SSN}, ("SSN", SSN))]     # one received time
    one = dataclasses.replace(ON, edges_per_value=1)
    for order in (held, list(reversed(held)), held[1:] + held[:1]):
        assert _leaving(order, {"subject": SSN}, crossing=one) == ([(SSN, cross.VALUE_ONLY, "Ta")], [(SSN, 3)])


def test_the_cap_keeps_the_surest_edge_and_the_rule_table_reads_every_holding_first():
    """The oldest of five holdings is the only one whose record comes back with it. The cap of one keeps that
    corroborated edge, not the newest value-only one: the level is set on everything found, and the cap comes after."""
    old = _holdings("T0", {"ssn": SSN, "dob": DOB}, ("SSN", SSN), ("DOB", DOB))
    newer = [h for i in range(1, 5) for h in _holdings(f"T{i}", {"ssn": SSN}, ("SSN", SSN), at=T0 + dt.timedelta(seconds=i))]
    one = dataclasses.replace(ON, edges_per_value=1)
    assert _leaving(old + newer, {"subject": SSN, "since": DOB}, crossing=one) == (
        [(SSN, cross.CORROBORATED, "T0"), (DOB, cross.CORROBORATED, "T0")], [(SSN, 5)])


def test_the_cap_ranks_by_the_level_the_edge_carries():
    """Both records come back, so the rule corroborates both SSN holdings; the newer one was itself crossed
    value-only, so its edge carries value-only. The cap of one keeps the older, which carries corroborated."""
    old = _holdings("T0", {"ssn": SSN, "dob": DOB}, ("SSN", SSN), ("DOB", DOB))
    newer = [dataclasses.replace(h, label_basis="crossed", level=cross.VALUE_ONLY) if h.value == SSN else h
             for h in _holdings("T1", {"ssn": SSN, "dob": DOB}, ("SSN", SSN), ("DOB", DOB),
                                at=T0 + dt.timedelta(seconds=1))]
    one = dataclasses.replace(ON, edges_per_value=1)
    assert _leaving(old + newer, {"subject": SSN, "since": DOB}, crossing=one) == (
        [(SSN, cross.CORROBORATED, "T0"), (DOB, cross.CORROBORATED, "T1")], [(SSN, 2), (DOB, 2)])


def test_two_origins_held_from_one_leg_are_cut_by_their_origin_whatever_order_they_come_in():
    """The same bytes received once as an SSN and once as an MRN tie on everything but their origin unit."""
    held = _holdings("T1", {"id": SSN}, ("SSN", SSN), ("MRN", SSN))
    output = ProjectedPayload(Site("T9", "ix-out", "response", "h-out"), project({"subject": SSN}))
    one = dataclasses.replace(ON, edges_per_value=1)
    for order in (held, held[::-1]):
        found = cross.leaving(cross.Memory(order), one, "v", output, T0 + dt.timedelta(minutes=5), ())[0]
        assert [o.unit.entity_type for o, _ in found] == ["MRN"]


def test_the_cap_is_per_value():
    held = _many(4) + _holdings("T1", {"mrn": "4471820"}, ("MRN", "4471820"), leg="m")
    one = dataclasses.replace(ON, edges_per_value=1)
    assert _leaving(held, {"subject": SSN, "ref": "4471820"}, crossing=one) == (
        [(SSN, cross.VALUE_ONLY, "T3"), ("4471820", cross.VALUE_ONLY, "T1")], [(SSN, 4)])


def test_a_value_held_from_many_conversations_corroborates_like_any_other():
    held = [h for i in range(4) for h in _holdings(f"T{i}", {"ssn": SSN, "dob": DOB}, ("SSN", SSN), ("DOB", DOB))]
    held = [h for h in held if h.value == DOB] + [h for h in held if h.trace_id == "T0" and h.value == SSN]
    assert _leaving(held, {"subject": SSN, "since": DOB}) == (
        [(SSN, cross.CORROBORATED, "T0"), (DOB, cross.CORROBORATED, "T0")], [])


def test_the_conversation_s_own_holdings_never_count():
    held = [h for i in range(3) for h in _holdings(f"T{i}", {"ssn": SSN}, ("SSN", SSN))]
    own = _holdings("T9", {"ssn": SSN}, ("SSN", SSN), leg="ix-own")
    assert _leaving(held + own, {"subject": SSN}, crossing=dataclasses.replace(ON, edges_per_value=1))[1] == [(SSN, 3)]


def test_an_own_holding_the_fold_already_routes_here_is_not_drawn_again():
    own = _holdings("T9", {"ssn": SSN}, ("SSN", SSN), leg="ix-own")
    assert _leaving(own, {"subject": SSN})[0] == [(SSN, cross.VALUE_ONLY, "T9")]
    assert _leaving(own, {"subject": SSN}, routed=[("ix-own", "request")]) == ([], [])


# --- the limits ------------------------------------------------------------------------------------

def test_over_the_holdings_limit_nothing_is_searched_and_the_leg_is_cut():
    held = _holdings("T1", {"ssn": SSN, "dob": DOB}, ("SSN", SSN), ("DOB", DOB))
    limited = dataclasses.replace(ON, holdings_limit=2)
    assert _cut(held, {"subject": SSN, "since": DOB}, crossing=limited, held=2)[2] == []
    assert _cut(held, {"subject": SSN, "since": DOB}, crossing=limited, held=3) == ([], [], ["holdings_per_entity"])


def test_only_the_first_payload_bytes_are_searched():
    held = _holdings("T1", {"ssn": SSN}, ("SSN", SSN))
    content = {"subject": SSN}                  # '{"subject": "123-45-6789"}': the value ends at character 24
    text = project(content)
    end = text.index(SSN) + len(SSN)
    at = dataclasses.replace(ON, payload_bytes=end)
    assert _cut(held, content, crossing=at) == ([(SSN, cross.VALUE_ONLY, "T1")], [], ["payload_bytes"])
    assert _cut(held, content, crossing=dataclasses.replace(ON, payload_bytes=end - 1)) == ([], [], ["payload_bytes"])
    assert _cut(held, content, crossing=dataclasses.replace(ON, payload_bytes=len(text)))[2] == []


def test_a_value_cut_at_the_byte_limit_keeps_the_whole_text_s_boundaries():
    held = _holdings("T1", {"ssn": SSN}, ("SSN", SSN))
    content = {"subject": f"{SSN}0"}            # a longer number: not the value, even if the limit ends after it
    text = project(content)
    limited = dataclasses.replace(ON, payload_bytes=text.index(SSN) + len(SSN))
    assert _cut(held, content, crossing=limited) == ([], [], ["payload_bytes"])


def test_over_the_candidates_limit_nothing_is_asserted():
    held = (_holdings("T1", {"ssn": SSN}, ("SSN", SSN))
            + _holdings("T2", {"ssn": SSN}, ("SSN", SSN), at=T0 + dt.timedelta(minutes=1)))
    assert _cut(held, {"subject": SSN}, crossing=dataclasses.replace(ON, candidates_limit=2))[2] == []
    assert _cut(held, {"subject": SSN}, crossing=dataclasses.replace(ON, candidates_limit=1)) == (
        [], [], ["candidates_per_payload"])


def test_the_candidates_limit_comes_before_the_cap():
    held = [h for i in range(4) for h in _holdings(f"T{i}", {"ssn": SSN}, ("SSN", SSN))]
    assert _cut(held, {"subject": SSN}, crossing=dataclasses.replace(ON, candidates_limit=1, edges_per_value=1)) == (
        [], [], ["candidates_per_payload"])


def test_the_search_finds_the_same_whatever_else_is_held():
    held = _holdings("T1", {"ssn": SSN, "dob": DOB}, ("SSN", SSN), ("DOB", DOB))
    others = [f"{i % 1000:03d}-{i % 97:02d}-{i:04d}" for i in range(1000, 3000)]
    noise = [h for v in others for h in _holdings("N", {"ssn": v}, ("SSN", v))]
    assert _leaving(held + noise, {"subject": SSN, "since": DOB}) == _leaving(held, {"subject": SSN, "since": DOB})


# --- the fold with a crossing --------------------------------------------------------------------------------------

ENTITIES = {e.id: e for e in (Entity("a", "agent:lab/a", "agent"), Entity("v", "service:partner", "service"),
                              Entity("t", "tool:lab/t", "tool"))}


def _trace(trace_id: str, legs: list[tuple[str, str, str, str, object, list | None]], t0: dt.datetime,
           entities=ENTITIES):
    """legs: (interaction, leg type, caller, callee, content, findings marks or None = unclassified)."""
    wire, payloads, findings = [], {}, {}
    for n, (ix, lt, caller, callee, content, marks) in enumerate(legs):
        h = f"h-{trace_id}-{n}"
        payloads[h] = content
        if marks is not None:
            findings[h] = _findings(project(content), *marks)
        wire.append(fold.WireLeg(ix, lt, n + 1, caller, callee, h, occurred_at=t0 + dt.timedelta(seconds=2 * n)))
    return fold.TraceInput(trace_id, wire, entities, payloads, findings)


RECORD = {"ssn": SSN, "dob": DOB}


def _first():
    return _trace("T1", [("1", "request", "a", "v", RECORD, [("SSN", SSN), ("DOB", DOB)]),
                         ("1", "response", "a", "v", {"ok": True}, [])], T0)


def _second(priors=()):
    t = _trace("T2", [("2", "request", "a", "v", {"q": "lookup"}, []),
                      ("2", "response", "a", "v", {"subject": SSN, "since": DOB}, []),
                      ("3", "request", "a", "t", {"subject": SSN, "since": DOB}, [])], T0 + dt.timedelta(minutes=5))
    return fold.TraceInput(t.trace_id, t.legs, t.entities, t.payloads, t.findings, priors=tuple(priors))


def test_what_a_persistent_entity_receives_is_registered_with_its_record_and_interval():
    d = fold.derive(_first(), find.exact, crossing=ON)
    assert sorted((h.holder_entity_id, h.value, h.label_basis, h.record is not None) for h in d.holdings) == [
        ("v", SSN, "classified", True), ("v", DOB, "classified", True)]
    assert {(h.valid_from, h.valid_to) for h in d.holdings} == {(T0, T0 + ON.retention)}


def _twice(records: list[dict], every: bool = True):
    """A first conversation in which v receives *records*, classified at every place a value sits, or at its
    first place only (what ``_findings`` marks)."""
    t = _trace("T1", [("1", "request", "a", "v", records, []), ("1", "response", "a", "v", {"ok": True}, [])], T0)
    text = project(records)
    places = [(kind, value, at) for kind, value in (("SSN", SSN), ("DOB", DOB))
              for at in range(len(text)) if text.startswith(value, at)]
    if not every:
        places = [next(p for p in places if p[0] == kind) for kind in ("SSN", "DOB")]
    t.findings[t.legs[0].payload_hash] = [
        {"entity_type": kind, "text": value, "start": at, "end": at + len(value), "regulatory_tags": ["PII"],
         "sensitivity_level": "RESTRICTED"} for kind, value, at in places]
    return t


@pytest.mark.parametrize("every", [True, False], ids=["classified-at-each-place", "classified-at-the-first"])
@pytest.mark.parametrize("records", [[{"ssn": SSN}, {"ssn": SSN, "dob": DOB}], [{"ssn": SSN, "dob": DOB}, {"ssn": SSN}]],
                         ids=["alone-first", "alone-last"])
def test_a_value_in_two_records_of_one_received_leg_is_held_in_each(records, every):
    """Equal findings are one origin, at its first place (and the classifier may mark that place only). Held there
    only, the identifier is alone in its record when that place is the record without the date, and the later
    conversation draws it value-only and drops the date: the answer followed the order of the records."""
    first = fold.derive(_twice(records, every), find.exact, crossing=ON)
    assert sum(len(os) for os in first.occurrences.values()) == 2      # the leg's own occurrences: one per value
    assert sorted((h.value, h.record is not None) for h in first.holdings) == [(SSN, True), (SSN, True), (DOB, True)]
    assert len({h.record for h in first.holdings}) == 2
    second = fold.derive(_second(first.holdings), find.exact, crossing=ON)
    assert sorted({(e.to.unit.entity_type, second.levels[e.key].level) for e in second.edges
                   if e.key in second.levels}) == [("DOB", cross.CORROBORATED), ("SSN", cross.CORROBORATED)]


def test_nothing_is_registered_without_the_switch():
    assert fold.derive(_first(), find.exact).holdings == ()


def test_a_finding_with_no_letter_or_digit_is_not_held():
    t = _trace("T1", [("1", "request", "a", "v", {"code": "--", "ssn": SSN}, [("SSN", "--"), ("SSN", SSN)]),
                      ("1", "response", "a", "v", {"ok": True}, [])], T0)
    assert [h.value for h in fold.derive(t, find.exact, crossing=ON).holdings] == [SSN]


def test_priors_are_refused_without_the_switch():
    with pytest.raises(ValueError, match="cross-conversation switch"):
        fold.derive(_second(fold.derive(_first(), find.exact, crossing=ON).holdings), find.exact)


def test_a_crossing_carries_the_earlier_origin_and_travels_on_as_an_ordinary_edge():
    d = fold.derive(_second(fold.derive(_first(), find.exact, crossing=ON).holdings), find.exact, crossing=ON)
    by = sorted((e.from_key, e.to_key, e.unit.entity_type, e.unit.origin.trace_id, e.from_trace_id,
                 d.levels[e.key].level if e.key in d.levels else None) for e in d.edges)
    assert by == [
        (("1", "request"), ("2", "response"), "DOB", "T1", "T1", cross.CORROBORATED),
        (("1", "request"), ("2", "response"), "SSN", "T1", "T1", cross.CORROBORATED),
        (("2", "response"), ("3", "request"), "DOB", "T1", None, None),
        (("2", "response"), ("3", "request"), "SSN", "T1", None, None),
    ]


def test_without_the_switch_the_second_conversation_is_its_own():
    d = fold.derive(_second(), find.exact)
    assert d.edges == () and d.levels == {} and d.capped == ()


def test_a_crossing_does_not_claim_a_finding_of_the_leg():
    first = fold.derive(_first(), find.exact, crossing=ON).holdings
    t = _second(first)
    findings = dict(t.findings)
    h = t.legs[1].payload_hash
    findings[h] = _findings(project(t.payloads[h]), ("SSN", SSN))
    t = fold.TraceInput(t.trace_id, t.legs, t.entities, t.payloads, findings, priors=t.priors)
    d = fold.derive(t, find.exact, crossing=ON)
    at_response = {(o.unit.origin.trace_id, o.unit.entity_type) for o in d.occurrences[("2", "response")]}
    assert at_response == {("T1", "SSN"), ("T1", "DOB"), ("T2", "SSN")}


def test_a_crossed_value_a_persistent_entity_receives_is_registered_as_crossed():
    first = fold.derive(_first(), find.exact, crossing=ON).holdings
    d = fold.derive(_second(first), find.exact, {"agent:lab/a": "persistent"}, crossing=ON)
    at = {(h.occurrence.at.leg, h.value, h.label_basis, h.level) for h in d.holdings if h.holder_entity_id == "a"}
    assert ((("2", "response"), SSN, "crossed", cross.CORROBORATED) in at)


def test_a_later_call_of_the_same_conversation_is_linked_once_and_one_call_stands_alone():
    t = _trace("T1", [("1", "request", "t", "v", {"ssn": SSN}, [("SSN", SSN)]),
                      ("1", "response", "t", "v", {"subject": SSN}, []),
                      ("2", "request", "t", "v", {"q": "again"}, []),
                      ("2", "response", "t", "v", {"subject": SSN}, [])], T0)
    plain = fold.derive(t, find.exact)
    d = fold.derive(t, find.exact, crossing=ON)
    assert [(e.from_key, e.to_key) for e in plain.edges] == [(("1", "request"), ("1", "response"))]
    assert sorted((e.from_key, e.to_key, d.levels.get(e.key) and d.levels[e.key].level) for e in d.edges) == [
        (("1", "request"), ("1", "response"), None),
        (("1", "request"), ("2", "response"), cross.VALUE_ONLY)]


def test_a_leg_without_an_event_time_is_neither_matched_nor_registered():
    t = _first()
    legs = [fold.WireLeg(x.interaction_id, x.leg_type, x.seq, x.caller_entity_id, x.callee_entity_id,
                         x.payload_hash, occurred_at=None) for x in t.legs]
    t = fold.TraceInput(t.trace_id, legs, t.entities, t.payloads, t.findings)
    assert fold.derive(t, find.exact, crossing=ON).holdings == ()


def test_every_leg_a_persistent_entity_sends_is_recorded_and_the_cut_legs_are_named():
    first = fold.derive(_first(), find.exact, crossing=ON).holdings
    d = fold.derive(_second(first), find.exact, crossing=ON)
    t2 = T0 + dt.timedelta(minutes=5, seconds=2)
    assert d.sends == (("v", ("2", "response"), t2),) and d.cuts == ()
    held_two = dataclasses.replace(_second(first), held_elsewhere={("2", "response"): 2})   # the driver counts
    limited = fold.derive(held_two, find.exact, crossing=dataclasses.replace(ON, holdings_limit=1))
    assert limited.cuts == ((("2", "response"), "holdings_per_entity"),) and limited.levels == {}
    assert fold.derive(_second(), find.exact).sends == ()


def test_the_holdings_limit_counts_other_conversations_and_the_conversation_s_own():
    t = _trace("T1", [("1", "request", "a", "v", RECORD, [("SSN", SSN), ("DOB", DOB)]),
                      ("1", "response", "a", "v", {"ok": True}, [])], T0)
    two = dataclasses.replace(ON, holdings_limit=2)
    assert fold.derive(t, find.exact, crossing=two).cuts == ()           # v holds its own two
    t = dataclasses.replace(t, held_elsewhere={("1", "response"): 1})
    assert fold.derive(t, find.exact, crossing=two).cuts == ((("1", "response"), "holdings_per_entity"),)


# --- the fold inside one conversation is unchanged -------------------------------------------------------------------

def test_candidates_from_inside_the_conversation_claim_exactly_as_before():
    text = '{"name": "Alice Moreno", "ssn": "123-45-6789", "ssn_suffix": "6789"}'
    store = Site("T", "ix-store", "response", "h-store")
    held = [at_origin(u) for u in units_of(_findings(text, ("SSN", SSN)), text, store)]
    out = ProjectedPayload(Site("T", "ix-out", "request", "h-out"), text)
    candidates = find.exact(held, out)
    own = units_of([{"entity_type": "SSN", "text": "6789", "start": text.rindex("6789"),
                     "end": text.rindex("6789") + 4}], text, out.site)
    assert fold.attribute(candidates, own) == fold.attribute(candidates, own, ())
    occs, _ = fold.attribute(candidates, own, ())
    assert [o.unit.origin for o in occs] == [store]
    occs, _ = fold.attribute([], own, candidates)
    assert sorted(o.unit.origin.interaction_id for o in occs) == ["ix-out", "ix-store"]


# --- the level on its way (R3) -------------------------------------------------------------------------

def _carried(d: fold.Derivation) -> list[tuple]:
    """Every edge: from leg, to leg, type, origin trace, the crossing's own level (rule table), and how the value
    is known at the to leg: (level, ambiguous) when it rests on a crossing, else None."""
    return sorted((e.from_key, e.to_key, e.unit.entity_type, e.unit.origin.trace_id,
                   d.levels[e.key].level if e.key in d.levels else None,
                   (d.carried[e.key].level, d.carried[e.key].ambiguous) if e.key in d.carried else None)
                  for e in d.edges)


def test_a_crossed_label_keeps_crossed_and_its_level_on_every_later_leg():
    d = fold.derive(_second(fold.derive(_first(), find.exact, crossing=ON).holdings), find.exact, crossing=ON)
    co = (cross.CORROBORATED, False)
    assert _carried(d) == [
        (("1", "request"), ("2", "response"), "DOB", "T1", cross.CORROBORATED, co),
        (("1", "request"), ("2", "response"), "SSN", "T1", cross.CORROBORATED, co),
        (("2", "response"), ("3", "request"), "DOB", "T1", None, co),
        (("2", "response"), ("3", "request"), "SSN", "T1", None, co),
    ]


@pytest.mark.parametrize("held_level, returned, rule, carried", [
    (cross.CORROBORATED, {"subject": SSN}, cross.VALUE_ONLY, cross.VALUE_ONLY),        # K17: co, then vo
    (cross.VALUE_ONLY, {"subject": SSN, "since": DOB}, cross.CORROBORATED, cross.VALUE_ONLY),  # vo, then co
])
def test_along_one_path_the_weakest_level_holds(held_level, returned, rule, carried):
    held = [dataclasses.replace(h, label_basis="crossed", level=held_level)
            for h in fold.derive(_first(), find.exact, crossing=ON).holdings]
    t = _second(held)
    h = t.legs[1].payload_hash
    t = fold.TraceInput(t.trace_id, t.legs, t.entities, {**t.payloads, h: returned}, t.findings, priors=t.priors)
    d = fold.derive(t, find.exact, crossing=ON)
    ssn = [row for row in _carried(d) if row[2] == "SSN"]
    assert [row[4:] for row in ssn] == [(rule, (carried, False)), (None, (carried, False))]


def test_several_crossed_origins_carry_the_strongest_level_and_are_ambiguous():
    held = (_holdings("T1", RECORD, ("SSN", SSN), ("DOB", DOB)) + _holdings("T0", {"ssn": SSN}, ("SSN", SSN)))
    d = fold.derive(_second(held), find.exact, crossing=ON)
    assert sorted(row[2:] for row in _carried(d) if row[4] is not None) == [
        ("DOB", "T1", cross.CORROBORATED, (cross.CORROBORATED, False)),
        ("SSN", "T0", cross.VALUE_ONLY, (cross.CORROBORATED, True)),
        ("SSN", "T1", cross.CORROBORATED, (cross.CORROBORATED, True)),
    ]
    assert sorted(row[2:] for row in _carried(d) if row[4] is None) == [
        ("DOB", "T1", None, (cross.CORROBORATED, False)),
        ("SSN", "T0", None, (cross.CORROBORATED, True)),
        ("SSN", "T1", None, (cross.CORROBORATED, True)),
    ]


USER = {**ENTITIES, "u": Entity("u", "user:alice", "user")}


def test_one_origin_through_two_carriers_is_not_ambiguous():
    first = _trace("T1", [("0", "request", "u", "a", {"ssn": SSN}, [("SSN", SSN)]),
                          ("1", "request", "a", "v", {"ssn": SSN}, []), ("1", "response", "a", "v", {"ok": 1}, []),
                          ("2", "request", "a", "v", {"ssn": SSN}, []), ("2", "response", "a", "v", {"ok": 1}, [])],
                   T0, USER)
    held = fold.derive(first, find.exact, crossing=ON).holdings
    assert len(held) == 2 and len({h.occurrence.unit.key for h in held}) == 1
    d = fold.derive(_second(held), find.exact, crossing=ON)
    at_return = [row for row in _carried(d) if row[1] == ("2", "response")]
    assert [row[4:] for row in at_return] == [(cross.VALUE_ONLY, (cross.VALUE_ONLY, False))] * 2


WITH_DOB, ALONE = {"ssn": SSN, "dob": DOB}, {"ssn": SSN}


# The projection sorts keys: {"since": .., "subject": ..} returns the DOB first, {"id": .., "since": ..} the SSN.
@pytest.mark.parametrize("priors", [list, lambda h: h[::-1]], ids=["as-registered", "reversed"])
@pytest.mark.parametrize("returned", [{"subject": SSN, "since": DOB}, {"id": SSN, "since": DOB}],
                         ids=["dob-first-in-return", "ssn-first-in-return"])
@pytest.mark.parametrize("records, level", [([WITH_DOB, ALONE], cross.CORROBORATED),
                                            ([ALONE, WITH_DOB], cross.CORROBORATED),
                                            ([ALONE, ALONE], cross.VALUE_ONLY)],
                         ids=["dob-record-first", "alone-first", "both-alone"])
def test_one_origin_held_twice_on_one_leg_crosses_at_its_strongest(records, level, returned, priors):
    """The SSN held twice on one received leg, in a record with its DOB and alone: both holdings cross onto one
    edge, and whatever the order, the strongest holds; of equal ones, the one first in the received payload."""
    first = _trace("Z1", [("0", "request", "u", "a", {"ssn": SSN}, [("SSN", SSN)]),
                          ("1", "request", "a", "v", records, [("DOB", DOB)] if WITH_DOB in records else []),
                          ("1", "response", "a", "v", {"ok": 1}, [])], T0, USER)
    held = fold.derive(first, find.exact, crossing=ON).holdings
    ssn = [h for h in held if h.value == SSN]
    assert len(ssn) == 2 and len({h.occurrence.unit.key for h in ssn}) == 1
    kept = next((h for h in ssn if any(x.value == DOB and x.record == h.record for x in held)),
                min(ssn, key=lambda h: h.occurrence.span))
    second = _trace("A2", [("2", "request", "a", "v", {"q": "lookup"}, []),
                           ("2", "response", "a", "v", returned, []),
                           ("3", "request", "a", "t", returned, [])], T0 + dt.timedelta(minutes=5), USER)
    d = fold.derive(dataclasses.replace(second, priors=tuple(priors(held))), find.exact, crossing=ON)
    (edge,) = [e for e in d.edges if e.unit.entity_type == "SSN" and e.key in d.levels]
    assert (d.levels[edge.key].level, d.levels[edge.key].holding) == (level, kept)
    assert sorted(d.carried[e.key].level for e in d.edges if e.unit.entity_type == "SSN") == [level, level]


def test_a_value_known_inside_the_conversation_too_is_certain_and_reads_inherited():
    """The same origin reaches a's request both inside the conversation (from the user) and by a crossing in a later
    call (R7): of the paths of one origin the best holds. The edge before the crossing is ordinary (K21's leg 6)."""
    t = _trace("T1", [("0", "request", "u", "a", {"ssn": SSN}, [("SSN", SSN)]),
                      ("1", "request", "a", "v", {"ssn": SSN}, []), ("1", "response", "a", "v", {"ok": 1}, []),
                      ("2", "request", "a", "v", {"q": "again"}, []), ("2", "response", "a", "v", {"subject": SSN}, []),
                      ("3", "request", "a", "t", {"subject": SSN}, [])], T0, USER)
    d = fold.derive(t, find.exact, crossing=ON)
    assert [(row[0], row[1], row[4], row[5]) for row in _carried(d)] == [
        (("0", "request"), ("1", "request"), None, None),
        (("0", "request"), ("3", "request"), None, None),
        (("1", "request"), ("2", "response"), cross.VALUE_ONLY, (cross.VALUE_ONLY, False)),
        (("2", "response"), ("3", "request"), None, None),
    ]


def test_a_later_leg_registers_a_crossed_value_as_crossed_with_its_level():
    first = fold.derive(_first(), find.exact, crossing=ON).holdings
    d = fold.derive(_second(first), find.exact, {"tool:lab/t": "persistent"}, crossing=ON)
    at = {(h.value, h.label_basis, h.level) for h in d.holdings if h.occurrence.at.leg == ("3", "request")}
    assert at == {(SSN, "crossed", cross.CORROBORATED), (DOB, "crossed", cross.CORROBORATED)}


def test_without_the_switch_nothing_is_carried():
    assert fold.derive(_second(), find.exact).carried == {}


def test_a_value_classified_one_leg_after_its_crossing_keeps_its_own_origin():
    """a crossed occurrence carried on inside the conversation claims no finding either; the
    classified origin and the crossed one both travel on, and a persistent receiver holds both."""
    first = fold.derive(_first(), find.exact, crossing=ON).holdings
    t = _trace("T2", [("2", "request", "a", "v", {"q": "lookup"}, []),
                      ("2", "response", "a", "v", {"subject": SSN, "since": DOB}, []),
                      ("3", "request", "a", "t", {"ssn": SSN, "since": DOB}, [("SSN", SSN)]),
                      ("3", "response", "a", "t", {"subject": SSN}, None)], T0 + dt.timedelta(minutes=5))
    t = fold.TraceInput(t.trace_id, t.legs, t.entities, t.payloads, t.findings, priors=tuple(first))
    d = fold.derive(t, find.exact, {"tool:lab/t": "persistent"}, crossing=ON)
    at_3 = {(o.unit.origin.trace_id, o.unit.entity_type) for o in d.occurrences[("3", "request")]}
    assert at_3 == {("T1", "SSN"), ("T1", "DOB"), ("T2", "SSN")}
    on = sorted((row[2], row[3], row[5]) for row in _carried(d) if row[1] == ("3", "response"))
    assert on == [("SSN", "T1", (cross.CORROBORATED, False)), ("SSN", "T2", None)]
    held = {(h.value, h.occurrence.unit.origin.trace_id, h.label_basis, h.level) for h in d.holdings
            if h.occurrence.at.leg == ("3", "request")}
    assert held == {(SSN, "T2", "classified", None), (SSN, "T1", "crossed", cross.CORROBORATED),
                    (DOB, "T1", "crossed", cross.CORROBORATED)}


def test_without_the_switch_a_carried_candidate_claims_as_before():
    t = _trace("T1", [("0", "request", "u", "a", {"ssn": SSN}, [("SSN", SSN)]),
                      ("1", "request", "a", "t", {"ssn": SSN}, [("SSN", SSN)])], T0, USER)
    for crossing in (None, ON):
        d = fold.derive(t, find.exact, crossing=crossing)
        assert {o.unit.origin.interaction_id for o in d.occurrences[("1", "request")]} == {"0"}


def test_a_holding_fed_by_two_crossings_begins_at_the_later():
    """V answers a twice in conversation A (clocked 25 s, then 20 s), each a crossing from what B gave it; a sends
    the value on to V, clocked 10 s. The holding rests on both crossings and begins at the later one, which is not
    the last one folded: it never begins before anything that fed it."""
    s = lambda n: T0 + dt.timedelta(seconds=n)
    t = _trace("A", [("a1", "request", "v", "a", {"subject": SSN}, []),
                     ("a1b", "request", "v", "a", {"subject": SSN}, []),
                     ("a2", "request", "a", "v", {"subject": SSN}, [])], T0)
    at, parent = {"a1": s(25), "a1b": s(20), "a2": s(10)}, {"a1b": "a1", "a2": "a1b"}
    legs = [dataclasses.replace(x, occurred_at=at[x.interaction_id], parent_interaction_id=parent.get(x.interaction_id))
            for x in t.legs]
    priors = _holdings("B", {"ssn": SSN}, ("SSN", SSN), at=T0)
    d = fold.derive(dataclasses.replace(t, legs=legs, priors=tuple(priors)), find.exact, crossing=ON)
    assert sorted(e.came_from.at.interaction_id for e in d.edges if e.to.at.interaction_id == "a2") == ["a1", "a1b"]
    assert [(h.occurrence.at.interaction_id, h.valid_from) for h in d.holdings] == [("a2", s(25))]
