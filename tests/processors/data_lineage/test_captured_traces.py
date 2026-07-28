"""Data lineage end-to-end over the captured multi-tool traces (issue #117).

Acceptance: "Verified end to end against the captured multi-tool traces already
in the graph fixtures: the reference travel-agent trace reproduces the spec's
worked example, where an agent's **first** outbound uses ``linear`` and later
outbounds use ``merge`` once two or more priors exist."

The full pipeline runs here — real spans through the graph P-interactions
algorithm into ``interactions``/``interaction_legs``, then the data-lineage
processor over the legs stream. Nothing is hand-built: ``travel_agent_III`` is the
ADR-0026 reference trace (``8ae1f64d4bb51b750168c6ef1e11a2d8``) captured verbatim
from the deployment, and the ``patent_agent_*`` fixtures are live multi-tool
traces. The op sequence asserted below is therefore whatever the real trace
structurally implies, not a shape chosen to fit.
"""

from __future__ import annotations

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
            "       lm.source_transformations, lm.entity_path, lm.seq, "
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
            "entity_path": r[4],
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


def _ops(trace_id: str) -> list[tuple[int, str, str]]:
    """``(leg seq, producing entity, operation)`` in execution order — re-derived
    through the pure traversal over exactly the persisted rows the driver read,
    so the op *choice* is observable (the table stores metadata, not the op)."""
    with db.transaction() as tx:
        legs, entities = driver.load_trace(tx, trace_id)
    result = traversal.derive_trace_lineage(legs, entities, matcher=_match_always)
    by_key = {(leg.interaction_id, leg.leg_type): leg for leg in legs}
    return sorted(
        (
            by_key[key].seq,
            entities[
                by_key[key].caller_entity_id
                if key[1] == "request"
                else by_key[key].callee_entity_id
            ].natural_key,
            entry.operation.value,
        )
        for key, entry in result.legs.items()
    )


def _match_always(payload_a: object, payload_b: object, /) -> MatchResult:
    """The trivial default's behaviour, stated explicitly — ``_ops`` must re-derive
    with the SAME matcher the drain used, or the ops it reports would not be the ops
    that produced the persisted rows."""
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


def test_canonical_trace_agent_first_outbound_is_linear_later_are_merge(
    configured_db: str,
) -> None:
    """**The acceptance criterion.** In the reference trace a single
    travel-advisor agent calls one LLM and three tools, so it produces several
    outbound legs. Its first outbound *that has an inbound payload* has exactly
    one and must use ``linear``; every later one has ≥2 retained priors (D2
    assumes transient memory always present) and must use ``merge``.

    The reference trace's op sequence for the agent is ``init, linear, merge,
    merge, …`` — the spec's worked-example pattern shifted by one, because this
    captured trace has **no user/client entity**: the agent IS the trace root (no
    caller of the agent emits a span, so no user→agent interaction is derived).
    Its very first outbound therefore has nothing inbound at all and is a genuine
    structural ``init`` (ADR-0027 D3(1) — "the payload originates outside the
    trace"), where the spec's hand-drawn example starts with an explicit
    ``-1-> Agent``. The linear→merge transition the criterion is about is
    unchanged, and asserted on the trace's own structure below rather than
    enumerated by hand."""
    trace_id = _run_pipeline("travel_agent_III")
    ops = _ops(trace_id)

    agents = {
        entity
        for _, entity, _ in ops
        if entity.startswith("agent:")
    }
    assert len(agents) == 1, f"the reference trace has one agent; got {agents}"
    agent = agents.pop()

    agent_ops = [op for _, entity, op in ops if entity == agent]
    assert len(agent_ops) >= 4, f"expected several agent-produced legs, got {agent_ops}"

    # The agent is the trace root, so its first payload originates here (D3(1)).
    assert agent_ops[0] == "init", agent_ops
    # Its FIRST outbound with an inbound: exactly one prior, so linear.
    assert agent_ops[1] == "linear", agent_ops
    # Every later one: priors have accumulated past one, so merge.
    assert all(op == "merge" for op in agent_ops[2:]), agent_ops


def test_canonical_trace_memoryless_peers_always_use_linear(
    configured_db: str,
) -> None:
    """The other half of D2: the LLM and the three tools do not accumulate, so
    every payload they produce comes from exactly one input — ``linear``, however
    many times they are called. (``init`` is still possible if a peer's inbound
    request carried no payload.)"""
    trace_id = _run_pipeline("travel_agent_III")
    ops = _ops(trace_id)

    peer_ops = [
        (seq, entity, op)
        for seq, entity, op in ops
        if entity.startswith(("llm:", "tool:"))
    ]
    assert peer_ops, "sanity: the reference trace has llm/tool peers"
    assert all(op in {"linear", "init"} for _, _, op in peer_ops), peer_ops
    assert any(op == "linear" for _, _, op in peer_ops), peer_ops


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
    assert any(e.startswith("llm:") for e in final["entity_path"]), final["entity_path"]
    assert any(e.startswith("tool:") for e in final["entity_path"]), final["entity_path"]


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
    first outbound with an inbound is ``linear``, and the rest ``merge``."""
    trace_id = _run_pipeline("patent_agent_II")
    ops = _ops(trace_id)

    agent_ops = [op for _, entity, op in ops if entity.startswith("agent:")]
    assert len(agent_ops) >= 4, agent_ops
    assert agent_ops[0] == "init", agent_ops
    assert agent_ops[1] == "linear", agent_ops
    assert all(op == "merge" for op in agent_ops[2:]), agent_ops


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
    """Intra-trace only (ADR-0027; inter-trace is Step II, deferred). Two captured
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
        assert set(r["entity_path"]) <= known, (key, r["entity_path"])
        # The map's keys are exactly the source set.
        assert set(r["source_transformations"]) == set(r["data_sources"]), key
