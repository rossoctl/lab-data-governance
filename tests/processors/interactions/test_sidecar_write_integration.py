"""Integration: the golden two-span trace through a real migrated Postgres,
derived by the sidecar algorithm into the ADR-0025 legs schema.

Asserts the exact derived tables (4 entities / 4 interactions / 8 legs / 10
interaction_spans / 7 payloads with the exact ids and leg types), then that
every arrival permutation converges to the identical tables, that prefix
arrivals expose in-flight rows (response leg absent), that anchor demotion
deletes the stale interaction AND its legs (the reconcile's shrink case —
the semantics state.flush cannot express), and that interleaved bridge (shim)
spans do not change the derivation.
"""

from __future__ import annotations

import json

import psycopg
import pytest

from data_governance import db
from data_governance.processors.interactions.procedure import _interaction_id
from data_governance.processors.interactions.sidecar import derive_trace
from data_governance.processors.interactions.sidecar_driver import drain

from . import sidecar_golden as golden

# ``configured_db`` (migrated DB + pool pointed at it) comes from
# tests/processors/conftest.py.

I1 = _interaction_id(golden.TRACE, golden.A1)
I2 = _interaction_id(golden.TRACE, golden.B1)
I3 = _interaction_id(golden.TRACE, golden.B3)
I4 = _interaction_id(golden.TRACE, golden.B5)


def _snapshot(dsn: str) -> dict:
    with psycopg.connect(dsn) as conn:
        entities = {r[0] for r in conn.execute("SELECT natural_key FROM entities").fetchall()}
        ix: dict[str, dict] = {
            r[0]: {"caller": r[1], "callee": r[2], "parent": r[3], "legs": {}}
            for r in conn.execute(
                "SELECT i.id, ca.natural_key, ce.natural_key, i.parent_interaction_id "
                "FROM interactions i "
                "JOIN entities ca ON ca.id = i.caller_entity_id "
                "JOIN entities ce ON ce.id = i.callee_entity_id"
            ).fetchall()
        }
        for iid, leg_type, occurred_at, payload_hash, error, seq in conn.execute(
            "SELECT interaction_id, leg_type, occurred_at, payload_hash, error, seq "
            "FROM interaction_legs"
        ).fetchall():
            assert iid in ix, f"orphan leg {iid}/{leg_type}"  # legs never outlive parents
            ix[iid]["legs"][leg_type] = {
                "occurred": occurred_at is not None,
                "hash": payload_hash,
                "error": error,
                "seq": seq,
            }
        # Read-side conveniences mirroring the old single-row shape.
        for row in ix.values():
            resp = row["legs"].get("response")
            row["error"] = resp["error"] if resp is not None else None
            row["req"] = row["legs"]["request"]["hash"] is not None
            row["resp"] = resp is not None and resp["hash"] is not None
            row["ended"] = resp is not None and resp["occurred"]
        spans = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            "SELECT span_id, interaction_id, role, leg_type FROM interaction_spans").fetchall()}
        roles: dict[str, int] = {}
        for _iid, role, _leg in spans.values():
            roles[role] = roles.get(role, 0) + 1
        payloads = conn.execute("SELECT count(*) FROM interaction_payloads").fetchone()[0]
        entity_spans = conn.execute("SELECT count(*) FROM entity_spans").fetchone()[0]
    return {"entities": entities, "ix": ix, "spans": spans, "roles": roles,
            "payloads": payloads, "entity_spans": entity_spans}


def _assert_golden(snap: dict) -> None:
    assert snap["entities"] == golden.ENTITIES
    assert set(snap["ix"]) == {I1, I2, I3, I4}
    # All four parented under the entry; the entry is the root.
    assert snap["ix"][I1]["parent"] is None
    assert snap["ix"][I2]["parent"] == I1
    assert snap["ix"][I3]["parent"] == I1
    assert snap["ix"][I4]["parent"] == I1
    # Endpoints.
    assert (snap["ix"][I1]["caller"], snap["ix"][I1]["callee"]) == (
        "user:alice", "agent:weather-service")
    assert snap["ix"][I3]["callee"] == "tool:weather-tool"  # from echo self.id
    assert snap["ix"][I2]["callee"] == f"llm:{golden._LLM_HOST}/qwen2.5:7b"
    # All complete: both observed legs per interaction (8 legs total), with the
    # request leg carrying no verdict (the wire has no request-side outcome)
    # and the response leg carrying error=False; request seq <= response seq
    # (the ADR-0027 watermark ordering) whatever the arrival order was.
    for row in snap["ix"].values():
        assert set(row["legs"]) == {"request", "response"}
        assert row["legs"]["request"]["error"] is None
        assert row["legs"]["response"]["error"] is False
        assert row["legs"]["request"]["seq"] <= row["legs"]["response"]["seq"]
        assert row["legs"]["request"]["occurred"] and row["legs"]["response"]["occurred"]
        assert row["req"] and row["resp"] and row["ended"]
    # 10 interaction_spans: 4 anchor + 6 connector. Anchors are the request
    # spans (leg_type 'request'); each paired response span is a 'response'
    # connector; the echo pair evidence the interaction, not a leg (NULL).
    assert snap["roles"] == {"anchor": 4, "connector": 6}
    assert len(snap["spans"]) == 10
    for aid, iid in [(golden.A1, I1), (golden.B1, I2), (golden.B3, I3), (golden.B5, I4)]:
        assert snap["spans"][aid] == (iid, "anchor", "request")
    for rid, iid in [(golden.A2, I1), (golden.B2, I2), (golden.B4, I3), (golden.B6, I4)]:
        assert snap["spans"][rid] == (iid, "connector", "response")
    # The echo pair are leg-less connectors of the tool interaction.
    assert snap["spans"][golden.D1] == (I3, "connector", None)
    assert snap["spans"][golden.D2] == (I3, "connector", None)
    # 7 payloads: I1-resp and I4-resp share a content hash (agent echoes the llm).
    assert snap["payloads"] == 7
    # 8 entity_spans: each of the 4 interactions records its caller + callee
    # 'discovered_via' its (distinct) anchor span (4 × 2, no dedup).
    assert snap["entity_spans"] == 8


def test_golden_exact_tables(configured_db: str):
    golden.insert_spans(configured_db)
    drain(0)
    _assert_golden(_snapshot(configured_db))


@pytest.mark.parametrize("order", [
    pytest.param([golden.D1, golden.D2, golden.B3, golden.B4, golden.A1, golden.A2,
                  golden.B1, golden.B2, golden.B5, golden.B6], id="echo-before-parent"),
    pytest.param([golden.A2, golden.A1, golden.B2, golden.B1, golden.B4, golden.B3,
                  golden.D2, golden.D1, golden.B6, golden.B5], id="response-before-request"),
    pytest.param([golden.B6, golden.D2, golden.A2, golden.B3, golden.B1, golden.D1,
                  golden.B5, golden.A1, golden.B4, golden.B2], id="fully-shuffled"),
])
def test_arrival_permutations_converge(configured_db: str, order: list[str]):
    golden.insert_spans(configured_db, order=order)
    drain(0)
    _assert_golden(_snapshot(configured_db))


def test_prefix_arrival_exposes_in_flight_then_completes(configured_db: str):
    dsn = configured_db
    # Only the entry request has arrived: I1 exists with ONLY a request leg —
    # the absent response leg IS the in-flight signal (never fabricated).
    nxt = golden.insert_subset(dsn, [golden.A1], 1)
    cur = drain(0)
    snap = _snapshot(dsn)
    assert set(snap["ix"]) == {I1}
    assert set(snap["ix"][I1]["legs"]) == {"request"}
    assert snap["ix"][I1]["error"] is None       # in-flight, not failed

    # Entry response arrives → the response leg appears, I1 completes.
    nxt = golden.insert_subset(dsn, [golden.A2], nxt)
    cur = drain(cur)
    snap = _snapshot(dsn)
    assert set(snap["ix"][I1]["legs"]) == {"request", "response"}
    assert snap["ix"][I1]["error"] is False
    assert snap["ix"][I1]["resp"] is True and snap["ix"][I1]["ended"] is True

    # llm#1 request arrives (in-flight), then its response → I2 completes.
    nxt = golden.insert_subset(dsn, [golden.B1], nxt)
    cur = drain(cur)
    assert set(_snapshot(dsn)["ix"][I2]["legs"]) == {"request"}   # I2 in-flight
    nxt = golden.insert_subset(dsn, [golden.B2], nxt)
    cur = drain(cur)
    assert _snapshot(dsn)["ix"][I2]["resp"] is True     # I2 complete


def test_anchor_demotion_deletes_stale_interaction_and_legs(configured_db: str):
    """The reconcile's shrink case: the callee-side echo (D1) arrives before its
    caller-side outbound (B3). Alone it IS a trace entry — a real interaction
    with two legs. When B3 arrives, D1 demotes to echo: its interaction, BOTH
    its legs, and its anchor row must all be deleted, and its identity folds
    into B3's interaction as the callee echo. This is the delete semantics
    ``state.flush`` (emit-once anchors) cannot express — pinned here."""
    dsn = configured_db
    stale = _interaction_id(golden.TRACE, golden.D1)

    nxt = golden.insert_subset(dsn, [golden.D1, golden.D2], 1)
    cur = drain(0)
    snap = _snapshot(dsn)
    assert set(snap["ix"]) == {stale}
    assert set(snap["ix"][stale]["legs"]) == {"request", "response"}
    assert snap["spans"][golden.D1] == (stale, "anchor", "request")

    nxt = golden.insert_subset(dsn, [golden.B3, golden.B4], nxt)
    drain(cur)
    snap = _snapshot(dsn)
    assert stale not in snap["ix"]
    with psycopg.connect(dsn) as conn:
        orphan_legs = conn.execute(
            "SELECT count(*) FROM interaction_legs WHERE interaction_id = %s",
            (stale,)).fetchone()[0]
    assert orphan_legs == 0
    # The outbound interaction owns the exchange now, enriched by the echo.
    assert set(snap["ix"]) == {I3}
    assert snap["ix"][I3]["callee"] == "tool:weather-tool"
    assert snap["spans"][golden.D1] == (I3, "connector", None)
    assert snap["spans"][golden.D2] == (I3, "connector", None)


# Interleaved fake shim spans (the propagate-only HTTP shim's httpx/starlette
# spans): stored, non-sidecar (no lineage.* attrs), sitting in the parent chain
# as non-anchors. The derivation must be byte-identical with them present.
_BRIDGE = [
    ("cccc000000000001", golden.A1, "INTERNAL", {"http.target": "/mcp"}),   # under entry
    ("cccc000000000002", golden.B3, "CLIENT", {"http.method": "POST"}),     # between B3 and echo
]
# Re-parent the echo under the bridge span so the walk must skip the bridge.
_BRIDGE_REPARENT = {golden.D1: "cccc000000000002"}


def test_bridge_variant_identical(configured_db: str):
    dsn = configured_db
    # Build the golden rows with the echo re-parented under a bridge span.
    rows = []
    for sid, parent, kind, attrs in golden.GOLDEN:
        rows.append((sid, _BRIDGE_REPARENT.get(sid, parent), kind, attrs))
    all_rows = rows + _BRIDGE
    order = [r[0] for r in all_rows]
    seq_of = {sid: i + 1 for i, sid in enumerate(order)}
    with psycopg.connect(dsn) as conn:
        golden._insert_rows(conn, all_rows, seq_of)
        conn.commit()
    drain(0)

    snap = _snapshot(dsn)
    # Same four interactions and entities; echo callee still resolved through the
    # bridge; the two bridge spans are connectors, so 12 interaction_spans total.
    assert snap["entities"] == golden.ENTITIES
    assert set(snap["ix"]) == {I1, I2, I3, I4}
    assert snap["ix"][I3]["callee"] == "tool:weather-tool"
    assert snap["ix"][I3]["parent"] == I1
    assert snap["payloads"] == 7
    assert snap["roles"]["anchor"] == 4
    # 6 golden connectors + 2 bridge connectors; bridges evidence no leg.
    assert snap["roles"]["connector"] == 8
    assert snap["spans"]["cccc000000000002"] == (I3, "connector", None)


# A second, unrelated trace: one inbound a2a exchange (bob → agent:svc-b).
# Derives to 1 interaction / 2 legs / 2 interaction_spans (anchor + response
# connector) / 2 entity_spans (caller + callee via the anchor).
_T2 = "99999999999999999999999999999999"
_T2_GHOST = "00aa00bb00cc00dd"
_E1, _E2 = "e1e1e1e1e1e1e1e1", "e2e2e2e2e2e2e2e2"
_SECOND: list[tuple[str, str | None, str, dict]] = [
    (_E1, _T2_GHOST, "SERVER", {
        "lineage.role": "request", "lineage.direction": "inbound",
        "lineage.protocol": "a2a", "lineage.exchange.id": _E1,
        "lineage.self.id": "svc-b", "lineage.peer.addr": "10.244.9.9:5000",
        "lineage.principal.sub": "bob", "a2a.method": "message/send",
        "input.value": "hello from another trace",
    }),
    (_E2, _E1, "SERVER", {
        "lineage.role": "response", "lineage.direction": "inbound",
        "lineage.protocol": "a2a", "lineage.exchange.id": _E1,
        "lineage.self.id": "svc-b", "lineage.outcome": "ok",
        "output.value": "hi bob",
    }),
]


def _insert_second_trace(dsn: str, start_seq: int) -> None:
    with psycopg.connect(dsn) as conn:
        for i, (sid, parent, kind, attrs) in enumerate(_SECOND):
            seq = start_seq + i
            conn.execute(
                "INSERT INTO spans (trace_id, span_id, parent_id, kind, name, service_name, "
                "started_at, ended_at, error, attributes, seq, arrival_seq, observed_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,false,%s::jsonb,%s,%s,%s)",
                (_T2, sid, parent, kind, attrs["lineage.self.id"], "authbridge",
                 golden._T0, golden._T0, json.dumps(attrs), seq, seq, golden._T0),
            )
        conn.execute("SELECT setval('spans_seq', (SELECT max(seq) FROM spans))")
        conn.commit()


def _trace_rows(dsn: str, trace_id: str) -> dict:
    """All derived rows for one trace, sorted — the cross-trace delete guard
    compares this before/after re-deriving a *different* trace, and the
    re-derive-idempotence assert pins the legs byte-identical (incl. seq —
    replay determinism, never the column DEFAULT)."""
    with psycopg.connect(dsn) as conn:
        ix = sorted(conn.execute(
            "SELECT id::text, caller_entity_id::text, callee_entity_id::text, "
            "parent_interaction_id::text FROM interactions WHERE trace_id = %s",
            (trace_id,)).fetchall())
        legs = sorted(conn.execute(
            "SELECT l.interaction_id::text, l.leg_type::text, l.payload_hash, "
            "l.error, l.seq, l.original_seq "
            "FROM interaction_legs l JOIN interactions i ON i.id = l.interaction_id "
            "WHERE i.trace_id = %s", (trace_id,)).fetchall())
        isp = sorted(conn.execute(
            "SELECT span_id, interaction_id::text, role, leg_type::text "
            "FROM interaction_spans WHERE trace_id = %s", (trace_id,)).fetchall())
        esp = sorted(conn.execute(
            "SELECT span_id, entity_id::text, role FROM entity_spans WHERE trace_id = %s",
            (trace_id,)).fetchall())
    return {"interactions": ix, "legs": legs, "interaction_spans": isp,
            "entity_spans": esp}


def test_cross_trace_delete_scoping(configured_db: str):
    """Re-deriving one trace must never touch another trace's rows: every DELETE
    in ``_write`` is trace-scoped (legs via the subselect on the trace's
    interactions). Derive two traces, snapshot the second, re-derive the first
    from scratch, and assert the second is untouched."""
    dsn = configured_db
    golden.insert_spans(dsn)            # trace T1 (golden), seq 1..10
    _insert_second_trace(dsn, 11)       # trace T2, seq 11..12
    drain(0)

    # Both traces derived. Snapshot each, trace-scoped (the global _snapshot()
    # would conflate the two).
    t1_before = _trace_rows(dsn, golden.TRACE)
    t2_before = _trace_rows(dsn, _T2)
    assert len(t1_before["interactions"]) == 4
    assert len(t1_before["legs"]) == 8
    assert len(t1_before["interaction_spans"]) == 10
    assert len(t1_before["entity_spans"]) == 8
    # Sanity: T2 really did derive (1 interaction, 2 legs, 2 interaction_spans,
    # 2 entity_spans).
    assert len(t2_before["interactions"]) == 1
    assert len(t2_before["legs"]) == 2
    assert len(t2_before["interaction_spans"]) == 2
    assert len(t2_before["entity_spans"]) == 2

    # Re-derive ONLY T1 (idempotent full reconcile). Its trace-scoped DELETEs
    # must leave T2 byte-identical, and T1 must reconverge to itself.
    with db.transaction() as tx:
        derive_trace(tx, golden.TRACE)

    assert _trace_rows(dsn, _T2) == t2_before      # the other trace untouched
    assert _trace_rows(dsn, golden.TRACE) == t1_before  # re-derive is idempotent


# A third trace: MCP protocol plumbing through the full write path. The
# classifier's (direction, protocol[, mcp.method]) term re-labels CONTENT KINDS
# only — every exchange still derives a complete first-class interaction
# (initialize → mcp_lifecycle_*, tools/list → tool_discovery_*, and a bodyless
# http exchange on /mcp → mcp_lifecycle_* with the plain-http entity row).
_T3 = "88888888888888888888888888888888"
_F1, _F2 = "f1f1f1f1f1f1f1f1", "f2f2f2f2f2f2f2f2"  # initialize req / resp
_F3, _F4 = "f3f3f3f3f3f3f3f3", "f4f4f4f4f4f4f4f4"  # tools/list req (bodyless) / resp
_F5, _F6 = "f5f5f5f5f5f5f5f5", "f6f6f6f6f6f6f6f6"  # bodyless http /mcp pair
_TOOL_HOST = "movie-tool.team1.svc:8000"
_PLUMBING: list[tuple[str, str | None, str, dict]] = [
    (_F1, None, "CLIENT", {
        "lineage.role": "request", "lineage.direction": "outbound",
        "lineage.protocol": "mcp", "lineage.exchange.id": _F1,
        "lineage.self.id": "svc-c", "lineage.peer.host": _TOOL_HOST,
        "mcp.method": "initialize",
        "input.value": {"clientInfo": {"name": "mcp"}, "protocolVersion": "2025-11-25"},
    }),
    (_F2, _F1, "CLIENT", {
        "lineage.role": "response", "lineage.direction": "outbound",
        "lineage.protocol": "mcp", "lineage.exchange.id": _F1,
        "lineage.self.id": "svc-c", "lineage.outcome": "ok",
        "output.value": {"serverInfo": {"name": "Movies"}},
    }),
    (_F3, None, "CLIENT", {
        "lineage.role": "request", "lineage.direction": "outbound",
        "lineage.protocol": "mcp", "lineage.exchange.id": _F3,
        "lineage.self.id": "svc-c", "lineage.peer.host": _TOOL_HOST,
        "mcp.method": "tools/list",
    }),
    (_F4, _F3, "CLIENT", {
        "lineage.role": "response", "lineage.direction": "outbound",
        "lineage.protocol": "mcp", "lineage.exchange.id": _F3,
        "lineage.self.id": "svc-c", "lineage.outcome": "ok",
        "output.value": {"tools": [{"name": "find_movie"}]},
    }),
    (_F5, None, "CLIENT", {
        "lineage.role": "request", "lineage.direction": "outbound",
        "lineage.protocol": "http", "lineage.exchange.id": _F5,
        "lineage.self.id": "svc-c", "lineage.peer.host": _TOOL_HOST,
        "url.path": "/mcp",
    }),
    (_F6, _F5, "CLIENT", {
        "lineage.role": "response", "lineage.direction": "outbound",
        "lineage.protocol": "http", "lineage.exchange.id": _F5,
        "lineage.self.id": "svc-c", "lineage.outcome": "ok",
    }),
]


def test_mcp_plumbing_kinds_through_write_path(configured_db: str):
    dsn = configured_db
    with psycopg.connect(dsn) as conn:
        for i, (sid, parent, kind, attrs) in enumerate(_PLUMBING):
            conn.execute(
                "INSERT INTO spans (trace_id, span_id, parent_id, kind, name, service_name, "
                "started_at, ended_at, error, attributes, seq, arrival_seq, observed_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,false,%s::jsonb,%s,%s,%s)",
                (_T3, sid, parent, kind, attrs["lineage.self.id"], "authbridge",
                 golden._T0, golden._T0, json.dumps(attrs), i + 1, i + 1, golden._T0),
            )
        conn.execute("SELECT setval('spans_seq', (SELECT max(seq) FROM spans))")
        conn.commit()
    drain(0)

    j1 = _interaction_id(_T3, _F1)
    j2 = _interaction_id(_T3, _F3)
    j3 = _interaction_id(_T3, _F5)
    snap = _snapshot(dsn)
    # Three complete interactions — plumbing is tagged, never dropped.
    assert set(snap["ix"]) == {j1, j2, j3}
    # Entity kinds untouched by the override: mcp rows keep tool:, http keeps service:.
    assert snap["ix"][j1]["callee"] == f"tool:{_TOOL_HOST}"
    assert snap["ix"][j2]["callee"] == f"tool:{_TOOL_HOST}"
    assert snap["ix"][j3]["callee"] == f"service:{_TOOL_HOST}"
    # Payload presence follows the bodies: tools/list request and the whole
    # http pair are bodyless — complete legs with NULL hashes (contract).
    assert (snap["ix"][j1]["req"], snap["ix"][j1]["resp"]) == (True, True)
    assert (snap["ix"][j2]["req"], snap["ix"][j2]["resp"]) == (False, True)
    assert (snap["ix"][j3]["req"], snap["ix"][j3]["resp"]) == (False, False)
    assert set(snap["ix"][j3]["legs"]) == {"request", "response"}  # complete, bodyless
    # The bodied halves carry the new content kinds into interaction_payloads.
    with psycopg.connect(dsn) as conn:
        kinds = {r[0] for r in conn.execute(
            "SELECT p.content_kind FROM interaction_legs l "
            "JOIN interactions i ON i.id = l.interaction_id "
            "JOIN interaction_payloads p ON p.content_hash = l.payload_hash "
            "WHERE i.trace_id = %s", (_T3,)).fetchall()}
    assert kinds == {"mcp_lifecycle_request", "mcp_lifecycle_result", "tool_discovery_result"}
