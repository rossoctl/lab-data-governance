"""In-process tests for the lineage **graph** and **summary** seams (ADR-0028 D14/D15).

These drive ``retrieval.get_lineage_graph`` / ``retrieval.get_lineage_summary``
against a migrated DB. The traversal *semantics* are covered without a DB in
``test_lineage_walk.py``; what these prove is the part only a real database can show:

- the SQL feeds the walk correctly — legs joined to their parent's participants, with
  the **stored ``data_sources`` array** (not a boolean) and the null-probe on the LEFT
  JOIN, so the walk can apply the source-membership rule at all;
- **trace scoping** through ``interactions`` (``lineage_metadata`` and
  ``interaction_legs`` both have no ``trace_id`` of their own), so a second trace's
  flow cannot leak into a walk;
- entity metadata resolution for the reached ids;
- ``list sources`` reads the metadata triple and ``list destinations`` the taxonomy
  kind default;
- the graceful-absence shapes: unknown trace / unknown seed / **unknown source** /
  migration not run all return an empty typed result rather than raising.

Seed shape: a three-entity chain ``user -> agent -> tool``, every leg payload-bearing
and lineage-derived, at ascending ``seq`` 1..4 (request out, request out, response
back, response back — a round trip, which is the real corpus shape). ``data_sources``
mirrors what the derivation would attribute: ``user`` from the start, with ``search``
joining once the tool has produced something.
"""

from __future__ import annotations

import json

import psycopg
import pytest

from data_governance import retrieval

_TID = "trace-lgraph-1"
_OTHER_TID = "trace-lgraph-2"

_USER = "ent-user"
_AGENT = "ent-agent"
_TOOL = "ent-tool"

_IX_UA = "ix-user-agent"
_IX_AT = "ix-agent-tool"

# An extra tool the agent calls and finishes with BEFORE the seed tool runs — the
# reduced ``charge_card``-at-seq-39 shape. Only used by the ``late_seeded`` fixture.
_EARLY = "ent-early"
_IX_AE = "ix-agent-early"

# The two data source natural keys the fixture attributes content to. `source` is a
# natural key, never an entity id (ADR-0027/0028): the derivation writes keys, so the
# read compares keys.
_SRC_USER = "user"
_SRC_SEARCH = "search"


def _seed_entity(
    conn: psycopg.Connection, entity_id: str, kind: str, natural_key: str, seq: int
) -> None:
    conn.execute(
        "INSERT INTO entities (id, kind, natural_key, display_name, "
        "detected_from, original_seq) VALUES (%s, %s, %s, %s, 'span-attr', %s)",
        (entity_id, kind, natural_key, natural_key.title(), seq),
    )


def _seed_entity_span(
    conn: psycopg.Connection, trace_id: str, entity_id: str, span_id: str
) -> None:
    """Link an entity into a trace — how ``list destinations`` scopes (ADR-0013:
    an Entity is cross-trace stable and carries no ``trace_id``)."""
    conn.execute(
        "INSERT INTO spans (trace_id, span_id, parent_id, kind, name, started_at, "
        "attributes, seq, arrival_seq, observed_at) "
        "VALUES (%s, %s, NULL, 'INTERNAL', 'n', now(), '{}'::jsonb, "
        "nextval('spans_seq'), currval('spans_seq'), now())",
        (trace_id, span_id),
    )
    conn.execute(
        "INSERT INTO entity_spans (trace_id, span_id, entity_id, role) "
        "VALUES (%s, %s, %s, 'discovered_via')",
        (trace_id, span_id, entity_id),
    )


def _seed_interaction(
    conn: psycopg.Connection,
    trace_id: str,
    ix_id: str,
    caller: str,
    callee: str,
) -> None:
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, "
        "callee_entity_id, summary) VALUES (%s, %s, %s, %s, 'call')",
        (ix_id, trace_id, caller, callee),
    )


def _seed_leg(
    conn: psycopg.Connection,
    ix_id: str,
    leg_type: str,
    payload_hash: str | None = "h",
) -> None:
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error) VALUES (%s, %s, '2026-01-01T00:00:00Z', %s, false)",
        (ix_id, leg_type, payload_hash),
    )


def _leg_seqs(conn: psycopg.Connection, trace_id: str) -> dict[tuple[str, str], int]:
    """``(interaction_id, leg_type) -> seq`` as the DB assigned them.

    ``interaction_legs.seq`` comes from a sequence on insert, so the fixture cannot
    hardcode the values — and the seq rule is now load-bearing, so a test that needs to
    reason about ordering has to read the real numbers rather than assume them.
    """
    rows = conn.execute(
        "SELECT l.interaction_id::text, l.leg_type::text, l.seq "
        "FROM interaction_legs l JOIN interactions i ON i.id = l.interaction_id "
        "WHERE i.trace_id = %s",
        (trace_id,),
    ).fetchall()
    return {(r[0], r[1]): r[2] for r in rows}


def _seed_lineage(
    conn: psycopg.Connection,
    ix_id: str,
    leg_type: str,
    *,
    data_sources: list[str],
    seq: int,
) -> None:
    """One ``lineage_metadata`` row — the shape ``driver._upsert`` writes.

    Its existence makes the leg *considered*; ``data_sources`` is what decides whether
    a given source may traverse it, and is what ``list sources`` unions.
    """
    conn.execute(
        "INSERT INTO lineage_metadata (interaction_id, leg_type, data_sources, "
        "source_transformations, entities, payload_hash, seq) "
        "VALUES (%s, %s, %s, %s::jsonb, %s, 'h', %s)",
        (
            ix_id,
            leg_type,
            data_sources,
            json.dumps({s: [] for s in data_sources}),
            data_sources,
            seq,
        ),
    )


@pytest.fixture()
def seeded(configured_db: str) -> str:
    """``user -> agent -> tool`` round trip, all four legs derived.

    Insert order fixes ``seq``: UA request (1), AT request (2), AT response (3), UA
    response (4). That is the real execution order of a delegated tool call, and the
    seq rule now depends on it, so the legs are inserted in it rather than pairwise.
    """
    with psycopg.connect(configured_db) as conn:
        _seed_entity(conn, _USER, "user", "user", 1)
        _seed_entity(conn, _AGENT, "agent", "advisor", 2)
        _seed_entity(conn, _TOOL, "tool", "search", 3)
        for eid, sid in ((_USER, "s-u"), (_AGENT, "s-a"), (_TOOL, "s-t")):
            _seed_entity_span(conn, _TID, eid, sid)

        _seed_interaction(conn, _TID, _IX_UA, _USER, _AGENT)
        _seed_interaction(conn, _TID, _IX_AT, _AGENT, _TOOL)
        # Execution order, so `interaction_legs.seq` ascends the way the trace ran.
        _seed_leg(conn, _IX_UA, "request")
        _seed_leg(conn, _IX_AT, "request")
        _seed_leg(conn, _IX_AT, "response")
        _seed_leg(conn, _IX_UA, "response")

        _seed_lineage(conn, _IX_UA, "request", data_sources=[_SRC_USER], seq=1)
        _seed_lineage(conn, _IX_AT, "request", data_sources=[_SRC_USER], seq=2)
        _seed_lineage(
            conn, _IX_AT, "response", data_sources=[_SRC_SEARCH, _SRC_USER], seq=3
        )
        _seed_lineage(
            conn, _IX_UA, "response", data_sources=[_SRC_SEARCH, _SRC_USER], seq=4
        )
        conn.commit()
    return _TID


@pytest.fixture()
def late_seeded(configured_db: str) -> str:
    """The reproduction shape: an entity whose legs are all EARLIER than the seed's.

    Distilled from live trace ``e62610bec7e8c1f4372aacc392eb9be5``, where the leaf tool
    ``search_destinations`` touches only seq 2/3 while ``charge_card`` touches only seq
    39/40 — and the old walk put the latter in the former's fan-in at 4 hops.

    This fixture inverts that so the *seed* is late, which is the same fact from the
    other side and easier to seed (``interaction_legs.seq`` is sequence-assigned on
    insert, so "earlier" means "inserted first"):

    ======  ==========================  ==================================
    ``seq``  leg                         ``data_sources``
    ======  ==========================  ==================================
    1       user -> agent (request)     ``user``
    2       agent -> early (request)    ``user``, ``search``
    3       early -> agent (response)   ``user``, ``search``
    4       agent -> tool (request)     ``user``, ``search``
    5       tool -> agent (response)    ``user``, ``search``
    6       agent -> user (response)    ``user``, ``search``
    ======  ==========================  ==================================

    The critical detail: ``ent-early``'s legs **do carry ``search``**. So clause 3
    cannot exclude it and clause 1 cannot either — it is adjacent to the agent in both
    directions. Only clause 4 can, which makes this a clean single-variable test of the
    seq rule. (Seeding ``search`` onto a leg that ran before the search tool existed is
    physically odd, and deliberately so: it removes the source rule as an explanation
    and forces the seq rule to do the work alone.)
    """
    with psycopg.connect(configured_db) as conn:
        _seed_entity(conn, _USER, "user", "user", 1)
        _seed_entity(conn, _AGENT, "agent", "advisor", 2)
        _seed_entity(conn, _TOOL, "tool", "search", 3)
        _seed_entity(conn, _EARLY, "tool", "early", 4)
        for eid, sid in (
            (_USER, "s-u"),
            (_AGENT, "s-a"),
            (_TOOL, "s-t"),
            (_EARLY, "s-e"),
        ):
            _seed_entity_span(conn, _TID, eid, sid)

        _seed_interaction(conn, _TID, _IX_UA, _USER, _AGENT)
        _seed_interaction(conn, _TID, _IX_AE, _AGENT, _EARLY)
        _seed_interaction(conn, _TID, _IX_AT, _AGENT, _TOOL)
        # Insert order IS execution order, which is what fixes `seq`.
        for ix_id, leg_type in (
            (_IX_UA, "request"),
            (_IX_AE, "request"),
            (_IX_AE, "response"),
            (_IX_AT, "request"),
            (_IX_AT, "response"),
            (_IX_UA, "response"),
        ):
            _seed_leg(conn, ix_id, leg_type)

        both = [_SRC_SEARCH, _SRC_USER]
        _seed_lineage(conn, _IX_UA, "request", data_sources=[_SRC_USER], seq=1)
        _seed_lineage(conn, _IX_AE, "request", data_sources=both, seq=2)
        _seed_lineage(conn, _IX_AE, "response", data_sources=both, seq=3)
        _seed_lineage(conn, _IX_AT, "request", data_sources=both, seq=4)
        _seed_lineage(conn, _IX_AT, "response", data_sources=both, seq=5)
        _seed_lineage(conn, _IX_UA, "response", data_sources=both, seq=6)
        conn.commit()
    return _TID


# ---------------------------------------------------------------------------
# get_lineage_graph — the happy paths, now source-scoped
# ---------------------------------------------------------------------------


def test_fanout_reaches_the_whole_derived_chain(seeded: str) -> None:
    """From the user, tracing ``user``'s data downstream reaches the agent then tool."""
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT, _SRC_USER)
    assert result.state == "derived"
    assert {e.id: e.hops for e in result.entities} == {_AGENT: 1, _TOOL: 2}
    assert result.direction == "fanout"
    assert result.seed_entity_id == _USER
    assert result.source == _SRC_USER
    assert result.truncated is False


def test_fanin_traces_provenance_back_to_the_user(seeded: str) -> None:
    """From the tool, ``user``'s data came via the agent (1 hop) from the user (2).

    This is the read the UI deliberately refuses to compute client-side
    (``lineageGraph.ts`` clause 3): a transitive claim belongs to the backend, which
    derived it, not to the view.
    """
    result = retrieval.get_lineage_graph(seeded, _TOOL, retrieval.FANIN, _SRC_USER)
    assert result.state == "derived"
    assert {e.id: e.hops for e in result.entities} == {_AGENT: 1, _USER: 2}


def test_entity_metadata_is_resolved(seeded: str) -> None:
    """A reached entity carries its natural key, kind and display name."""
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT, _SRC_USER)
    tool = next(e for e in result.entities if e.id == _TOOL)
    assert (tool.natural_key, tool.kind, tool.display_name) == (
        "search",
        "tool",
        "Search",
    )


def test_the_traversed_legs_are_reported_as_the_route(seeded: str) -> None:
    """The answer includes the arrows, not just the nodes — so it can be drawn.

    Ordered by leg ``seq``, the trace's execution order. All four legs carry ``user``,
    and all four are seq-forward of the seed's (unconstrained) departure, so the whole
    round trip is traversed: the requests carry data outward and the responses carry it
    back.
    """
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT, _SRC_USER)
    assert [
        (leg.from_entity_id, leg.to_entity_id, leg.leg_type) for leg in result.legs
    ] == [
        (_USER, _AGENT, "request"),
        (_AGENT, _TOOL, "request"),
        (_TOOL, _AGENT, "response"),
        (_AGENT, _USER, "response"),
    ]


# ---------------------------------------------------------------------------
# The reproduction: fan-in and fan-out must now DIFFER
# ---------------------------------------------------------------------------


def test_a_leaf_tools_fanout_and_fanin_are_not_identical(seeded: str) -> None:
    """**The regression from live trace ``e62610bec7e8c1f4372aacc392eb9be5``.**

    Seeding the leaf tool and tracing the source the *tool itself* originated
    (``search``, which first appears on the tool's own seq-3 response):

    - ``fanout`` — the tool's output travels back up: agent (1), user (2).
    - ``fanin`` — nothing. ``search`` originates at this tool, so it has no ancestors;
      the only legs carrying it are at seq 3 and 4, both *after* the tool's arrival.

    The old walk returned identical entity sets for both directions here, because
    ``_adjacency`` builds fan-in as the exact edge-reversal of fan-out and the
    round-trip edge set is symmetric — so once time is discarded, reversing it is a
    no-op. On the live trace that produced all 10 entities and all 50 legs for both.
    """
    fanout = retrieval.get_lineage_graph(
        seeded, _TOOL, retrieval.FANOUT, _SRC_SEARCH
    )
    fanin = retrieval.get_lineage_graph(seeded, _TOOL, retrieval.FANIN, _SRC_SEARCH)
    assert {e.id: e.hops for e in fanout.entities} == {_AGENT: 1, _USER: 2}
    assert fanin.entities == []
    assert [e.id for e in fanout.entities] != [e.id for e in fanin.entities]


def test_fanout_does_not_reach_an_entity_whose_legs_are_all_earlier(
    late_seeded: str, configured_db: str
) -> None:
    """**The ``charge_card``-at-seq-39 case, reduced.** See :func:`late_seeded`.

    ``ent-early`` is a tool the agent called and got a response from *before* the seed
    tool ran. Its legs are fully derived and deliberately DO list ``search`` among their
    sources, so clause 3 cannot exclude it — only the seq rule can. Tracing ``search``
    downstream out of the seed tool must not reach it, because ``search`` did not exist
    yet when those legs fired.

    The old untimed walk reported it (and reported it identically for ``fanin``), which
    on the live trace is how a leaf tool's fan-out came to include ``charge_card`` at
    seq 39.
    """
    with psycopg.connect(configured_db) as conn:
        seqs = _leg_seqs(conn, _TID)
    tool_departure = seqs[(_IX_AT, "response")]
    early_legs = {seq for (ix, _leg), seq in seqs.items() if ix == _IX_AE}
    # The premise: every one of the early tool's legs really is before the seed's
    # departure. If the fixture ever stopped guaranteeing that, this test would be
    # vacuous rather than failing, so it is asserted rather than assumed.
    assert early_legs
    assert max(early_legs) < tool_departure

    result = retrieval.get_lineage_graph(
        late_seeded, _TOOL, retrieval.FANOUT, _SRC_SEARCH
    )
    assert result.state == "derived"
    assert _EARLY not in {e.id for e in result.entities}
    # And the general property that makes "downstream" mean downstream *in time*: the
    # seed departs on its own response leg, so no earlier leg is on the route.
    assert result.legs
    assert min(leg.seq for leg in result.legs) == tool_departure


def test_the_earlier_tool_is_reachable_so_the_exclusion_is_the_seq_rule(
    late_seeded: str,
) -> None:
    """The companion: proof the previous test is not passing merely by disconnection.

    ``ent-early`` is genuinely connected to the agent and genuinely carries ``search``
    on its legs, so neither clause 1 nor clause 3 excludes it. Seeded *from the agent* —
    which has no arrival constraint, being the question rather than a hop — it is
    reached in both directions. So the only thing that removed it from the seed tool's
    fan-out is clause 4, which is what the previous test claims.
    """
    for direction in (retrieval.FANIN, retrieval.FANOUT):
        result = retrieval.get_lineage_graph(
            late_seeded, _AGENT, direction, _SRC_SEARCH
        )
        assert _EARLY in {e.id for e in result.entities}, direction


def test_the_seed_is_unconstrained_but_its_successors_are_not(
    late_seeded: str,
) -> None:
    """Where the seq rule does and does not bite — the distinction is deliberate.

    The **seed** may depart on any leg (it has not arrived on one; pinning it to a
    particular leg would narrow the question to "downstream of that leg" when the caller
    asked about the entity). Every hop **after** the first is constrained by the seq it
    arrived on.

    Seeded at the agent, ``fanout`` therefore reaches ``ent-early`` — but the route
    *through* the seed tool cannot, which is why seeding at the tool excludes it. The two
    answers differ on exactly the entity clause 4 governs.
    """
    from_agent = retrieval.get_lineage_graph(
        late_seeded, _AGENT, retrieval.FANOUT, _SRC_SEARCH
    )
    from_tool = retrieval.get_lineage_graph(
        late_seeded, _TOOL, retrieval.FANOUT, _SRC_SEARCH
    )
    assert _EARLY in {e.id for e in from_agent.entities}
    assert _EARLY not in {e.id for e in from_tool.entities}


def test_fanin_does_not_reach_a_later_entity(
    late_seeded: str, configured_db: str
) -> None:
    """The mirror of the seq exclusion, on the fan-in side.

    Tracing ``search`` upstream from the **user**: the user's inbound arrival is the
    seq-6 response from the agent, so fan-in may then only follow legs earlier than 6.
    That reaches the agent (6), the seed tool (5) and the early tool (3) — but the walk
    can never come back *forward* to a leg it has already passed, so the answer is a
    strictly seq-descending chain.

    Asserted as the chain's shape rather than as one missing entity, because on this
    fixture everything happens to be upstream of the last leg. The property that matters
    is that each hop is strictly earlier than the arrival that reached it, which is what
    makes "ancestor" mean ancestor *in time* — the claim the old walk could not make.
    """
    with psycopg.connect(configured_db) as conn:
        seqs = _leg_seqs(conn, _TID)
    user_arrival = seqs[(_IX_UA, "response")]

    result = retrieval.get_lineage_graph(
        late_seeded, _USER, retrieval.FANIN, _SRC_SEARCH
    )
    assert result.state == "derived"
    # Every leg on the route is at or before the user's own last inbound — nothing
    # after it can be an ancestor of it.
    assert result.legs
    assert max(leg.seq for leg in result.legs) == user_arrival
    # Distances follow the seq-descending chain: agent at 1 (via the seq-6 leg), then
    # the two tools it heard from earlier at 2. "Upstream" is a time-ordered claim now,
    # so the hop counts have to line up with the ordering rather than with mere
    # adjacency — under the old untimed walk both tools also sat at 2, but so did
    # everything else reachable, for no reason connected to when it ran.
    assert {e.id: e.hops for e in result.entities} == {
        _AGENT: 1,
        _TOOL: 2,
        _EARLY: 2,
    }


def test_the_seq_boundary_at_a_request_response_pair_is_strict(
    seeded: str, configured_db: str
) -> None:
    """A request and its response are two legs at two seqs — so ``>`` suffices.

    The evidence for strict-over-non-strict, read off the real schema rather than
    assumed: the tool's inbound request and outbound response are two distinct
    ``interaction_legs`` rows with two distinct ``seq`` values (ADR-0025 splits them).
    So a genuine round trip is expressible under ``>``, and ``>=`` would only ever
    re-admit the arrival leg itself — data flowing straight back in zero elapsed time.

    Tracing ``user`` out of the tool: the walk departs on the seq-3 response, NOT the
    seq-2 request it arrived on, and still reaches the agent and the user.
    """
    with psycopg.connect(configured_db) as conn:
        seqs = _leg_seqs(conn, _TID)
    inbound = seqs[(_IX_AT, "request")]
    outbound = seqs[(_IX_AT, "response")]
    # The premise: two legs of ONE interaction, at two different seqs.
    assert inbound < outbound

    result = retrieval.get_lineage_graph(seeded, _TOOL, retrieval.FANOUT, _SRC_USER)
    assert {e.id: e.hops for e in result.entities} == {_AGENT: 1, _USER: 2}
    # Strictness: nothing at or before the arrival is traversed.
    assert all(leg.seq > inbound for leg in result.legs)


# ---------------------------------------------------------------------------
# The source filter — clause 3, and the two-facts distinction
# ---------------------------------------------------------------------------


def test_the_walk_stops_where_the_source_is_absent(
    seeded: str, configured_db: str
) -> None:
    """A source present in some legs' ``data_sources`` and absent from others.

    ``search`` is dropped from the seq-4 leg (agent -> user). Tracing ``search`` out of
    the tool then reaches the agent and stops: the onward leg to the user is fully
    derived but does not carry this source, so this source's content demonstrably did
    not travel it.
    """
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "UPDATE lineage_metadata SET data_sources = %s "
            "WHERE interaction_id = %s AND leg_type = 'response'",
            ([_SRC_USER], _IX_UA),
        )
        conn.commit()
    result = retrieval.get_lineage_graph(
        seeded, _TOOL, retrieval.FANOUT, _SRC_SEARCH
    )
    assert [e.id for e in result.entities] == [_AGENT]
    assert _USER not in {e.id for e in result.entities}


def test_a_source_absent_leg_is_settled_not_pending(
    seeded: str, configured_db: str
) -> None:
    """**A derived leg lacking the source vs an undelivered leg — different facts.**

    Half one: the row EXISTS and simply does not list ``search``. That is final, so the
    user must NOT appear on ``pending_frontier`` — polling would never change it. The
    companion test below is the identical topology with the row *deleted*.
    """
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "UPDATE lineage_metadata SET data_sources = %s "
            "WHERE interaction_id = %s AND leg_type = 'response'",
            ([_SRC_USER], _IX_UA),
        )
        conn.commit()
    result = retrieval.get_lineage_graph(
        seeded, _TOOL, retrieval.FANOUT, _SRC_SEARCH
    )
    assert result.state == "derived"
    assert result.pending_frontier == []


def test_an_undelivered_leg_is_pending_not_settled(
    seeded: str, configured_db: str
) -> None:
    """Half two: same topology, but the row is ABSENT rather than source-free.

    Now the user IS on ``pending_frontier`` — "P-data-lineage has not got here yet",
    which a consumer should expect to change. Asserting the pair together is what stops
    the two branches of ``_adjacency`` being merged by a later editor who reads them as
    the same case.
    """
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "DELETE FROM lineage_metadata "
            "WHERE interaction_id = %s AND leg_type = 'response'",
            (_IX_UA,),
        )
        conn.commit()
    result = retrieval.get_lineage_graph(
        seeded, _TOOL, retrieval.FANOUT, _SRC_SEARCH
    )
    assert result.state == "derived"
    assert result.pending_frontier == [_USER]


def test_an_undelivered_leg_stops_the_walk_and_is_disclosed(
    seeded: str, configured_db: str
) -> None:
    """Dropping the agent->tool lineage ends the walk at the agent.

    The tool then appears on ``pending_frontier`` rather than vanishing — the spec's
    "no lineage through an entity is the end of fanout", with the eventual-consistency
    window kept visible.
    """
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "DELETE FROM lineage_metadata WHERE interaction_id = %s", (_IX_AT,)
        )
        conn.commit()
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT, _SRC_USER)
    assert [e.id for e in result.entities] == [_AGENT]
    assert result.pending_frontier == [_TOOL]
    assert result.state == "derived"  # an eligible hop was still followed


def test_a_seed_with_only_undelivered_legs_is_pending_not_empty(
    seeded: str, configured_db: str
) -> None:
    """With NO lineage derived at all, the answer is ``pending``, never "no sources".

    The distinction the tri-state exists for: an empty entity list here means "not
    computed yet", which is the opposite claim from "nothing flowed". Note this holds
    for a source that is not (yet) attested anywhere, which is exactly why an unknown
    source must not be a 404 — the same request answers ``pending`` now and ``derived``
    later.
    """
    with psycopg.connect(configured_db) as conn:
        conn.execute("DELETE FROM lineage_metadata")
        conn.commit()
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT, _SRC_USER)
    assert result.entities == []
    assert result.state == "pending"
    assert result.pending_frontier == [_AGENT]


def test_a_derived_trace_with_no_matching_source_is_no_adjacent(seeded: str) -> None:
    """**The unknown-source decision: a valid, complete, EMPTY answer.**

    Every leg is derived, so there is nothing pending; none carries this key, so there
    is nothing to follow. ``no-adjacent`` is the truthful state — "lineage for this
    source does not flow onward from here" — and a 404 would instead have claimed the
    question was malformed. Contrast the previous test: the *same* key against an
    undelivered trace answers ``pending``, which is why absence cannot be a 404.
    """
    result = retrieval.get_lineage_graph(
        seeded, _USER, retrieval.FANOUT, "no-such-source"
    )
    assert result.entities == []
    assert result.legs == []
    assert result.state == "no-adjacent"
    assert result.pending_frontier == []
    assert result.source == "no-such-source"


def test_a_tools_response_carries_data_back_downstream(seeded: str) -> None:
    """A tool's FANOUT is not empty — its response delivers data to its caller.

    Worth pinning because the intuition "a leaf tool has no downstream" is wrong once
    per-leg direction is honoured: the response leg runs tool -> agent, and the
    agent's own response then runs agent -> user. This is the same fact ADR-0025
    turns on, seen from the other end. It survives the seq rule because those two legs
    are seq 3 and 4 — after the tool's arrival, as a genuine return path must be.
    """
    result = retrieval.get_lineage_graph(
        seeded, _TOOL, retrieval.FANOUT, _SRC_SEARCH
    )
    assert {e.id: e.hops for e in result.entities} == {_AGENT: 1, _USER: 2}


# ---------------------------------------------------------------------------
# Absent / malformed questions
# ---------------------------------------------------------------------------


def test_an_entity_with_no_adjacent_legs_is_no_adjacent(
    seeded: str, configured_db: str
) -> None:
    """One of the states where an empty answer is a COMPLETE answer.

    An entity that is in the trace but participates in no leg at all — so there is
    nothing to walk and nothing pending, as distinct from the state that means
    "ask again later".
    """
    with psycopg.connect(configured_db) as conn:
        _seed_entity(conn, "ent-bystander", "llm", "bystander", 21)
        _seed_entity_span(conn, _TID, "ent-bystander", "s-bystander")
        conn.commit()
    result = retrieval.get_lineage_graph(
        seeded, "ent-bystander", retrieval.FANOUT, _SRC_USER
    )
    assert result.entities == []
    assert result.state == "no-adjacent"
    assert result.pending_frontier == []


def test_an_unknown_seed_is_empty_not_an_error(seeded: str) -> None:
    result = retrieval.get_lineage_graph(
        seeded, "ent-nobody", retrieval.FANOUT, _SRC_USER
    )
    assert result.entities == []
    assert result.state == "no-adjacent"


def test_an_unknown_trace_is_empty_not_an_error(seeded: str) -> None:
    result = retrieval.get_lineage_graph(
        "no-such-trace", _USER, retrieval.FANOUT, _SRC_USER
    )
    assert result.entities == []
    assert isinstance(result, retrieval.GetLineageGraphResult)


def test_an_unknown_direction_raises(seeded: str) -> None:
    """A caller error, and deliberately NOT defaulted to one of the two.

    Guessing which way a provenance question points would answer a different
    question; the two answers are not interchangeable.
    """
    with pytest.raises(retrieval.UnknownDirection):
        retrieval.get_lineage_graph(seeded, _USER, "sideways", _SRC_USER)
    with pytest.raises(retrieval.UnknownDirection):
        retrieval.get_lineage_graph(seeded, _USER, "", _SRC_USER)


def test_a_missing_source_raises(seeded: str) -> None:
    """``source`` is required, exactly as ``direction`` is, and for the same reason.

    It is half the question: an entity handles several sources at once and each has its
    own fanout. The only candidate default — the union over all sources — is the
    multi-source read the spec explicitly defers, so answering it silently would ship a
    guess at an open design question.

    Note this is the ONE source-shaped failure. An *unknown* source is a valid empty
    answer (see ``test_a_derived_trace_with_no_matching_source_is_no_adjacent``); only
    a missing one is malformed.
    """
    with pytest.raises(retrieval.MissingSource):
        retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT, "")


def test_direction_is_validated_before_source(seeded: str) -> None:
    """Both malformed: the direction error wins, so the message is deterministic."""
    with pytest.raises(retrieval.UnknownDirection):
        retrieval.get_lineage_graph(seeded, _USER, "sideways", "")


# ---------------------------------------------------------------------------
# Scoping and coverage
# ---------------------------------------------------------------------------


def test_the_walk_is_scoped_to_one_trace(seeded: str, configured_db: str) -> None:
    """A second trace's flow must not leak in.

    ``interaction_legs`` and ``lineage_metadata`` both lack a ``trace_id``, so the
    scoping lives entirely in the join through ``interactions``. Getting it wrong
    would be a false cross-trace data-flow claim — the one thing a governance tool
    must not make (inter-trace lineage is Step II, deferred).

    The other trace's leg deliberately carries the SAME source key, so only the join
    can exclude it — a source-scoped walk must not become a cross-trace one just
    because two traces share a source name.
    """
    other_ix = "ix-other"
    with psycopg.connect(configured_db) as conn:
        _seed_entity(conn, "ent-leak", "tool", "leak", 9)
        _seed_interaction(conn, _OTHER_TID, other_ix, _AGENT, "ent-leak")
        _seed_leg(conn, other_ix, "request")
        _seed_lineage(conn, other_ix, "request", data_sources=[_SRC_USER], seq=99)
        conn.commit()
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT, _SRC_USER)
    assert "ent-leak" not in {e.id for e in result.entities}


def test_trace_coverage_status_rides_along(seeded: str, configured_db: str) -> None:
    """The D6 coverage travels with the walk — a partial trace's reachability is
    computed over a prefix, which the caller has to be able to see."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO lineage_trace_status (trace_id, status, stopped_at_seq) "
            "VALUES (%s, 'partial', 7)",
            (_TID,),
        )
        conn.commit()
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT, _SRC_USER)
    assert (result.status, result.stopped_at_seq) == ("partial", 7)


def test_status_is_unknown_when_undeclared(seeded: str) -> None:
    """No status row means *unknown* — never defaulted to ``complete``."""
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT, _SRC_USER)
    assert result.status is None
    assert result.stopped_at_seq is None


def test_bounds_are_still_reported(seeded: str) -> None:
    """``truncated`` survives the rewrite — a bounded walk says so.

    Driven through the public seam with the module's real caps left alone would need a
    pathological trace, so this asserts the un-truncated case at the seam and leaves the
    cap behaviour to ``test_lineage_walk.py``, which can set the bounds directly. What
    matters here is that the flag is plumbed through the result at all.
    """
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT, _SRC_USER)
    assert result.truncated is False


# ---------------------------------------------------------------------------
# get_lineage_summary — unchanged by this amendment, and it feeds `source`
# ---------------------------------------------------------------------------


def test_sources_are_the_union_of_the_derived_triples(seeded: str) -> None:
    """``list sources`` reads the metadata triple, not the taxonomy (D14).

    Both natural keys the seeded rows attribute content to, deduped and sorted. This is
    also the *menu* for the graph read's ``source`` parameter: the two are keyed the
    same way on purpose, so a caller can list then drill in.
    """
    result = retrieval.get_lineage_summary(seeded)
    assert result.sources == [_SRC_SEARCH, _SRC_USER]


def test_every_listed_source_is_answerable_by_the_graph_read(seeded: str) -> None:
    """The contract between the two reads: ``sources`` are valid ``source`` values.

    Pinned because they are keyed on different tables' columns and could drift — if
    ``list sources`` ever returned entity ids while the walk compared natural keys, every
    drill-in would silently answer ``no-adjacent``.
    """
    for key in retrieval.get_lineage_summary(seeded).sources:
        result = retrieval.get_lineage_graph(
            seeded, _AGENT, retrieval.FANOUT, key
        )
        assert result.state == "derived", key


def test_destinations_follow_the_taxonomy_kind_default(seeded: str) -> None:
    """``list destinations`` selects the trace's entities declared targets.

    The kind default is ``tool`` ✓ / ``llm`` ✗ / ``agent`` ✗, read from the one named
    place (``memory.TARGET_KINDS``), so only the tool is a destination — the user and
    the agent are not.
    """
    result = retrieval.get_lineage_summary(seeded)
    assert [d.id for d in result.destinations] == [_TOOL]
    assert result.destinations[0].kind == "tool"


def test_the_summary_is_scoped_to_one_trace(seeded: str, configured_db: str) -> None:
    """A tool that only appears in another trace is not this trace's destination."""
    with psycopg.connect(configured_db) as conn:
        _seed_entity(conn, "ent-elsewhere", "tool", "elsewhere", 11)
        _seed_entity_span(conn, _OTHER_TID, "ent-elsewhere", "s-e")
        conn.commit()
    result = retrieval.get_lineage_summary(seeded)
    assert "ent-elsewhere" not in {d.id for d in result.destinations}


def test_summary_of_an_unknown_trace_is_empty(seeded: str) -> None:
    result = retrieval.get_lineage_summary("no-such-trace")
    assert result.sources == []
    assert result.destinations == []


# ---------------------------------------------------------------------------
# Not-yet-migrated
# ---------------------------------------------------------------------------


def test_graph_is_empty_when_lineage_migration_absent(
    seeded: str, configured_db: str
) -> None:
    """A mid-upgrade DB serves an empty result, never a 500."""
    with psycopg.connect(configured_db) as conn:
        conn.execute("DROP TABLE IF EXISTS lineage_metadata CASCADE")
        conn.commit()
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT, _SRC_USER)
    assert result.entities == []
    assert result.legs == []
    # The question is still echoed back, so a caller can tell which request this was.
    assert result.source == _SRC_USER


def test_summary_is_empty_when_lineage_migration_absent(
    seeded: str, configured_db: str
) -> None:
    with psycopg.connect(configured_db) as conn:
        conn.execute("DROP TABLE IF EXISTS lineage_metadata CASCADE")
        conn.commit()
    result = retrieval.get_lineage_summary(seeded)
    assert result.sources == []
    assert result.destinations == []


def test_graph_is_empty_when_interactions_migration_absent(
    seeded: str, configured_db: str
) -> None:
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "DROP TABLE IF EXISTS interaction_spans, interaction_legs, "
            "entity_spans, interaction_payloads, interactions, entities CASCADE"
        )
        conn.commit()
    assert (
        retrieval.get_lineage_graph(
            seeded, _USER, retrieval.FANOUT, _SRC_USER
        ).entities
        == []
    )


def test_summary_is_empty_when_interactions_migration_absent(
    seeded: str, configured_db: str
) -> None:
    """Covers the ``entity_spans`` dependency of ``list destinations``.

    ``entity_spans`` has no probe of its own because it ships in migration 0004 with
    ``interactions``, so the ``interactions`` probe stands in for it. This asserts
    that stand-in actually holds — dropping the 0004 tables must yield an empty
    summary rather than an ``UndefinedTable``.
    """
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "DROP TABLE IF EXISTS interaction_spans, interaction_legs, "
            "entity_spans, interaction_payloads, interactions, entities CASCADE"
        )
        conn.commit()
    result = retrieval.get_lineage_summary(seeded)
    assert result.sources == []
    assert result.destinations == []
