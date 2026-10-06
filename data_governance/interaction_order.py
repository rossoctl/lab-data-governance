"""Trace-local execution order for interaction legs.

``seq`` records when a leg entered the durable stream. It cannot establish the
order of calls across services: a child may arrive before its parent. This module
orders the currently known legs from the interaction tree and each call's own
request/response relationship, then uses occurrence time for otherwise ready
legs. Every trace reader and data-lineage consumer uses this same rule.
"""

from __future__ import annotations

import datetime as dt
import heapq
from dataclasses import dataclass
from collections.abc import Iterable


@dataclass(frozen=True, slots=True)
class LegOrderKey:
    interaction_id: str
    leg_type: str


@dataclass(frozen=True, slots=True)
class LegOrderEvent:
    interaction_id: str
    leg_type: str
    parent_interaction_id: str | None
    occurred_at: dt.datetime | str | None
    seq: int

    @property
    def key(self) -> LegOrderKey:
        return LegOrderKey(self.interaction_id, self.leg_type)


def _priority(event: LegOrderEvent) -> tuple[bool, dt.datetime, int, str, str]:
    occurred_at = event.occurred_at
    if isinstance(occurred_at, str):
        try:
            occurred_at = dt.datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            occurred_at = None
    if occurred_at is not None:
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=dt.timezone.utc)
        occurred_at = occurred_at.astimezone(dt.timezone.utc)
    return (
        occurred_at is None,
        occurred_at or dt.datetime.max.replace(tzinfo=dt.timezone.utc),
        event.seq,
        event.interaction_id,
        event.leg_type,
    )


def order_legs(events: Iterable[LegOrderEvent]) -> list[LegOrderKey]:
    """Order every known leg once, even with missing parents, times or bad cycles.

    A parent request precedes each child request; a call's request precedes its
    response. No child-response/parent-response constraint is invented, since an
    asynchronous parent may finish first. Among currently unconstrained legs,
    the earliest occurrence wins. ``seq`` is only a deterministic tie-breaker.
    If invalid parent links form a cycle, the smallest remaining event breaks it.
    """
    by_key = {event.key: event for event in events}
    followers: dict[LegOrderKey, set[LegOrderKey]] = {key: set() for key in by_key}
    indegree = {key: 0 for key in by_key}

    def precedes(before: LegOrderKey, after: LegOrderKey) -> None:
        if before in by_key and after in by_key and after not in followers[before]:
            followers[before].add(after)
            indegree[after] += 1

    for event in by_key.values():
        if event.leg_type == "request" and event.parent_interaction_id:
            precedes(
                LegOrderKey(event.parent_interaction_id, "request"), event.key
            )
        if event.leg_type == "response":
            precedes(LegOrderKey(event.interaction_id, "request"), event.key)

    ready = [(_priority(by_key[key]), key) for key, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    remaining = set(by_key)
    ordered: list[LegOrderKey] = []
    while remaining:
        if ready:
            _, key = heapq.heappop(ready)
            if key not in remaining:
                continue
        else:
            # Break a parent-link cycle at a request. A response may have the
            # earliest timestamp, but its own request must still come first.
            requests = (key for key in remaining if key.leg_type == "request")
            key = min(requests, key=lambda candidate: _priority(by_key[candidate]))
        remaining.remove(key)
        ordered.append(key)
        for child in followers[key]:
            indegree[child] -= 1
            if indegree[child] == 0 and child in remaining:
                heapq.heappush(ready, (_priority(by_key[child]), child))
    return ordered
