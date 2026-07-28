"""Op selection + inbound routing over a trace's legs (issue #117, ADR-0027 D1/D2/D4).

Pure: the traversal takes the trace's legs and entities as plain values and
returns per-leg **data lineage**, so the whole algorithm is testable without a
database. The DB driver (``data_lineage/driver.py``) only supplies these values
and persists the result.

The centrepiece is ``test_spec_worked_example_*``: the five-op sequence from
``docs/data_lineage_alg.md`` lines 140-153, asserted op-by-op — an agent's FIRST
outbound uses ``linear`` and later outbounds use ``merge`` once ≥2 priors exist.
"""

from __future__ import annotations

import pytest

from data_governance.matching import MatchResult, Transformation
from data_governance.processors.data_lineage import operations
from data_governance.processors.data_lineage.traversal import (
    Entity,
    Leg,
    Operation,
    derive_trace_lineage,
)


# --- fixture helpers ---------------------------------------------------------


def _always(payload_a: object, payload_b: object, /) -> MatchResult:
    return MatchResult(matched=True)


def _never(payload_a: object, payload_b: object, /) -> MatchResult:
    return MatchResult(matched=False)


def _entities(**kinds: str) -> dict[str, Entity]:
    """``natural_key -> Entity``, keyed by its own id for terse test wiring."""
    return {name: Entity(id=name, natural_key=name, kind=kind) for name, kind in kinds.items()}


_DEFAULT_HASH = object()  # sentinel: "give me h<seq>", distinct from an absent payload


def _leg(
    interaction_id: str,
    leg_type: str,
    seq: int,
    caller: str,
    callee: str,
    payload_hash: str | None | object = _DEFAULT_HASH,
) -> Leg:
    """A leg with a payload hash of ``h<seq>`` unless one is given. Passing an
    explicit ``None`` means an ABSENT payload — hence the sentinel default, so
    "unspecified" and "genuinely absent" stay distinguishable."""
    return Leg(
        interaction_id=interaction_id,
        leg_type=leg_type,
        seq=seq,
        caller_entity_id=caller,
        callee_entity_id=callee,
        payload_hash=f"h{seq}" if payload_hash is _DEFAULT_HASH else payload_hash,  # type: ignore[arg-type]
    )


# --- the spec's worked example (docs/data_lineage_alg.md:140-153) ------------
#
#   -1-> Agent
#        Agent -2-> LLM
#        Agent <-3- LLM
#        Agent -4-> LLM
#        Agent <-5- LLM
#   <-6- Agent
#
# In the ADR-0025 schema each numbered arrow is one interaction LEG: #1/#6 are
# the request/response legs of user→agent, #2/#3 of the first agent→llm call,
# #4/#5 of the second. A leg's producing entity is the caller for a request leg
# and the callee for a response leg (D1's routing rule, read backwards).


@pytest.fixture()
def worked_example() -> tuple[list[Leg], dict[str, Entity]]:
    ents = _entities(user="user", agent="agent", llm="llm")
    legs = [
        _leg("ix_ua", "request", 1, "user", "agent"),
        _leg("ix_al1", "request", 2, "agent", "llm"),
        _leg("ix_al1", "response", 3, "agent", "llm"),
        _leg("ix_al2", "request", 4, "agent", "llm"),
        _leg("ix_al2", "response", 5, "agent", "llm"),
        _leg("ix_ua", "response", 6, "user", "agent"),
    ]
    return legs, ents


def test_spec_worked_example_op_sequence(worked_example) -> None:
    """The spec's five ops, verbatim (``data_lineage_alg.md:147-151``)::

        #1  init            (the user's payload originates outside the trace)
        #2  linear (1 -> 2)     the agent's FIRST outbound: one inbound only
        #3  linear (2 -> 3)     the LLM is memoryless
        #4  merge  (1, 3 -> 4)  the agent now retains two priors
        #5  linear (4 -> 5)     the LLM is STILL memoryless
        #6  merge  (1, 3, 5 -> 6)
    """
    legs, ents = worked_example
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert [result[(leg.interaction_id, leg.leg_type)].operation for leg in legs] == [
        Operation.INIT,
        Operation.LINEAR,
        Operation.LINEAR,
        Operation.MERGE,
        Operation.LINEAR,
        Operation.MERGE,
    ]


def test_spec_worked_example_inbound_sets(worked_example) -> None:
    """The exact inbound payload sets the spec names in each op call. This is the
    load-bearing half — the op *name* alone would pass with the wrong inputs."""
    legs, ents = worked_example
    result = derive_trace_lineage(legs, ents, matcher=_always)

    inbound = {
        leg.seq: tuple(result[(leg.interaction_id, leg.leg_type)].inbound_payloads)
        for leg in legs
    }
    assert inbound == {
        1: (),  # init: nothing inbound
        2: ("h1",),  # linear_lineage(1, 2)
        3: ("h2",),  # linear_lineage(2, 3)
        4: ("h1", "h3"),  # Merge lineage (1, 3, 4)
        5: ("h4",),  # linear_lineage(4, 5)
        6: ("h1", "h3", "h5"),  # Merge lineage (1, 3, 5, 6)
    }


def test_spec_worked_example_agents_first_outbound_is_linear_not_merge(
    worked_example,
) -> None:
    """ADR-0027 D4's explicit call-out: "An accumulating entity's **first**
    outbound legitimately has one inbound and uses (b)". Pinned on its own
    because treating "is an agent" as "always merge" is the natural mistake."""
    legs, ents = worked_example
    result = derive_trace_lineage(legs, ents, matcher=_always)

    first_outbound = result[("ix_al1", "request")]  # #2
    later_outbound = result[("ix_al2", "request")]  # #4
    assert first_outbound.operation is Operation.LINEAR
    assert later_outbound.operation is Operation.MERGE


def test_spec_worked_example_metadata_accumulates_the_user_as_the_source(
    worked_example,
) -> None:
    """With the trivial always-match matcher every structural edge is real flow,
    so every payload downstream of the user traces back to the user."""
    legs, ents = worked_example
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert result[("ix_ua", "request")].lineage.data_sources == frozenset({"user"})
    for key in [
        ("ix_al1", "request"),
        ("ix_al1", "response"),
        ("ix_al2", "request"),
        ("ix_al2", "response"),
        ("ix_ua", "response"),
    ]:
        assert result[key].lineage.data_sources == frozenset({"user"}), key

    # The final response has passed through the agent and the LLM.
    assert set(result[("ix_ua", "response")].lineage.entity_path) == {"agent", "llm"}


# --- D3(1): structural init -------------------------------------------------


def test_trace_root_request_leg_is_structural_init() -> None:
    """D3(1): the output leg's payload has no producing interaction (nothing is
    inbound to the user), so it is a genuine trace root → ``init``."""
    ents = _entities(user="user", agent="agent")
    legs = [_leg("ix", "request", 1, "user", "agent")]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    entry = result[("ix", "request")]
    assert entry.operation is Operation.INIT
    assert entry.lineage == operations.init_lineage("user")


def test_init_roots_at_the_producing_entity_not_the_receiving_one() -> None:
    """The request leg is produced by the CALLER. Rooting it at the callee would
    name the agent as the data source of the user's own prompt."""
    ents = _entities(client="client", agent="agent")
    result = derive_trace_lineage(
        [_leg("ix", "request", 1, "client", "agent")], ents, matcher=_always
    )
    assert result[("ix", "request")].lineage.data_sources == frozenset({"client"})


# --- D1: structural inbound routing -----------------------------------------


def test_request_legs_are_inbound_to_the_callee() -> None:
    """D1, first half: "E is the **callee** and the payload is the **request**
    leg". The tool's response inherits from the request that was made to it."""
    ents = _entities(agent="agent", tool="tool")
    legs = [
        _leg("ix", "request", 1, "agent", "tool", payload_hash="req"),
        _leg("ix", "response", 2, "agent", "tool", payload_hash="resp"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert result[("ix", "response")].inbound_payloads == ("req",)


def test_response_legs_are_inbound_to_the_caller() -> None:
    """D1, second half: "E is the **caller** and the payload is the **response**
    leg". The agent's second outbound sees the first call's response."""
    ents = _entities(agent="agent", tool="tool", llm="llm")
    legs = [
        _leg("ix_at", "request", 1, "agent", "tool", payload_hash="to_tool"),
        _leg("ix_at", "response", 2, "agent", "tool", payload_hash="from_tool"),
        _leg("ix_al", "request", 3, "agent", "llm", payload_hash="to_llm"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    # The agent produced `to_tool` (seq 1) with nothing inbound → init; its next
    # outbound has exactly one inbound, the tool's response.
    assert result[("ix_at", "request")].operation is Operation.INIT
    assert result[("ix_al", "request")].inbound_payloads == ("from_tool",)


def test_a_leg_never_takes_itself_or_a_later_leg_as_inbound() -> None:
    """D1 is "in an interaction with **lower** sequence". A response leg must not
    see the response of a later call, and a request leg must not see its own
    interaction's response (which does not exist yet)."""
    ents = _entities(agent="agent", llm="llm")
    legs = [
        _leg("ix1", "request", 1, "agent", "llm"),
        _leg("ix1", "response", 2, "agent", "llm"),
        _leg("ix2", "request", 3, "agent", "llm"),
        _leg("ix2", "response", 4, "agent", "llm"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    for leg in legs:
        entry = result[(leg.interaction_id, leg.leg_type)]
        assert leg.payload_hash not in entry.inbound_payloads, leg


def test_routing_ignores_entities_not_party_to_the_interaction() -> None:
    """Purely structural: a third entity's traffic is not inbound to anyone
    else, no matter how it interleaves in seq."""
    ents = _entities(agent_a="agent", agent_b="agent", llm="llm")
    legs = [
        _leg("ix_b", "request", 1, "agent_b", "llm", payload_hash="b_out"),
        _leg("ix_b", "response", 2, "agent_b", "llm", payload_hash="b_in"),
        _leg("ix_a", "request", 3, "agent_a", "llm", payload_hash="a_out"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    # agent_a has seen nothing → structural init, despite two earlier legs.
    assert result[("ix_a", "request")].operation is Operation.INIT
    assert result[("ix_a", "request")].inbound_payloads == ()


# --- D2: memory / the accumulating-entity predicate -------------------------


def test_a_memoryless_entity_keeps_only_its_latest_inbound() -> None:
    """D2: "An LLM/tool does not accumulate, so it always sees one input and uses
    ``linear``". The second LLM response sees only the second request — which is
    exactly why the spec's #5 is ``linear`` and not ``merge``."""
    ents = _entities(agent="agent", llm="llm")
    legs = [
        _leg("ix1", "request", 1, "agent", "llm", payload_hash="q1"),
        _leg("ix1", "response", 2, "agent", "llm", payload_hash="a1"),
        _leg("ix2", "request", 3, "agent", "llm", payload_hash="q2"),
        _leg("ix2", "response", 4, "agent", "llm", payload_hash="a2"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert result[("ix2", "response")].inbound_payloads == ("q2",)
    assert result[("ix2", "response")].operation is Operation.LINEAR


def test_an_accumulating_entity_retains_every_prior() -> None:
    """D2: transient/session memory is assumed ALWAYS present, so an agent's
    inbound set grows monotonically and it merges from its second outbound on."""
    ents = _entities(user="user", agent="agent", llm="llm", tool="tool")
    legs = [
        _leg("ix_ua", "request", 1, "user", "agent", payload_hash="prompt"),
        _leg("ix_al", "request", 2, "agent", "llm", payload_hash="to_llm"),
        _leg("ix_al", "response", 3, "agent", "llm", payload_hash="from_llm"),
        _leg("ix_at", "request", 4, "agent", "tool", payload_hash="to_tool"),
        _leg("ix_at", "response", 5, "agent", "tool", payload_hash="from_tool"),
        _leg("ix_ua", "response", 6, "user", "agent", payload_hash="answer"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert result[("ix_at", "request")].inbound_payloads == ("prompt", "from_llm")
    assert result[("ix_ua", "response")].inbound_payloads == (
        "prompt",
        "from_llm",
        "from_tool",
    )


def test_two_priors_with_identical_payloads_are_two_priors() -> None:
    """Priors are retained by **position**, not by content hash — the same reason
    ADR-0027 D5 keys the table on the leg. Payloads are content-addressed and
    deduped, so two distinct priors can carry byte-identical content; collapsing
    them would demote the resulting ``merge`` to a ``linear``.

    This is the ``patent_agent_II`` regression: in that captured trace an LLM's
    response leg and the tool leg inferred from that response's ``tool_calls`` carry
    the SAME ``payload_hash``, so a hash-keyed inbound pool silently lost one of the
    agent's two priors."""
    ents = _entities(agent="agent", llm="llm", tool="tool")
    legs = [
        _leg("ix_al", "request", 1, "agent", "llm", payload_hash="q"),
        _leg("ix_al", "response", 2, "agent", "llm", payload_hash="echo"),
        # The inferred tool call's response repeats the LLM response's bytes.
        _leg("ix_at", "request", 3, "agent", "tool", payload_hash="to_tool"),
        _leg("ix_at", "response", 4, "agent", "tool", payload_hash="echo"),
        _leg("ix_al2", "request", 5, "agent", "llm", payload_hash="q2"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    entry = result[("ix_al2", "request")]
    assert len(entry.inbound_payloads) == 2, entry.inbound_payloads
    assert entry.inbound_payloads == ("echo", "echo")
    assert entry.operation is Operation.MERGE


def test_accumulating_kinds_are_declared_in_exactly_one_place() -> None:
    """Acceptance: "The accumulating-entity predicate lives in exactly one named
    place". The traversal must consult that predicate rather than testing
    ``kind == 'agent'`` inline, so this test can move the goalposts by patching
    the single declaration."""
    from data_governance.processors.data_lineage import memory

    assert memory.accumulates(Entity(id="a", natural_key="a", kind="agent"))
    assert not memory.accumulates(Entity(id="l", natural_key="l", kind="llm"))
    assert not memory.accumulates(Entity(id="t", natural_key="t", kind="tool"))


def test_traversal_honours_a_redeclared_accumulating_predicate(monkeypatch) -> None:
    """Proof the predicate really is the single source of truth: declare an LLM
    accumulating and #5 turns from ``linear`` into ``merge`` with no other
    change. If any ``kind == 'agent'`` check had been scattered into the
    traversal this would still say ``linear``."""
    from data_governance.processors.data_lineage import memory

    monkeypatch.setattr(memory, "ACCUMULATING_KINDS", frozenset({"agent", "llm"}))

    ents = _entities(user="user", agent="agent", llm="llm")
    legs = [
        _leg("ix_ua", "request", 1, "user", "agent"),
        _leg("ix_al1", "request", 2, "agent", "llm"),
        _leg("ix_al1", "response", 3, "agent", "llm"),
        _leg("ix_al2", "request", 4, "agent", "llm"),
        _leg("ix_al2", "response", 5, "agent", "llm"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert result[("ix_al2", "response")].operation is Operation.MERGE
    assert result[("ix_al2", "response")].inbound_payloads == ("h2", "h4")


def test_memory_node_is_keyed_by_entity_and_a_nullable_memory_key() -> None:
    """Acceptance: "the memory node carries a nullable memory key defaulting to
    unkeyed". ADR-0027's open item — keying memory per session/user/thread later
    must be a value change, not a migration, so the node is ``(entity_id,
    memory_key)`` with ``None`` meaning unkeyed/blob (the v1 default)."""
    from data_governance.processors.data_lineage import memory

    ent = Entity(id="e1", natural_key="agent:a", kind="agent")
    node = memory.memory_node(ent)
    assert node == memory.MemoryNode(entity_id="e1", memory_key=None)
    assert node.memory_key is None, "v1 default is unkeyed/blob"


def test_distinct_memory_keys_do_not_share_inbound(monkeypatch) -> None:
    """The seam works: give the agent a per-interaction memory key and its priors
    stop pooling — each keyed node accumulates alone. Nothing in the traversal
    changes, which is the point of routing inbound through the memory node."""
    from data_governance.processors.data_lineage import memory

    ents = _entities(user="user", agent="agent", llm="llm", tool="tool")
    legs = [
        _leg("ix_ua", "request", 1, "user", "agent", payload_hash="prompt"),
        _leg("ix_al", "request", 2, "agent", "llm", payload_hash="to_llm"),
        _leg("ix_al", "response", 3, "agent", "llm", payload_hash="from_llm"),
        _leg("ix_at", "request", 4, "agent", "tool", payload_hash="to_tool"),
    ]

    # Partition the agent's memory by the interaction it is party to.
    def _keyed(entity: Entity, leg: Leg) -> memory.MemoryNode:
        if entity.kind == "agent":
            return memory.MemoryNode(entity_id=entity.id, memory_key=leg.interaction_id)
        return memory.MemoryNode(entity_id=entity.id, memory_key=None)

    monkeypatch.setattr(memory, "memory_node_for_leg", _keyed)
    result = derive_trace_lineage(legs, ents, matcher=_always)

    # Under the keyed partition the agent's `ix_at` outbound no longer sees the
    # `ix_ua`/`ix_al` priors — they live in different memory nodes.
    assert result[("ix_at", "request")].inbound_payloads == ()
    assert result[("ix_at", "request")].operation is Operation.INIT


# --- D3(2): degrade to init at runtime --------------------------------------


def test_a_refusing_matcher_makes_every_leg_its_own_origin() -> None:
    """Acceptance: "A no-match result from the matcher yields origin-shaped
    metadata per D3(2), verified by a test with a stub matcher that refuses to
    match". Op *selection* is unchanged (it is structural); only the metadata
    degrades."""
    ents = _entities(user="user", agent="agent", llm="llm")
    legs = [
        _leg("ix_ua", "request", 1, "user", "agent"),
        _leg("ix_al1", "request", 2, "agent", "llm"),
        _leg("ix_al1", "response", 3, "agent", "llm"),
        _leg("ix_al2", "request", 4, "agent", "llm"),
        _leg("ix_ua", "response", 5, "user", "agent"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_never)

    # Selection is structural, so the merge is still SELECTED at #4...
    assert result[("ix_al2", "request")].operation is Operation.MERGE
    # ...but every payload is now its own origin, rooted at its producer.
    assert result[("ix_ua", "request")].lineage == operations.init_lineage("user")
    assert result[("ix_al1", "request")].lineage == operations.init_lineage("agent")
    assert result[("ix_al1", "response")].lineage == operations.init_lineage("llm")
    assert result[("ix_al2", "request")].lineage == operations.init_lineage("agent")
    assert result[("ix_ua", "response")].lineage == operations.init_lineage("agent")
    for entry in result.values():
        assert entry.lineage.entity_path == (), "an origin has passed through nothing"


def test_a_partially_refusing_matcher_prunes_only_the_unmatched_source() -> None:
    """The middle case D3(2) really cares about: the merge keeps the sources of
    the inputs that matched and drops the rest, rather than degrading wholesale."""
    ents = _entities(user="user", agent="agent", llm="llm")
    legs = [
        _leg("ix_ua", "request", 1, "user", "agent", payload_hash="prompt"),
        _leg("ix_al1", "request", 2, "agent", "llm", payload_hash="q1"),
        _leg("ix_al1", "response", 3, "agent", "llm", payload_hash="a1"),
        _leg("ix_al2", "request", 4, "agent", "llm", payload_hash="q2"),
    ]

    def _matcher(payload_a: object, payload_b: object, /) -> MatchResult:
        # The agent's second outbound (q2) inherits from `a1` only, not `prompt`.
        if payload_b == "q2":
            return MatchResult(
                matched=payload_a == "a1", transformation=Transformation.SUMMARIZATION
            )
        return MatchResult(matched=True)

    result = derive_trace_lineage(legs, ents, matcher=_matcher)
    entry = result[("ix_al2", "request")]

    assert entry.operation is Operation.MERGE
    assert entry.inbound_payloads == ("prompt", "a1")
    # `a1`'s lineage traces to the user, so the user survives via that branch;
    # the direct `prompt` branch was pruned. The distinguishing evidence is the
    # transformation, attached only through the matching source.
    assert entry.lineage.data_sources == frozenset({"user"})
    assert entry.lineage.source_transformations == {
        "user": frozenset({Transformation.SUMMARIZATION})
    }


# --- determinism / whole-trace properties -----------------------------------


def test_matcher_sees_resolved_payload_content_when_supplied() -> None:
    """The ops compare payload *content* (the spec's ``match(payload_a, payload_b)``),
    so the traversal resolves each leg's hash through the supplied ``payloads`` map.
    The default matcher reads neither argument, hence the map is optional — but a
    real matcher must not be handed hashes."""
    seen: list[tuple[object, object]] = []

    def _recording(payload_a: object, payload_b: object, /) -> MatchResult:
        seen.append((payload_a, payload_b))
        return MatchResult(matched=True)

    ents = _entities(user="user", agent="agent")
    legs = [
        _leg("ix", "request", 1, "user", "agent", payload_hash="h_in"),
        _leg("ix", "response", 2, "user", "agent", payload_hash="h_out"),
    ]
    derive_trace_lineage(
        legs,
        ents,
        matcher=_recording,
        payloads={"h_in": {"prompt": "hi"}, "h_out": {"answer": "yo"}},
    )

    assert seen == [({"prompt": "hi"}, {"answer": "yo"})]


def test_an_unresolvable_hash_falls_back_to_the_hash_itself() -> None:
    """A leg whose payload row is not (yet) present must still be derivable —
    structure is enough for the default matcher, and the matching contract treats a
    payload as opaque ("whatever the caller holds")."""
    seen: list[tuple[object, object]] = []

    def _recording(payload_a: object, payload_b: object, /) -> MatchResult:
        seen.append((payload_a, payload_b))
        return MatchResult(matched=True)

    ents = _entities(user="user", agent="agent")
    legs = [
        _leg("ix", "request", 1, "user", "agent", payload_hash="h_in"),
        _leg("ix", "response", 2, "user", "agent", payload_hash="h_out"),
    ]
    derive_trace_lineage(legs, ents, matcher=_recording, payloads={"h_in": "resolved"})

    assert seen == [("resolved", "h_out")]


def test_every_leg_gets_exactly_one_entry() -> None:
    ents = _entities(user="user", agent="agent", llm="llm")
    legs = [
        _leg("ix_ua", "request", 1, "user", "agent"),
        _leg("ix_al", "request", 2, "agent", "llm"),
        _leg("ix_al", "response", 3, "agent", "llm"),
        _leg("ix_ua", "response", 4, "user", "agent"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert len(result) == len(legs)
    assert set(result) == {(leg.interaction_id, leg.leg_type) for leg in legs}


def test_traversal_is_order_independent_on_input() -> None:
    """The traversal sorts by leg ``seq`` itself, so the caller's row order (a
    ``SELECT`` without an explicit ORDER BY, say) cannot change the answer."""
    ents = _entities(user="user", agent="agent", llm="llm")
    legs = [
        _leg("ix_ua", "request", 1, "user", "agent"),
        _leg("ix_al", "request", 2, "agent", "llm"),
        _leg("ix_al", "response", 3, "agent", "llm"),
        _leg("ix_ua", "response", 4, "user", "agent"),
    ]
    forward = derive_trace_lineage(legs, ents, matcher=_always)
    reversed_ = derive_trace_lineage(list(reversed(legs)), ents, matcher=_always)

    assert forward == reversed_


def test_a_leg_whose_entity_is_unknown_is_skipped_not_crashed() -> None:
    """Derived tables are eventually consistent — a leg can reference an entity
    row the lineage read has not seen yet. Skip that leg rather than fail the
    whole trace (and never invent an entity name for it)."""
    ents = _entities(agent="agent")
    legs = [
        _leg("ix", "request", 1, "ghost", "agent"),
        _leg("ix", "response", 2, "ghost", "agent"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert ("ix", "request") not in result
    # The response leg's producer (the agent) IS known; it just has no inbound
    # lineage from the skipped request, so it is an origin.
    assert result[("ix", "response")].operation is Operation.INIT


def test_a_leg_with_no_payload_is_skipped() -> None:
    """Absent-payload handling and the trace-level partial flag are ticket #120;
    this ticket assumes a payload on every leg. A NULL hash is therefore simply
    not lineage-bearing here — it must not crash, and must not be routed as
    inbound (which would make ``|inbound|`` lie)."""
    ents = _entities(user="user", agent="agent", llm="llm")
    legs = [
        _leg("ix_ua", "request", 1, "user", "agent", payload_hash="prompt"),
        _leg("ix_al", "request", 2, "agent", "llm", payload_hash=None),
        _leg("ix_al", "response", 3, "agent", "llm", payload_hash="answer"),
    ]
    result = derive_trace_lineage(legs, ents, matcher=_always)

    assert ("ix_al", "request") not in result
    # The LLM's response has no matchable inbound (the request carried none), so
    # it becomes an origin rather than silently inheriting the user's lineage.
    assert result[("ix_al", "response")].operation is Operation.INIT
