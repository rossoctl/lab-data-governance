"""Trace-local execution order for interaction legs.

``seq`` records when a leg entered the durable stream. It cannot establish the
order of calls across services: a child may arrive before its parent. This module
orders the currently known legs from the interaction tree and each call's own
request/response relationship, then orders otherwise ready legs by a sort time:
the occurrence time, with a child's legs pulled no later than its parent's
response. Every trace reader and data-lineage consumer uses this same rule.
"""

from __future__ import annotations

import datetime as dt
import heapq
from collections import Counter
from collections.abc import Hashable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import TypeVar

#: How far a child's leg may be timed after its parent's response and still be
#: read as part of the synchronous call that response returned from. Leg times
#: come from different pods' clocks and from span ends that each SDK closes at
#: its own point after the bytes leave, so a callee can appear to answer a few
#: milliseconds after its caller already has. Cross-pod skew plus that jitter
#: stays well inside one second, while a child the parent genuinely did not wait
#: for (a fire-and-forget delegation or a stream it stopped waiting on) is apart
#: from the parent's response by far more than that.
CLOCK_SKEW_TOLERANCE = dt.timedelta(seconds=1)

_LAST = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
_Node = TypeVar("_Node", bound=Hashable)


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


def _clearly_asynchronous(child_at: dt.datetime, parent_response_at: dt.datetime) -> bool:
    """Whether a child's leg came more than :data:`CLOCK_SKEW_TOLERANCE` after
    its parent's response, which only a child the parent did not wait for does."""
    return child_at - parent_response_at > CLOCK_SKEW_TOLERANCE


def _sort_time(own: dt.datetime | None, bound: dt.datetime | None) -> dt.datetime | None:
    """A child leg's sort time from its own time and its parent response's."""
    if bound is None:
        # An untimed parent response already sorts after every timed leg.
        return own
    if own is None:
        return bound
    if _clearly_asynchronous(own, bound):
        return own
    return min(own, bound)


def _bounds(by_key: Mapping[LegOrderKey, LegOrderEvent]) -> dict[LegOrderKey, LegOrderKey | None]:
    """Each leg's bound: the present response of the parent its event names.

    A self-parent, or a parent link that lies on a cycle of parent links,
    bounds nothing. Following bounds therefore moves to another strongly
    connected component of the parent links at every step and always ends.
    """
    links: dict[str, set[str]] = {event.interaction_id: set() for event in by_key.values()}
    for event in by_key.values():
        if event.parent_interaction_id in links:
            links[event.interaction_id].add(event.parent_interaction_id)
    component = _strong_components(set(links), links)
    bounds: dict[LegOrderKey, LegOrderKey | None] = {}
    for key, event in by_key.items():
        parent = event.parent_interaction_id
        bound = LegOrderKey(parent, "response") if parent else None
        if bound not in by_key or component[parent] == component[key.interaction_id]:
            bound = None
        bounds[key] = bound
    return bounds


def _sort_ranks(by_key: Mapping[LegOrderKey, LegOrderEvent]) -> dict[LegOrderKey, int]:
    """Each leg's rank in sort-time order; a lower rank is preferred when ready.

    Sort times are computed from the root of the interaction tree down, so a
    grandchild is bounded by its parent's already-bounded time. A leg whose
    sort time equals its bound's was pulled into that response: it joins the
    group of legs ranked immediately before the response, ordered among
    themselves by their own times, each followed by its own group. So at an
    equal sort time a descendant's response precedes its ancestor's, while a
    leg pulled into a sibling's response stays behind a response that answered
    before that sibling did. Other legs rank by sort time; occurrence time,
    ``seq`` and the leg key break every remaining tie.
    """
    bounds = _bounds(by_key)
    own = {key: _utc(event.occurred_at) for key, event in by_key.items()}
    at: dict[LegOrderKey, dt.datetime | None] = {}
    group: dict[LegOrderKey, list[LegOrderKey]] = {key: [] for key in by_key}
    roots: list[LegOrderKey] = []
    for start in by_key:
        unplaced: list[LegOrderKey] = []
        key: LegOrderKey | None = start
        while key is not None and key not in at:
            unplaced.append(key)
            key = bounds[key]
        for key in reversed(unplaced):
            bound = bounds[key]
            at[key] = own[key] if bound is None else _sort_time(own[key], at[bound])
            if bound is not None and at[key] == at[bound]:
                group[bound].append(key)
            else:
                roots.append(key)

    def by_own_time(key: LegOrderKey) -> tuple:
        event = by_key[key]
        return (own[key] is None, own[key] or _LAST, event.seq, key.interaction_id, key.leg_type)

    rank: dict[LegOrderKey, int] = {}
    for root in sorted(roots, key=lambda key: (at[key] is None, at[key] or _LAST, by_own_time(key))):
        pending = [(root, iter(sorted(group[root], key=by_own_time)))]
        while pending:
            key, members = pending[-1]
            member = next(members, None)
            if member is None:
                pending.pop()
                rank[key] = len(rank)
            else:
                pending.append((member, iter(sorted(group[member], key=by_own_time))))
    return rank


def _preceder(event: LegOrderEvent, by_key: Mapping[LegOrderKey, LegOrderEvent]) -> LegOrderKey | None:
    """The one present leg *event* must follow: its parent's request for a
    request, its own request for a response."""
    if event.leg_type == "request":
        parent = event.parent_interaction_id
        before = LegOrderKey(parent, "request") if parent else None
    elif event.leg_type == "response":
        before = LegOrderKey(event.interaction_id, "request")
    else:
        before = None
    return before if before in by_key and before != event.key else None


def order_legs(events: Iterable[LegOrderEvent]) -> list[LegOrderKey]:
    """Order every known leg once, even with missing parents, times or bad cycles.

    A parent request precedes each child request; a call's request precedes its
    response. Each constraint applies only between legs that are both present.
    Among currently unconstrained legs, the earliest sort time wins. A leg's
    sort time is its occurrence time, pulled no later than its parent's
    response's sort time, so a synchronous child answers before its parent even
    when the callee's clock runs ahead, while the parent's response is never
    moved later for its child. An untimed child leg takes the parent response's
    time; a child leg timed more than :data:`CLOCK_SKEW_TOLERANCE` after it,
    which only an asynchronous child is, keeps its own. A child still in flight
    holds nothing back. Legs pulled into a response rank just before it, so at
    an equal sort time a descendant's response precedes its ancestor's;
    occurrence time, ``seq`` and the leg key break remaining ties. If
    invalid parent links form a cycle, the earliest leg on a remaining cycle is
    released first; a link on a cycle pulls no time, and a self-parent
    constrains nothing.
    """
    by_key = {event.key: event for event in events}
    rank = _sort_ranks(by_key)

    followers: dict[LegOrderKey, list[LegOrderKey]] = {key: [] for key in by_key}
    ready: list[tuple[int, LegOrderKey]] = []
    for key, event in by_key.items():
        before = _preceder(event, by_key)
        if before is None:
            ready.append((rank[key], key))
        else:
            followers[before].append(key)
    heapq.heapify(ready)

    remaining = set(by_key)
    ordered: list[LegOrderKey] = []
    while remaining:
        if ready:
            _, key = heapq.heappop(ready)
        else:
            # Every remaining leg waits on its one remaining preceder, so invalid
            # parent links form a cycle of requests. A leg follows at most one
            # other, so no leg outside a cycle constrains a leg on it: release
            # the earliest leg on a cycle. A leg merely downstream of one, such
            # as a valid child or a response, keeps waiting for its preceder.
            key = min(_on_cycles(remaining, followers), key=rank.__getitem__)
        remaining.remove(key)
        ordered.append(key)
        for after in followers[key]:
            # A leg released from a cycle has already been ordered.
            if after in remaining:
                heapq.heappush(ready, (rank[after], after))
    return ordered


def leg_positions(events: Iterable[LegOrderEvent]) -> dict[LegOrderKey, int]:
    """Each leg's zero-based position in :func:`order_legs`, iterating in that order.

    The one way a reader or the lineage derivation turns the causal order into
    sort keys, so every consumer numbers a trace's legs identically.
    """
    return {key: position for position, key in enumerate(order_legs(events))}


def _on_cycles(
    nodes: set[LegOrderKey], followers: Mapping[LegOrderKey, Iterable[LegOrderKey]]
) -> list[LegOrderKey]:
    """The legs of *nodes* that lie on a cycle among them. No leg follows
    itself, so they are the members of every strongly connected component of
    two or more legs."""
    component = _strong_components(nodes, followers)
    size = Counter(component.values())
    return [node for node in nodes if size[component[node]] > 1]


def _strong_components(
    nodes: set[_Node], followers: Mapping[_Node, Iterable[_Node]]
) -> dict[_Node, int]:
    """Tarjan's strongly connected components of the subgraph on *nodes*,
    iterative so that a long parent chain cannot exhaust the recursion limit."""
    index: dict[_Node, int] = {}
    low: dict[_Node, int] = {}
    component: dict[_Node, int] = {}
    stack: list[_Node] = []
    on_stack: set[_Node] = set()

    def visit(node: _Node) -> Iterator[_Node]:
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
