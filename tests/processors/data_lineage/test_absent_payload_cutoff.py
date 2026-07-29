"""Absent-payload positional prefix cutoff + the trace-level status (issue #120).

ADR-0027 **D6**, interim rule: if a leg's ``payload_hash`` is absent, lineage is
computed **up to that point only** — a positional prefix in leg ``seq`` order.
Processing stops at the FIRST such leg; legs with lower ``seq`` keep their
lineage, legs at and after it get none, and the trace's lineage is marked
``partial`` recording the leg ``seq`` it stopped at.

The point of the flag is stated negatively in the ADR and worth restating: a
governance consumer must never read a truncated prefix as the full set of
sources. **Silent truncation is the failure mode this exists to prevent**, so
every test below that asserts "legs after the gap have no lineage" has a partner
asserting the status says so out loud.

Pure-traversal level here; the persistence half (including the stale-row problem
that a re-derivation with a *shorter* prefix creates) is in ``test_driver.py``.

Deliberately NOT tested, because deliberately NOT built (ADR-0027 D6 keeps them
open): telling *not captured* / *redacted* / *genuinely empty* / *in-flight*
apart, per-case break-chain vs conservative pass-through, and taint/reachability
cutoff. This ticket stops the WHOLE trace at the gap.
"""

from __future__ import annotations

import pytest

from data_governance.matching import MatchResult
from data_governance.processors.data_lineage.traversal import (
    Entity,
    Leg,
    LineageStatus,
    Operation,
    derive_trace_lineage,
)


def _always(payload_a: object, payload_b: object, /) -> MatchResult:
    return MatchResult(matched=True)


def _entities(**kinds: str) -> dict[str, Entity]:
    return {
        name: Entity(id=name, natural_key=name, kind=kind)
        for name, kind in kinds.items()
    }


def _leg(
    interaction_id: str,
    leg_type: str,
    seq: int,
    caller: str,
    callee: str,
    payload_hash: str | None = "",
) -> Leg:
    return Leg(
        interaction_id=interaction_id,
        leg_type=leg_type,
        seq=seq,
        caller_entity_id=caller,
        callee_entity_id=callee,
        payload_hash=f"h{seq}" if payload_hash == "" else payload_hash,
    )


# --- the realistic trigger: a missing mid-trace RESPONSE payload -------------
#
# Per ADR-0025 the request leg always exists, so the realistic absent payload is
# a response that was never captured. This fixture is the spec's worked example
# with the FIRST llm response (leg #3) unpayloaded.


@pytest.fixture()
def missing_mid_trace_response() -> tuple[list[Leg], dict[str, Entity]]:
    ents = _entities(user="user", agent="agent", llm="llm")
    legs = [
        _leg("ix_ua", "request", 1, "user", "agent"),
        _leg("ix_al1", "request", 2, "agent", "llm"),
        _leg("ix_al1", "response", 3, "agent", "llm", payload_hash=None),  # the gap
        _leg("ix_al2", "request", 4, "agent", "llm"),
        _leg("ix_al2", "response", 5, "agent", "llm"),
        _leg("ix_ua", "response", 6, "user", "agent"),
    ]
    return legs, ents


def test_lineage_stops_at_the_first_absent_payload_leg(
    missing_mid_trace_response,
) -> None:
    """D6: "Processing stops at the first leg with an absent payload; legs with
    lower ``seq`` get lineage, legs from that point on get none"."""
    legs, ents = missing_mid_trace_response
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert set(result.legs) == {("ix_ua", "request"), ("ix_al1", "request")}


def test_legs_before_the_gap_keep_their_full_lineage(
    missing_mid_trace_response,
) -> None:
    """The prefix is a real answer, not a degraded one: the legs that made it in
    carry exactly the lineage they would have with no gap at all."""
    legs, ents = missing_mid_trace_response
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert result.legs[("ix_ua", "request")].operation is Operation.INIT
    first_outbound = result.legs[("ix_al1", "request")]
    assert first_outbound.operation is Operation.LINEAR
    assert first_outbound.inbound_payloads == ("h1",)
    assert first_outbound.lineage.data_sources == frozenset({"user"})
    assert first_outbound.lineage.entities == frozenset({"agent"})


def test_the_gap_leg_itself_gets_no_lineage(missing_mid_trace_response) -> None:
    """"legs from that point on" INCLUDES the gap leg. It has no payload, so
    there is nothing for the matcher to compare and no lineage to claim."""
    legs, ents = missing_mid_trace_response
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert ("ix_al1", "response") not in result.legs


def test_legs_after_the_gap_get_no_lineage_even_though_they_have_payloads(
    missing_mid_trace_response,
) -> None:
    """The load-bearing assertion. Legs 4/5/6 all carry payloads and would
    derive perfectly well on their own — they are withheld because the trace's
    flow ran THROUGH the gap, and whatever they inherited would be a claim about
    data whose provenance we cannot see. This ticket stops the whole trace (no
    taint/reachability cutoff — explicitly deferred in D6)."""
    legs, ents = missing_mid_trace_response
    result = derive_trace_lineage(legs, ents, matcher=_always)

    for key in [("ix_al2", "request"), ("ix_al2", "response"), ("ix_ua", "response")]:
        assert key not in result.legs, key


def test_the_trace_is_partial_and_records_where_it_stopped(
    missing_mid_trace_response,
) -> None:
    """"The trace's lineage is then marked ``partial`` (vs ``complete``),
    recording the leg ``seq`` at which it stopped"."""
    legs, ents = missing_mid_trace_response
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert result.status is LineageStatus.PARTIAL
    assert result.stopped_at_seq == 3


# --- the complete case -------------------------------------------------------


def test_a_fully_payloaded_trace_is_complete_with_no_stop_position() -> None:
    """The other half of the flag: a trace with a payload on every leg is
    ``complete``, and there is no stop position to record."""
    ents = _entities(user="user", agent="agent", llm="llm")
    legs = [
        _leg("ix_ua", "request", 1, "user", "agent"),
        _leg("ix_al", "request", 2, "agent", "llm"),
        _leg("ix_al", "response", 3, "agent", "llm"),
        _leg("ix_ua", "response", 4, "user", "agent"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert result.status is LineageStatus.COMPLETE
    assert result.stopped_at_seq is None
    assert len(result.legs) == 4


def test_an_empty_trace_is_complete() -> None:
    """No legs is not a gap. Nothing was truncated, so there is nothing to warn
    about — the flag must not cry partial over an absence of input."""
    result = derive_trace_lineage([], {}, matcher=_always)

    assert result.status is LineageStatus.COMPLETE
    assert result.stopped_at_seq is None
    assert result.legs == {}


# --- ordering is by leg seq --------------------------------------------------


def test_the_stop_is_the_lowest_seq_gap_not_the_first_row_supplied() -> None:
    """"a positional prefix in leg ``seq`` order". The traversal sorts by ``seq``
    itself (ADR-0025 puts no ``seq`` on the parent ``interactions`` row, so leg
    order is the only ordering available), so the caller's row order cannot move
    the cutoff."""
    ents = _entities(user="user", agent="agent", llm="llm")
    legs = [
        _leg("ix_al", "response", 5, "agent", "llm", payload_hash=None),
        _leg("ix_ua", "request", 1, "user", "agent"),
        _leg("ix_al", "request", 4, "agent", "llm", payload_hash=None),
        _leg("ix_ua", "response", 9, "user", "agent"),
    ]
    forward = derive_trace_lineage(legs, ents, matcher=_always)
    backward = derive_trace_lineage(list(reversed(legs)), ents, matcher=_always)

    assert forward.stopped_at_seq == 4
    assert forward == backward


def test_only_the_first_gap_is_reported_when_several_legs_lack_payloads() -> None:
    """Multiple gaps still yield ONE stop position — the earliest. Everything
    from the first gap on is already withheld, so later gaps add nothing."""
    ents = _entities(user="user", agent="agent", llm="llm")
    legs = [
        _leg("ix_ua", "request", 1, "user", "agent"),
        _leg("ix_al", "request", 2, "agent", "llm", payload_hash=None),
        _leg("ix_al", "response", 3, "agent", "llm", payload_hash=None),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert result.status is LineageStatus.PARTIAL
    assert result.stopped_at_seq == 2
    assert set(result.legs) == {("ix_ua", "request")}


def test_a_gap_on_the_very_first_leg_yields_no_lineage_at_all() -> None:
    """The degenerate prefix: an empty one. The trace is partial at ``seq`` 1 and
    carries no lineage rows — which must NOT read as "not yet derived"."""
    ents = _entities(user="user", agent="agent")
    legs = [
        _leg("ix", "request", 1, "user", "agent", payload_hash=None),
        _leg("ix", "response", 2, "user", "agent"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert result.legs == {}
    assert result.status is LineageStatus.PARTIAL
    assert result.stopped_at_seq == 1


def test_a_gap_on_the_last_leg_still_marks_the_trace_partial() -> None:
    """A response in flight looks exactly like a never-captured one to this
    interim rule (telling them apart is deferred, D6). The prefix is almost the
    whole trace, and the status still says so — the alternative is a consumer
    reading an all-but-final-leg prefix as complete."""
    ents = _entities(user="user", agent="agent")
    legs = [
        _leg("ix", "request", 1, "user", "agent"),
        _leg("ix", "response", 2, "user", "agent", payload_hash=None),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert set(result.legs) == {("ix", "request")}
    assert result.status is LineageStatus.PARTIAL
    assert result.stopped_at_seq == 2


# --- the gap must not leak into the prefix's inbound sets --------------------


def test_the_gap_leg_is_not_routed_as_inbound_to_anyone() -> None:
    """A payload we do not have cannot be inbound to anything: routing it anyway
    would make ``|inbound|`` lie about how many payloads reached the entity, and
    the op selection reads exactly that number (D4). Moot for legs after the gap
    (they get nothing), but it must hold *at* the gap leg's own consumer."""
    ents = _entities(user="user", agent="agent", llm="llm")
    legs = [
        _leg("ix_ua", "request", 1, "user", "agent"),
        _leg("ix_al", "request", 2, "agent", "llm", payload_hash=None),
        _leg("ix_al", "response", 3, "agent", "llm"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert ("ix_al", "response") not in result.legs
    assert result.stopped_at_seq == 2


def test_an_unknown_producer_is_not_a_payload_gap() -> None:
    """The two skip reasons stay separate. A leg whose *entity* row has not
    landed yet (eventual consistency, #117) is skipped without truncating the
    trace — its payload exists, so the flow through it is still observable and
    the trace stays ``complete``. Only an absent PAYLOAD is a D6 gap."""
    ents = _entities(agent="agent")
    legs = [
        _leg("ix", "request", 1, "ghost", "agent"),
        _leg("ix", "response", 2, "ghost", "agent"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert ("ix", "request") not in result.legs
    assert result.legs[("ix", "response")].operation is Operation.INIT
    assert result.status is LineageStatus.COMPLETE
    assert result.stopped_at_seq is None
