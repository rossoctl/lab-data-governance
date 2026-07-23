"""DB round-trip for the graph adapter: ``adapt`` → ``state.flush`` → query.

Runs against a real migrated Postgres (the shared ``configured_db`` fixture,
testcontainers). Skipped automatically where Docker is unavailable.

This proves the adapter's rows satisfy the production schema end to end: the
``entity_kind`` ENUM accepts every emitted kind, the shared ``state.flush`` write
path persists the graph algorithm's output into main's tables, and re-running the
same trace is idempotent (deterministic ids collapse on ``ON CONFLICT``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_governance import db, retrieval
from data_governance.processors.interactions import graph_adapter, state
from data_governance.processors.otlp_receiver.write_span import write_span
from data_governance.processors.interactions.graph.extractor import extract
from data_governance.processors.interactions.graph.load_fixture import _row_to_span_row

_FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "processors"
    / "interactions"
    / "graph"
    / "fixtures"
)

# One collision-free trace, one inferred-co-anchored trace, and one A2A
# multi-agent delegation trace — exercising the plain path, the synthetic-span-id
# path, and split-anchor request/response legs against the real PK.
FIXTURES = ["travel_agent_I", "patent_agent_I", "travel_agent_II"]


def _load_into_db(name: str) -> str:
    """Insert a fixture's spans through the real ingest path; return its trace_id."""
    rows = json.loads((_FIXTURES / f"{name}.json").read_text())
    trace_id = rows[0]["trace_id"]
    for row in rows:
        write_span(_row_to_span_row(row))
    return trace_id


def _read_trace(trace_id: str) -> list[retrieval.Span]:
    out: list[retrieval.Span] = []
    cursor = 0
    while True:
        res = retrieval.get_spans(cursor=cursor, limit=500, trace_id=trace_id, order="asc")
        out.extend(res.spans)
        if len(res.spans) < 500:
            break
        cursor = res.spans[-1].seq
    return out


def _sentinel(spans: list[retrieval.Span]) -> retrieval.Span:
    """A stand-in span carrying the trace's MAX seq — the aggregate-recompute
    horizon in ``flush`` (`s.seq <= span.seq`) must cover the whole trace."""
    return max(spans, key=lambda s: s.seq)


def _flush(rows: graph_adapter.ProductionRows, sentinel: retrieval.Span) -> None:
    # Mirror graph_driver.process_span: pass the graph's per-edge legs explicitly
    # so flush takes the graph leg-projection path (own occurred_at/payload/error/
    # order per leg), not the streaming derived-leg default.
    with db.transaction() as tx:
        state.flush(tx, rows, sentinel, legs_by_ix=rows.legs_by_ix)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_adapt_then_flush_persists_production_rows(configured_db: str, fixture: str) -> None:
    trace_id = _load_into_db(fixture)
    spans = _read_trace(trace_id)
    rows = graph_adapter.adapt(extract(spans), spans)

    _flush(rows, _sentinel(spans))

    with db.transaction() as tx:
        n_ent = tx.fetch_one("SELECT count(*) FROM entities")[0]
        n_ix = tx.fetch_one(
            "SELECT count(*) FROM interactions WHERE trace_id = %s", (trace_id,)
        )[0]
        n_isp = tx.fetch_one(
            "SELECT count(*) FROM interaction_spans WHERE trace_id = %s", (trace_id,)
        )[0]

    assert n_ent == len(rows.entities)
    assert n_ix == len(rows.interactions_by_anchor)
    assert n_isp == len(rows.interaction_spans)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_reflush_is_idempotent(configured_db: str, fixture: str) -> None:
    trace_id = _load_into_db(fixture)
    spans = _read_trace(trace_id)
    rows = graph_adapter.adapt(extract(spans), spans)
    sentinel = _sentinel(spans)

    _flush(rows, sentinel)
    with db.transaction() as tx:
        first = tx.fetch_one(
            "SELECT count(*) FROM interactions WHERE trace_id = %s", (trace_id,)
        )[0]

    # A second flush of a freshly-adapted result (deterministic ids) must not
    # create new rows — they collapse on ON CONFLICT.
    rows2 = graph_adapter.adapt(extract(spans), spans)
    _flush(rows2, sentinel)
    with db.transaction() as tx:
        second = tx.fetch_one(
            "SELECT count(*) FROM interactions WHERE trace_id = %s", (trace_id,)
        )[0]

    assert first == second


def test_enum_accepts_every_emitted_kind(configured_db: str) -> None:
    """The travel trace exercises agent + llm; patent adds tool. Flushing both
    proves the entity_kind ENUM accepts every kind the adapter emits."""
    for fixture in FIXTURES:
        trace_id = _load_into_db(fixture)
        spans = _read_trace(trace_id)
        rows = graph_adapter.adapt(extract(spans), spans)
        _flush(rows, _sentinel(spans))  # would raise on a bad ENUM value

    with db.transaction() as tx:
        kinds = {r[0] for r in tx.fetch_all("SELECT DISTINCT kind FROM entities")}
    assert kinds <= {"user", "client", "agent", "tool", "llm", "service"}
    assert {"agent", "llm", "tool"} <= kinds


def test_graph_legs_persist_request_response_from_own_edges(configured_db: str) -> None:
    """ADR-0025: the graph adapter's request/response legs land in
    ``interaction_legs`` with each leg's OWN edge data — the request leg's ``seq``
    (the request edge's ``order``) strictly precedes the response leg's, and step
    4b never widens a graph-supplied leg. For the A2A-delegation trace the two
    legs anchor on different spans, so the response leg's ``occurred_at`` is the
    responding agent's own completion time, distinct from the request edge's."""
    trace_id = _load_into_db("travel_agent_II")
    spans = _read_trace(trace_id)
    rows = graph_adapter.adapt(extract(spans), spans)
    _flush(rows, _sentinel(spans))

    with db.transaction() as tx:
        # Every interaction has exactly one request + one response leg.
        leg_rows = tx.fetch_all(
            "SELECT l.interaction_id::text, l.leg_type::text, l.occurred_at, l.seq "
            "FROM interaction_legs l JOIN interactions i ON i.id = l.interaction_id "
            "WHERE i.trace_id = %s",
            (trace_id,),
        )

    by_ix: dict[str, dict] = {}
    for iid, leg_type, occurred_at, seq in leg_rows:
        by_ix.setdefault(iid, {})[leg_type] = (occurred_at, seq)

    assert by_ix, "expected persisted legs"
    assert len(by_ix) == len(rows.interactions_by_anchor)
    for iid, legs in by_ix.items():
        assert set(legs) == {"request", "response"}, iid
        (req_occ, req_seq), (resp_occ, resp_seq) = legs["request"], legs["response"]
        # order-derived seq: request edge precedes its response edge.
        assert req_seq < resp_seq, f"{iid}: req seq {req_seq} !< resp seq {resp_seq}"
        # step 4b left the graph-supplied occurred_at intact (response is the
        # responding span's completion, so never before the request's start).
        assert resp_occ >= req_occ, iid
