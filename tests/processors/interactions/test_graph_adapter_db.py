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
import re
from pathlib import Path

import pytest

from data_governance import db, retrieval
from data_governance.processors.interactions import graph_adapter, state
from data_governance.processors.otlp_receiver.write_span import write_span
from data_governance.processors.interactions.graph.extractor import extract
from data_governance.processors.interactions.graph.load_fixture import _row_to_span_row
from tests.processors.interactions.graph.travel_agent_II.test_interactions import (
    EXPECTED_ORDER,
)

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


def _short_key(kind: str, natural_key: str) -> str:
    """Collapse the adapter's rich entity key back to the extractor's short
    natural-key vocabulary (what ``EXPECTED_ORDER`` uses). The adapter re-derives
    entities with Step-3.a group keys (``agent:(group,service)``), fully-qualified
    llm keys (``llm:<host>/<model>``) and scoped tool keys
    (``tool:agent:(...):<name>``); the extractor's oracle uses the bare
    ``agent:<service>`` / ``llm:<model>`` / ``tool:<name>``."""
    # The natural_key already carries its own ``<kind>:`` prefix; strip it so we
    # can re-prefix uniformly below.
    body = natural_key[len(kind) + 1 :] if natural_key.startswith(f"{kind}:") else natural_key
    m = re.match(r"\((?:[^,]*),([^)]*)\)$", body)
    if kind == "agent" and m:
        short = m.group(1)
    elif kind == "llm":
        short = body.rsplit("/", 1)[-1]
    elif kind == "tool":
        short = body.rsplit(":", 1)[-1]
    else:
        short = body
    # Service names hyphenate where natural keys may use underscores
    # (booking_agent vs booking-agent); normalise for a vocabulary-free compare.
    return f"{kind}:{short}".replace("_", "-")


def test_persisted_leg_seq_is_globally_unique_and_matches_expected_order(
    configured_db: str,
) -> None:
    """END-TO-END guard for the whole derive-and-persist process (the bug where a
    self-paired one-sided call gave two legs the SAME ``seq``): after
    ``adapt`` → ``state.flush`` against the real schema, the persisted
    ``interaction_legs`` of the human-validated ``travel_agent_II`` trace must have

      1. GLOBALLY-UNIQUE ``seq`` — one row per graph edge, never a duplicate; and
      2. a ``seq`` ordering that reproduces the oracle ``EXPECTED_ORDER`` EXACTLY
         (request leg = caller→callee, response leg = callee→caller).

    This is the invariant the fixture-level `extract`/`adapt` tests could not
    catch on their own once the rows are written: it reads them straight back out
    of Postgres, so a regression in the adapter OR the flush write path fails here.
    """
    trace_id = _load_into_db("travel_agent_II")
    spans = _read_trace(trace_id)
    rows = graph_adapter.adapt(extract(spans), spans)
    _flush(rows, _sentinel(spans))

    with db.transaction() as tx:
        leg_rows = tx.fetch_all(
            "SELECT l.seq, l.leg_type::text, "
            "ec.kind::text, ec.natural_key, ee.kind::text, ee.natural_key "
            "FROM interaction_legs l "
            "JOIN interactions i ON i.id = l.interaction_id "
            "JOIN entities ec ON ec.id = i.caller_entity_id "
            "JOIN entities ee ON ee.id = i.callee_entity_id "
            "WHERE i.trace_id = %s "
            "ORDER BY l.seq",
            (trace_id,),
        )

    seqs = [r[0] for r in leg_rows]
    assert len(seqs) == len(EXPECTED_ORDER), (
        f"persisted {len(seqs)} legs, expected {len(EXPECTED_ORDER)}"
    )
    # (1) No two persisted legs share a seq — the exact invariant the self-pair
    # bug violated (72 legs / 50 distinct seq in the live DB before the fix).
    assert len(set(seqs)) == len(seqs), f"duplicate leg seq persisted: {seqs}"
    assert seqs == sorted(seqs) and seqs[0] == 0 and seqs[-1] == len(seqs) - 1, (
        f"seq is not the dense 0..N-1 ordinal: {seqs}"
    )

    # (2) The seq ordering reproduces the human-validated execution order. The
    # interaction stores caller→callee (the request sense); a response leg's
    # displayed direction is the swap (callee→caller).
    persisted_order = []
    for seq, leg_type, ck, cnk, ek, enk in leg_rows:
        caller, callee = _short_key(ck, cnk), _short_key(ek, enk)
        persisted_order.append(
            (caller, callee) if leg_type == "request" else (callee, caller)
        )

    expected = [
        (c.replace("_", "-"), e.replace("_", "-")) for c, e in EXPECTED_ORDER
    ]
    assert persisted_order == expected
