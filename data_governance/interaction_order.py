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

#: How far a parent's response may be timed before its child's response and
#: still be read as the synchronous return that waited for it. Leg times come
#: from different pods' clocks and from span ends that each SDK closes at its
#: own point after the bytes leave, so a synchronous caller can appear to answer
#: a few milliseconds before its callee. Cross-pod skew plus that jitter stays
#: well inside one second, while a parent that genuinely finished first (a
#: fire-and-forget delegation or a stream it stopped waiting on) is apart from
#: the child's response by far more than that.
CLOCK_SKEW_TOLERANCE = dt.timedelta(seconds=1)


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


def _utc(occurred_at: dt.datetime | str | None) -> dt.datetime | None:
    """A leg time as an aware UTC datetime: ISO strings are parsed, naive values
    are UTC, and an unparseable string is an unknown time."""
    if isinstance(occurred_at, str):
        try:
            occurred_at = dt.datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    if occurred_at is None:
        return None
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=dt.timezone.utc)
    return occurred_at.astimezone(dt.timezone.utc)


def _priority(event: LegOrderEvent) -> tuple[bool, dt.datetime, int, str, str]:
    occurred_at = _utc(event.occurred_at)
    return (
        occurred_at is None,
        occurred_at or dt.datetime.max.replace(tzinfo=dt.timezone.utc),
        event.seq,
        event.interaction_id,
        event.leg_type,
    )


def _clearly_asynchronous(child_response: LegOrderEvent, parent_response: LegOrderEvent) -> bool:
    """Whether both response times are known and the parent answered more than
    :data:`CLOCK_SKEW_TOLERANCE` before its child did."""
    child_at = _utc(child_response.occurred_at)
    parent_at = _utc(parent_response.occurred_at)
    return (
        child_at is not None
        and parent_at is not None
        and child_at - parent_at > CLOCK_SKEW_TOLERANCE
    )


def order_legs(events: Iterable[LegOrderEvent]) -> list[LegOrderKey]:
    """Order every known leg once, even with missing parents, times or bad cycles.

    A parent request precedes each child request; a call's request precedes its
    response. A child's response precedes its parent's response, unless both
    times are known and the parent answered more than
    :data:`CLOCK_SKEW_TOLERANCE` earlier, which only an asynchronous parent does.
    Each constraint applies only between legs that are both present, so a child
    still in flight never holds its parent's response back. Among currently
    unconstrained legs, the earliest occurrence wins. ``seq`` is only a
    deterministic tie-breaker. If invalid parent links form a cycle, the
    earliest leg on a cycle that nothing outside it still constrains is released
    first; a self-parent constrains nothing.
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
        parent = event.parent_interaction_id
        if event.leg_type == "request":
            if parent:
                precedes(LegOrderKey(parent, "request"), event.key)
            continue
        if event.leg_type != "response":
            continue
        precedes(LegOrderKey(event.interaction_id, "request"), event.key)
        parent_response = by_key.get(LegOrderKey(parent, "response")) if parent else None
        if parent_response is not None and not _clearly_asynchronous(
            event, parent_response
        ):
            precedes(event.key, parent_response.key)

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



def leg_positions(events: Iterable[LegOrderEvent]) -> dict[LegOrderKey, int]:
    """Each leg's zero-based position in :func:`order_legs`, iterating in that order.

    The one way a reader or the lineage derivation turns the causal order into
    sort keys, so every consumer numbers a trace's legs identically.
    """
    return {key: position for position, key in enumerate(order_legs(events))}

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
