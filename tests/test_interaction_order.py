"""Trace-local causal order with incomplete and inconsistent evidence."""

from __future__ import annotations

import dataclasses
import datetime as dt
import random
from itertools import permutations

from data_governance.interaction_order import (
    CLOCK_SKEW_TOLERANCE,
    LegOrderEvent,
    LegOrderKey,
    leg_positions,
    order_legs,
)


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


def test_cyclic_parents_make_a_response_cycle_broken_after_both_requests() -> None:
    # A <-> B are each other's parent and answer within the tolerance, so each
    # response would wait for the other. Both requests are released first.
    events = [
        _event("A", "request", 3, None, "B"),
        _event("B", "request", 4, None, "A"),
        _event("A", "response", 1, "2026-01-01T00:00:00.500Z", "B"),
        _event("B", "response", 2, "2026-01-01T00:00:00Z", "A"),
    ]
    assert _assert_order_independent(events) == [
        ("A", "request"), ("B", "request"), ("B", "response"), ("A", "response"),
    ]


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
    """The specified precedence edges, written out independently of the module."""
    by_key = {event.key: event for event in events}
    edges: dict[LegOrderKey, set[LegOrderKey]] = {}

    def add(before: LegOrderKey, after: LegOrderKey) -> None:
        if before != after and before in by_key and after in by_key:
            edges.setdefault(before, set()).add(after)

    for event in events:
        parent = event.parent_interaction_id or ""
        if event.leg_type == "request":
            add(LegOrderKey(parent, "request"), event.key)
            continue
        add(LegOrderKey(event.interaction_id, "request"), event.key)
        parent_response = by_key.get(LegOrderKey(parent, "response"))
        if parent_response is None:
            continue
        child_at = _as_utc(event.occurred_at)
        parent_at = _as_utc(parent_response.occurred_at)
        asynchronous = (
            child_at is not None
            and parent_at is not None
            and child_at - parent_at > CLOCK_SKEW_TOLERANCE
        )
        if not asynchronous:
            add(event.key, parent_response.key)
    return edges


def test_random_traces_keep_every_constraint_outside_a_cycle() -> None:
    rng = random.Random(281)
    checked_response_edges = 0
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

        # Parent request -> child request, and child response -> parent
        # response unless clearly asynchronous: each holds off a cycle.
        edges = _constraints(events)
        for before, afters in edges.items():
            for after in afters:
                if before not in _reachable(after, edges):
                    assert position[before] < position[after], (before, after, events, result)
                    checked_response_edges += before.leg_type == after.leg_type == "response"

        shuffled = list(events)
        rng.shuffle(shuffled)
        assert order_legs(shuffled) == result
    assert checked_response_edges > 100
