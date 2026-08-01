"""In-process tests for the lineage **graph** and **summary** seams (ADR-0028 D14).

These drive ``retrieval.get_lineage_graph`` / ``retrieval.get_lineage_summary``
against a migrated DB. The traversal *semantics* are covered without a DB in
``test_lineage_walk.py``; what these prove is the part only a real database can show:

- the SQL feeds the walk correctly — legs joined to their parent's participants, with
  the derived-lineage probe on the LEFT JOIN;
- **trace scoping** through ``interactions`` (``lineage_metadata`` and
  ``interaction_legs`` both have no ``trace_id`` of their own), so a second trace's
  flow cannot leak into a walk;
- entity metadata resolution for the reached ids;
- ``list sources`` reads the metadata triple and ``list destinations`` the taxonomy
  kind default;
- the graceful-absence shapes: unknown trace / unknown seed / migration not run all
  return an empty typed result rather than raising.

Seed shape: a three-entity chain ``user -> agent -> tool``, every leg payload-bearing
and lineage-derived, so a fanout from the user reaches both and a fanin from the tool
reaches back to the user.
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


def _seed_lineage(
    conn: psycopg.Connection,
    ix_id: str,
    leg_type: str,
    *,
    data_sources: list[str],
    seq: int,
) -> None:
    """One ``lineage_metadata`` row — the shape ``driver._upsert`` writes.

    Its mere existence is what makes the leg traversable; ``data_sources`` is what
    ``list sources`` unions.
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
    """``user -> agent -> tool``, all four legs derived."""
    with psycopg.connect(configured_db) as conn:
        _seed_entity(conn, _USER, "user", "user", 1)
        _seed_entity(conn, _AGENT, "agent", "advisor", 2)
        _seed_entity(conn, _TOOL, "tool", "search", 3)
        for eid, sid in ((_USER, "s-u"), (_AGENT, "s-a"), (_TOOL, "s-t")):
            _seed_entity_span(conn, _TID, eid, sid)

        _seed_interaction(conn, _TID, _IX_UA, _USER, _AGENT)
        _seed_leg(conn, _IX_UA, "request")
        _seed_leg(conn, _IX_UA, "response")
        _seed_interaction(conn, _TID, _IX_AT, _AGENT, _TOOL)
        _seed_leg(conn, _IX_AT, "request")
        _seed_leg(conn, _IX_AT, "response")

        _seed_lineage(conn, _IX_UA, "request", data_sources=["user"], seq=1)
        _seed_lineage(conn, _IX_AT, "request", data_sources=["user"], seq=2)
        _seed_lineage(
            conn, _IX_AT, "response", data_sources=["search", "user"], seq=3
        )
        _seed_lineage(
            conn, _IX_UA, "response", data_sources=["search", "user"], seq=4
        )
        conn.commit()
    return _TID


# ---------------------------------------------------------------------------
# get_lineage_graph
# ---------------------------------------------------------------------------


def test_fanout_reaches_the_whole_derived_chain(seeded: str) -> None:
    """From the user, downstream reaches the agent (1 hop) and the tool (2)."""
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT)
    assert result.state == "derived"
    assert {e.id: e.hops for e in result.entities} == {_AGENT: 1, _TOOL: 2}
    assert result.direction == "fanout"
    assert result.seed_entity_id == _USER
    assert result.truncated is False


def test_fanin_traces_provenance_back_to_the_user(seeded: str) -> None:
    """From the tool, upstream reaches the agent (1 hop) and the user (2).

    This is the read the UI deliberately refuses to compute client-side
    (``lineageGraph.ts`` clause 3): a transitive claim belongs to the backend, which
    derived it, not to the view.
    """
    result = retrieval.get_lineage_graph(seeded, _TOOL, retrieval.FANIN)
    assert result.state == "derived"
    assert {e.id: e.hops for e in result.entities} == {_AGENT: 1, _USER: 2}


def test_entity_metadata_is_resolved(seeded: str) -> None:
    """A reached entity carries its natural key, kind and display name."""
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT)
    tool = next(e for e in result.entities if e.id == _TOOL)
    assert (tool.natural_key, tool.kind, tool.display_name) == (
        "search",
        "tool",
        "Search",
    )


def test_the_traversed_legs_are_reported_as_the_route(seeded: str) -> None:
    """The answer includes the arrows, not just the nodes — so it can be drawn.

    Ordered by leg ``seq``, which is the trace's execution order (the fixture inserts
    the user↔agent pair before the agent↔tool pair, and ``seq`` is DB-assigned on
    insert). All four legs are traversed: the two requests carry data outward and the
    two responses carry it back, so the route is a round trip rather than a line.
    """
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT)
    assert [
        (leg.from_entity_id, leg.to_entity_id, leg.leg_type) for leg in result.legs
    ] == [
        (_USER, _AGENT, "request"),
        (_AGENT, _USER, "response"),
        (_AGENT, _TOOL, "request"),
        (_TOOL, _AGENT, "response"),
    ]


def test_an_undelivered_leg_stops_the_walk_and_is_disclosed(
    seeded: str, configured_db: str
) -> None:
    """Dropping the agent->tool lineage row ends the walk at the agent.

    The tool then appears on ``pending_frontier`` rather than vanishing — the spec's
    "no lineage through an entity is the end of fanout", with the eventual-consistency
    window kept visible.
    """
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "DELETE FROM lineage_metadata WHERE interaction_id = %s", (_IX_AT,)
        )
        conn.commit()
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT)
    assert [e.id for e in result.entities] == [_AGENT]
    assert result.pending_frontier == [_TOOL]
    assert result.state == "derived"  # a derived hop was still followed


def test_a_seed_with_only_undelivered_legs_is_pending_not_empty(
    seeded: str, configured_db: str
) -> None:
    """With NO lineage derived at all, the answer is ``pending``, never "no sources".

    The distinction the tri-state exists for: an empty entity list here means "not
    computed yet", which is the opposite claim from "nothing flowed".
    """
    with psycopg.connect(configured_db) as conn:
        conn.execute("DELETE FROM lineage_metadata")
        conn.commit()
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT)
    assert result.entities == []
    assert result.state == "pending"
    assert result.pending_frontier == [_AGENT]


def test_a_tools_response_carries_data_back_downstream(seeded: str) -> None:
    """A tool's FANOUT is not empty — its response delivers data to its caller.

    Worth pinning because the intuition "a leaf tool has no downstream" is wrong once
    per-leg direction is honoured: the response leg runs tool -> agent, and the
    agent's own response then runs agent -> user. This is the same fact ADR-0025
    turns on, seen from the other end.
    """
    result = retrieval.get_lineage_graph(seeded, _TOOL, retrieval.FANOUT)
    assert {e.id: e.hops for e in result.entities} == {_AGENT: 1, _USER: 2}


def test_an_entity_with_no_adjacent_legs_is_no_adjacent(
    seeded: str, configured_db: str
) -> None:
    """The one state where an empty answer is a COMPLETE answer.

    An entity that is in the trace but participates in no leg at all — so there is
    nothing to walk and nothing pending, as distinct from the two states that mean
    "ask again later".
    """
    with psycopg.connect(configured_db) as conn:
        _seed_entity(conn, "ent-bystander", "llm", "bystander", 21)
        _seed_entity_span(conn, _TID, "ent-bystander", "s-bystander")
        conn.commit()
    result = retrieval.get_lineage_graph(seeded, "ent-bystander", retrieval.FANOUT)
    assert result.entities == []
    assert result.state == "no-adjacent"
    assert result.pending_frontier == []


def test_an_unknown_seed_is_empty_not_an_error(seeded: str) -> None:
    result = retrieval.get_lineage_graph(seeded, "ent-nobody", retrieval.FANOUT)
    assert result.entities == []
    assert result.state == "no-adjacent"


def test_an_unknown_trace_is_empty_not_an_error(seeded: str) -> None:
    result = retrieval.get_lineage_graph("no-such-trace", _USER, retrieval.FANOUT)
    assert result.entities == []
    assert isinstance(result, retrieval.GetLineageGraphResult)


def test_an_unknown_direction_raises(seeded: str) -> None:
    """A caller error, and deliberately NOT defaulted to one of the two.

    Guessing which way a provenance question points would answer a different
    question; the two answers are not interchangeable.
    """
    with pytest.raises(retrieval.UnknownDirection):
        retrieval.get_lineage_graph(seeded, _USER, "sideways")
    with pytest.raises(retrieval.UnknownDirection):
        retrieval.get_lineage_graph(seeded, _USER, "")


def test_the_walk_is_scoped_to_one_trace(seeded: str, configured_db: str) -> None:
    """A second trace's flow must not leak in.

    ``interaction_legs`` and ``lineage_metadata`` both lack a ``trace_id``, so the
    scoping lives entirely in the join through ``interactions``. Getting it wrong
    would be a false cross-trace data-flow claim — the one thing a governance tool
    must not make (inter-trace lineage is Step II, deferred).
    """
    other_ix = "ix-other"
    with psycopg.connect(configured_db) as conn:
        _seed_entity(conn, "ent-leak", "tool", "leak", 9)
        _seed_interaction(conn, _OTHER_TID, other_ix, _AGENT, "ent-leak")
        _seed_leg(conn, other_ix, "request")
        _seed_lineage(conn, other_ix, "request", data_sources=["leak"], seq=99)
        conn.commit()
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT)
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
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT)
    assert (result.status, result.stopped_at_seq) == ("partial", 7)


def test_status_is_unknown_when_undeclared(seeded: str) -> None:
    """No status row means *unknown* — never defaulted to ``complete``."""
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT)
    assert result.status is None
    assert result.stopped_at_seq is None


# ---------------------------------------------------------------------------
# get_lineage_summary
# ---------------------------------------------------------------------------


def test_sources_are_the_union_of_the_derived_triples(seeded: str) -> None:
    """``list sources`` reads the metadata triple, not the taxonomy (D14).

    Both natural keys the seeded rows attribute content to, deduped and sorted.
    """
    result = retrieval.get_lineage_summary(seeded)
    assert result.sources == ["search", "user"]


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
    result = retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT)
    assert result.entities == []
    assert result.legs == []


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
    assert retrieval.get_lineage_graph(seeded, _USER, retrieval.FANOUT).entities == []


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
