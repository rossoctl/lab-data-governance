"""Trace-local causal order with incomplete and inconsistent evidence."""

from __future__ import annotations

import datetime as dt
import random
from itertools import permutations

from data_governance.interaction_order import LegOrderEvent, LegOrderKey, order_legs


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


def test_random_traces_keep_every_constraint_outside_a_cycle() -> None:
    rng = random.Random(281)
    for _ in range(500):
        events = _random_trace(rng)
        result = order_legs(events)
        keys = {event.key for event in events}
        assert sorted(result, key=str) == sorted(keys, key=str)
        assert len(result) == len(set(result))
        position = {key: index for index, key in enumerate(result)}

        for key in keys:
            request = LegOrderKey(key.interaction_id, "request")
            if key.leg_type == "response" and request in keys:
                assert position[request] < position[key], (events, result)

        # Parent request -> child request, for each edge that is not on a cycle.
        request_edges: dict[LegOrderKey, set[LegOrderKey]] = {}
        for event in events:
            parent = LegOrderKey(event.parent_interaction_id or "", "request")
            if event.leg_type == "request" and parent in keys and parent != event.key:
                request_edges.setdefault(parent, set()).add(event.key)
        for parent, children in request_edges.items():
            for child in children:
                if parent not in _reachable(child, request_edges):
                    assert position[parent] < position[child], (events, result)

        shuffled = list(events)
        rng.shuffle(shuffled)
        assert order_legs(shuffled) == result
