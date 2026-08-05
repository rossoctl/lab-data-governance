"""Data lineage end-to-end over the captured multi-tool traces (issue #117).

Acceptance: "Verified end to end against the captured multi-tool traces already
in the graph fixtures: the reference travel-agent trace reproduces the spec's
worked example, where an agent's **first** outbound has one inbound payload and
later outbounds pool two or more priors."

Since ADR-0028 D11 collapsed the algebra, the op *name* no longer distinguishes
those two cases — every non-root leg reads ``merge``. So the assertions here are on
the **inbound sets**, which is where the acceptance criterion's content actually
lives and which these tests already pinned as the "load-bearing half".

The full pipeline runs here — real spans through the graph P-interactions
algorithm into ``interactions``/``interaction_legs``, then the data-lineage
processor over the legs stream. Nothing is hand-built: ``travel_agent_III`` is the
ADR-0026 reference trace (``8ae1f64d4bb51b750168c6ef1e11a2d8``) captured verbatim
from the deployment, and the ``patent_agent_*`` fixtures are live multi-tool
traces. The op sequence asserted below is therefore whatever the real trace
structurally implies, not a shape chosen to fit.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from data_governance import db
from data_governance.matching import MatchResult
from data_governance.processors.data_lineage import driver, traversal
from data_governance.processors.interactions import graph_driver
from data_governance.processors.interactions.graph.load_fixture import _row_to_span_row
from data_governance.processors.otlp_receiver.write_span import write_span

_FIXTURES = (
    Path(__file__).resolve().parents[1] / "interactions" / "graph" / "fixtures"
)

CANONICAL_TRACE_ID = "8ae1f64d4bb51b750168c6ef1e11a2d8"


# --- pipeline harness --------------------------------------------------------


def _run_pipeline(name: str) -> str:
    """Load a captured trace, derive interactions (graph algorithm), then derive
    data lineage. Returns the trace id."""
    rows = json.loads((_FIXTURES / f"{name}.json").read_text())
    trace_id = rows[0]["trace_id"]
    for row in rows:
        write_span(_row_to_span_row(row))
    graph_driver.drain(0)
    driver.drain(0)
    return trace_id


def _lineage(trace_id: str) -> dict[tuple[str, str], dict]:
    """The persisted lineage rows of *trace_id*, joined to the leg's seq and the
    producing entity so the assertions can talk in execution order."""
    with db.transaction() as tx:
        rows = tx.fetch_all(
            "SELECT lm.interaction_id, lm.leg_type::text, lm.data_sources, "
            "       lm.source_transformations, lm.entities, lm.seq, "
            "       caller.natural_key, callee.natural_key, caller.kind, callee.kind "
            "FROM lineage_metadata lm "
            "JOIN interactions i ON i.id = lm.interaction_id "
            "JOIN entities caller ON caller.id = i.caller_entity_id "
            "JOIN entities callee ON callee.id = i.callee_entity_id "
            "WHERE i.trace_id = %s",
            (trace_id,),
        )
    return {
        (r[0], r[1]): {
            "data_sources": r[2],
            "source_transformations": r[3],
            "entities": r[4],
            "seq": r[5],
            "caller": r[6],
            "callee": r[7],
            "caller_kind": r[8],
            "callee_kind": r[9],
            # A leg is produced by its caller (request) or its callee (response).
            "producer": r[6] if r[1] == "request" else r[7],
            "producer_kind": r[8] if r[1] == "request" else r[9],
        }
        for r in rows
    }


@dataclasses.dataclass(frozen=True)
class DerivedLeg:
    """One leg of a captured trace, joined to what the traversal derived for it.

    ``inbound`` is the leg's :attr:`~traversal.LegLineage.inbound_payloads` — the
    *load-bearing half* of the worked example (``test_traversal.py``
    ``test_spec_worked_example_inbound_sets``): an op name alone would pass with the
    wrong inputs, so the fixture tests pin the inputs too. They are payload hashes,
    which is why the assertions below express an expected inbound set as the
    payloads *of named prior legs* (see :func:`_payloads_of`) rather than as hash
    literals — the spec talks in prior legs, and a captured trace's hashes are
    opaque.
    """

    seq: int
    interaction_id: str
    leg_type: str
    caller: str
    callee: str
    producer: str
    payload_hash: str | None
    operation: str
    inbound: tuple[str, ...]


def _derived(trace_id: str) -> list[DerivedLeg]:
    """Every leg of *trace_id* with its derived op and inbound set, in ``seq`` order.

    Re-derived through the pure traversal over exactly the persisted rows the driver
    read, so both the op *choice* and its *inputs* are observable (the table stores
    metadata, not the op and not the inbound set)."""
    with db.transaction() as tx:
        legs, entities = driver.load_trace(tx, trace_id)
    result = traversal.derive_trace_lineage(legs, entities, matcher=_match_always)
    out = []
    for leg in legs:
        entry = result.legs.get((leg.interaction_id, leg.leg_type))
        if entry is None:  # skipped leg (unknown entity) or past a D6 cutoff
            continue
        caller = entities[leg.caller_entity_id].natural_key
        callee = entities[leg.callee_entity_id].natural_key
        out.append(
            DerivedLeg(
                seq=leg.seq,
                interaction_id=leg.interaction_id,
                leg_type=leg.leg_type,
                caller=caller,
                callee=callee,
                # A leg is produced by its caller (request) or its callee (response).
                producer=caller if leg.leg_type == "request" else callee,
                payload_hash=leg.payload_hash,
                operation=entry.operation.value,
                inbound=tuple(entry.inbound_payloads),
            )
        )
    return sorted(out, key=lambda d: d.seq)


def _payloads_of(legs: list[DerivedLeg]) -> tuple[str, ...]:
    """The payload hashes *legs* carry, in ``seq`` order — how an expected inbound
    set is spelled here.

    Inbound sets are compared as payload *sequences* because that is what the
    traversal reports, but they are *specified* as "these prior legs, in seq order":
    the spec's own language (D1: "in an interaction with lower sequence"), and the
    only form that stays readable against a captured trace's 64-hex hashes. It is no
    weaker than pinning literals — the legs are named individually and their order
    is fixed — and it keeps duplicates honest: two distinct priors carrying
    byte-identical content appear twice, which is exactly the ``patent_agent_II``
    regression ``test_traversal.py`` pins by hand."""
    ordered = sorted(legs, key=lambda d: d.seq)
    # An absent payload would truncate the trace (D6), so a leg the traversal
    # derived always has one — assert it rather than silently comparing a None.
    assert all(leg.payload_hash is not None for leg in ordered), ordered
    return tuple(leg.payload_hash for leg in ordered)  # type: ignore[misc]


def _priors_of(entity: str, produced: DerivedLeg, trace: list[DerivedLeg]) -> list[DerivedLeg]:
    """The legs of *trace* whose payloads are inbound to *entity* before it produced
    *produced* — **ADR-0028 D1 transcribed**, and the single place these tests express
    it: "a payload is inbound to entity E iff, in a leg with lower ``seq``, E is the
    callee and the payload is the request, OR E is the caller and the payload is the
    response".

    Written out from the ADR rather than read back off the traversal, so it is an
    independent expectation: if the implementation's routing flipped a side, the
    traversal's answer would move and this would not.

    Returns **every** such prior, so it is the expectation for an *accumulating*
    entity only (D2 — an agent retains all of them). A memoryless peer keeps just the
    latest, and the peer tests assert that directly."""
    return [
        leg
        for leg in trace
        if leg.seq < produced.seq
        and (
            (leg.leg_type == "response" and leg.caller == entity)
            or (leg.leg_type == "request" and leg.callee == entity)
        )
    ]


def _match_always(payload_a: object, payload_b: object, /) -> MatchResult:
    """The trivial default's behaviour, stated explicitly — ``_derived`` must
    re-derive with the SAME matcher the drain used, or the ops it reports would not
    be the ops that produced the persisted rows."""
    return MatchResult(matched=True)


# --- the reference trace: travel_agent_III ----------------------------------


def test_canonical_trace_gets_lineage_for_every_leg(configured_db: str) -> None:
    trace_id = _run_pipeline("travel_agent_III")
    assert trace_id == CANONICAL_TRACE_ID

    with db.transaction() as tx:
        n_legs = tx.fetch_one(
            "SELECT count(*) FROM interaction_legs l JOIN interactions i "
            "ON i.id = l.interaction_id WHERE i.trace_id = %s "
            "AND l.payload_hash IS NOT NULL",
            (trace_id,),
        )[0]
    rows = _lineage(trace_id)

    assert n_legs > 0, "sanity: the reference trace has payload-bearing legs"
    assert len(rows) == n_legs, "one lineage row per payload-bearing leg"


def test_canonical_trace_agent_roots_the_trace_then_merges_growing_priors(
    configured_db: str,
) -> None:
    """**The acceptance criterion.** In the reference trace a single
    travel-advisor agent calls one LLM and three tools, so it produces several
    outbound legs. Its first outbound has nothing inbound; its next has exactly one
    prior; every later one has ≥2 retained priors (D2 assumes transient memory always
    present).

    The reference trace's op sequence for the agent is ``init, merge, merge, …`` —
    the spec's worked-example pattern shifted by one, because this captured trace has
    **no user/client entity**: the agent IS the trace root (no caller of the agent
    emits a span, so no user→agent interaction is derived). Its very first outbound
    therefore has nothing inbound at all and is a genuine structural ``init``
    (ADR-0028 D3(1) — "the payload originates outside the trace"), where the spec's
    hand-drawn example starts with an explicit ``-1-> Agent``.

    The one-prior → many-priors transition the criterion is about is asserted on the
    **inbound counts**: since D11 both cases read ``merge``, so an op-name assertion
    would no longer see it. The counts come from the trace's own structure rather than
    being enumerated by hand."""
    trace_id = _run_pipeline("travel_agent_III")
    derived = _derived(trace_id)

    agents = {d.producer for d in derived if d.producer.startswith("agent:")}
    assert len(agents) == 1, f"the reference trace has one agent; got {agents}"
    agent = agents.pop()

    agent_legs = [d for d in derived if d.producer == agent]
    assert len(agent_legs) >= 4, f"expected several agent-produced legs, got {agent_legs}"

    # The agent is the trace root, so its first payload originates here (D3(1)).
    assert agent_legs[0].operation == "init", agent_legs[0]
    assert agent_legs[0].inbound == ()
    # Every later leg runs the single generic op (D11) — selection is two-way now.
    assert all(d.operation == "merge" for d in agent_legs[1:]), [
        d.operation for d in agent_legs
    ]
    # Its FIRST outbound with an inbound: exactly one prior.
    assert len(agent_legs[1].inbound) == 1, agent_legs[1].inbound
    # Every later one: priors have accumulated past one.
    assert all(len(d.inbound) >= 2 for d in agent_legs[2:]), [
        len(d.inbound) for d in agent_legs
    ]


def test_canonical_trace_agent_inbound_accumulates_exactly_its_priors(
    configured_db: str,
) -> None:
    """**The load-bearing half of the acceptance criterion.** The op *names* above
    would read the same if the ops had run on the wrong inputs, so pin the inputs:
    for every leg the agent produces, its inbound set must be **exactly the payloads
    of the response legs it received earlier, in seq order** — nothing more, nothing
    fewer, in that order.

    That single expectation is D1 and D2 together, and it fails if either breaks:

    * D1 (routing) — the agent is the *caller* of every interaction in this trace,
      so responses are inbound to it and requests are not. Route requests inbound to
      the caller instead and the expected set would contain the agent's own
      outbound payloads; drop the caller side and it would be empty.
    * D2 (memory) — an agent accumulates, so *every* prior response is retained.
      Treat the agent as memoryless and only the latest would appear.

    Expressed as prior legs rather than hashes (see :func:`_payloads_of`) because
    that is the spec's own language and because this captured trace contains a real
    hash collision: the two ``get_flights`` calls (seq 11 and 15) errored with
    byte-identical payloads, so the agent's later inbound sets legitimately list
    that one hash *twice* — once per prior leg. Priors are retained by position, not
    by content (ADR-0028 D5), and this trace is where that matters.
    """
    trace_id = _run_pipeline("travel_agent_III")
    derived = _derived(trace_id)

    agents = {d.producer for d in derived if d.producer.startswith("agent:")}
    assert len(agents) == 1, f"the reference trace has one agent; got {agents}"
    agent = agents.pop()

    agent_legs = [d for d in derived if d.producer == agent]
    assert len(agent_legs) >= 4, f"expected several agent-produced legs, got {agent_legs}"

    for produced in agent_legs:
        expected_priors = _priors_of(agent, produced, derived)
        assert produced.inbound == _payloads_of(expected_priors), (
            produced.seq,
            produced.inbound,
            [(d.seq, d.leg_type) for d in expected_priors],
        )
        # In this trace the agent is the caller of every interaction, so every prior
        # is a RESPONSE it received. Spelled out because it is what makes the check
        # above a routing check: if requests were (wrongly) routed to the caller the
        # agent's own outbounds would appear among its priors.
        assert all(d.leg_type == "response" for d in expected_priors), expected_priors

    # D2's consequence, stated directly: the inbound count grows by exactly one
    # response per agent-produced leg, so it is strictly monotonic after the root.
    counts = [len(d.inbound) for d in agent_legs]
    assert counts[0] == 0, counts  # the trace root: nothing reached the agent
    assert counts == list(range(len(counts))), counts
    # ...and the earlier inbound set is a genuine PREFIX of the later one — an
    # accumulating entity never drops or reorders a prior (D2).
    for earlier, later in zip(agent_legs, agent_legs[1:]):
        assert later.inbound[: len(earlier.inbound)] == earlier.inbound, (
            earlier.seq,
            later.seq,
        )


def test_canonical_trace_memoryless_peers_merge_over_exactly_one_input(
    configured_db: str,
) -> None:
    """The other half of D2: the LLM and the three tools do not accumulate, so
    every payload they produce comes from exactly one input, however many times they
    are called.

    In *this* trace every peer leg is a **response** whose interaction's request leg
    is payload-bearing, so no peer leg is ever a structural ``init``. That is asserted
    outright rather than as ``{"merge", "init"}`` — the loose form would pass an
    all-``init`` regression, which is precisely the failure that would mean inbound
    routing had stopped delivering requests to callees.

    And the *input* is pinned, not just the op name — since D11 that matters more, not
    less: ``merge`` alone no longer says "one input", so the single-element inbound
    tuple is the whole memoryless claim. It must be the request leg of the peer's
    **own interaction** (D1, first half — "E is the callee and the payload is the
    request")."""
    trace_id = _run_pipeline("travel_agent_III")
    derived = _derived(trace_id)

    request_of = {
        d.interaction_id: d for d in derived if d.leg_type == "request"
    }
    peer_legs = [d for d in derived if d.producer.startswith(("llm:", "tool:"))]
    assert peer_legs, "sanity: the reference trace has llm/tool peers"

    for peer in peer_legs:
        # Every peer-produced leg in this trace is the response side of a call made
        # TO that peer; it is never a caller, so it never roots the trace.
        assert peer.leg_type == "response", peer
        assert peer.operation == "merge", peer
        assert peer.inbound == (request_of[peer.interaction_id].payload_hash,), (
            peer.seq,
            peer.inbound,
        )
    # No peer is a structural init anywhere in this trace — the point of dropping
    # the `{"merge", "init"}` disjunction.
    assert not [d for d in peer_legs if d.operation == "init"], peer_legs


def test_canonical_trace_tool_results_flow_into_the_agents_answer(
    configured_db: str,
) -> None:
    """The governance question the whole feature answers — "what are the data
    sources of this answer". The agent's LAST outbound is its reply, and under the
    trivial always-match matcher it must carry the trace's origin and name the
    entities it passed through, including the tools."""
    trace_id = _run_pipeline("travel_agent_III")
    rows = _lineage(trace_id)

    agent_legs = sorted(
        (r["seq"], key)
        for key, r in rows.items()
        if r["producer"].startswith("agent:")
    )
    final = rows[agent_legs[-1][1]]

    assert final["data_sources"], "the answer must trace to at least one source"
    # It passed through the LLM and at least one tool.
    assert any(e.startswith("llm:") for e in final["entities"]), final["entities"]
    assert any(e.startswith("tool:") for e in final["entities"]), final["entities"]


def test_canonical_trace_answer_roots_at_every_tool_that_fired(
    configured_db: str,
) -> None:
    """**The observable D12 was landed for** (ADR-0028 D12, issue #131). Before it,
    every leg of this trace reported the agent as its sole source: a tool that read an
    external store had its ingress attributed to the caller, which is the exact
    failure a governance tool must not make.

    Now a ``kind='tool'`` entity contributes itself, and those contributions
    accumulate down the trace through the agent's memory (D2), so the agent's final
    answer roots at **every tool that fired** — not just at the agent.

    Under ``simple_match`` every tool leg adds its tool, so the set grows
    monotonically. That is the accepted trade (ADR-0028 §Semantic matching): erring
    toward over-reporting origins, since the failure being replaced was
    *under*-reporting an external data ingress. It is also why this asserts equality
    against the trace's own tool set rather than a hand-written list."""
    trace_id = _run_pipeline("travel_agent_III")
    rows = _lineage(trace_id)

    tools_that_produced = {
        r["producer"] for r in rows.values() if r["producer_kind"] == "tool"
    }
    assert tools_that_produced, "sanity: the reference trace has tool legs"

    agent_legs = sorted(
        (r["seq"], key)
        for key, r in rows.items()
        if r["producer"].startswith("agent:")
    )
    final = rows[agent_legs[-1][1]]

    assert tools_that_produced <= set(final["data_sources"]), (
        tools_that_produced - set(final["data_sources"])
    )
    # ...and the agent itself is still there — the entity's own contribution is added
    # ALONGSIDE what was inherited, never instead of it.
    assert any(s.startswith("agent:") for s in final["data_sources"]), final[
        "data_sources"
    ]


def test_canonical_trace_a_tool_leg_names_its_own_tool_as_a_source(
    configured_db: str,
) -> None:
    """The same fact one leg at a time, and the sharper claim: *every* tool-produced
    leg lists its own tool among its sources, with an EMPTY transformation set.

    The empty set is D12's construction rule — the tool's own contribution did not
    undergo the transformation the inherited sources did. An LLM-produced leg must NOT
    list its LLM, which is the ✗ half of the taxonomy defaults and the guard against
    "every mid-trace entity is now a source"."""
    trace_id = _run_pipeline("travel_agent_III")
    rows = _lineage(trace_id)

    tool_legs = [r for r in rows.values() if r["producer_kind"] == "tool"]
    llm_legs = [r for r in rows.values() if r["producer_kind"] == "llm"]
    assert tool_legs and llm_legs, "sanity: the reference trace has tool and llm legs"

    for r in tool_legs:
        assert r["producer"] in r["data_sources"], (r["seq"], r["data_sources"])
        assert r["source_transformations"][r["producer"]] == [], (
            r["seq"],
            r["source_transformations"],
        )
    for r in llm_legs:
        assert r["producer"] not in r["data_sources"], (r["seq"], r["data_sources"])


def test_canonical_trace_lineage_is_idempotent(configured_db: str) -> None:
    """Acceptance: re-deriving converges. The first drain already re-derives the
    trace once per arriving leg; a cursor reset and re-drain must not change the
    row count or any content."""
    trace_id = _run_pipeline("travel_agent_III")
    first = _lineage(trace_id)

    with db.transaction() as tx:
        tx.execute(
            "UPDATE processor_state SET last_processed_seq = 0 WHERE processor_name = %s",
            (driver.PROCESSOR_NAME,),
        )
    driver.drain(0)
    second = _lineage(trace_id)

    assert first == second


# --- the multi-tool merge traces: patent_agent_I / II ------------------------


def test_patent_agent_multitool_trace_merges_after_the_first_outbound(
    configured_db: str,
) -> None:
    """``patent_agent_II`` is the live three-LLM-turn, two-distinct-tool trace.
    Same rule on a different real shape: the agent roots the trace (``init``), its
    first outbound with an inbound pools one prior and the rest pool more — with the
    inbound sets pinned, as above, since the op names alone do not say which priors
    were consumed (and since D11, do not say how many either).

    This is also **the** trace the by-position retention rule exists for: the tool
    interactions are inferred from the LLM response's ``tool_calls``, so a tool's
    response leg carries the SAME ``payload_hash`` as the LLM response it was
    inferred from (``test_traversal.py``
    ``test_two_priors_with_identical_payloads_are_two_priors``). The agent's inbound
    sets therefore contain repeated hashes on purpose; asserting them as "the prior
    response legs, in seq order" keeps that visible, where a hash-set comparison
    would silently accept a collapsed form that loses one of the agent's priors."""
    trace_id = _run_pipeline("patent_agent_II")
    derived = _derived(trace_id)

    agent_legs = [d for d in derived if d.producer.startswith("agent:")]
    agent_ops = [d.operation for d in agent_legs]
    assert len(agent_ops) >= 4, agent_ops
    assert agent_ops[0] == "init", agent_ops
    assert all(op == "merge" for op in agent_ops[1:]), agent_ops
    assert len(agent_legs[1].inbound) == 1, agent_legs[1].inbound
    assert all(len(d.inbound) >= 2 for d in agent_legs[2:]), [
        len(d.inbound) for d in agent_legs
    ]

    agent = agent_legs[0].producer
    for produced in agent_legs:
        expected_priors = _priors_of(agent, produced, derived)
        assert produced.inbound == _payloads_of(expected_priors), (
            produced.seq,
            produced.inbound,
            [(d.seq, d.leg_type) for d in expected_priors],
        )
    # The by-position evidence, made explicit: at least one of the agent's merges
    # consumed two priors carrying identical bytes, and both were counted.
    assert any(len(set(d.inbound)) < len(d.inbound) for d in agent_legs), [
        d.inbound for d in agent_legs
    ]

    # And the peers stay memoryless here too — one input each, its own request.
    request_of = {d.interaction_id: d for d in derived if d.leg_type == "request"}
    peer_legs = [d for d in derived if d.producer.startswith(("llm:", "tool:"))]
    assert peer_legs
    for peer in peer_legs:
        assert peer.operation == "merge", peer
        assert peer.inbound == (request_of[peer.interaction_id].payload_hash,), peer


def test_patent_agent_replay_trace_accumulates_sources_monotonically(
    configured_db: str,
) -> None:
    """``patent_agent_I`` replays earlier tool calls on later turns' inputs. As
    the agent accumulates priors, the set of data sources on its successive
    outbound payloads can only grow — a merge unions, it never drops a source
    (with the always-match default matcher)."""
    trace_id = _run_pipeline("patent_agent_I")
    rows = _lineage(trace_id)

    agent_legs = sorted(
        (r["seq"], key) for key, r in rows.items() if r["producer"].startswith("agent:")
    )
    sources = [set(rows[key]["data_sources"]) for _, key in agent_legs]
    assert len(sources) >= 2, agent_legs
    for earlier, later in zip(sources, sources[1:]):
        assert earlier <= later, (earlier, later)


def test_two_traces_do_not_share_lineage(configured_db: str) -> None:
    """Intra-trace only (ADR-0028; inter-trace is Step II, deferred). Two captured
    traces in one database must produce disjoint source sets — a leak here would
    be a false cross-trace data-flow claim, the exact failure a governance tool
    must not make."""
    travel = _run_pipeline("travel_agent_III")
    patent = _run_pipeline("patent_agent_II")

    travel_sources = {s for r in _lineage(travel).values() for s in r["data_sources"]}
    patent_sources = {s for r in _lineage(patent).values() for s in r["data_sources"]}

    assert travel_sources and patent_sources
    assert travel_sources.isdisjoint(patent_sources), (
        travel_sources & patent_sources
    )


def test_every_data_source_is_a_real_entity_of_the_trace(configured_db: str) -> None:
    """A data source is an **Entity** natural key (spec rule 1: "the data source
    is assigned the entity name"), so every source must name an entity that
    actually participates in the trace — no invented names."""
    trace_id = _run_pipeline("travel_agent_III")
    rows = _lineage(trace_id)

    with db.transaction() as tx:
        known = {
            r[0]
            for r in tx.fetch_all(
                "SELECT DISTINCT e.natural_key FROM entities e "
                "JOIN entity_spans es ON es.entity_id = e.id WHERE es.trace_id = %s",
                (trace_id,),
            )
        }
    for key, r in rows.items():
        assert set(r["data_sources"]) <= known, (key, r["data_sources"])
        assert set(r["entities"]) <= known, (key, r["entities"])
        # The map's keys are exactly the source set.
        assert set(r["source_transformations"]) == set(r["data_sources"]), key
