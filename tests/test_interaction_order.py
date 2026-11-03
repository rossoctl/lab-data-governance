"""Trace-local causal order with incomplete and inconsistent evidence."""

from __future__ import annotations

import dataclasses
import datetime as dt
import random
from itertools import permutations

import pytest

from data_governance.interaction_order import (
    CLOCK_SKEW_TOLERANCE,
    LegOrderEvent,
    LegOrderKey,
    leg_positions,
    order_legs,
)
from data_governance.matching import simple_match
from data_governance.processors.data_lineage import traversal


def _event(
    interaction_id: str,
    leg_type: str,
    seq: int,
    occurred_at: str | None,
    parent_id: str | None = None,
) -> LegOrderEvent:
    return LegOrderEvent(interaction_id, leg_type, parent_id, occurred_at, seq)


def _keys(events: list[LegOrderEvent]) -> list[tuple[str, str]]:
    return [(key.interaction_id, key.leg_type) for key in order_legs(events)]


def test_parent_and_own_request_precede_a_child_despite_clock_disagreement() -> None:
    events = [
        _event("child", "response", 1, "2026-01-01T00:00:00Z", "parent"),
        _event("child", "request", 2, "2026-01-01T00:00:01Z", "parent"),
        _event("parent", "response", 3, "2026-01-01T00:00:12Z"),
        _event("parent", "request", 4, "2026-01-01T00:00:10Z"),
    ]
    assert _keys(events) == [
        ("parent", "request"),
        ("child", "request"),
        ("child", "response"),
        ("parent", "response"),
    ]


def test_parent_response_may_precede_an_asynchronous_child_response() -> None:
    events = [
        _event("child", "response", 4, "2026-01-01T00:00:04Z", "parent"),
        _event("parent", "response", 3, "2026-01-01T00:00:02Z"),
        _event("child", "request", 2, "2026-01-01T00:00:01Z", "parent"),
        _event("parent", "request", 1, "2026-01-01T00:00:00Z"),
    ]
    assert _keys(events) == [
        ("parent", "request"),
        ("child", "request"),
        ("parent", "response"),
        ("child", "response"),
    ]


def test_missing_request_parent_and_times_still_return_each_leg() -> None:
    events = [
        _event("orphan", "response", 3, None),
        _event("child", "request", 1, None, "not-yet-present"),
        _event("root", "request", 2, None),
    ]
    assert _keys(events) == [
        ("child", "request"), ("root", "request"), ("orphan", "response"),
    ]


def test_invalid_parent_cycle_has_a_deterministic_complete_order() -> None:
    events = [
        _event("a", "request", 1, "2026-01-01T00:00:01Z", "b"),
        _event("b", "request", 2, "2026-01-01T00:00:02Z", "a"),
        _event("a", "response", 3, "2026-01-01T00:00:00Z", "b"),
    ]
    orders = {tuple(order_legs(variant)) for variant in permutations(events)}
    assert len(orders) == 1
    assert next(iter(orders))[0] == LegOrderKey("a", "request")
    for variant in permutations(events):
        result = order_legs(variant)
        assert len(result) == 3
        assert set(result) == {event.key for event in events}
        assert result.index(LegOrderKey("a", "request")) < result.index(
            LegOrderKey("a", "response")
        )


def test_leg_positions_number_the_causal_order_and_iterate_in_it() -> None:
    events = [
        _event("child", "request", 1, "2026-01-01T00:00:01Z", "parent"),
        _event("parent", "response", 2, "2026-01-01T00:00:12Z"),
        _event("parent", "request", 3, "2026-01-01T00:00:10Z"),
    ]
    positions = leg_positions(events)
    assert list(positions) == order_legs(events)
    assert positions == {
        LegOrderKey("parent", "request"): 0,
        LegOrderKey("child", "request"): 1,
        LegOrderKey("parent", "response"): 2,
    }
    assert leg_positions(iter(events)) == positions
    assert leg_positions([]) == {}


# --- a child's response precedes its parent's, unless clearly asynchronous ---
#
# The child's legs are pulled no later than the parent's response; the
# parent's response itself is never moved.

_T0 = dt.datetime(2026, 11, 2, 13, 0, 20, tzinfo=dt.timezone.utc)


def _at(milliseconds: int | None) -> dt.datetime | None:
    return None if milliseconds is None else _T0 + dt.timedelta(milliseconds=milliseconds)


def _nested_call(child_response_ms: int | None, parent_response_ms: int | None) -> list[LegOrderEvent]:
    """Parent P delegates to child K; only the two response times vary."""
    return [
        LegOrderEvent("P", "request", None, _at(0), 1),
        LegOrderEvent("K", "request", "P", _at(10), 2),
        LegOrderEvent("K", "response", "P", _at(child_response_ms), 3),
        LegOrderEvent("P", "response", None, _at(parent_response_ms), 4),
    ]


_SYNCHRONOUS = [
    ("P", "request"), ("K", "request"), ("K", "response"), ("P", "response"),
]
_TOLERANCE_MS = CLOCK_SKEW_TOLERANCE // dt.timedelta(milliseconds=1)


def test_the_clock_skew_tolerance_is_one_second() -> None:
    assert CLOCK_SKEW_TOLERANCE == dt.timedelta(seconds=1)


def test_child_response_precedes_parent_response_within_callee_clock_skew() -> None:
    # The callee's clock stamps its response 5 ms after the caller's response.
    assert _assert_order_independent(_nested_call(105, 100)) == _SYNCHRONOUS


def test_child_response_precedes_parent_response_at_exactly_the_tolerance() -> None:
    events = _nested_call(100 + _TOLERANCE_MS, 100)
    assert _assert_order_independent(events) == _SYNCHRONOUS


def test_parent_response_may_precede_a_child_response_beyond_the_tolerance() -> None:
    events = _nested_call(100 + _TOLERANCE_MS + 1, 100)
    assert _assert_order_independent(events) == [
        ("P", "request"), ("K", "request"), ("P", "response"), ("K", "response"),
    ]


def test_an_untimed_child_response_precedes_its_parent_response() -> None:
    assert _assert_order_independent(_nested_call(None, 100)) == _SYNCHRONOUS


def test_an_untimed_parent_response_follows_its_child_response() -> None:
    # Untimed legs sort after timed ones and then by seq, which here would put
    # P's response (seq 3) before K's untimed request and its response.
    events = [
        LegOrderEvent("P", "request", None, _at(0), 1),
        LegOrderEvent("K", "request", "P", None, 5),
        LegOrderEvent("K", "response", "P", _at(50), 6),
        LegOrderEvent("P", "response", None, None, 3),
    ]
    assert _assert_order_independent(events) == _SYNCHRONOUS


def test_untimed_responses_put_the_child_response_first_despite_seq() -> None:
    events = [
        LegOrderEvent("P", "request", None, _at(0), 1),
        LegOrderEvent("K", "request", "P", _at(10), 2),
        LegOrderEvent("K", "response", "P", None, 4),
        LegOrderEvent("P", "response", None, None, 3),
    ]
    assert _assert_order_independent(events) == _SYNCHRONOUS


def test_child_response_rule_compares_times_across_representations() -> None:
    # Naive datetimes and offset-free strings are UTC, as they are for ordering.
    naive_child = (_T0 + dt.timedelta(milliseconds=105)).replace(tzinfo=None)
    zulu_parent = (_T0 + dt.timedelta(milliseconds=100)).isoformat().replace("+00:00", "Z")
    events = _nested_call(0, 0)
    events[2] = dataclasses.replace(events[2], occurred_at=naive_child)
    events[3] = dataclasses.replace(events[3], occurred_at=zulu_parent)
    assert _assert_order_independent(events) == _SYNCHRONOUS
    paris = dt.timezone(dt.timedelta(hours=1))
    events[2] = dataclasses.replace(
        events[2], occurred_at=(_T0 + dt.timedelta(milliseconds=2000)).astimezone(paris)
    )
    assert _assert_order_independent(events) == [
        ("P", "request"), ("K", "request"), ("P", "response"), ("K", "response"),
    ]


def test_child_response_rule_chains_through_a_grandchild() -> None:
    events = [
        LegOrderEvent("P", "request", None, _at(0), 1),
        LegOrderEvent("K", "request", "P", _at(10), 2),
        LegOrderEvent("G", "request", "K", _at(20), 3),
        LegOrderEvent("G", "response", "K", _at(130), 4),
        LegOrderEvent("K", "response", "P", _at(120), 5),
        LegOrderEvent("P", "response", None, _at(110), 6),
    ]
    assert _assert_order_independent(events) == [
        ("P", "request"), ("K", "request"), ("G", "request"),
        ("G", "response"), ("K", "response"), ("P", "response"),
    ]


def test_an_in_flight_child_does_not_hold_back_its_parent_response() -> None:
    # K has no response yet. P's response is still placed by its own time,
    # before a later unrelated request, rather than waiting for K.
    events = [
        LegOrderEvent("P", "request", None, _at(0), 1),
        LegOrderEvent("K", "request", "P", _at(10), 2),
        LegOrderEvent("P", "response", None, _at(100), 3),
        LegOrderEvent("S", "request", None, _at(200), 4),
    ]
    assert _assert_order_independent(events) == [
        ("P", "request"), ("K", "request"), ("P", "response"), ("S", "request"),
    ]


def test_cyclic_parents_pull_no_time_so_each_response_keeps_its_own() -> None:
    # A <-> B are each other's parent and answer within the tolerance. A
    # parent link on a cycle pulls no time, so once the request cycle breaks
    # at A, A's timed response is ready before B's untimed request.
    events = [
        _event("A", "request", 3, None, "B"),
        _event("B", "request", 4, None, "A"),
        _event("A", "response", 1, "2026-01-01T00:00:00.500Z", "B"),
        _event("B", "response", 2, "2026-01-01T00:00:00Z", "A"),
    ]
    assert _assert_order_independent(events) == [
        ("A", "request"), ("A", "response"), ("B", "request"), ("B", "response"),
    ]


def test_a_valid_child_of_a_cycle_member_is_pulled_to_that_member_response() -> None:
    # A <-> B is an invalid cycle; C is a valid child of A whose response is
    # stamped after A's within the tolerance. C still answers before A does.
    events = [
        _event("A", "request", 1, "2026-01-01T00:00:01Z", "B"),
        _event("B", "request", 2, "2026-01-01T00:00:02Z", "A"),
        _event("A", "response", 3, "2026-01-01T00:00:05Z", "B"),
        _event("C", "request", 4, "2026-01-01T00:00:03Z", "A"),
        _event("C", "response", 5, "2026-01-01T00:00:05.300Z", "A"),
    ]
    assert _assert_order_independent(events) == [
        ("A", "request"), ("B", "request"), ("C", "request"),
        ("C", "response"), ("A", "response"),
    ]


# --- the parent's response is never pushed later for its child ----------------
#
# R = client -> agent, P = agent -> tool (child of R), K = tool -> db (child of
# P), and Q = agent -> llm (child of R), sent 2 ms after P's response. K's
# response is stamped 5 ms after P's (the callee's clock runs fast) or not at
# all. K's response must still precede P's, and Q must still follow P's
# response, so the agent's call to the LLM carries the tool's answer.

_SIBLING_ROWS = (
    # interaction, leg, caller, callee, parent, ms, seq
    ("R", "request", "client", "agent", None, 0, 1),
    ("P", "request", "agent", "tool", "R", 10, 2),
    ("K", "request", "tool", "db", "P", 20, 3),
    ("K", "response", "tool", "db", "P", "k-response", 4),
    ("P", "response", "agent", "tool", "R", 100, 5),
    ("Q", "request", "agent", "llm", "R", 102, 6),
    ("Q", "response", "agent", "llm", "R", 200, 7),
    ("R", "response", "client", "agent", None, 300, 8),
)
_SIBLING_ORDER = [
    ("R", "request"), ("P", "request"), ("K", "request"), ("K", "response"),
    ("P", "response"), ("Q", "request"), ("Q", "response"), ("R", "response"),
]
# 105: K's response 5 ms after P's, within the tolerance. None: untimed.
_K_RESPONSE_TIMES = pytest.mark.parametrize("k_response_ms", [105, None], ids=["skew", "untimed"])


def _sibling_rows(k_response_ms: int | None):
    for interaction_id, leg_type, caller, callee, parent, ms, seq in _SIBLING_ROWS:
        yield interaction_id, leg_type, caller, callee, parent, (
            k_response_ms if ms == "k-response" else ms
        ), seq


def _sibling_events(k_response_ms: int | None) -> list[LegOrderEvent]:
    return [
        LegOrderEvent(interaction_id, leg_type, parent, _at(ms), seq)
        for interaction_id, leg_type, _, _, parent, ms, seq in _sibling_rows(k_response_ms)
    ]


def _assert_shuffle_independent(events: list[LegOrderEvent], rounds: int = 200) -> list[tuple[str, str]]:
    """The order of *events*, asserted unchanged by reversal and by seeded
    shuffles. For traces too long for every permutation."""
    expected = _keys(events)
    assert _keys(events[::-1]) == expected
    rng = random.Random(281)
    for _ in range(rounds):
        shuffled = list(events)
        rng.shuffle(shuffled)
        assert _keys(shuffled) == expected
    return expected


@_K_RESPONSE_TIMES
def test_a_sibling_sent_after_the_parent_response_follows_it(k_response_ms: int | None) -> None:
    assert _assert_shuffle_independent(_sibling_events(k_response_ms)) == _SIBLING_ORDER


@_K_RESPONSE_TIMES
def test_lineage_reaches_both_the_parent_response_and_the_later_sibling(
    k_response_ms: int | None,
) -> None:
    entities = {
        name: traversal.Entity(name, natural_key, kind)
        for name, natural_key, kind in (
            ("client", "client:c", "client"),
            ("agent", "agent:a", "agent"),
            ("tool", "tool:t", "tool"),
            ("db", "tool:db", "tool"),
            ("llm", "llm:m", "llm"),
        )
    }
    legs = [
        traversal.Leg(
            interaction_id, leg_type, seq, caller, callee,
            f"h-{interaction_id}-{leg_type}", parent, _at(ms),
        )
        for interaction_id, leg_type, caller, callee, parent, ms, seq in _sibling_rows(k_response_ms)
    ]
    result = traversal.derive_trace_lineage(legs, entities, matcher=simple_match)

    # The database's answer reaches the tool's answer to the agent ...
    assert result.legs[("P", "response")].lineage.data_sources == {
        "client:c", "tool:t", "tool:db",
    }
    # ... and the agent's later call to the LLM carries all of it.
    assert result.legs[("Q", "request")].lineage.data_sources == {
        "client:c", "tool:t", "tool:db",
    }


@pytest.mark.parametrize("k_request_ms", [104, None], ids=["skew", "untimed"])
def test_a_child_request_timed_after_the_parent_response_still_precedes_it(
    k_request_ms: int | None,
) -> None:
    # K's request is stamped (or not stamped) after P's response, so P's
    # response would be ready first. K's legs are still pulled before it, and
    # the unrelated request S sent after P's response still follows it.
    events = [
        LegOrderEvent("P", "request", None, _at(0), 1),
        LegOrderEvent("K", "request", "P", _at(k_request_ms), 2),
        LegOrderEvent("K", "response", "P", _at(106), 3),
        LegOrderEvent("P", "response", None, _at(100), 4),
        LegOrderEvent("S", "request", None, _at(102), 5),
    ]
    assert _assert_order_independent(events) == [
        ("P", "request"), ("K", "request"), ("K", "response"),
        ("P", "response"), ("S", "request"),
    ]


def test_a_child_called_clearly_after_the_parent_response_keeps_its_time() -> None:
    # K's request is sent more than the tolerance after P answered: an
    # asynchronous follow-up, so neither of K's legs is pulled before P's response.
    events = [
        LegOrderEvent("P", "request", None, _at(0), 1),
        LegOrderEvent("K", "request", "P", _at(100 + _TOLERANCE_MS + 1), 2),
        LegOrderEvent("K", "response", "P", _at(100 + _TOLERANCE_MS + 2), 3),
        LegOrderEvent("P", "response", None, _at(100), 4),
        LegOrderEvent("S", "request", None, _at(600), 5),
    ]
    assert _assert_order_independent(events) == [
        ("P", "request"), ("P", "response"), ("S", "request"),
        ("K", "request"), ("K", "response"),
    ]


def test_at_an_equal_time_a_descendant_response_precedes_its_ancestor_despite_seq() -> None:
    # All three responses share one time and their seqs run the wrong way.
    events = [
        LegOrderEvent("P", "request", None, _at(0), 1),
        LegOrderEvent("K", "request", "P", _at(10), 2),
        LegOrderEvent("G", "request", "K", _at(20), 3),
        LegOrderEvent("P", "response", None, _at(100), 4),
        LegOrderEvent("K", "response", "P", _at(100), 5),
        LegOrderEvent("G", "response", "K", _at(100), 6),
        LegOrderEvent("U", "request", None, _at(100), 0),
    ]
    order = _assert_order_independent(events)
    assert order.index(("G", "response")) < order.index(("K", "response"))
    assert order.index(("K", "response")) < order.index(("P", "response"))


def test_legs_pulled_to_one_parent_response_time_keep_their_own_time_order() -> None:
    # R's response is stamped 10 ms before P's, within the tolerance, so P's
    # response and Q's legs are all pulled to R's response time. Among them
    # their own times still decide, not seq: Q's request follows P's response.
    events = [
        LegOrderEvent("R", "request", None, _at(0), 1),
        LegOrderEvent("P", "request", "R", _at(10), 2),
        LegOrderEvent("Q", "request", "R", _at(102), 3),
        LegOrderEvent("Q", "response", "R", _at(150), 4),
        LegOrderEvent("P", "response", "R", _at(100), 5),
        LegOrderEvent("R", "response", None, _at(90), 6),
    ]
    assert _assert_order_independent(events) == [
        ("R", "request"), ("P", "request"), ("P", "response"),
        ("Q", "request"), ("Q", "response"), ("R", "response"),
    ]


def test_a_pulled_leg_sorts_just_before_its_parent_response_not_by_depth() -> None:
    # R's response is stamped 10 ms before P's, so every response below R is
    # pulled to R's response time. Z is a grandchild through Q; its request,
    # sent 400 ms after P's response, is pulled deeper than P's response but
    # only into Q's group, so it still follows P's response.
    events = [
        LegOrderEvent("R", "request", None, _at(0), 1),
        LegOrderEvent("P", "request", "R", _at(10), 2),
        LegOrderEvent("Q", "request", "R", _at(50), 3),
        LegOrderEvent("P", "response", "R", _at(100), 4),
        LegOrderEvent("Z", "request", "Q", _at(500), 5),
        LegOrderEvent("Z", "response", "Q", _at(600), 6),
        LegOrderEvent("Q", "response", "R", _at(900), 7),
        LegOrderEvent("R", "response", None, _at(90), 8),
    ]
    assert _assert_shuffle_independent(events) == [
        ("R", "request"), ("P", "request"), ("Q", "request"), ("P", "response"),
        ("Z", "request"), ("Z", "response"), ("Q", "response"), ("R", "response"),
    ]


def test_a_long_parent_chain_and_a_long_cycle_are_ordered_without_recursion() -> None:
    # Every response is stamped within a few ms after the trace root's, so each
    # is pulled to its parent's already-pulled time; only depth orders them.
    depth = 5000
    chain = [
        event
        for n in range(depth)
        for event in (
            LegOrderEvent(f"c{n}", "request", f"c{n - 1}" if n else None, _at(n), 2 * n),
            LegOrderEvent(
                f"c{n}", "response", f"c{n - 1}" if n else None, _at(2 * depth + n % 7), 2 * n + 1
            ),
        )
    ]
    cycle = [
        LegOrderEvent(f"y{n}", leg_type, f"y{(n + 1) % depth}", _at(n), 2 * depth + n)
        for n in range(depth)
        for leg_type in ("request", "response")
    ]
    order = order_legs(chain + cycle)
    assert len(order) == len(set(order)) == len(chain) + len(cycle)
    position = {key: index for index, key in enumerate(order)}
    for n in range(depth - 1):
        child, parent = f"c{n + 1}", f"c{n}"
        assert position[LegOrderKey(parent, "request")] < position[LegOrderKey(child, "request")]
        assert position[LegOrderKey(child, "response")] < position[LegOrderKey(parent, "response")]


# --- cycle breaking: only a leg ON an unconstrained cycle is released early ---


def _assert_order_independent(events: list[LegOrderEvent]) -> list[tuple[str, str]]:
    """The order of *events* in every input arrangement, asserted to be one order."""
    orders = {tuple(_keys(list(variant))) for variant in permutations(events)}
    assert len(orders) == 1, orders
    return list(next(iter(orders)))


def test_cycle_break_never_releases_a_valid_child_of_the_cycle_first() -> None:
    # A <-> B is an invalid parent cycle. C is a valid child of A with the
    # earliest time, but its parent's request must still come first.
    events = [
        _event("A", "request", 1, "2026-01-01T00:00:50Z", "B"),
        _event("B", "request", 2, "2026-01-01T00:01:00Z", "A"),
        _event("C", "request", 3, "2026-01-01T00:00:00Z", "A"),
    ]
    assert _assert_order_independent(events) == [
        ("A", "request"), ("C", "request"), ("B", "request"),
    ]


def test_three_cycle_is_broken_at_its_earliest_leg_then_followed_in_order() -> None:
    # A -> B -> C -> A through invalid parent links; D hangs off C validly.
    events = [
        _event("A", "request", 3, "2026-01-01T00:00:03Z", "C"),
        _event("B", "request", 1, "2026-01-01T00:00:02Z", "A"),
        _event("C", "request", 2, "2026-01-01T00:00:04Z", "B"),
        _event("D", "request", 4, "2026-01-01T00:00:00Z", "C"),
    ]
    assert _assert_order_independent(events) == [
        ("B", "request"), ("C", "request"), ("D", "request"), ("A", "request"),
    ]


def test_two_disjoint_cycles_each_release_one_leg_and_keep_valid_children() -> None:
    events = [
        _event("A", "request", 1, "2026-01-01T00:00:10Z", "B"),
        _event("B", "request", 2, "2026-01-01T00:00:11Z", "A"),
        _event("A-child", "request", 3, "2026-01-01T00:00:00Z", "A"),
        _event("X", "request", 4, "2026-01-01T00:00:05Z", "Y"),
        _event("Y", "request", 5, "2026-01-01T00:00:06Z", "X"),
        _event("Y-child", "request", 6, "2026-01-01T00:00:01Z", "Y"),
    ]
    assert _keys(events) == [
        ("X", "request"),
        ("Y", "request"),
        ("Y-child", "request"),
        ("A", "request"),
        ("A-child", "request"),
        ("B", "request"),
    ]


def test_a_self_parent_constrains_nothing_but_its_children_still_follow() -> None:
    events = [
        _event("A", "response", 1, "2026-01-01T00:00:09Z", "A"),
        _event("A", "request", 2, "2026-01-01T00:00:01Z", "A"),
        _event("C", "request", 3, "2026-01-01T00:00:00Z", "A"),
        _event("C", "response", 4, "2026-01-01T00:00:05Z", "A"),
    ]
    assert _assert_order_independent(events) == [
        ("A", "request"), ("C", "request"), ("C", "response"), ("A", "response"),
    ]


def test_cycle_with_only_response_times_releases_a_request_first() -> None:
    # The responses carry the only (and earliest) times, but each response
    # still waits for its own untimed request.
    events = [
        _event("A", "request", 1, None, "B"),
        _event("B", "request", 2, None, "A"),
        _event("A", "response", 3, "2026-01-01T00:00:00Z", "B"),
        _event("B", "response", 4, "2026-01-01T00:00:05Z", "A"),
    ]
    assert _assert_order_independent(events) == [
        ("A", "request"), ("A", "response"), ("B", "request"), ("B", "response"),
    ]


# --- property: the order honours every constraint outside a cycle ------------


def _reachable(start: LegOrderKey, edges: dict[LegOrderKey, set[LegOrderKey]]) -> set[LegOrderKey]:
    seen: set[LegOrderKey] = set()
    pending = [start]
    while pending:
        for after in edges.get(pending.pop(), ()):
            if after not in seen:
                seen.add(after)
                pending.append(after)
    return seen


def _random_trace(rng: random.Random) -> list[LegOrderEvent]:
    """A small trace with arbitrary (possibly cyclic, self or dangling) parents,
    missing legs and times, and mixed time representations."""
    ids = [f"i{n}" for n in range(rng.randint(1, 6))]
    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    events = []
    seqs = rng.sample(range(1, 100), 2 * len(ids))
    for interaction_id in ids:
        parent = rng.choice([None, "absent", *ids])
        legs = rng.choice([["request"], ["response"], ["request", "response"]] + [["request", "response"]] * 3)
        for leg_type in legs:
            if rng.random() < 0.2:
                occurred_at: dt.datetime | str | None = None
            else:
                at = base + dt.timedelta(milliseconds=rng.choice([0, 400, 1000, 1500, 3000]) * rng.randint(0, 4))
                occurred_at = rng.choice([at, at.isoformat(), at.replace(tzinfo=None)])
            events.append(LegOrderEvent(interaction_id, leg_type, parent, occurred_at, seqs.pop()))
    return events


def _as_utc(occurred_at: dt.datetime | str | None) -> dt.datetime | None:
    if isinstance(occurred_at, str):
        occurred_at = dt.datetime.fromisoformat(occurred_at)
    if occurred_at is not None and occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=dt.timezone.utc)
    return occurred_at


def _constraints(events: list[LegOrderEvent]) -> dict[LegOrderKey, set[LegOrderKey]]:
    """The specified precedence edges, written out independently of the module:
    parent request -> child request and request -> its own response."""
    by_key = {event.key: event for event in events}
    edges: dict[LegOrderKey, set[LegOrderKey]] = {}

    def add(before: LegOrderKey, after: LegOrderKey) -> None:
        if before != after and before in by_key and after in by_key:
            edges.setdefault(before, set()).add(after)

    for event in events:
        if event.leg_type == "request":
            add(LegOrderKey(event.parent_interaction_id or "", "request"), event.key)
        else:
            add(LegOrderKey(event.interaction_id, "request"), event.key)
    return edges


def _parents(events: list[LegOrderEvent]) -> dict[str, str | None]:
    """Each interaction's parent link; :func:`_random_trace` gives both legs one."""
    return {event.interaction_id: event.parent_interaction_id for event in events}


def _ancestry(interaction_id: str, parents: dict[str, str | None]) -> list[str]:
    """The present ancestors of *interaction_id*, nearest first, up to a repeat."""
    chain: list[str] = []
    node = parents.get(interaction_id)
    while node in parents and node not in chain:
        chain.append(node)
        node = parents[node]
    return chain


def _linked_parents(events: list[LegOrderEvent]) -> dict[str, set[str]]:
    """Each present interaction's present parents, as named by any of its legs."""
    present = {event.interaction_id for event in events}
    linked: dict[str, set[str]] = {interaction_id: set() for interaction_id in present}
    for event in events:
        if event.parent_interaction_id in present:
            linked[event.interaction_id].add(event.parent_interaction_id)
    return linked


def _reaches(start: str, goal: str, linked: dict[str, set[str]]) -> bool:
    seen = {start}
    pending = [start]
    while pending:
        node = pending.pop()
        if node == goal:
            return True
        for parent in linked[node] - seen:
            seen.add(parent)
            pending.append(parent)
    return False


def _bounding_response(key: LegOrderKey, events: list[LegOrderEvent]) -> LegOrderKey | None:
    """The present response of the parent named by *key*'s own event, unless
    that parent link is a self-parent or lies on a cycle of parent links."""
    by_key = {event.key: event for event in events}
    parent = by_key[key].parent_interaction_id
    if not parent or parent == key.interaction_id:
        return None
    bound = LegOrderKey(parent, "response")
    if bound not in by_key or _reaches(parent, key.interaction_id, _linked_parents(events)):
        return None
    return bound


def _sort_times(events: list[LegOrderEvent]) -> dict[LegOrderKey, dt.datetime | None]:
    """Each leg's sort time, written out from ADR-0035 independently of the module.

    A child's leg is pulled no later than its parent's response's sort time; an
    untimed child leg takes that time; a leg timed more than the tolerance after
    it keeps its own; an untimed parent response pulls no timed leg; and a
    parent link on a parent cycle pulls nothing.
    """
    by_key = {event.key: event for event in events}

    def sort_time(key: LegOrderKey) -> dt.datetime | None:
        own = _as_utc(by_key[key].occurred_at)
        bound = _bounding_response(key, events)
        if bound is None:
            return own
        bound_time = sort_time(bound)
        if bound_time is None:
            return own
        if own is None:
            return bound_time
        if own - bound_time > CLOCK_SKEW_TOLERANCE:
            return own
        return min(own, bound_time)

    return {key: sort_time(key) for key in by_key}


def _assert_synchronous_children_answer_first(
    events: list[LegOrderEvent],
    times: dict[LegOrderKey, dt.datetime | None],
    position: dict[LegOrderKey, int],
) -> int:
    """A child response precedes its parent's response unless the child is
    clearly asynchronous: its request or response is timed more than the
    tolerance after the parent response's sort time. A child linked to its
    parent through a parent cycle is exempt. Returns how many pairs were
    checked."""
    keys = {event.key for event in events}
    own = {event.key: _as_utc(event.occurred_at) for event in events}
    checked = 0
    for key in keys:
        if key.leg_type != "response":
            continue
        parent_response = _bounding_response(key, events)
        if parent_response is None:
            continue
        bound = times[parent_response]
        child_legs = (LegOrderKey(key.interaction_id, leg) for leg in ("request", "response"))
        if bound is not None and any(
            own.get(leg) is not None and own[leg] - bound > CLOCK_SKEW_TOLERANCE
            for leg in child_legs
        ):
            continue
        assert position[key] < position[parent_response], (key, events)
        checked += 1
    return checked


def _waited_on_requests(
    interaction_id: str, keys: set[LegOrderKey], parents: dict[str, str | None]
) -> list[LegOrderKey] | None:
    """The requests a response of *interaction_id* waits on through precedence:
    its own request, then each parent's request while present. ``None`` if
    they form a cycle."""
    chain: list[LegOrderKey] = []
    request = LegOrderKey(interaction_id, "request")
    while request in keys:
        if request in chain:
            return None
        chain.append(request)
        parent = parents[request.interaction_id]
        if not parent or parent == request.interaction_id:
            break
        request = LegOrderKey(parent, "request")
    return chain


def _assert_later_legs_follow_each_response(
    events: list[LegOrderEvent],
    times: dict[LegOrderKey, dt.datetime | None],
    position: dict[LegOrderKey, int],
) -> int:
    """The sibling guarantee: no response is pushed later to wait for a child.

    For a response P.resp with a known own time t, whose waited-on requests
    (P's request and each present ancestor request above it, acyclic) all
    sort no later than t: every leg whose sort time is unknown or later than
    t is ordered after P.resp. That includes every leg timed after t that no
    call it belongs to pulls earlier, such as a sibling sent after P.resp. A
    leg in P's subtree meets the condition only if a clearly asynchronous call
    or an invalid parent cycle separates it from P, so the subtree needs no
    exclusion.

    A leg timed after t can still precede P.resp when its own parent's
    response pulls it earlier: that is the sort time ADR-0035 assigns it.

    Returns how many pairs were checked."""
    keys = {event.key for event in events}
    own = {event.key: _as_utc(event.occurred_at) for event in events}
    parents = _parents(events)
    checked = 0
    for response in keys:
        answered_at = own[response]
        if response.leg_type != "response" or answered_at is None:
            continue
        waited_on = _waited_on_requests(response.interaction_id, keys, parents)
        if waited_on is None or any(
            times[request] is None or times[request] > answered_at for request in waited_on
        ):
            continue
        for later in keys:
            if times[later] is None or times[later] > answered_at:
                assert position[response] < position[later], (response, later, events)
                checked += 1
    return checked


def test_random_traces_keep_every_constraint_outside_a_cycle() -> None:
    rng = random.Random(281)
    checked_child_requests = checked_children = checked_later_legs = 0
    for _ in range(500):
        events = _random_trace(rng)
        result = order_legs(events)
        keys = {event.key for event in events}
        assert sorted(result, key=str) == sorted(keys, key=str)
        assert len(result) == len(set(result))
        position = {key: index for index, key in enumerate(result)}

        # A response never precedes its own request, even when a cycle is broken.
        for key in keys:
            request = LegOrderKey(key.interaction_id, "request")
            if key.leg_type == "response" and request in keys:
                assert position[request] < position[key], (events, result)

        # Parent request -> child request, and request -> its own response,
        # wherever the edge is not on a cycle.
        edges = _constraints(events)
        for before, afters in edges.items():
            for after in afters:
                if before not in _reachable(after, edges):
                    assert position[before] < position[after], (before, after, events, result)
                    checked_child_requests += before.interaction_id != after.interaction_id

        times = _sort_times(events)
        checked_children += _assert_synchronous_children_answer_first(events, times, position)
        checked_later_legs += _assert_later_legs_follow_each_response(events, times, position)

        shuffled = list(events)
        rng.shuffle(shuffled)
        assert order_legs(shuffled) == result
    assert checked_child_requests > 100
    assert checked_children > 100
    assert checked_later_legs > 100
