"""Priors: occurrences at another conversation's payloads joining this one's fold
(no database). Two hand-built traces through one store:

    T1  (earlier)   u -> a -> store  WRITE request  W = {"key": "k", "payload": {"ssn": S}}   S classified C
    T2  (later)     u -> a -> store  READ  request  {"key": "k"}
                         a <- store  READ  response P = {"ssn": S}                           classified or not
                         a -> partner request       E = {"payload": {"ssn": S}}             unclassified

The store received W in T1 and emits P in T2: a prior from the store (what it was
handed earlier) joins the legs the store emits in T2. What the fold must and must
not do with it is pinned here; the window and the loading of priors from the
tables are the driver's (``tests/processors/label_lineage/test_priors_load.py``).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json

import pytest

from data_governance.label_lineage import find, fold
from data_governance.processors.classification.projection import project
from data_governance.processors.data_lineage.traversal import Entity

S = "123-45-6789"
C = {"entity_type": "SSN", "regulatory_tags": ["PII"], "sensitivity_level": "RESTRICTED",
     "category": "person.govID"}
ENTITIES = {
    "u": Entity(id="u", natural_key="user:u", kind="user"),
    "a": Entity(id="a", natural_key="agent:a", kind="agent"),
    "store": Entity(id="store", natural_key="tool:store", kind="tool"),
    "partner": Entity(id="partner", natural_key="tool:partner", kind="tool"),
}
W = {"key": "k", "payload": {"ssn": S}}
P = {"ssn": S}
E = {"payload": {"ssn": S}}
T0 = dt.datetime(2026, 10, 18, 9, 0, tzinfo=dt.timezone.utc)

T1_WRITE = ("t1-store", "request")
T2_READ = ("t2-store", "response")
T2_EGRESS = ("t2-partner", "request")


def _hash(content: object) -> str:
    return "h-" + json.dumps(content, sort_keys=True)[:40].replace(" ", "")


def _finding(content: object) -> dict:
    text = project(content)
    at = text.index(S)
    return {**C, "start": at, "end": at + len(S), "text": S}


def _trace(trace_id: str, plan: list[tuple], start: dt.datetime, *, classified: set[str]) -> fold.TraceInput:
    legs, payloads, findings = [], {}, {}
    for seq, (ix, leg_type, caller, callee, content) in enumerate(plan, start=1):
        h = _hash(content)
        payloads[h] = content
        if h in classified:
            findings[h] = [_finding(content)]
        elif S not in project(content):
            findings[h] = []
        legs.append(fold.WireLeg(ix, leg_type, seq, caller, callee, h,
                                 occurred_at=start + dt.timedelta(milliseconds=seq)))
    return fold.TraceInput(trace_id=trace_id, legs=legs, entities=ENTITIES, payloads=payloads,
                           findings=findings)


def t1() -> fold.TraceInput:
    return _trace("T1", [
        ("t1-ua", "request", "u", "a", "save k"),
        ("t1-store", "request", "a", "store", W),
        ("t1-store", "response", "a", "store", {"written": True}),
        ("t1-ua", "response", "u", "a", "saved"),
    ], T0, classified={_hash(W)})


def t2(*, read_classified: bool = True, start: dt.datetime = T0 + dt.timedelta(minutes=10)) -> fold.TraceInput:
    return _trace("T2", [
        ("t2-ua", "request", "u", "a", "send k"),
        ("t2-store", "request", "a", "store", {"key": "k"}),
        ("t2-store", "response", "a", "store", P),
        ("t2-partner", "request", "a", "partner", E),
        ("t2-ua", "response", "u", "a", "sent"),
    ], start, classified={_hash(P)} if read_classified else set())


def priors_from(d: fold.Derivation, key: tuple[str, str], entity_id: str, at: dt.datetime) -> tuple[fold.Prior, ...]:
    occs = d.occurrences[key]
    (leg_site,) = {o.at for o in occs}
    return (fold.Prior(at=leg_site, occurrences=occs, entity_id=entity_id, occurred_at=at),)


def store_priors(at: dt.datetime = T0 + dt.timedelta(milliseconds=2)) -> tuple[fold.Prior, ...]:
    """What the store was handed in T1 (the write request), as a prior from the store."""
    return priors_from(fold.derive(t1(), find.exact), T1_WRITE, "store", at)


def into(d: fold.Derivation, key: tuple[str, str]) -> list[fold.Edge]:
    return [e for e in d.edges if e.to_key == key]


def crossing(d: fold.Derivation) -> list[fold.Edge]:
    return [e for e in d.edges if e.from_trace_id is not None]


def test_a_prior_from_the_entity_that_emits_a_leg_joins_that_leg() -> None:
    d = fold.derive(dataclasses.replace(t2(), priors=store_priors()), find.exact)
    (edge,) = crossing(d)
    assert edge.to_key == T2_READ and edge.from_key == T1_WRITE
    assert edge.from_trace_id == "T1" and edge.came_from.at.trace_id == "T1"
    assert edge.unit.origin.trace_id == "T1" and edge.match_kind == "whole" and edge.to.matched_text == S


def test_the_crossing_edge_names_its_trace_and_everything_after_it_names_the_origin() -> None:
    d = fold.derive(dataclasses.replace(t2(), priors=store_priors()), find.exact)
    foreign = [e for e in into(d, T2_EGRESS) if e.unit.origin.trace_id == "T1"]
    (edge,) = foreign
    assert edge.from_trace_id is None and edge.from_key == T2_READ  # the carrier is this trace's read
    assert edge.unit.origin.leg == T1_WRITE


def test_a_prior_joins_only_the_legs_its_entity_emits() -> None:
    d1 = fold.derive(t1(), find.exact)
    through_partner = priors_from(d1, T1_WRITE, "partner", T0)
    d = fold.derive(dataclasses.replace(t2(), priors=through_partner), find.exact)
    assert not crossing(d)  # the partner emits no payload-bearing leg in T2
    through_a = priors_from(d1, T1_WRITE, "a", T0)
    d = fold.derive(dataclasses.replace(t2(), priors=through_a), find.exact)
    assert {e.to_key for e in crossing(d)} == {T2_EGRESS}  # the agent emits the egress, not the read


def test_the_crossing_is_recorded_where_the_value_enters() -> None:
    """The store AND the agent held S in T1. In T2 the store's read response is
    where S enters (one crossing edge); the egress already gets S from that read
    through the agent, so the agent's prior adds no second crossing there."""
    d1 = fold.derive(t1(), find.exact)
    both = priors_from(d1, T1_WRITE, "store", T0) + priors_from(d1, T1_WRITE, "a", T0)
    d = fold.derive(dataclasses.replace(t2(), priors=both), find.exact)
    assert [e.to_key for e in crossing(d)] == [T2_READ]
    assert {e.from_key for e in into(d, T2_EGRESS) if e.unit.origin.trace_id == "T1"} == {T2_READ}


def test_a_prior_later_than_the_leg_does_not_join_it() -> None:
    late = store_priors(at=T0 + dt.timedelta(hours=1))
    d = fold.derive(dataclasses.replace(t2(), priors=late), find.exact)
    assert not crossing(d)
    untimed = tuple(dataclasses.replace(p, occurred_at=None) for p in store_priors())
    d = fold.derive(dataclasses.replace(t2(), priors=untimed), find.exact)
    assert not crossing(d)


def test_a_foreign_unit_never_claims_a_finding_of_this_conversation() -> None:
    """The classifier found S on T2's read response. Under exact match that
    finding is the same bytes T1 wrote, but it is T2's own origin: the prior's
    occurrence sits beside it on the same span, it does not confirm it. So the
    egress inherits S from T2's read (slice 1, unchanged) AND carries the path
    back into T1 (slice 2, lineage information)."""
    d = fold.derive(dataclasses.replace(t2(), priors=store_priors()), find.exact)
    origins = {(e.unit.origin.trace_id, e.unit.origin.leg) for e in into(d, T2_EGRESS)}
    assert origins == {("T2", T2_READ), ("T1", T1_WRITE)}
    assert {o.unit.origin.trace_id for o in d.occurrences[T2_READ]} == {"T1", "T2"}


def test_summaries_and_own_edges_never_depend_on_priors() -> None:
    """What this conversation inherited is the same with or without priors: the
    same-conversation edges and every ``LegSummary`` are identical; the cross
    edges are extra, and only the trace status counts them."""
    for classified in (True, False):
        alone = fold.derive(t2(read_classified=classified), find.exact)
        with_priors = fold.derive(dataclasses.replace(t2(read_classified=classified), priors=store_priors()), find.exact)
        own = lambda d: sorted(e.key for e in d.edges if e.unit.origin.trace_id == "T2")
        assert own(with_priors) == own(alone)
        assert with_priors.legs == alone.legs
        assert with_priors.status.edge_count == len(with_priors.edges) > alone.status.edge_count


def test_a_leg_whose_only_edges_cross_has_an_empty_summary() -> None:
    d = fold.derive(dataclasses.replace(t2(read_classified=False), priors=store_priors()), find.exact)
    assert into(d, T2_READ) and all(e.unit.origin.trace_id == "T1" for e in d.edges)
    for key in (T2_READ, T2_EGRESS):
        leg = d.legs[key]
        assert leg.inherited_tags_whole == () and leg.edge_count == 0 and leg.origin_count == 0
        assert leg.max_inherited_level is None
    assert d.status.edge_count == len(d.edges) == 2


def test_an_entity_carries_each_unit_once_from_its_latest_holding() -> None:
    """The agent said S twice in T1 (the write request, then a note a second
    later). In T2 the egress gets ONE crossing edge for S, from the later payload."""
    d1 = fold.derive(t1(), find.exact)
    first = priors_from(d1, T1_WRITE, "a", T0 + dt.timedelta(seconds=1))
    note = {"note": f"saved {S}"}
    later_site = fold.Site("T1", "t1-note", "request", _hash(note))
    text = project(note)
    unit = dataclasses.replace(first[0].occurrences[0].unit)  # the same origin, held again later
    later_occ = fold.Occurrence(unit=unit, at=later_site, span=(text.index(S), text.index(S) + len(S)),
                                held_from=0, held_to=len(S), matched_text=S)
    second = (fold.Prior(at=later_site, occurrences=(later_occ,), entity_id="a", occurred_at=T0 + dt.timedelta(seconds=2)),)
    d = fold.derive(dataclasses.replace(t2(), priors=first + second), find.exact)
    (edge,) = crossing(d)
    assert edge.from_key == ("t1-note", "request") and edge.to_key == T2_EGRESS
    d_rev = fold.derive(dataclasses.replace(t2(), priors=second + first), find.exact)
    assert [e.key for e in crossing(d_rev)] == [edge.key]


def test_a_prior_never_carries_this_conversations_own_units_back() -> None:
    """T2's own unit, seen at a T1-side payload (a later conversation that read T2
    and is now a prior of T2's re-derivation), is dropped: what came back through
    another conversation is not lineage information about this one."""
    d2 = fold.derive(t2(), find.exact)
    back = priors_from(d2, T2_EGRESS, "store", T0)  # occurrences whose unit is T2's own
    d = fold.derive(dataclasses.replace(t2(), priors=back), find.exact)
    assert not crossing(d) and d.legs == d2.legs and sorted(e.key for e in d.edges) == sorted(e.key for e in d2.edges)


def test_no_priors_is_slice_one() -> None:
    t = t2()
    assert fold.derive(dataclasses.replace(t, priors=()), find.exact) == fold.derive(t, find.exact)


def test_nesting_is_judged_within_one_conversation_class() -> None:
    """A longer foreign value never suppresses a shorter own one of the same type
    (slice 1's inheritance must not shrink when the window opens)."""
    name_t1 = _trace("T1", [("t1-store", "request", "a", "store", {"payload": {"name": "Alice Moreno"}})], T0,
                     classified=set())
    text = project({"payload": {"name": "Alice Moreno"}})
    at = text.index("Alice Moreno")
    f = {"entity_type": "PN", "regulatory_tags": ["PII"], "sensitivity_level": "CONFIDENTIAL",
         "category": "person.name", "start": at, "end": at + 12, "text": "Alice Moreno"}
    name_t1 = dataclasses.replace(name_t1, findings={_hash({"payload": {"name": "Alice Moreno"}}): [f]})
    d1 = fold.derive(name_t1, find.exact)
    prior = priors_from(d1, ("t1-store", "request"), "a", T0)  # the agent said it; the agent emits the egress
    read = {"name": "Alice"}
    egress = {"payload": {"name": "Alice Moreno"}}
    short = _trace("T2", [
        ("t2-store", "request", "a", "store", {"key": "k"}),
        ("t2-store", "response", "a", "store", read),
        ("t2-partner", "request", "a", "partner", egress),
    ], T0 + dt.timedelta(minutes=1), classified=set())
    rt = project(read)
    g = {**f, "start": rt.index("Alice"), "end": rt.index("Alice") + 5, "text": "Alice"}
    short = dataclasses.replace(short, findings={_hash(read): [g]})
    alone = fold.derive(short, find.exact)
    with_prior = fold.derive(dataclasses.replace(short, priors=prior), find.exact)
    assert alone.legs[T2_EGRESS].inherited_tags_whole == ("PII",)
    assert with_prior.legs == alone.legs
    assert {(e.unit.origin.trace_id, e.to.matched_text) for e in into(with_prior, T2_EGRESS)} == {
        ("T2", "Alice"), ("T1", "Alice Moreno")}


@pytest.mark.parametrize("row", ["R4", "R8"])
def test_the_replayed_fixtures_derive_identically_with_empty_priors(row: str) -> None:
    from tests.label_lineage.fixtures import trace_input

    t = trace_input(row)
    assert fold.derive(dataclasses.replace(t, priors=()), find.exact) == fold.derive(t, find.exact)
