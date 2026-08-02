"""Pure tests for the lineage walk — the edge rule, with no database (ADR-0028 D15).

``_adjacency`` + ``_walk`` are the whole of the traversal's *semantics*: which hops
exist, which direction they run, whether **this source** travelled on them, whether the
leg is on the right side of the arrival in time, where the walk stops and what it
discloses when it stops early. Testing them directly (hand-built rows in, plain values
out) is what keeps those claims assertable without a migrated DB per case — the
DB-backed tests in ``test_lineage_graph.py`` then only have to prove the SQL feeds these
correctly.

Row shape throughout is the one :func:`~data_governance.retrieval.lineage_graph.
_fetch_legs` returns: ``(interaction_id, leg_type, seq, caller_id, callee_id,
data_sources)``, where ``data_sources`` is the **stored** ``TEXT[]`` — or ``None`` for a
leg whose lineage is not derived yet. It is a set, not a boolean: the previous shape was
``has_lineage: bool``, and reducing the row to that boolean is precisely what made the
old walk unable to test the spec's hop rule at all.

The four clauses of the edge rule, and where each is covered:

1. per-leg direction — "Direction" below;
2. a derived lineage row exists — "The lineage filter";
3. the seeded source is in that row's ``data_sources`` — "The source filter";
4. ``seq`` runs away from the arrival — "The sequence filter".
"""

from __future__ import annotations

from data_governance.retrieval.lineage_graph import (
    FANIN,
    FANOUT,
    _adjacency,
    _walk,
)

# The source under trace in every case that does not care which one it is. A short
# stand-in for the natural keys the derivation actually writes
# (``tool:agent:(travel_advisor,travel-advisor):search_destinations`` and friends).
_S = "src"

# "Argument not supplied", distinct from an explicitly passed ``None``. ``None`` is a
# MEANINGFUL value here — it is the ``LEFT JOIN``'s null, i.e. "no derived row yet" — so
# it cannot double as the default sentinel. Using it for both is how the first draft of
# this module silently turned every undelivered leg into a derived one.
_UNSET = object()


def _row(
    interaction_id: str,
    leg_type: str,
    seq: int,
    caller: str | None,
    callee: str | None,
    data_sources: list[str] | None | object = _UNSET,
) -> tuple:
    """One ``_fetch_legs`` row.

    ``data_sources`` defaults to "derived, carries ``_S``" so a case that is not about
    the source filter does not have to say so. Pass ``None`` explicitly for an
    undelivered leg, or a list for a derived one with a specific set.
    """
    return (
        interaction_id,
        leg_type,
        seq,
        caller,
        callee,
        [_S] if data_sources is _UNSET else data_sources,
    )


def _reach(
    rows: list[tuple], seed: str, direction: str, source: str = _S, **kwargs
) -> dict[str, int]:
    """``entity_id -> hops`` for *seed* in *direction*, tracing *source*."""
    derived, undelivered = _adjacency(rows, direction, source)
    return _walk(derived, undelivered, seed, direction, **kwargs)[0]


def _walk_all(
    rows: list[tuple], seed: str, direction: str, source: str = _S, **kwargs
) -> tuple:
    """The full ``(hops, legs, frontier, truncated)`` tuple."""
    derived, undelivered = _adjacency(rows, direction, source)
    return _walk(derived, undelivered, seed, direction, **kwargs)


# ---------------------------------------------------------------------------
# Clause 1 — per-leg direction, the subtle half of the topology
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


def test_fanin_walks_against_the_flow() -> None:
    """``fanin``'s topology is ``fanout``'s reversed — but see the next test.

    Reversing the edges is necessary and NOT sufficient: on a symmetric edge set,
    reversal alone is a no-op, which is exactly the bug the seq rule fixes.
    """
    rows = [_row("i1", "request", 1, "a", "b")]
    assert _reach(rows, "b", FANIN) == {"a": 1}
    assert _reach(rows, "a", FANIN) == {}


def test_fanin_and_fanout_differ_on_a_symmetric_edge_set() -> None:
    """**The regression that motivated ADR-0028 D15's amendment.**

    A request/response pair makes the aggregate edge set symmetric, so reversing it —
    which is all ``_adjacency`` does — cannot distinguish the two directions. The first
    shipped walk therefore returned byte-identical answers for ``fanin`` and ``fanout``
    on the live corpus. What separates them is clause 4: ``b``'s data went *onward* only
    after seq 2, and came *from* upstream only before it.

    ``a -> b`` at seq 1, ``b -> a`` at seq 2, ``a -> c`` at seq 3.
    """
    rows = [
        _row("i1", "request", 1, "a", "b"),
        _row("i1", "response", 2, "a", "b"),
        _row("i2", "request", 3, "a", "c"),
    ]
    # Seeded at `b`: its data leaves on seq 2 and reaches `c` via seq 3.
    assert _reach(rows, "b", FANOUT) == {"a": 1, "c": 2}
    # Upstream of `b` is only what happened BEFORE seq 2 — `a` at seq 1. `c` is at
    # seq 3, after `b` was done, so it cannot be `b`'s ancestor.
    assert _reach(rows, "b", FANIN) == {"a": 1}
    assert _reach(rows, "b", FANOUT) != _reach(rows, "b", FANIN)


def test_the_two_directions_answer_different_questions() -> None:
    """On an asymmetric trace (requests only), fanin and fanout genuinely differ.

    ``u -> a`` (1), ``a -> t`` (2), ``a -> l`` (3). The tool's provenance runs back to
    the user; it has no downstream at all.
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
# Clause 2 — "no derived lineage row ends the walk", and is disclosed
# ---------------------------------------------------------------------------


def test_a_leg_without_derived_lineage_is_not_an_edge() -> None:
    """The spec's rule: the walk stops where provenance stops.

    ``u -> a`` is derived, ``a -> t`` is not (``data_sources`` is ``None`` — no row), so
    ``t`` is NOT reached even though the trace structurally connects them.
    """
    rows = [
        _row("i1", "request", 1, "u", "a"),
        _row("i2", "request", 2, "a", "t", None),
    ]
    assert _reach(rows, "u", FANOUT) == {"a": 1}


def test_an_undelivered_leg_is_reported_as_pending_frontier() -> None:
    """Not reached is not the same as not there.

    The entity beyond an undelivered leg is named, so "P-data-lineage has not got
    here yet" stays distinguishable from "provenance genuinely ends here" — two facts
    an empty tail cannot tell apart.
    """
    rows = [
        _row("i1", "request", 1, "u", "a"),
        _row("i2", "request", 2, "a", "t", None),
    ]
    hops, _legs, frontier, truncated = _walk_all(rows, "u", FANOUT)
    assert hops == {"a": 1}
    assert frontier == ["t"]
    assert truncated is False


def test_an_entity_reached_by_an_eligible_route_is_not_pending() -> None:
    """An eligible route to an entity settles it, whatever else is undelivered.

    ``a`` is reachable both by a derived leg and by an undelivered one. It belongs in
    the answer, not on the frontier — otherwise the same entity would be reported as
    both found and pending.
    """
    rows = [
        _row("i1", "request", 1, "u", "a"),
        _row("i2", "request", 2, "u", "a", None),
    ]
    hops, _legs, frontier, _trunc = _walk_all(rows, "u", FANOUT)
    assert hops == {"a": 1}
    assert frontier == []


def test_a_seq_INELIGIBLE_undelivered_leg_is_not_on_the_frontier() -> None:
    """Clause 4 applies to the frontier too — and clause 3 deliberately does not.

    The asymmetry is not arbitrary. A leg's ``seq`` lives on ``interaction_legs`` and is
    known **whether or not** the lineage row has landed; its ``data_sources`` is exactly
    the thing that has not landed. So:

    - seq-ineligible + undelivered -> already a settled "never an edge". Excluded, because
      naming it pending would promise growth no derivation can deliver.
    - seq-eligible + undelivered -> genuinely unknown. Named, honestly.

    Here the seed departs at seq 50, and ``a``'s only other leg is an undelivered one at
    seq 10 — before the content ever arrived. ``early`` is unreachable no matter what
    P-data-lineage eventually writes, so the frontier is empty.
    """
    rows = [
        _row("i0", "request", 50, "seed", "a"),
        _row("i1", "request", 10, "a", "early", None),
    ]
    hops, _legs, frontier, _trunc = _walk_all(rows, "seed", FANOUT)
    assert hops == {"a": 1}
    assert frontier == []


def test_a_seq_ELIGIBLE_undelivered_leg_is_on_the_frontier() -> None:
    """The companion: same shape, the undelivered leg merely moved past the arrival.

    Asserting the pair is what pins the distinction — otherwise a future editor could
    drop the seq filter on the frontier (over-promising) or add a source filter to it
    (under-promising, since the source is precisely what is not yet known) and no test
    would notice.
    """
    rows = [
        _row("i0", "request", 50, "seed", "a"),
        _row("i1", "request", 90, "a", "later", None),
    ]
    hops, _legs, frontier, _trunc = _walk_all(rows, "seed", FANOUT)
    assert hops == {"a": 1}
    assert frontier == ["later"]


def test_the_seed_is_never_its_own_frontier() -> None:
    """An undelivered leg pointing back at the seed does not make it pending."""
    rows = [_row("i1", "response", 1, "u", "a", None)]
    _hops, _legs, frontier, _trunc = _walk_all(rows, "u", FANIN)
    assert "u" not in frontier


# ---------------------------------------------------------------------------
# Clause 3 — the SOURCE filter. The spec: "traverse an edge towards the
# next/previous entity iff the source is part of the ... metadata sources".
# ---------------------------------------------------------------------------


def test_the_walk_stops_where_the_source_is_absent() -> None:
    """A derived leg that does not carry THIS source is not an edge.

    ``u -> a`` carries ``src``; ``a -> t`` is fully derived but attributes its content
    to ``other`` only. So ``src``'s content demonstrably did not travel a -> t, and the
    walk must not cross it — even though *some* source's did.
    """
    rows = [
        _row("i1", "request", 1, "u", "a", [_S]),
        _row("i2", "request", 2, "a", "t", ["other"]),
    ]
    assert _reach(rows, "u", FANOUT) == {"a": 1}


def test_a_source_absent_leg_is_NOT_on_the_pending_frontier() -> None:
    """**The distinction that must never collapse.**

    A derived row lacking the source is a *final* answer — no amount of waiting adds
    the source to a row that has already been computed. Putting it on
    ``pending_frontier`` would send a caller back to poll forever. Contrast the next
    test, which is the same topology with the row *undelivered*.
    """
    rows = [
        _row("i1", "request", 1, "u", "a", [_S]),
        _row("i2", "request", 2, "a", "t", ["other"]),
    ]
    hops, _legs, frontier, _trunc = _walk_all(rows, "u", FANOUT)
    assert hops == {"a": 1}
    assert frontier == []


def test_an_undelivered_leg_IS_on_the_frontier_same_topology() -> None:
    """The companion to the previous test: identical shape, opposite disclosure.

    Derived-but-source-absent -> silently not an edge (settled). Not-derived-yet ->
    named on the frontier (ask again later). Asserting them as a pair is what stops a
    future editor from "simplifying" the two branches of ``_adjacency`` into one.
    """
    rows = [
        _row("i1", "request", 1, "u", "a", [_S]),
        _row("i2", "request", 2, "a", "t", None),
    ]
    hops, _legs, frontier, _trunc = _walk_all(rows, "u", FANOUT)
    assert hops == {"a": 1}
    assert frontier == ["t"]


def test_the_source_is_held_constant_across_the_whole_walk() -> None:
    """The source is the thing being TRACED, not a per-hop comparison.

    Every leg on the chain must carry ``src`` itself. A leg carrying only the *previous
    entity* as a source is not enough: that would be a different rule (and would let
    the walk drift onto content ``src`` never touched). Here ``a -> t`` lists ``a``, the
    entity just departed, but not ``src`` — and is correctly not followed.
    """
    rows = [
        _row("i1", "request", 1, "u", "a", [_S]),
        _row("i2", "request", 2, "a", "t", ["a"]),
        _row("i3", "request", 3, "a", "v", [_S, "a"]),
    ]
    assert _reach(rows, "u", FANOUT) == {"a": 1, "v": 2}


def test_a_source_in_no_leg_at_all_reaches_nothing() -> None:
    """An unknown source is an empty answer, not an error and not a 404.

    The walk genuinely computed "no eligible edge exists", which is the truthful reply.
    ``get_lineage_graph`` documents why this is not a 404.
    """
    rows = [_row("i1", "request", 1, "u", "a", ["something-else"])]
    assert _reach(rows, "u", FANOUT, source="never-heard-of-it") == {}


def test_an_empty_data_sources_array_carries_no_source() -> None:
    """A derived row with an EMPTY triple is derived, and carries nothing.

    Distinct from ``None``: the row exists (so this is settled, not pending) and the
    membership test simply fails. This is the case ``.lineage._lineage_view``'s
    null-probe reasoning exists for — "a derived row whose triple is empty" must not
    read as "no row".
    """
    rows = [_row("i1", "request", 1, "u", "a", [])]
    hops, _legs, frontier, _trunc = _walk_all(rows, "u", FANOUT)
    assert hops == {}
    assert frontier == []


# ---------------------------------------------------------------------------
# Clause 4 — the SEQUENCE filter. The spec: "the interaction sequence number
# governs the edges to be considered and their order (fanout - larger numbers,
# fanin - smaller numbers)".
# ---------------------------------------------------------------------------


def test_fanout_does_not_follow_a_leg_that_happened_earlier() -> None:
    """Data cannot flow backwards in time.

    The seed departs at seq 10; ``a -> old`` fired at seq 5, before the content ever
    arrived at ``a``, so it cannot carry it onward. This is the class of error that put
    ``charge_card`` (seq 39) in a leaf tool's fan-in on the live corpus.
    """
    rows = [
        _row("i0", "request", 10, "seed", "a"),
        _row("i1", "request", 5, "a", "old"),
        _row("i2", "request", 20, "a", "new"),
    ]
    assert _reach(rows, "seed", FANOUT) == {"a": 1, "new": 2}


def test_fanin_does_not_follow_a_leg_that_happened_later() -> None:
    """The mirror: an ancestor cannot be something that ran after you."""
    rows = [
        _row("i0", "request", 10, "a", "seed"),
        _row("i1", "request", 20, "later", "a"),
        _row("i2", "request", 5, "earlier", "a"),
    ]
    assert _reach(rows, "seed", FANIN) == {"a": 1, "earlier": 2}


def test_the_seed_may_depart_on_any_leg_regardless_of_seq() -> None:
    """The seed has no arrival, so nothing constrains its departures.

    Pinning the seed to its earliest/latest touching leg would silently narrow the
    question to "downstream of that one leg", and the caller asked about the entity.
    Both of the seed's outbound legs are followed here even though they are 30 apart.
    """
    rows = [
        _row("i1", "request", 5, "seed", "early"),
        _row("i2", "request", 35, "seed", "late"),
    ]
    assert _reach(rows, "seed", FANOUT) == {"early": 1, "late": 1}


def test_the_seq_gate_is_strict_at_a_request_response_pair() -> None:
    """``>`` not ``>=``, and the request/response split is the evidence.

    ADR-0025 puts a request and its response at two DIFFERENT seqs (on the live trace,
    ``search_destinations`` is seq 2 request / seq 3 response), so a real round trip
    never needs ``>=``. Since ``seq`` is a per-leg cursor, two legs can never share one;
    ``>=`` could therefore only re-admit the very leg just arrived on, sending data
    straight back where it came from in zero elapsed time.

    Here ``t`` arrives from ``a`` on the seq-2 request. Departing on that same leg
    reversed is not available (a request runs one way only), and the seq-2 leg is not
    ``> 2``, so the only way back is the genuine seq-3 response.
    """
    rows = [
        _row("i1", "request", 2, "a", "t"),
        _row("i1", "response", 3, "a", "t"),
    ]
    _hops, legs, _frontier, _trunc = _walk_all(rows, "a", FANOUT)
    # The route is out on 2 and back on 3 — two legs, not one leg twice.
    assert [(leg.seq, leg.from_entity_id, leg.to_entity_id) for leg in legs] == [
        (2, "a", "t"),
        (3, "t", "a"),
    ]


def test_a_leg_at_exactly_the_arrival_seq_is_not_eligible() -> None:
    """Strictness, asserted directly rather than only via the pair above.

    Two legs cannot really share a ``seq``, so this is a guard on the comparison rather
    than a reachable state — but it is the line a future editor would relax, and
    relaxing it removes the termination argument (see ``_walk``).
    """
    rows = [
        _row("i0", "request", 7, "seed", "a"),
        _row("i1", "request", 7, "a", "b"),
    ]
    assert _reach(rows, "seed", FANOUT) == {"a": 1}


# ---------------------------------------------------------------------------
# Re-entry and termination — the visited-set redesign
# ---------------------------------------------------------------------------


def test_an_entity_re_entered_at_a_better_seq_opens_new_edges() -> None:
    """**Why ``visited: set[str]`` is no longer sound.**

    ``agent`` is discovered twice at the same depth: once via ``p`` arriving at seq 30,
    once via ``q`` arriving at seq 10. Only the seq-10 arrival can depart on the seq-20
    leg to ``far`` (fanout departs on ``seq > arrival``, so an EARLIER arrival is the
    more permissive one). A plain "seen it, skip it" set would keep whichever arrival
    happened to be dequeued first and, half the time, drop ``far`` entirely.

    Note the sign: for fanout, *earlier* is better. That is the opposite of the
    direction of travel and is the easiest thing in ``_walk`` to get backwards.
    """
    rows = [
        _row("i0", "request", 1, "seed", "p"),
        _row("i1", "request", 30, "p", "agent"),
        _row("i2", "request", 5, "seed", "q"),
        _row("i3", "request", 10, "q", "agent"),
        _row("i4", "request", 20, "agent", "far"),
    ]
    assert _reach(rows, "seed", FANOUT) == {
        "p": 1,
        "q": 1,
        "agent": 2,
        "far": 3,
    }


def test_a_DEEPER_but_more_permissive_arrival_is_still_expanded() -> None:
    """**The trap one refinement in from the ``visited`` bug** — caught in review.

    ``agent`` is reached at depth 1 (directly, arriving at seq 50) and again at depth 2
    (via ``mid``, arriving at seq 10). The depth-2 arrival is *deeper* but *more
    permissive*, and it alone opens the seq-20 leg to ``far``.

    A ``best[(depth, arrival)]`` dominance test reading "shallower, or equal depth and
    more permissive" rejects it — the depth-2 arrival is neither — and silently loses
    ``far``. So permissiveness alone decides whether to expand, and ``hops`` is tracked
    separately: ``agent`` still reports 1, because the shallow route was real.

    This is the same class of silent under-reporting as the plain visited set, merely
    rarer, which is exactly why it needs its own test rather than being assumed covered
    by the re-entry case above.
    """
    rows = [
        _row("i0", "request", 50, "seed", "agent"),
        _row("i1", "request", 5, "seed", "mid"),
        _row("i2", "request", 10, "mid", "agent"),
        _row("i3", "request", 20, "agent", "far"),
    ]
    hops = _reach(rows, "seed", FANOUT)
    assert hops == {"mid": 1, "agent": 1, "far": 3}


def test_re_entry_is_mirrored_for_fanin() -> None:
    """For ``fanin`` a LATER arrival is the permissive one (departs on ``seq <``).

    ``agent`` is reachable from ``seed`` via ``p`` (arrival 10) and via ``q`` (arrival
    90). Only the arrival-90 route leaves room for the seq-50 leg onward to ``deep``.
    """
    rows = [
        _row("i0", "request", 10, "p", "seed"),
        _row("i1", "request", 90, "q", "seed"),
        _row("i2", "request", 50, "agent", "q"),
        _row("i3", "request", 20, "agent", "p"),
        _row("i4", "request", 40, "deep", "agent"),
    ]
    assert _reach(rows, "seed", FANIN) == {
        "p": 1,
        "q": 1,
        "agent": 2,
        "deep": 3,
    }


def test_a_request_response_pair_is_a_cycle_and_terminates() -> None:
    """``agent -> tool -> agent`` is the ORDINARY shape of a tool call.

    Both legs of one interaction form a two-node cycle. It terminates for a stronger
    reason than the old visited set gave: each traversal must strictly ADVANCE ``seq``,
    and the trace has finitely many legs, so the seq gate subsumes the cycle guard.
    """
    rows = [
        _row("i1", "request", 1, "a", "t"),
        _row("i1", "response", 2, "a", "t"),
    ]
    assert _reach(rows, "a", FANOUT) == {"t": 1}


def test_a_longer_cycle_terminates() -> None:
    """``a -> b -> c -> a`` closes back on the seed."""
    rows = [
        _row("i1", "request", 1, "a", "b"),
        _row("i2", "request", 2, "b", "c"),
        _row("i3", "request", 3, "c", "a"),
    ]
    assert _reach(rows, "a", FANOUT) == {"b": 1, "c": 2}


def test_a_repeated_two_node_cycle_terminates() -> None:
    """Four legs bouncing between the same two entities, at four ascending seqs.

    Without the seq gate this is an infinite loop; with it, each bounce consumes a leg
    and the supply is finite. ``b`` is still reported once, at its shortest distance.
    """
    rows = [
        _row("i1", "request", 1, "a", "b"),
        _row("i1", "response", 2, "a", "b"),
        _row("i2", "request", 3, "a", "b"),
        _row("i2", "response", 4, "a", "b"),
    ]
    assert _reach(rows, "a", FANOUT) == {"b": 1}


def test_a_dense_cyclic_mesh_terminates() -> None:
    """A ring of 7 entities with 400 interleaved legs each way — the stress case.

    Termination here is the claim ``_walk``'s docstring proves: ``(depth,
    arrival_seq)`` can only be replaced by a dominating value, ``depth`` is bounded by
    the hop cap, and at fixed depth ``arrival_seq`` moves strictly one way through a
    finite set. If that argument were wrong this test would hang rather than fail.
    """
    rows = []
    for seq in range(400):
        a, b = f"e{seq % 7}", f"e{(seq + 1) % 7}"
        rows.append(_row(f"i{seq}", "request", seq, a, b))
        rows.append(_row(f"r{seq}", "response", seq + 10_000, a, b))
    hops, _legs, _frontier, truncated = _walk_all(rows, "e0", FANOUT)
    assert set(hops) == {f"e{n}" for n in range(1, 7)}
    assert truncated is False


def test_a_self_call_terminates() -> None:
    """An entity calling itself is a one-node cycle, and the seed is not its own answer."""
    rows = [_row("i1", "request", 1, "a", "a")]
    assert _reach(rows, "a", FANOUT) == {}


def test_a_traversed_leg_into_a_known_entity_is_still_reported() -> None:
    """The route includes the hop even when it reaches nothing new.

    A cycle's closing leg really happened; omitting it would draw the cycle as a dead
    end and leave the reader without the arrow that closes it.
    """
    rows = [
        _row("i1", "request", 1, "a", "b"),
        _row("i2", "request", 2, "b", "a"),
    ]
    _hops, legs, _frontier, _trunc = _walk_all(rows, "a", FANOUT)
    assert [(leg.from_entity_id, leg.to_entity_id) for leg in legs] == [
        ("a", "b"),
        ("b", "a"),
    ]


def test_a_leg_is_reported_once_even_if_re_expanded() -> None:
    """Re-entry must not duplicate the route.

    ``agent`` is expanded twice (two arrivals at the same depth), so the leg onward to
    ``far`` is *considered* twice. It appears once: the route is a set of arrows, and a
    doubled arrow would be read as two distinct flows.
    """
    rows = [
        _row("i0", "request", 1, "seed", "p"),
        _row("i1", "request", 30, "p", "agent"),
        _row("i2", "request", 5, "seed", "q"),
        _row("i3", "request", 10, "q", "agent"),
        _row("i4", "request", 40, "agent", "far"),
    ]
    _hops, legs, _frontier, _trunc = _walk_all(rows, "seed", FANOUT)
    onward = [leg for leg in legs if leg.to_entity_id == "far"]
    assert len(onward) == 1


# ---------------------------------------------------------------------------
# Hops are a shortest SEQ-RESPECTING distance, and bounds are disclosed
# ---------------------------------------------------------------------------


def test_hops_is_the_shortest_route() -> None:
    """First arrival at the shallowest depth is the fewest hops.

    ``d`` is reachable in one hop directly (seq 4) and in three the long way round; the
    short answer is the one reported.
    """
    rows = [
        _row("i1", "request", 1, "a", "b"),
        _row("i2", "request", 2, "b", "c"),
        _row("i3", "request", 3, "c", "d"),
        _row("i4", "request", 4, "a", "d"),
    ]
    assert _reach(rows, "a", FANOUT)["d"] == 1


def test_hops_counts_the_shortest_SEQ_RESPECTING_path() -> None:
    """**Plain hop-BFS no longer yields the right number.**

    Structurally ``a -> d`` is one hop from ``a``, so an untimed walk would report
    ``d`` at 2 from the seed. But that leg fired at seq 1, *before* the content reached
    ``a`` at seq 10, so it is closed. The only open route is ``a -> b -> d`` (seq 20,
    30), which is 3 hops — and 3 is the truthful distance.
    """
    rows = [
        _row("i0", "request", 10, "seed", "a"),
        _row("i1", "request", 1, "a", "d"),
        _row("i2", "request", 20, "a", "b"),
        _row("i3", "request", 30, "b", "d"),
    ]
    assert _reach(rows, "seed", FANOUT) == {"a": 1, "b": 2, "d": 3}


def test_hops_is_not_lengthened_by_a_later_better_arrival() -> None:
    """A shallower route already found must not be overwritten by a deeper one.

    ``x`` is reachable at depth 1 (seq 50) and again at depth 2 via ``mid``. The
    depth-2 arrival is more *permissive* in seq terms, so it is enqueued to expand
    further — but the reported distance stays 1, because that route was real.
    """
    rows = [
        _row("i0", "request", 50, "seed", "x"),
        _row("i1", "request", 5, "seed", "mid"),
        _row("i2", "request", 10, "mid", "x"),
    ]
    assert _reach(rows, "seed", FANOUT)["x"] == 1


def test_hops_beyond_a_dominated_arrival_use_the_shallow_route() -> None:
    """The regression the removed stale-entry skip caused, one hop further out.

    Identical fixture to the test above plus ``x -(60)-> y``, and that one extra leg is
    what the previous implementation got wrong. ``x`` is reached at depth 1 via seq 50
    and again at depth 2 via seq 10; seq 10 is more *permissive*, so the depth-1 queue
    entry was skipped as "stale" when it popped, and ``y`` was only ever discovered from
    the depth-2 arrival — reported at **3** hops.

    But ``seed -(50)-> x -(60)-> y`` satisfies clause 4 at every step (60 > 50), so 2 is
    the truthful distance. The test above passes either way, because ``min`` rescues the
    dominated entity ITSELF; nothing rescued the entities beyond it. Hence this case.
    """
    rows = [
        _row("i0", "request", 50, "seed", "x"),
        _row("i1", "request", 5, "seed", "mid"),
        _row("i2", "request", 10, "mid", "x"),
        _row("i3", "request", 60, "x", "y"),
    ]
    reached = _reach(rows, "seed", FANOUT)
    assert reached["x"] == 1
    assert reached["y"] == 2


def test_a_truncated_answer_cites_no_leg_to_an_entity_it_withheld() -> None:
    """`legs` may never reference an entity absent from `entities`.

    The entity bound is a *reporting* bound, not a licence to return a route with holes
    in it: a client drawing an edge to an id that never appeared in `entities` has a
    dangling edge, i.e. an unexplained node — and ``truncated`` does not cover that. It
    says the answer is incomplete, not that parts of it point at nothing.

    Five leaves behind a bound of three used to yield 3 entities and 5 legs, the last two
    naming leaves the caller was never given, because the leg was appended before the
    bound was consulted.
    """
    rows = [_row(f"i{n}", "request", 10 + n, "hub", f"leaf{n}") for n in range(5)]
    hops, legs, _frontier, truncated = _walk_all(rows, "hub", FANOUT, max_entities=3)

    assert truncated is True
    assert len(hops) == 3
    cited = {leg.to_entity_id for leg in legs} | {leg.from_entity_id for leg in legs}
    # Every endpoint of every reported leg is either the seed or a returned entity.
    assert cited - set(hops) - {"hub"} == set()


def test_the_hop_bound_truncates_and_says_so() -> None:
    """A bounded answer is reported as bounded, never silently returned as whole."""
    rows = [_row(f"i{n}", "request", n, f"e{n}", f"e{n + 1}") for n in range(10)]
    hops, _legs, _frontier, truncated = _walk_all(rows, "e0", FANOUT, max_hops=3)
    assert hops == {"e1": 1, "e2": 2, "e3": 3}
    assert truncated is True


def test_the_entity_bound_truncates_and_says_so() -> None:
    rows = [_row(f"i{n}", "request", n, "hub", f"leaf{n}") for n in range(10)]
    hops, _legs, _frontier, truncated = _walk_all(
        rows, "hub", FANOUT, max_entities=4
    )
    assert len(hops) == 4
    assert truncated is True


def test_truncation_keeps_the_EARLIEST_flows_not_an_arbitrary_prefix() -> None:
    """The spec's "and their order" earns its keep here.

    Departures are expanded in seq order (ascending for fanout), so when the entity cap
    bites the answer is the *earliest* flows rather than whatever order the rows happened
    to arrive in. The fixture deliberately lists legs in descending seq, so an
    insertion-order walk would keep ``leaf0``/``leaf1`` (seqs 100, 99) while a
    seq-ordered one keeps ``leaf5``/``leaf4`` (seqs 95, 96).

    Worth pinning because ordering does not change *reachability* — it is easy to
    dismiss as cosmetic — but it does decide which prefix a truncated answer returns, and
    a deterministic, time-ordered prefix is the only one a reader can interpret.
    """
    rows = [_row(f"i{n}", "request", 100 - n, "hub", f"leaf{n}") for n in range(6)]
    hops, _legs, _frontier, truncated = _walk_all(
        rows, "hub", FANOUT, max_entities=2
    )
    assert set(hops) == {"leaf4", "leaf5"}
    assert truncated is True


def test_a_cap_skipped_entity_is_truncated_not_pending() -> None:
    """The two disclosures mean different things and must not be conflated.

    ``pending_frontier`` says "not derived yet, ask again later"; a cap-skipped entity
    IS derived and was merely not returned, so reporting it as pending would send a
    caller back to poll for something no amount of waiting will deliver. It is
    ``truncated``'s business alone.
    """
    rows = [_row(f"i{n}", "request", n, "hub", f"leaf{n}") for n in range(6)]
    hops, _legs, frontier, truncated = _walk_all(
        rows, "hub", FANOUT, max_entities=2
    )
    assert len(hops) == 2
    assert truncated is True
    assert frontier == []


def test_truncation_and_pending_can_be_reported_together() -> None:
    """They are independent facts, so a walk can hit a bound AND have a frontier."""
    rows = [
        _row("i0", "request", 0, "hub", "a"),
        _row("i1", "request", 1, "hub", "b"),
        _row("i2", "request", 2, "hub", "c", None),
    ]
    hops, _legs, frontier, truncated = _walk_all(
        rows, "hub", FANOUT, max_entities=1
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
    _hops, _legs, _frontier, truncated = _walk_all(rows, "a", FANOUT, max_hops=1)
    assert truncated is False


def test_the_bound_does_not_cry_truncation_over_seq_ineligible_legs() -> None:
    """A leg past the cap that the SEQ rule already excluded is not "more to see".

    ``b`` sits at the hop bound and has one onward leg, at seq 1 — earlier than ``b``'s
    own arrival at seq 2, so it was never eligible. Reporting ``truncated`` here would
    tell the caller a wider bound would reveal more, which is false.
    """
    rows = [
        _row("i0", "request", 2, "a", "b"),
        _row("i1", "request", 1, "b", "c"),
    ]
    _hops, _legs, _frontier, truncated = _walk_all(rows, "a", FANOUT, max_hops=1)
    assert truncated is False


# ---------------------------------------------------------------------------
# Unresolvable participants
# ---------------------------------------------------------------------------


def test_a_leg_with_an_unresolved_participant_is_no_edge() -> None:
    """A NULL caller/callee (the caller-inference window) yields no hop.

    There is no node to walk to, and naming one would be a claim about an entity the
    data cannot identify. True whether or not the lineage row is present.
    """
    assert _adjacency([_row("i1", "request", 1, None, "a")], FANOUT, _S) == ({}, {})
    assert _adjacency([_row("i1", "request", 1, "a", None)], FANOUT, _S) == ({}, {})
    assert _adjacency(
        [_row("i1", "request", 1, None, "a", None)], FANOUT, _S
    ) == ({}, {})


def test_an_absent_seed_reaches_nothing() -> None:
    """A seed with no legs in the trace is an empty walk, not an error."""
    rows = [_row("i1", "request", 1, "a", "b")]
    assert _reach(rows, "nobody", FANOUT) == {}


def test_an_empty_trace_reaches_nothing() -> None:
    assert _reach([], "a", FANOUT) == {}
