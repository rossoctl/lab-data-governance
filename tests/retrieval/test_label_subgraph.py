"""The derivations over the label-lineage sub-graph (``retrieval.labels``), pure:
how much of the sub-graph each one reads and holds, and how large the route count
may grow. The routes, counts and carriers themselves are pinned against a DB in
``test_get_leg_labels.py``.

A holding (a payload holding one origin unit) with N edges in and N edges out
must cost about N, not N x N: every edge out of it has the same parents, so the
derivations work per holding, never per pair of edges.
"""

from __future__ import annotations

import tracemalloc

from data_governance.retrieval import labels

_UNIT = ("ix-o", "response", "o-hash", 0, 9, "PID")


def _row(eid: str, to: str, frm: str, at_origin: bool) -> tuple:
    """One sub-graph row (:data:`labels._SUBGRAPH_SQL`'s columns): an edge from
    the holding at leg *frm* to the holding at leg *to*, in one trace."""
    return (eid, "t", f"ix-{to}", "request", f"{to}-hash", "t", f"ix-{frm}", "request", f"{frm}-hash",
            *_UNIT, at_origin)


class _CountedList(list):
    """A list that adds its length to *reads* every time it is iterated."""

    def __init__(self, items: list[str], reads: list[int]) -> None:
        super().__init__(items)
        self.reads = reads

    def __iter__(self):
        self.reads[0] += len(self)
        return super().__iter__()


class _CountedDict(dict):
    """A dict that adds one to *reads* on every lookup by key."""

    def __init__(self, items: dict, reads: list[int]) -> None:
        super().__init__(items)
        self.reads = reads

    def __getitem__(self, key):
        self.reads[0] += 1
        return super().__getitem__(key)


def test_a_holding_with_n_edges_in_and_n_out_costs_n_not_n_squared() -> None:
    n = 500
    rows = [_row(f"in-{i}", "x", "o", True) for i in range(n)]       # the value reaches x n times ...
    rows += [_row(f"out-{j}", "y", "x", False) for j in range(n)]    # ... and leaves it n times
    anchors = [f"out-{j}" for j in range(n)]
    sub = labels._sub_graph(rows)
    # Each edge is held once, under the holding it runs into; no edge holds a copy of its parents.
    assert sum(len(ids) for ids in sub.into.values()) == 2 * n
    # Count the work: every edge id read from a holding's list, every edge looked up.
    reads = [0]
    for holding, ids in list(sub.into.items()):
        sub.into[holding] = _CountedList(ids, reads)
    sub = labels._SubGraph(_CountedDict(sub.edges, reads), sub.into)

    counts = labels._route_counts(anchors, sub)
    routes = labels._shortest_routes(anchors, sub)
    carriers = labels._carriers(anchors, sub)

    assert counts == {a: n for a in anchors}
    assert all(routes[a] == ([a, "in-0"], True) for a in anchors)
    assert all(len(carriers[a]) == 3 for a in anchors)
    # A few reads per edge (15 here), never one per pair: reading x's n edges
    # once per edge out of x is n * n = 250,000.
    assert reads[0] <= 30 * len(rows), reads[0]


def test_the_route_count_saturates_at_the_cap() -> None:
    # 100 diamonds in series: 2**100 routes reach the last holding; the count
    # stops at the cap.
    k = 100
    rows = []
    for i in range(k):
        frm = "o" if i == 0 else f"p{i}"
        rows += [_row(f"d{i}", f"p{i + 1}", frm, i == 0), _row(f"a{i}", f"m{i}", frm, i == 0),
                 _row(f"b{i}", f"p{i + 1}", f"m{i}", False)]
    sub = labels._sub_graph(rows)
    counts = labels._route_counts([f"d{k - 1}", f"b{k - 1}"], sub)
    assert counts == {f"d{k - 1}": labels.MAX_ROUTE_COUNT, f"b{k - 1}": labels.MAX_ROUTE_COUNT}


def test_the_carriers_of_a_long_chain_hold_a_few_bit_sets_at_a_time() -> None:
    # A chain of 20,000 holdings: what lies behind the k-th is k bits. Kept for
    # every holding, that is 20,000**2 / 2 bits (25 MB); each is dropped once the
    # next holding has read it.
    n = 20_000
    rows = [_row(f"c{i}", f"h{i}", "o" if i == 0 else f"h{i - 1}", i == 0) for i in range(n)]
    sub = labels._sub_graph(rows)
    tracemalloc.start()
    try:
        carriers = labels._carriers([f"c{n - 1}"], sub)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert len(carriers[f"c{n - 1}"]) == n + 1
    assert peak < 10_000_000, peak


def test_a_cycle_through_the_leg_read_ends_where_the_walk_meets_the_leg_read() -> None:
    # A data fault: y took the value from x, and x from y. The route ends at the
    # edge that brings the walk back to y, the leg read, instead of running on
    # through y's edges again; its chain opens with y as a hop and ends with y here.
    rows = [_row("y-x", "y", "x", False), _row("x-y", "x", "y", False)]
    sub = labels._sub_graph(rows)
    assert labels._shortest_routes(["y-x"], sub) == {"y-x": (["y-x", "x-y"], False)}


def test_a_route_that_cannot_reach_the_origin_ends_at_the_nearest_dead_end() -> None:
    # z took the value from x; x from a (a <- a2 <- a3) and from b. Neither a3
    # nor b holds an edge of its own; b is the nearer, though x-a sorts first.
    rows = [_row("z-x", "z", "x", False), _row("x-a", "x", "a", False), _row("a-a2", "a", "a2", False),
            _row("a2-a3", "a2", "a3", False), _row("x-b", "x", "b", False)]
    sub = labels._sub_graph(rows)
    assert labels._shortest_routes(["z-x"], sub) == {"z-x": (["z-x", "x-b"], False)}


class _Tx:
    """A transaction whose edges are deleted between the sub-graph read and the hops read."""

    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    def fetch_all(self, sql: str, params) -> list[tuple]:
        return self.rows if sql == labels._SUBGRAPH_SQL else []


def test_an_edge_deleted_between_the_reads_keeps_its_count_and_carriers() -> None:
    # A re-derivation in flight deletes the edges after the sub-graph was read:
    # the chain cannot be built, the count and the carriers still stand.
    lineage = labels._read_lineage(_Tx([_row("y-x", "y", "x", False), _row("x-o", "x", "o", True)]), ["y-x"])
    (edge,) = lineage.values()
    assert (edge.chain, edge.route_count, len(edge.carriers)) == (None, 1, 3)
