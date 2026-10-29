"""``traversal.inbound_sets``: the D1 routing + D2 memory core, shared by data
lineage (``seq`` order, legs before the D6 cutoff) and label lineage (wire order,
every leg). The traversal's own tests prove data lineage is unchanged; these pin
the function's contract and its one option, ``pair_responses``."""

from __future__ import annotations

from data_governance.processors.data_lineage.traversal import Entity, Leg, inbound_sets

ENTITIES = {
    "u": Entity(id="u", natural_key="user:u", kind="user"),
    "a": Entity(id="a", natural_key="agent:a", kind="agent"),
    "b": Entity(id="b", natural_key="agent:b", kind="agent"),
    "t": Entity(id="t", natural_key="tool:t", kind="tool"),
}


def _leg(ix: str, leg_type: str, seq: int, caller: str, callee: str, payload: bool = True) -> Leg:
    return Leg(ix, leg_type, seq, caller, callee, f"h{seq}" if payload else None)


def test_requests_reach_the_callee_and_responses_the_caller_in_the_order_given() -> None:
    legs = [_leg("ua", "request", 1, "u", "a"), _leg("at", "request", 2, "a", "t"),
            _leg("at", "response", 3, "a", "t")]
    sets = inbound_sets(legs, ENTITIES)
    assert sets[("ua", "request")] == ()
    assert sets[("at", "request")] == (("ua", "request"),)
    assert sets[("at", "response")] == (("at", "request"),)
    # walked backwards, the response comes first and reaches its caller first
    backwards = inbound_sets(list(reversed(legs)), ENTITIES)
    assert backwards[("at", "request")] == (("at", "response"),)


def test_an_agent_keeps_every_prior_and_a_tool_only_its_latest() -> None:
    legs = [_leg("ua", "request", 1, "u", "a"),
            _leg("t1", "request", 2, "a", "t"), _leg("t1", "response", 3, "a", "t"),
            _leg("t2", "request", 4, "a", "t"), _leg("t2", "response", 5, "a", "t"),
            _leg("ua", "response", 6, "u", "a")]
    sets = inbound_sets(legs, ENTITIES)
    assert sets[("t2", "request")] == (("ua", "request"), ("t1", "response"))
    assert sets[("t2", "response")] == (("t2", "request"),)
    assert sets[("ua", "response")] == (("ua", "request"), ("t1", "response"), ("t2", "response"))


def test_paired_responses_answer_their_own_request_under_two_callers() -> None:
    """Two agents call one tool; the first answer lands after the second question."""
    legs = [_leg("bt", "request", 1, "b", "t"), _leg("at", "request", 2, "a", "t"),
            _leg("bt", "response", 3, "b", "t"), _leg("at", "response", 4, "a", "t")]
    assert inbound_sets(legs, ENTITIES)[("bt", "response")] == (("at", "request"),)
    paired = inbound_sets(legs, ENTITIES, pair_responses=True)
    assert paired[("bt", "response")] == (("bt", "request"),)
    assert paired[("at", "response")] == (("at", "request"),)


def test_pairing_leaves_an_accumulating_callee_alone() -> None:
    legs = [_leg("ab", "request", 1, "a", "b"), _leg("ab", "response", 2, "a", "b"),
            _leg("ab2", "request", 3, "a", "b"), _leg("ab2", "response", 4, "a", "b")]
    paired = inbound_sets(legs, ENTITIES, pair_responses=True)
    assert paired[("ab2", "response")] == (("ab", "request"), ("ab2", "request"))


def test_an_unknown_producer_gets_no_entry_but_its_leg_is_still_routed() -> None:
    legs = [_leg("xa", "request", 1, "x", "a"), _leg("at", "request", 2, "a", "t")]
    sets = inbound_sets(legs, ENTITIES)
    assert ("xa", "request") not in sets
    assert sets[("at", "request")] == (("xa", "request"),)


def test_a_leg_without_a_payload_is_routed_like_any_other() -> None:
    """The caller decides which legs are routed: label lineage passes them all."""
    legs = [_leg("at", "request", 1, "a", "t", payload=False), _leg("at", "response", 2, "a", "t")]
    assert inbound_sets(legs, ENTITIES)[("at", "response")] == (("at", "request"),)


def test_data_lineage_keeps_d2_and_does_not_pair_responses() -> None:
    """The traversal calls the shared helper without ``pair_responses``: under two
    callers a tool's response still merges over the latest request the tool saw
    (ADR-0028 D2 verbatim), which is what data lineage specifies."""
    from data_governance.matching.simple import simple_match
    from data_governance.processors.data_lineage.traversal import derive_trace_lineage

    legs = [_leg("bt", "request", 1, "b", "t"), _leg("at", "request", 2, "a", "t"),
            _leg("bt", "response", 3, "b", "t"), _leg("at", "response", 4, "a", "t")]
    result = derive_trace_lineage(legs, ENTITIES, matcher=simple_match)
    assert result.legs[("bt", "response")].inbound_payloads == ("h2",)  # at's request, not bt's own
