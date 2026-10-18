"""The three scenarios and the control, on a hand-built trace (no database).

One conversation: the user asks agent ``a``; ``a`` reads a record from the store
(payload ``p``, holding the string ``S`` classified ``C``, beside a string ``S'``
the classifier never marks), hands something to tool ``t`` (``p1``), takes the
tool's answer (``p2``) and sends something to the partner (``p3``):

    u -> a            "look up k1"
    a -> store  req   {"key": "k1"}
    a <- store  res   p       S classified C, S' unclassified
    a -> t      req   p1
    a <- t      res   p2
    a -> partner req  p3
    u <- a            "done"

(i)   p1 = p2 = p3 = p          same hash on consecutive legs; one origin throughout
(ii)  p2 = p3 = q               a different context holding S, classified
(iii) p2 = q, p3 = r            r holds S and is NOT classified: it must inherit C
control                         S' is never classified anywhere: no edge, anywhere
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from data_governance.label_lineage import find, fold
from data_governance.processors.classification.projection import project
from data_governance.processors.data_lineage.traversal import Entity

S = "123-45-6789"
S_PRIME = "987-65-4321"
C = {"entity_type": "SSN", "regulatory_tags": ["PII"], "sensitivity_level": "RESTRICTED",
     "category": "person.govID"}

ENTITIES = {
    "u": Entity(id="u", natural_key="user:u", kind="user"),
    "a": Entity(id="a", natural_key="agent:a", kind="agent"),
    "store": Entity(id="store", natural_key="tool:store", kind="tool"),
    "t": Entity(id="t", natural_key="tool:t", kind="tool"),
    "partner": Entity(id="partner", natural_key="tool:partner", kind="tool"),
}

P = {"name": "k1", "ssn": S, "ticket": S_PRIME}
Q = {"note": f"on file: {S}", "ref": S_PRIME}
R = {"payload": {"forwarded": S, "ticket": S_PRIME}}

STORE = ("ix-store", "response")
TOOL_REQ, TOOL_RES = ("ix-t", "request"), ("ix-t", "response")
EGRESS = ("ix-partner", "request")


def _finding(content: object, value: str) -> dict:
    text = project(content)
    at = text.index(value)
    return {**C, "start": at, "end": at + len(value), "text": value}


def _hash(content: object) -> str:
    return "h-" + json.dumps(content, sort_keys=True)[:24].replace(" ", "")


def trace(p1: object, p2: object, p3: object, *, classified: set[str]) -> fold.TraceInput:
    """The trace above with the given payloads; *classified* names the payloads
    (``p``, ``q``, ``r``) the classifier has a verdict for."""
    names = {_hash(P): "p", _hash(Q): "q", _hash(R): "r"}
    contents = {"u-req": "look up k1", "store-req": {"key": "k1"}, "done": "done"}
    plan = [
        ("ix-ua", "request", "u", "a", "u-req"),
        ("ix-store", "request", "a", "store", "store-req"),
        ("ix-store", "response", "a", "store", P),
        ("ix-t", "request", "a", "t", p1),
        ("ix-t", "response", "a", "t", p2),
        ("ix-partner", "request", "a", "partner", p3),
        ("ix-ua", "response", "u", "a", "done"),
    ]
    legs, payloads, findings = [], {}, {}
    t0 = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc)
    for seq, (ix, leg_type, caller, callee, body) in enumerate(plan, start=1):
        content = contents.get(body, body) if isinstance(body, str) else body
        h = _hash(content)
        payloads[h] = content
        if names.get(h) in classified:
            findings[h] = [_finding(content, S)]
        elif h not in names:
            findings[h] = []  # plumbing payloads: classified, nothing found
        legs.append(fold.WireLeg(ix, leg_type, seq, caller, callee, h,
                                 occurred_at=t0 + dt.timedelta(milliseconds=seq)))
    return fold.TraceInput(trace_id="T", legs=legs, entities=ENTITIES, payloads=payloads,
                           findings=findings)


SCENARIOS = {
    "i_same_hash": (P, P, P, {"p"}),
    "ii_other_context_classified": (P, Q, Q, {"p", "q"}),
    "iii_unclassified_inherits": (P, Q, R, {"p", "q"}),
}


def derive(name: str) -> fold.Derivation:
    p1, p2, p3, classified = SCENARIOS[name]
    return fold.derive(trace(p1, p2, p3, classified=classified), find.exact)


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_every_edge_carries_the_store_reads_unit_whole(name: str) -> None:
    d = derive(name)
    assert d.edges
    origins = {(e.unit.origin.interaction_id, e.unit.origin.leg_type, e.unit.origin.payload_hash,
                e.unit.value.start, e.unit.value.end) for e in d.edges}
    at = project(P).index(S)
    assert origins == {(*STORE, _hash(P), at, at + len(S))}
    assert {e.match_kind for e in d.edges} == {"whole"}
    assert {e.to.matched_text for e in d.edges} == {S}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_the_edges_follow_the_wire(name: str) -> None:
    d = derive(name)
    hops = sorted((e.from_key, e.to_key) for e in d.edges)
    assert hops == sorted([
        (STORE, TOOL_REQ),     # the agent hands what it read to the tool
        (TOOL_REQ, TOOL_RES),  # the tool's answer to its own request
        (STORE, EGRESS),       # the agent's memory: the store read ...
        (TOOL_RES, EGRESS),    # ... and the tool's answer, two carriers of one origin
    ])


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_the_spans_sit_where_the_bytes_are(name: str) -> None:
    p1, p2, p3, _ = SCENARIOS[name]
    d = derive(name)
    for e in d.edges:
        content = {TOOL_REQ: p1, TOOL_RES: p2, EGRESS: p3}[e.to_key]
        at = project(content).index(S)
        assert e.to.span == (at, at + len(S))


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_each_leg_summary_says_what_was_inherited(name: str) -> None:
    _, _, _, classified = SCENARIOS[name]
    d = derive(name)
    store = d.legs[STORE]
    assert store.classified and store.edge_count == 0 and store.inherited_tags_whole == ()
    for key, edges in ((TOOL_REQ, 1), (TOOL_RES, 1), (EGRESS, 2)):
        leg = d.legs[key]
        assert leg.inherited_tags_whole == ("PII",), key
        assert leg.inherited_tags_fragment == ()
        assert leg.max_inherited_level == "RESTRICTED"
        assert leg.origin_count == 1
        assert leg.edge_count == edges
        assert leg.recombined is False and leg.recombined_units == ()
    assert d.legs[EGRESS].classified is (name != "iii_unclassified_inherits")
    assert d.status.status == ("partial" if name == "iii_unclassified_inherits" else "complete")


def test_iii_the_unclassified_payload_holds_the_unit_and_passes_it_nowhere_new() -> None:
    d = derive("iii_unclassified_inherits")
    (occ,) = d.occurrences[EGRESS]
    assert occ.unit.entity_type == "SSN" and occ.unit.regulatory_tags == ("PII",)
    assert (occ.unit.origin.interaction_id, occ.unit.origin.leg_type) == STORE
    assert d.status.unclassified_legs == 1


def test_i_same_hash_legs_never_become_origins_of_their_own() -> None:
    """p is classified once (one verdict per hash), so the tool request, the
    tool's answer and the egress each carry the classifier's finding of S again —
    every one is the store read's unit confirmed, not a new origin."""
    d = derive("i_same_hash")
    for key in (TOOL_REQ, TOOL_RES, EGRESS):
        assert {(o.unit.origin.interaction_id, o.unit.origin.leg_type) for o in d.occurrences[key]} == {STORE}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_control_a_string_never_classified_travels_nowhere(name: str) -> None:
    d = derive(name)
    assert all(S_PRIME not in (e.to.matched_text or "") for e in d.edges)
    assert all(S_PRIME not in o.unit.value.canonical for occs in d.occurrences.values() for o in occs)
