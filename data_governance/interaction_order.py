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
from collections.abc import Iterable, Iterator


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
    If invalid parent links form a cycle, the earliest leg on a cycle that
    nothing outside it still constrains is released first; a self-parent
    constrains nothing.
    """
    by_key = {event.key: event for event in events}
    followers: dict[LegOrderKey, set[LegOrderKey]] = {key: set() for key in by_key}
    preceders: dict[LegOrderKey, set[LegOrderKey]] = {key: set() for key in by_key}
    indegree = {key: 0 for key in by_key}

    def precedes(before: LegOrderKey, after: LegOrderKey) -> None:
        if (
            before != after
            and before in by_key
            and after in by_key
            and after not in followers[before]
        ):
            followers[before].add(after)
            preceders[after].add(before)
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
            # Every remaining leg waits on another remaining leg, so invalid
            # parent links form a cycle. Release a leg only from a cycle that
            # no other remaining leg constrains: a leg merely downstream of a
            # cycle, such as a valid child or a response, keeps its order.
            candidates = _unconstrained_cycles(remaining, followers, preceders)
            key = min(candidates, key=lambda candidate: _priority(by_key[candidate]))
        remaining.remove(key)
        ordered.append(key)
        for child in followers[key]:
            indegree[child] -= 1
            if indegree[child] == 0 and child in remaining:
                heapq.heappush(ready, (_priority(by_key[child]), child))
    return ordered


def _unconstrained_cycles(
    remaining: set[LegOrderKey],
    followers: dict[LegOrderKey, set[LegOrderKey]],
    preceders: dict[LegOrderKey, set[LegOrderKey]],
) -> list[LegOrderKey]:
    """The legs of each remaining strongly connected component with no remaining
    predecessor outside itself.

    Called only when no remaining leg is ready and self-edges are never added,
    so each such component is a genuine cycle. A response and its own request
    never share one, because no constraint leads from a response to a request:
    a response in such a component has had its request released already.
    """
    component = _strong_components(remaining, followers)
    blocked = {
        component[after]
        for after in remaining
        for before in preceders[after]
        if before in remaining and component[before] != component[after]
    }
    return [key for key in remaining if component[key] not in blocked]


def _strong_components(
    nodes: set[LegOrderKey], followers: dict[LegOrderKey, set[LegOrderKey]]
) -> dict[LegOrderKey, int]:
    """Tarjan's strongly connected components of the subgraph on *nodes*,
    iterative so that a long parent chain cannot exhaust the recursion limit."""
    index: dict[LegOrderKey, int] = {}
    low: dict[LegOrderKey, int] = {}
    component: dict[LegOrderKey, int] = {}
    stack: list[LegOrderKey] = []
    on_stack: set[LegOrderKey] = set()

    def visit(node: LegOrderKey) -> Iterator[LegOrderKey]:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        return iter([after for after in followers[node] if after in nodes])

    for root in nodes:
        if root in index:
            continue
        work = [(root, visit(root))]
        while work:
            node, pending = work[-1]
            for after in pending:
                if after not in index:
                    work.append((after, visit(after)))
                    break
                if after in on_stack:
                    low[node] = min(low[node], index[after])
            else:
                work.pop()
                if work:
                    caller = work[-1][0]
                    low[caller] = min(low[caller], low[node])
                if low[node] == index[node]:
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component[member] = index[node]
                        if member == node:
                            break
    return component
