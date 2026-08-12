"""The golden two-span trace — the single fixture for the sidecar-algorithm derivation.

One turn: alice → weather-service (agent) → qwen2.5:7b (llm, plaintext, no
sidecar) ×2 and → weather-tool (mcp, sidecar'd). Ten spans (5 exchanges × 2
halves), one of them the tool pod's callee-side echo. Expected derivation:
4 entities, 4 interactions (all under the entry), 10 interaction_spans (4 anchor
+ 6 connector), 7 payloads (I1-resp and I4-resp share one content hash because
the agent returns the llm's final answer verbatim).

Every field here is a *fact the sidecar emits* per the wire contract — no
hop.kind, no source/target ids. Both a Span-list builder (for pure unit tests)
and a DB inserter (for the integration test) are derived from ``GOLDEN`` so the
two test surfaces cannot drift.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import psycopg

from data_governance.retrieval import Span

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
GHOST = "00f067aa0ba902b7"  # entry wire parent — never exported, never stored

# Ids used in assertions.
A1, A2 = "a1a1a1a1a1a1a1a1", "a2a2a2a2a2a2a2a2"  # entry req / resp
B1, B2 = "b1b1b1b1b1b1b1b1", "b2b2b2b2b2b2b2b2"  # llm#1 req / resp
B3, B4 = "b3b3b3b3b3b3b3b3", "b4b4b4b4b4b4b4b4"  # tool call, caller side
D1, D2 = "d1d1d1d1d1d1d1d1", "d2d2d2d2d2d2d2d2"  # tool call, callee echo
B5, B6 = "b5b5b5b5b5b5b5b5", "b6b6b6b6b6b6b6b6"  # llm#2 req / resp

# The agent's final answer == the llm#2 completion (byte-identical → shared hash).
_ANSWER = "The weather in Tokyo is sunny, 22C."
_LLM_HOST = "host.containers.internal:11434"

# span_id, parent, kind, {facts...}. `role`/`direction`/`protocol` and the
# endpoint facts are all real sidecar attributes.
GOLDEN: list[tuple[str, str | None, str, dict[str, Any]]] = [
    (A1, GHOST, "SERVER", {
        "lineage.role": "request", "lineage.direction": "inbound",
        "lineage.protocol": "a2a", "lineage.exchange.id": A1,
        "lineage.self.id": "weather-service",
        "lineage.principal.sub": "alice", "a2a.method": "message/send",
        "input.value": "what is the weather in Tokyo?",
    }),
    (A2, A1, "SERVER", {
        "lineage.role": "response", "lineage.direction": "inbound",
        "lineage.protocol": "a2a", "lineage.exchange.id": A1,
        "lineage.self.id": "weather-service", "lineage.outcome": "ok",
        "output.value": _ANSWER,
    }),
    (B1, A1, "CLIENT", {
        "lineage.role": "request", "lineage.direction": "outbound",
        "lineage.protocol": "inference", "lineage.exchange.id": B1,
        "lineage.self.id": "weather-service", "lineage.peer.host": _LLM_HOST,
        "inference.model": "qwen2.5:7b",
        "input.value": {"messages": [{"message.content": "user asks weather in Tokyo"}]},
    }),
    (B2, B1, "CLIENT", {
        "lineage.role": "response", "lineage.direction": "outbound",
        "lineage.protocol": "inference", "lineage.exchange.id": B1,
        "lineage.self.id": "weather-service", "lineage.outcome": "ok",
        "output.value": {"messages": [{"message.content": "call get_weather(Tokyo)"}]},
    }),
    (B3, A1, "CLIENT", {
        "lineage.role": "request", "lineage.direction": "outbound",
        "lineage.protocol": "mcp", "lineage.exchange.id": B3,
        "lineage.self.id": "weather-service",
        "lineage.peer.host": "weather-tool-mcp.team1.svc:8000",
        "mcp.method": "tools/call", "mcp.tool": "get_weather",
        "input.value": {"city": "Tokyo"},
    }),
    (B4, B3, "CLIENT", {
        "lineage.role": "response", "lineage.direction": "outbound",
        "lineage.protocol": "mcp", "lineage.exchange.id": B3,
        "lineage.self.id": "weather-service", "lineage.outcome": "ok",
        "output.value": {"tempC": 22, "sky": "sunny"},
    }),
    (D1, B3, "SERVER", {
        "lineage.role": "request", "lineage.direction": "inbound",
        "lineage.protocol": "mcp", "lineage.exchange.id": D1,
        "lineage.self.id": "weather-tool",
        "mcp.method": "tools/call", "mcp.tool": "get_weather",
        "input.value": {"city": "Tokyo"},
    }),
    (D2, D1, "SERVER", {
        "lineage.role": "response", "lineage.direction": "inbound",
        "lineage.protocol": "mcp", "lineage.exchange.id": D1,
        "lineage.self.id": "weather-tool", "lineage.outcome": "ok",
        "output.value": {"tempC": 22, "sky": "sunny"},
    }),
    (B5, A1, "CLIENT", {
        "lineage.role": "request", "lineage.direction": "outbound",
        "lineage.protocol": "inference", "lineage.exchange.id": B5,
        "lineage.self.id": "weather-service", "lineage.peer.host": _LLM_HOST,
        "inference.model": "qwen2.5:7b",
        "input.value": {"messages": [{"message.content": "weather is 22C sunny; summarize"}]},
    }),
    (B6, B5, "CLIENT", {
        "lineage.role": "response", "lineage.direction": "outbound",
        "lineage.protocol": "inference", "lineage.exchange.id": B5,
        "lineage.self.id": "weather-service", "lineage.outcome": "ok",
        "output.value": _ANSWER,  # == entry response → shared content hash
    }),
]

_T0 = dt.datetime(2026, 7, 21, 12, 0, 0, tzinfo=dt.timezone.utc)

# Expected derived entities (natural keys).
ENTITIES = {
    "user:alice",
    "agent:weather-service",
    "tool:weather-tool",
    f"llm:{_LLM_HOST}/qwen2.5:7b",
}
ANCHORS = {A1, B1, B3, B5}  # the four interaction anchors


def build_spans(order: list[str] | None = None) -> list[Span]:
    """Build the golden spans as :class:`Span` objects. *order* is a list of span
    ids giving the seq assignment (arrival order); defaults to declaration order.
    ``parent_id`` is preserved, so reordering scrambles arrival without changing
    structure. Bridge spans in *order* that aren't in ``GOLDEN`` are ignored."""
    by_id = {sid: (parent, kind, attrs) for sid, parent, kind, attrs in GOLDEN}
    order = order or [g[0] for g in GOLDEN]
    seq_of = {sid: i + 1 for i, sid in enumerate(order)}
    spans = []
    for sid in order:
        if sid not in by_id:
            continue
        parent, kind, attrs = by_id[sid]
        spans.append(Span(
            seq=seq_of[sid], trace_id=TRACE, span_id=sid, parent_id=parent,
            name=f"{attrs.get('lineage.self.id')} {attrs.get('lineage.protocol')}",
            started_at=_T0, ended_at=_T0, attributes=dict(attrs), observed_at=_T0,
            arrival_seq=seq_of[sid], kind=kind, error=False,
        ))
    return spans


def to_span_rows(order: list[str] | None = None) -> list[dict[str, Any]]:
    """Build the golden spans as ``spans``-table-row dicts — the fixture shape
    ``tools/load_trace.py`` consumes to replay a trace *over the OTLP wire*.

    This is the single source for the committed OTLP fixture
    (``tests/fixtures/golden_two_span.json``) and the E1 wire-replay test, so
    the replay content cannot drift from the DB-insert / Span-object surfaces.
    ``name``/``service_name`` mirror :func:`_insert_rows` exactly (self.id as the
    span name; ``authbridge`` for sidecar spans, ``shim`` for bridge spans), and
    the timestamps are the shared ``_T0`` — the derivation is order- and
    time-independent, so a single instant per span is faithful."""
    by_id = {sid: (parent, kind, attrs) for sid, parent, kind, attrs in GOLDEN}
    order = order or [g[0] for g in GOLDEN]
    ts = _T0.isoformat()
    rows: list[dict[str, Any]] = []
    for sid in order:
        if sid not in by_id:
            continue
        parent, kind, attrs = by_id[sid]
        rows.append({
            "trace_id": TRACE, "span_id": sid, "parent_id": parent, "kind": kind,
            "name": str(attrs.get("lineage.self.id") or "bridge"),
            "service_name": "authbridge" if attrs.get("lineage.role") else "shim",
            "started_at": ts, "ended_at": ts, "error": False,
            "attributes": dict(attrs),
        })
    return rows


def _insert_rows(conn, rows: list[tuple[str, str | None, str, dict]], seq_of: dict[str, int]) -> None:
    for sid, parent, kind, attrs in rows:
        conn.execute(
            "INSERT INTO spans (trace_id, span_id, parent_id, kind, name, service_name, "
            "started_at, ended_at, error, attributes, seq, arrival_seq, observed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,false,%s::jsonb,%s,%s,%s)",
            (TRACE, sid, parent, kind, str(attrs.get("lineage.self.id") or "bridge"),
             "authbridge" if attrs.get("lineage.role") else "shim",
             _T0, _T0, json.dumps(attrs), seq_of[sid], seq_of[sid], _T0),
        )
    conn.execute("SELECT setval('spans_seq', (SELECT max(seq) FROM spans))")


def insert_spans(dsn: str, order: list[str] | None = None,
                 extra: list[tuple[str, str | None, str, dict[str, Any]]] | None = None) -> None:
    """Insert the golden spans (plus optional *extra* non-sidecar bridge spans)
    into a migrated DB, assigning seq in *order*."""
    rows = list(GOLDEN) + list(extra or [])
    by_id = {r[0]: r for r in rows}
    order = order or [r[0] for r in rows]
    seq_of = {sid: i + 1 for i, sid in enumerate(order)}
    with psycopg.connect(dsn) as conn:
        _insert_rows(conn, [by_id[sid] for sid in order], seq_of)
        conn.commit()


def insert_subset(dsn: str, sids: list[str], start_seq: int) -> int:
    """Insert just *sids* (in order) with contiguous seq from *start_seq*. Returns
    the next free seq. For staged/prefix-arrival tests."""
    by_id = {r[0]: r for r in GOLDEN}
    seq_of = {sid: start_seq + i for i, sid in enumerate(sids)}
    with psycopg.connect(dsn) as conn:
        _insert_rows(conn, [by_id[sid] for sid in sids], seq_of)
        conn.commit()
    return start_seq + len(sids)
