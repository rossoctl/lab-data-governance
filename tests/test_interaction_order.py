"""Trace-local causal order with incomplete and inconsistent evidence."""

from __future__ import annotations

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
