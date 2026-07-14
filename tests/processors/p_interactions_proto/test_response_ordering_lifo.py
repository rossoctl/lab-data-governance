"""Coverage for ADR-0007 / spec **Step 3.b point 2** — the GLOBAL-ORDINAL edge
ordering in `step3_entity_graph._order_responses_lifo`, which implements a
**recursive execution-order walk** of the chain nesting forest.

`order` is a TRUE GLOBAL ORDINAL: a single monotonic integer sequence across the
whole trace such that sorting the interactions by `order` ALONE yields the
correct display. It is derived from **both structure and timing** — structure
governs nesting (an inner chain's anchor span is a traceparent descendant of the
outer chain's anchor span) while `started_at` sequences sibling chains among
themselves and sequences the top-level/independent roots. No per-turn bands are
reused; every edge gets a globally-unique value.

The walk: order each node's children by `started_at`; for each child in turn
emit its **request**, recurse into its subtree, then emit its **response**;
finally the parent's own response. Consequences:

- **Deep nesting** ``A ─…─▶ B ─…─▶ C`` (C nested inside B) produces four
  consecutive monotonic edges — requests outer→inner, then responses inner→outer:

      order(A→B) < order(B→C) < order(C→B) < order(B→A)

  even though the anchor spans have DIFFERENT `started_at` (a parent span opens
  before its child) — nesting derives from traceparent STRUCTURE, not timestamps.

- **Sibling calls** (chains sharing a parent, neither a descendant of the other)
  INTERLEAVE: each leaf sibling's response is emitted immediately after its own
  request, and siblings are sequenced by `started_at` — NOT batched (that was the
  OLD rule this replaced). A sibling with its own nested subtree is fully unwound
  before its response.

For two SEPARATE groups at different times, every order in the earlier group is
less than every order in the later group (cross-turn chronology).

The pass is run as a pure function over hand-built chain records and `Span`s,
mirroring the other unit tests in this directory (builder/extractor as pure
functions over hand-built graphs, no Postgres).
"""

from __future__ import annotations

import datetime as dt

from data_governance.processors.p_interactions_proto.graph import EntityEdge
from data_governance.processors.p_interactions_proto.step3_entity_graph import (
    _order_responses_lifo,
)
from data_governance.retrieval import Span

_BASE = dt.datetime.fromisoformat("2026-06-14T12:00:00+00:00")


def _span(span_id: str, parent_id: str | None, secs: float) -> Span:
    """Minimal Span — only `span_id` / `parent_id` / `started_at` / `seq` drive
    the ordering pass."""
    t = _BASE + dt.timedelta(seconds=secs)
    return Span(
        seq=int(secs),
        trace_id="t",
        span_id=span_id,
        parent_id=parent_id,
        name=span_id,
        started_at=t,
        attributes={},
        observed_at=None,
        arrival_seq=int(secs),
        ended_at=t + dt.timedelta(seconds=0.1),
        scope={"name": "test"},
    )


def _chain(anchor: str, call_order: int = 0) -> dict:
    """One reconstructed chain record as `build_entity_graph` builds it: a
    call edge, a response edge, its anchor span id, and its Step 2.c seed band
    (overwritten by the global-ordinal pass)."""
    return {
        "call": EntityEdge.make("s", "d", order=call_order),
        "resp": EntityEdge.make("d", "s", order=call_order + 1),
        "call_order": call_order,
        "anchor": anchor,
    }


def test_nested_chains_get_consecutive_lifo_ordinals():
    """Three chains nested by traceparent (A anchor ⟶ B anchor ⟶ C anchor, each
    a child of the previous, each at a LATER started_at) must produce a
    consecutive, monotonic LIFO ordinal:

        order(A→B) < order(B→C) < order(C→B) < order(B→A)

    i.e. requests outer→inner, then responses inner→outer, all four consecutive.
    """
    # Traceparent chain a ◂ b ◂ c, with strictly increasing started_at so the
    # ordinal cannot merely echo started_at for the responses.
    spans = {
        "a": _span("a", None, 0),
        "b": _span("b", "a", 1),
        "c": _span("c", "b", 2),
    }
    chain_a = _chain("a")  # outer
    chain_b = _chain("b")  # middle
    chain_c = _chain("c")  # inner
    chains = [chain_a, chain_b, chain_c]

    _order_responses_lifo(chains, spans)

    ab = chain_a["call"].order  # A→B (outer request)
    bc = chain_b["call"].order  # B→C (inner request)
    cb = chain_c["resp"].order  # C→B (inner response)
    ba = chain_b["resp"].order  # B→A (outer response)

    # LIFO, consecutive and monotonic.
    assert ab < bc < cb < ba
    # A single group of 3 chains occupies 6 consecutive ordinals 0..5.
    all_orders = sorted(c[k].order for c in chains for k in ("call", "resp"))
    assert all_orders == [0, 1, 2, 3, 4, 5]
    # Requests are the first half (outer→inner), responses the second (inner→out).
    assert [chain_a["call"].order, chain_b["call"].order, chain_c["call"].order] == [0, 1, 2]
    assert [chain_c["resp"].order, chain_b["resp"].order, chain_a["resp"].order] == [3, 4, 5]


def test_nesting_ignores_started_at_direction():
    """Determinism/structure guard: even if the INNER chain's anchor happens to
    have an EARLIER started_at than the outer (clock skew / out-of-order
    ingestion), the ordinal still follows traceparent STRUCTURE (inner response
    first), not the timestamp."""
    spans = {
        "a": _span("a", None, 5),   # outer anchor starts LATER
        "b": _span("b", "a", 0),    # inner anchor starts EARLIER
    }
    outer = _chain("a")
    inner = _chain("b")
    _order_responses_lifo([outer, inner], spans)

    # Inner (b) is a traceparent descendant of outer (a): inner response first.
    assert inner["resp"].order < outer["resp"].order
    # Inner request after outer request.
    assert outer["call"].order < inner["call"].order
    # All four consecutive within the one group.
    assert sorted(
        c[k].order for c in (outer, inner) for k in ("call", "resp")
    ) == [0, 1, 2, 3]


def test_two_separate_groups_stay_chronological():
    """Two INDEPENDENT delegations at DIFFERENT times must not interleave: every
    ordinal of the earlier group is less than every ordinal of the later group
    (cross-turn chronology), while each group is internally LIFO.

    This locks the property the naive band-flip destroyed: the global counter
    orders groups by their root's started_at, so a later turn's edges never sort
    ahead of an earlier turn's.
    """
    # Group 1 (earlier): traceparent p1 ◂ q1, roots at t=0.
    # Group 2 (later):   traceparent p2 ◂ q2, roots at t=100.
    spans = {
        "p1": _span("p1", None, 0),
        "q1": _span("q1", "p1", 1),
        "p2": _span("p2", None, 100),
        "q2": _span("q2", "p2", 101),
    }
    g1_outer = _chain("p1")
    g1_inner = _chain("q1")
    g2_outer = _chain("p2")
    g2_inner = _chain("q2")

    # Feed the LATER group first to prove ordering is by anchor, not list order.
    _order_responses_lifo([g2_outer, g2_inner, g1_outer, g1_inner], spans)

    g1_orders = [c[k].order for c in (g1_outer, g1_inner) for k in ("call", "resp")]
    g2_orders = [c[k].order for c in (g2_outer, g2_inner) for k in ("call", "resp")]

    # The earlier group occupies the lower half of the global sequence.
    assert max(g1_orders) < min(g2_orders)
    # Contiguous 0..7 with no interleave.
    assert sorted(g1_orders) == [0, 1, 2, 3]
    assert sorted(g2_orders) == [4, 5, 6, 7]
    # Each group internally LIFO.
    assert g1_outer["call"].order < g1_inner["call"].order
    assert g1_inner["resp"].order < g1_outer["resp"].order
    assert g2_outer["call"].order < g2_inner["call"].order
    assert g2_inner["resp"].order < g2_outer["resp"].order


def test_independent_singletons_ordered_by_time_and_deterministic():
    """Independent (non-nested) chains are each their own group; groups sort by
    the root anchor's started_at, and equal-time groups break ties by anchor
    span_id — so the assignment is fully deterministic regardless of input list
    order."""
    spans = {
        "early": _span("early", None, 0),
        "mid": _span("mid", None, 5),
        "late": _span("late", None, 9),
    }
    early = _chain("early")
    mid = _chain("mid")
    late = _chain("late")

    _order_responses_lifo([late, early, mid], spans)  # scrambled input order

    # Chronological by anchor started_at: early < mid < late for BOTH legs.
    assert early["call"].order < mid["call"].order < late["call"].order
    assert early["resp"].order < mid["resp"].order < late["resp"].order
    # A singleton group is call-then-response, consecutive.
    assert early["resp"].order == early["call"].order + 1


def test_equal_time_groups_break_ties_by_span_id_stably():
    """Two independent chains sharing a `started_at` still get DISTINCT, stable
    ordinals — the tie breaks by anchor span_id, independent of list order."""
    spans = {
        "x": _span("x", None, 1),
        "y": _span("y", None, 1),  # same started_at as x
    }
    cx = _chain("x")
    cy = _chain("y")
    cx2 = _chain("x")
    cy2 = _chain("y")
    _order_responses_lifo([cx, cy], spans)
    _order_responses_lifo([cy2, cx2], spans)  # opposite list order

    # Distinct and stable regardless of input order.
    assert cx["call"].order != cy["call"].order
    assert cx["call"].order == cx2["call"].order
    assert cy["call"].order == cy2["call"].order
    assert cx["resp"].order == cx2["resp"].order
    assert cy["resp"].order == cy2["resp"].order
    # span_id "x" < "y", so x's group sorts first.
    assert cx["call"].order < cy["call"].order


def test_single_chain_gets_ordinal_zero():
    """A lone chain is its own group: request 0, response 1."""
    spans = {"a": _span("a", None, 0)}
    only = _chain("a")
    _order_responses_lifo([only], spans)
    assert only["call"].order == 0
    assert only["resp"].order == 1


def test_leaf_siblings_interleave_ordered_by_started_at():
    """Spec Example 2 — a parent B making three sequential sibling calls L, T, L
    (none nested in another) INTERLEAVES: each leaf sibling returns immediately
    (its response right after its request), siblings sequenced by `started_at`.

    Modelled as: an outer chain ``B`` (anchor span ``b``) and three child chains
    whose anchors (``l1``, ``t``, ``l2``) are all direct traceparent children of
    ``b`` — so they are siblings of each other, none a descendant of another. The
    OLD batch-LIFO rule would emit all three requests then all three responses;
    the recursive walk emits request/response per sibling, in `started_at` order.
    """
    spans = {
        "b": _span("b", None, 0),     # outer (parent) anchor
        "l1": _span("l1", "b", 1),    # first sibling  (L)
        "t": _span("t", "b", 2),      # second sibling (T)
        "l2": _span("l2", "b", 3),    # third sibling  (L)
    }
    b = _chain("b")
    l1 = _chain("l1")
    t = _chain("t")
    l2 = _chain("l2")
    # Scramble input order to prove the walk sorts children by started_at.
    _order_responses_lifo([l2, b, t, l1], spans)

    seq = [
        b["call"].order,    # A → B
        l1["call"].order,   # B → L
        l1["resp"].order,   # L → B  (immediately after its request)
        t["call"].order,    # B → T
        t["resp"].order,    # T → B
        l2["call"].order,   # B → L
        l2["resp"].order,   # L → B
        b["resp"].order,    # B → A  (parent's own response last)
    ]
    assert seq == [0, 1, 2, 3, 4, 5, 6, 7]


def test_nested_sibling_fully_unwound_before_next_sibling():
    """A sibling that itself has a nested subtree is FULLY UNWOUND (its inner
    call recursed and returned) before its own response — and therefore before
    the next sibling begins.

    Parent B has two sibling children: X (which itself calls a nested Y) and Z.
    X starts before Z. The walk must produce, for X's subtree: B→X, X→Y, Y→X,
    X→B — all before Z's B→Z, Z→B.
    """
    spans = {
        "b": _span("b", None, 0),    # parent
        "x": _span("x", "b", 1),     # first sibling (has a nested child)
        "y": _span("y", "x", 2),     # nested inside x
        "z": _span("z", "b", 3),     # second sibling (leaf), starts after x
    }
    b = _chain("b")
    x = _chain("x")
    y = _chain("y")
    z = _chain("z")
    _order_responses_lifo([z, y, x, b], spans)  # scrambled

    seq = [
        b["call"].order,   # A → B
        x["call"].order,   # B → X
        y["call"].order,   # X → Y   (recurse into X's subtree)
        y["resp"].order,   # Y → X
        x["resp"].order,   # X → B   (X fully unwound before Z)
        z["call"].order,   # B → Z
        z["resp"].order,   # Z → B
        b["resp"].order,   # B → A
    ]
    assert seq == [0, 1, 2, 3, 4, 5, 6, 7]
