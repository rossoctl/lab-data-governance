"""Pure tests for the lineage walk — the edge rule, with no database (ADR-0028 D14).

``_adjacency`` + ``_walk`` are the whole of the traversal's *semantics*: which hops
exist, which direction they run, where the walk stops and what it discloses when it
stops early. Testing them directly (hand-built rows in, plain values out) is what
keeps those claims assertable without a migrated DB per case — the DB-backed tests in
``test_lineage_graph.py`` then only have to prove the SQL feeds these correctly.

Row shape throughout is the one :func:`~data_governance.retrieval.lineage_graph.
_fetch_legs` returns: ``(interaction_id, leg_type, seq, caller_id, callee_id,
has_lineage)``.
"""

from __future__ import annotations

from data_governance.retrieval.lineage_graph import (
    FANIN,
    FANOUT,
    _adjacency,
    _walk,
)


def _row(
    interaction_id: str,
    leg_type: str,
    seq: int,
    caller: str | None,
    callee: str | None,
    has_lineage: bool = True,
) -> tuple:
    return (interaction_id, leg_type, seq, caller, callee, has_lineage)


def _reach(rows: list[tuple], seed: str, direction: str, **kwargs) -> dict[str, int]:
    """``entity_id -> hops`` for *seed* in *direction*."""
    derived, undelivered = _adjacency(rows, direction)
    return _walk(derived, undelivered, seed, **kwargs)[0]


# ---------------------------------------------------------------------------
# Direction — the per-leg rule, which is the subtle half of the edge rule
# ---------------------------------------------------------------------------


def test_request_leg_runs_caller_to_callee() -> None:
    """A request is produced by the caller and delivered to the callee."""
    rows = [_row("i1", "request", 1, "caller", "callee")]
    assert _reach(rows, "caller", FANOUT) == {"callee": 1}
    assert _reach(rows, "callee", FANOUT) == {}


def test_response_leg_runs_callee_to_caller() -> None:
    """A response travels BACK: callee -> caller (ADR-0025).

    The load-bearing case for using per-leg direction rather than the interaction's
    fixed caller->callee. An agent's data mostly arrives as the responses to calls it
    made, so a walk keyed on the interaction's direction would miss all of it.
    """
    rows = [_row("i1", "response", 1, "caller", "callee")]
    assert _reach(rows, "callee", FANOUT) == {"caller": 1}
    assert _reach(rows, "caller", FANOUT) == {}


def test_fanin_is_fanout_reversed() -> None:
    """``fanin`` walks against the flow — that is the entire difference."""
    rows = [_row("i1", "request", 1, "a", "b")]
    assert _reach(rows, "b", FANIN) == {"a": 1}
    assert _reach(rows, "a", FANIN) == {}


def test_the_two_directions_answer_different_questions() -> None:
    """On an asymmetric trace (requests only), fanin and fanout genuinely differ.

    ``u -> a``, ``a -> t``, ``a -> l``. The tool's provenance runs back to the user;
    it has no downstream at all.
    """
    rows = [
        _row("i1", "request", 1, "u", "a"),
        _row("i2", "request", 2, "a", "t"),
        _row("i3", "request", 3, "a", "l"),
    ]
    assert _reach(rows, "u", FANOUT) == {"a": 1, "t": 2, "l": 2}
    assert _reach(rows, "u", FANIN) == {}
    assert _reach(rows, "t", FANIN) == {"a": 1, "u": 2}
    assert _reach(rows, "t", FANOUT) == {}


# ---------------------------------------------------------------------------
# The lineage filter — "no lineage through an entity ends the walk"
# ---------------------------------------------------------------------------


def test_a_leg_without_derived_lineage_is_not_an_edge() -> None:
    """The spec's rule: the walk stops where provenance stops.

    ``u -> a`` is derived, ``a -> t`` is not, so ``t`` is NOT reached — even though
    the trace structurally connects them.
    """
    rows = [
        _row("i1", "request", 1, "u", "a", True),
        _row("i2", "request", 2, "a", "t", False),
    ]
    assert _reach(rows, "u", FANOUT) == {"a": 1}


def test_an_undelivered_leg_is_reported_as_pending_frontier() -> None:
    """Not reached is not the same as not there.

    The entity beyond an undelivered leg is named, so "P-data-lineage has not got
    here yet" stays distinguishable from "provenance genuinely ends here" — two facts
    an empty tail cannot tell apart.
    """
    rows = [
        _row("i1", "request", 1, "u", "a", True),
        _row("i2", "request", 2, "a", "t", False),
    ]
    derived, undelivered = _adjacency(rows, FANOUT)
    hops, _legs, frontier, truncated = _walk(derived, undelivered, "u")
    assert hops == {"a": 1}
    assert frontier == ["t"]
    assert truncated is False


def test_an_entity_reached_by_a_derived_route_is_not_pending() -> None:
    """A derived route to an entity settles it, whatever else is undelivered.

    ``a`` is reachable both by a derived leg and by an undelivered one. It belongs in
    the answer, not on the frontier — otherwise the same entity would be reported as
    both found and pending.
    """
    rows = [
        _row("i1", "request", 1, "u", "a", True),
        _row("i2", "request", 2, "u", "a", False),
    ]
    derived, undelivered = _adjacency(rows, FANOUT)
    hops, _legs, frontier, _trunc = _walk(derived, undelivered, "u")
    assert hops == {"a": 1}
    assert frontier == []


def test_the_seed_is_never_its_own_frontier() -> None:
    """An undelivered leg pointing back at the seed does not make it pending."""
    rows = [_row("i1", "response", 1, "u", "a", False)]
    derived, undelivered = _adjacency(rows, FANIN)
    _hops, _legs, frontier, _trunc = _walk(derived, undelivered, "u")
    assert "u" not in frontier


# ---------------------------------------------------------------------------
# Termination — the cycle guard is load-bearing here, unlike in a span tree
# ---------------------------------------------------------------------------


def test_a_request_response_pair_is_a_cycle_and_terminates() -> None:
    """``agent -> tool -> agent`` is the ORDINARY shape of a tool call.

    Both legs of one interaction form a two-node cycle, so without the visited set
    the commonest case in the corpus would not terminate. This is the difference from
    the span-tree walks in ``processors/interactions/state.py``, which need no guard
    because a tree cannot cycle.
    """
    rows = [
        _row("i1", "request", 1, "a", "t"),
        _row("i1", "response", 2, "a", "t"),
    ]
    hops = _reach(rows, "a", FANOUT)
    assert hops == {"t": 1}


def test_a_longer_cycle_terminates() -> None:
    """``a -> b -> c -> a`` closes back on the seed."""
    rows = [
        _row("i1", "request", 1, "a", "b"),
        _row("i2", "request", 2, "b", "c"),
        _row("i3", "request", 3, "c", "a"),
    ]
    assert _reach(rows, "a", FANOUT) == {"b": 1, "c": 2}


def test_a_self_call_terminates() -> None:
    """An entity calling itself is a one-node cycle."""
    rows = [_row("i1", "request", 1, "a", "a")]
    assert _reach(rows, "a", FANOUT) == {}


def test_a_traversed_leg_into_a_visited_entity_is_still_reported() -> None:
    """The route includes the hop even when it reaches nothing new.

    A cycle's closing leg really happened; omitting it would draw the cycle as a dead
    end and leave the reader without the arrow that closes it.
    """
    rows = [
        _row("i1", "request", 1, "a", "b"),
        _row("i2", "request", 2, "b", "a"),
    ]
    derived, undelivered = _adjacency(rows, FANOUT)
    _hops, legs, _frontier, _trunc = _walk(derived, undelivered, "a")
    assert [(leg.from_entity_id, leg.to_entity_id) for leg in legs] == [
        ("a", "b"),
        ("b", "a"),
    ]


# ---------------------------------------------------------------------------
# Hops are a shortest distance, and bounds are disclosed
# ---------------------------------------------------------------------------


def test_hops_is_the_shortest_route() -> None:
    """Breadth-first, so first arrival is the fewest hops.

    ``d`` is reachable in one hop directly and in three the long way round; the short
    answer is the one reported.
    """
    rows = [
        _row("i1", "request", 1, "a", "b"),
        _row("i2", "request", 2, "b", "c"),
        _row("i3", "request", 3, "c", "d"),
        _row("i4", "request", 4, "a", "d"),
    ]
    assert _reach(rows, "a", FANOUT)["d"] == 1


def test_the_hop_bound_truncates_and_says_so() -> None:
    """A bounded answer is reported as bounded, never silently returned as whole."""
    rows = [_row(f"i{n}", "request", n, f"e{n}", f"e{n + 1}") for n in range(10)]
    derived, undelivered = _adjacency(rows, FANOUT)
    hops, _legs, _frontier, truncated = _walk(
        derived, undelivered, "e0", max_hops=3
    )
    assert hops == {"e1": 1, "e2": 2, "e3": 3}
    assert truncated is True


def test_the_entity_bound_truncates_and_says_so() -> None:
    rows = [_row(f"i{n}", "request", n, "hub", f"leaf{n}") for n in range(10)]
    derived, undelivered = _adjacency(rows, FANOUT)
    hops, _legs, _frontier, truncated = _walk(
        derived, undelivered, "hub", max_entities=4
    )
    assert len(hops) == 4
    assert truncated is True


def test_a_cap_skipped_entity_is_truncated_not_pending() -> None:
    """The two disclosures mean different things and must not be conflated.

    ``pending_frontier`` says "not derived yet, ask again later"; a cap-skipped entity
    IS derived and was merely not returned, so reporting it as pending would send a
    caller back to poll for something no amount of waiting will deliver. It is
    ``truncated``'s business alone.
    """
    rows = [_row(f"i{n}", "request", n, "hub", f"leaf{n}") for n in range(6)]
    derived, undelivered = _adjacency(rows, FANOUT)
    hops, _legs, frontier, truncated = _walk(
        derived, undelivered, "hub", max_entities=2
    )
    assert len(hops) == 2
    assert truncated is True
    assert frontier == []


def test_truncation_and_pending_can_be_reported_together() -> None:
    """They are independent facts, so a walk can hit a bound AND have a frontier."""
    rows = [
        _row("i0", "request", 0, "hub", "a", True),
        _row("i1", "request", 1, "hub", "b", True),
        _row("i2", "request", 2, "hub", "c", False),
    ]
    derived, undelivered = _adjacency(rows, FANOUT)
    hops, _legs, frontier, truncated = _walk(
        derived, undelivered, "hub", max_entities=1
    )
    assert len(hops) == 1
    assert truncated is True
    assert frontier == ["c"]


def test_an_exactly_bounded_walk_is_not_truncated() -> None:
    """Reaching the bound with nowhere left to go is a COMPLETE answer.

    The flag means "there was more"; a walk that happens to end on the boundary must
    not cry truncation, or every reader learns to ignore it.
    """
    rows = [_row("i1", "request", 1, "a", "b")]
    derived, undelivered = _adjacency(rows, FANOUT)
    _hops, _legs, _frontier, truncated = _walk(
        derived, undelivered, "a", max_hops=1
    )
    assert truncated is False


# ---------------------------------------------------------------------------
# Unresolvable participants
# ---------------------------------------------------------------------------


def test_a_leg_with_an_unresolved_participant_is_no_edge() -> None:
    """A NULL caller/callee (the caller-inference window) yields no hop.

    There is no node to walk to, and naming one would be a claim about an entity the
    data cannot identify.
    """
    assert _adjacency([_row("i1", "request", 1, None, "a")], FANOUT) == ({}, {})
    assert _adjacency([_row("i1", "request", 1, "a", None)], FANOUT) == ({}, {})


def test_an_absent_seed_reaches_nothing() -> None:
    """A seed with no legs in the trace is an empty walk, not an error."""
    rows = [_row("i1", "request", 1, "a", "b")]
    assert _reach(rows, "nobody", FANOUT) == {}


def test_an_empty_trace_reaches_nothing() -> None:
    assert _reach([], "a", FANOUT) == {}
