"""Shape assertions over the live database: the lineage forest first, the
risk layer second (including that every payload-bearing leg was classified
before its record was computed), alerts when the deployed branch has a
writer for them.

Everything here is a read of what the real chain wrote. The lineage
queries are the ones the 2026-09-02 travel notebook and the kit's fleet
harness settled on; the risk invariants are ported from the in-process
suite's ``assert_forest_shape`` (tests/risk/system/test_risk_pipeline.py)
so the two tiers assert the same shape from opposite ends.

**Shape, not counts.** A harness that only checks counts scored a total
attribution failure clean (measured 2026-08-14): an app whose outbound
calls each land in their own trace has perfect per-trace numbers. So every
scenario ends here, and counts are recorded for the report, never
asserted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

FALLBACK_RULE_ID = "0000"

AGENT_SELF_IDS = {"travel-advisor", "research-agent", "booking-agent", "payment-agent"}
TOOL_HOSTS = {"search-destinations", "create-booking", "get-payment-info",
              "send-notification", "get-weather", "get-flights", "charge-card"}
ENTRY_SELF_ID = "demo-client"


# --- lineage forest ------------------------------------------------------------


@dataclass
class Forest:
    trace_id: str
    interactions: int
    roots: int
    orphans: int
    unpaired_requests: int
    inbound_parent_sources: dict[str, int]
    dup_anchors: int
    escaped_traces: list[str]
    depth_histogram: dict[int, int]
    window: tuple[str, str]
    entry_self_id: str | None = None
    unstamped_requests: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def trace_window(dg, trace_id: str, *, lead_seconds: int = 5) -> tuple[datetime, datetime]:
    (first,) = dg.one("SELECT min(started_at) FROM spans WHERE trace_id = %s", (trace_id,))
    assert first is not None, f"trace {trace_id} has no spans at all"
    return first - timedelta(seconds=lead_seconds), datetime.now(timezone.utc)


def forest(dg, trace_id: str, *, known: "set[str] | frozenset[str]" = frozenset()) -> Forest:
    """*known*: every trace id the test minted itself; an escape is a trace
    demo-client started that the test did NOT ask for (two turns run at
    once are two known traces, not an escape)."""
    start, end = trace_window(dg, trace_id)
    (n,) = dg.one("SELECT count(*) FROM interactions WHERE trace_id = %s", (trace_id,))
    (roots,) = dg.one(
        "SELECT count(*) FROM interactions WHERE trace_id = %s "
        "AND parent_interaction_id IS NULL", (trace_id,))
    (orphans,) = dg.one("""
        SELECT count(*) FROM interactions i
        WHERE i.trace_id = %s AND i.parent_interaction_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM interactions p
                          WHERE p.id = i.parent_interaction_id AND p.trace_id = i.trace_id)""",
        (trace_id,))
    (unpaired,) = dg.one("""
        SELECT count(*) FROM spans r
        WHERE r.trace_id = %s AND r.attributes->>'lineage.role' = 'request'
          AND NOT EXISTS (SELECT 1 FROM spans s
                          WHERE s.trace_id = r.trace_id
                            AND s.attributes->>'lineage.role' = 'response'
                            AND s.attributes->>'lineage.exchange.id'
                                = r.attributes->>'lineage.exchange.id')""",
        (trace_id,))
    sources = dict(dg.q("""
        SELECT attributes->>'lineage.parent.source', count(*) FROM spans
        WHERE trace_id = %s AND attributes->>'lineage.role' = 'request'
          AND attributes->>'lineage.direction' = 'inbound'
        GROUP BY 1""", (trace_id,)))
    (dups,) = dg.one("""
        SELECT count(*) FROM (
          SELECT span_id FROM interaction_spans
          WHERE trace_id = %s AND role = 'anchor'
          GROUP BY span_id HAVING count(DISTINCT interaction_id) > 1) d""", (trace_id,))
    escaped = [r[0] for r in dg.q("""
        SELECT DISTINCT trace_id FROM spans
        WHERE trace_id <> %s AND NOT (trace_id = ANY(%s))
          AND started_at BETWEEN %s AND %s
          AND attributes->>'lineage.self.id' = %s
          AND attributes->>'lineage.direction' = 'outbound'
          AND attributes->>'lineage.role' = 'request'
          AND attributes->>'lineage.parent.source' IN ('wire', 'none')""",
        (trace_id, list(known), start, end, ENTRY_SELF_ID))]
    depth = dict(dg.q("""
        WITH RECURSIVE t AS (
          SELECT id, 0 AS d FROM interactions
          WHERE trace_id = %s AND parent_interaction_id IS NULL
          UNION ALL
          SELECT i.id, t.d + 1 FROM interactions i JOIN t ON i.parent_interaction_id = t.id
          WHERE i.trace_id = %s)
        SELECT d, count(*) FROM t GROUP BY d ORDER BY d""", (trace_id, trace_id)))
    # Every request span whose parent came from the wire or from nothing: in
    # a healthy trace that is exactly one span, the entry. Which side it is
    # depends on the caller: a sidecar'd caller's OUTBOUND span (demo-client
    # here), a bare caller's first INBOUND span. Everything after it is
    # stamp-parented (contract §3.2).
    unstamped = dg.q("""
        SELECT attributes->>'lineage.direction', attributes->>'lineage.self.id' FROM spans
        WHERE trace_id = %s AND attributes->>'lineage.role' = 'request'
          AND (attributes->>'lineage.parent.source' IN ('wire', 'none')
               OR attributes->>'lineage.parent.source' IS NULL)
        ORDER BY seq""", (trace_id,))
    entry = [(d, sid) for d, sid in unstamped]
    return Forest(
        trace_id=trace_id, interactions=int(n), roots=int(roots), orphans=int(orphans),
        unpaired_requests=int(unpaired),
        inbound_parent_sources={k or "absent": int(v) for k, v in sources.items()},
        dup_anchors=int(dups), escaped_traces=escaped,
        depth_histogram={int(k): int(v) for k, v in depth.items()},
        window=(start.isoformat(), end.isoformat()),
        entry_self_id=entry[0][1] if entry else None,
        unstamped_requests=entry,
    )


def assert_lineage_forest(f: Forest, *, expect_entry: str = ENTRY_SELF_ID,
                          expect_entries: int = 1) -> None:
    """The forest law: one root per entry the test injected into the trace
    (the turn, and each probe — a probe is its own wire-parented exchange
    from demo-client, so it derives as its own root), every one of them the
    entry caller's; no orphan, no unpaired span, no anchor shared; nothing
    of demo-client's escaped into another trace; and the app's own hops all
    hang under the turn's root (the forest is not just roots)."""
    assert f.interactions >= 1, f"trace {f.trace_id}: no interactions derived"
    assert f.roots == expect_entries, (
        f"trace {f.trace_id}: expected {expect_entries} root(s) (one per injected entry), got {f.roots}")
    assert f.orphans == 0, f"trace {f.trace_id}: {f.orphans} orphan interaction(s)"
    assert f.unpaired_requests == 0, (
        f"trace {f.trace_id}: {f.unpaired_requests} request span(s) without a response twin")
    assert f.dup_anchors == 0, f"trace {f.trace_id}: an anchor span maps to two interactions"
    assert len(f.unstamped_requests) == expect_entries, (
        f"trace {f.trace_id}: expected {expect_entries} unstamped request span(s) (the entries), "
        f"got {f.unstamped_requests}; inbound parent sources {f.inbound_parent_sources}")
    assert all(sid == expect_entry for _d, sid in f.unstamped_requests), (
        f"trace {f.trace_id}: an entry is not {expect_entry!r}: {f.unstamped_requests}")
    assert f.escaped_traces == [], (
        f"trace {f.trace_id}: {ENTRY_SELF_ID} started other traces in the window: "
        f"{f.escaped_traces}")
    assert f.interactions > expect_entries or f.interactions == expect_entries == f.roots, (
        f"trace {f.trace_id}: forest collapsed (every row a root)")


# --- strays --------------------------------------------------------------------


def strays(dg, trace_id: str, *, known: "set[str] | frozenset[str]" = frozenset()) -> list[dict]:
    """Other traces whose first outbound request in the window was not
    parented by anything: the openai-agents / langgraph MCP background
    sessions the demo README documents. Tolerated only when characterised.
    *known*: the session's own traces (probes on the baseline trace, a
    concurrent turn) are not strays."""
    start, end = trace_window(dg, trace_id)
    rows = dg.q("""
        SELECT trace_id, attributes->>'lineage.self.id', attributes->>'lineage.protocol',
               attributes->>'lineage.peer.host', attributes->>'lineage.direction'
        FROM spans
        WHERE trace_id <> %s AND NOT (trace_id = ANY(%s)) AND started_at BETWEEN %s AND %s
          AND attributes->>'lineage.direction' = 'outbound'
          AND attributes->>'lineage.role' = 'request'
          AND attributes->>'lineage.parent.source' IN ('wire', 'none')""",
        (trace_id, list(known), start, end))
    return [{"trace_id": r[0], "self_id": r[1], "protocol": r[2], "peer_host": r[3]}
            for r in rows]


def assert_strays_tolerated(dg, rows: list[dict]) -> None:
    bad = []
    for r in rows:
        peer = (r["peer_host"] or "").split(":")[0].split(".")[0]
        peer = peer[:-4] if peer.endswith("-mcp") else peer  # Services are <tool>-mcp
        if r["protocol"] != "mcp":
            bad.append(("not mcp", r))
        elif r["self_id"] not in AGENT_SELF_IDS:
            bad.append(("not from an agent", r))
        elif peer not in TOOL_HOSTS:
            bad.append(("not to a tool", r))
    assert not bad, f"stray traces that are not background MCP sessions: {bad}"
    ids = sorted({r["trace_id"] for r in rows})
    if ids:
        (crit,) = dg.one(
            "SELECT count(*) FROM interaction_risk_records "
            "WHERE trace_id = ANY(%s) AND risk_level = 'critical'", (ids,))
        assert crit == 0, f"a probe payload leaked into a stray trace: {ids}"


# --- risk layer ----------------------------------------------------------------


def current_records(dg, trace_id: str, *, live_only: bool = True) -> dict[str, dict]:
    """Latest record per interaction of *trace_id*; by default only for
    interactions that still exist (the rollup's own "current" definition:
    a re-keyed interaction's records are ghosts)."""
    join = "JOIN interactions i ON i.id = r.interaction_id" if live_only else ""
    rows = dg.q(f"""
        SELECT DISTINCT ON (r.interaction_id) r.interaction_id, r.interaction_risk_id::text,
               r.version, r.risk_level, r.enforcement_type, r.triggered_rule_ids,
               r.legs_evidenced, r.classification_summary, r.opa_policy_versions_used,
               r.overall_confidence, r.trace_id
        FROM interaction_risk_records r {join} WHERE r.trace_id = %s
        ORDER BY r.interaction_id, r.version DESC""", (trace_id,))
    return {
        r[0]: {"id": r[1], "version": r[2], "risk_level": r[3], "enforcement_type": r[4],
               "rules": sorted(r[5]), "legs": list(r[6]), "summary": r[7],
               "policy_versions": list(r[8]),
               "confidence": float(r[9]) if r[9] is not None else None, "trace_id": r[10]}
        for r in rows
    }


def record_history(dg, interaction_id: str) -> list[dict]:
    rows = dg.q("""
        SELECT version, risk_level, enforcement_type, triggered_rule_ids, legs_evidenced,
               classification_summary
        FROM interaction_risk_records WHERE interaction_id = %s ORDER BY version""",
        (interaction_id,))
    return [{"version": r[0], "risk_level": r[1], "enforcement_type": r[2],
             "rules": sorted(r[3]), "legs": list(r[4]), "summary": r[5]} for r in rows]


def latest_decision(dg, interaction_id: str) -> dict | None:
    rows = dg.q("""
        SELECT version, risk_level, enforcement_type, allowed_actions, explanation,
               triggered_rules, confidence, policy_version, evidence_fingerprint
        FROM interaction_policy_decisions WHERE interaction_id = %s
        ORDER BY version DESC LIMIT 1""", (interaction_id,))
    if not rows:
        return None
    r = rows[0]
    return {"version": r[0], "risk_level": r[1], "enforcement_type": r[2],
            "allowed_actions": list(r[3]), "explanation": r[4], "rules": sorted(r[5]),
            "confidence": float(r[6]) if r[6] is not None else None,
            "policy_version": r[7], "fingerprint": r[8]}


def decision_fingerprints(dg, interaction_id: str) -> int:
    (n,) = dg.one("SELECT count(DISTINCT evidence_fingerprint) FROM interaction_policy_decisions "
                  "WHERE interaction_id = %s", (interaction_id,))
    return int(n)


def current_trace(dg, trace_id: str) -> dict | None:
    rows = dg.q("""
        SELECT trace_risk_id::text, version, trace_risk_level, trace_enforcement_type,
               interaction_count, all_entity_ids, triggered_rule_ids,
               contributing_interaction_risk_ids::text[], risk_compounding_mode,
               enforcement_aggregation_mode, overall_confidence
        FROM trace_risk_records WHERE trace_id = %s ORDER BY version DESC LIMIT 1""",
        (trace_id,))
    if not rows:
        return None
    r = rows[0]
    return {"id": r[0], "version": r[1], "level": r[2], "enforcement": r[3],
            "interaction_count": r[4], "entities": sorted(r[5]), "rules": sorted(r[6]),
            "contributing": sorted(r[7]), "risk_mode": r[8], "enforcement_mode": r[9],
            "confidence": float(r[10]) if r[10] is not None else None}


def trace_alerts(dg, trace_id: str) -> list[dict]:
    rows = dg.q("""
        SELECT alert_id::text, trace_risk_record_id::text, risk_level, title,
               triggered_rule_ids, status, duplicate_count, superseded_by::text
        FROM alerts WHERE trace_id = %s ORDER BY "timestamp", alert_id""", (trace_id,))
    return [{"id": r[0], "record": r[1], "level": r[2], "title": r[3], "rules": sorted(r[4]),
             "status": r[5], "duplicates": r[6], "superseded_by": r[7]} for r in rows]


def _real(rules: list[str]) -> set[str]:
    return set(rules) - {FALLBACK_RULE_ID}


def unclassified_legs(dg, trace_id: str) -> list[tuple]:
    """Legs of the trace's interactions that carry a payload and have no
    ``payload_classifications`` row. Settle only proves this indirectly
    (leg-ready delivers a leg only once its payload is classified, and
    settle waits for that cursor); a delivery-rule bug would pass settle,
    so the tier reads it directly."""
    return dg.q("""
        SELECT l.interaction_id, l.leg_type::text, l.payload_hash
        FROM interaction_legs l
        JOIN interactions i ON i.id = l.interaction_id
        LEFT JOIN payload_classifications pc ON pc.content_hash = l.payload_hash
        WHERE i.trace_id = %s AND l.payload_hash IS NOT NULL AND pc.content_hash IS NULL
        ORDER BY l.seq""", (trace_id,))


def pending_records(records: dict[str, dict]) -> dict[str, list[str]]:
    """Current records whose ``classification_summary`` carries the
    engine's pending marker (``{"classification_pending": true}``,
    utils.classification_summary) for any leg: the record was computed
    while a payload was still unclassified, i.e. leg-ready delivered before
    the classifier finished, or the re-delivery never came."""
    return {
        iid: sorted(leg for leg, v in (r["summary"] or {}).items()
                    if isinstance(v, dict) and v.get("classification_pending"))
        for iid, r in records.items()
        if any(isinstance(v, dict) and v.get("classification_pending")
               for v in (r["summary"] or {}).values())
    }


def assert_risk_shape(dg, trace_id: str, caps, *, alert_heads: int | None = None) -> dict:
    """The seven invariants of the in-process suite, read from the live
    tables. Returns what it read (for the run record)."""
    live = {r[0] for r in dg.q("SELECT id FROM interactions WHERE trace_id = %s", (trace_id,))}
    records = current_records(dg, trace_id)
    assert set(records) == live, (
        f"trace {trace_id}: exactly one current risk record per live interaction; "
        f"missing={sorted(live - set(records))} extra={sorted(set(records) - live)}")
    assert {r["trace_id"] for r in records.values()} == {trace_id}
    (distinct,) = dg.one(
        "SELECT count(DISTINCT trace_id) FROM interaction_risk_records "
        "WHERE interaction_id = ANY(%s)", (list(live),))
    assert distinct == 1, f"trace {trace_id}: its interactions' records scatter across traces"
    ghosts = {r["id"] for k, r in current_records(dg, trace_id, live_only=False).items()
              if k not in live}

    trace = current_trace(dg, trace_id)
    assert trace is not None, f"trace {trace_id}: drained but no trace risk record"
    assert trace["interaction_count"] == len(live), (
        f"trace {trace_id}: trace record counts {trace['interaction_count']} "
        f"interactions, {len(live)} are live")
    assert trace["risk_mode"] == "severity_max" and trace["enforcement_mode"] == "severity_max", (
        f"trace {trace_id}: aggregation modes {trace['risk_mode']}/{trace['enforcement_mode']}")
    assert trace["contributing"] == sorted(r["id"] for r in records.values()), (
        f"trace {trace_id}: contributing ids are not the current record set (AC-DAS-009)")
    assert not ghosts & set(trace["contributing"]), (
        f"trace {trace_id}: ghost records contribute to the rollup: {ghosts}")
    entities = {e for r in dg.q("SELECT caller_entity_id, callee_entity_id FROM interactions "
                                 "WHERE trace_id = %s", (trace_id,)) for e in r}
    assert trace["entities"] == sorted(entities), f"trace {trace_id}: entity set mismatch"
    union = set().union(*(_real(r["rules"]) for r in records.values())) if records else set()
    assert _real(trace["rules"]) == union, (
        f"trace {trace_id}: trace rules {trace['rules']} != union of record rules {sorted(union)}")

    unclassified = unclassified_legs(dg, trace_id)
    assert unclassified == [], (
        f"trace {trace_id}: payload-bearing legs without a classification: {unclassified}")
    pending = pending_records(records)
    assert pending == {}, (
        f"trace {trace_id}: current records computed on a pending leg: {pending}")

    alerts = trace_alerts(dg, trace_id) if caps.alerts else []
    if caps.alerts:
        heads = [a for a in alerts if a["superseded_by"] is None]
        if alert_heads is not None:
            assert len(heads) == alert_heads, (
                f"trace {trace_id}: expected {alert_heads} alert head(s), got {heads}")
        if heads:
            assert heads[-1]["level"] == trace["level"], (
                f"trace {trace_id}: open alert level {heads[-1]['level']} != trace level")
    else:
        (n,) = dg.one("SELECT count(*) FROM alerts WHERE trace_id = %s", (trace_id,))
        assert n == 0, f"trace {trace_id}: alerts rows exist but no alerts processor is deployed"
    return {"live": sorted(live), "records": records, "trace": trace, "ghosts": sorted(ghosts),
            "unclassified_legs": unclassified, "pending_records": pending, "alerts": alerts}


def interaction_of_probe(dg, trace_id: str, parent_id: str) -> str:
    """The interaction whose anchor is the request span parented on the
    probe's minted span id (probe legs are ``parent.source = wire``)."""
    rows = dg.q("""
        SELECT isp.interaction_id FROM spans s
        JOIN interaction_spans isp ON isp.trace_id = s.trace_id AND isp.span_id = s.span_id
        WHERE s.trace_id = %s AND s.parent_id = %s
          AND s.attributes->>'lineage.role' = 'request' AND isp.role = 'anchor'""",
        (trace_id, parent_id))
    assert len(rows) == 1, (
        f"trace {trace_id}: probe parent {parent_id} anchors {len(rows)} interactions, expected 1")
    return rows[0][0]


def legs_of(dg, interaction_id: str) -> dict[str, dict]:
    rows = dg.q("""
        SELECT leg_type::text, payload_hash, error, occurred_at
        FROM interaction_legs WHERE interaction_id = %s""", (interaction_id,))
    return {r[0]: {"payload_hash": r[1], "error": r[2], "occurred_at": r[3].isoformat()}
            for r in rows}


def response_span_of(dg, trace_id: str, interaction_id: str) -> dict | None:
    rows = dg.q("""
        SELECT s.attributes->>'lineage.outcome', s.attributes->>'http.status_code'
        FROM interaction_spans isp JOIN spans s
          ON s.trace_id = isp.trace_id AND s.span_id = isp.span_id
        WHERE isp.trace_id = %s AND isp.interaction_id = %s
          AND s.attributes->>'lineage.role' = 'response' LIMIT 1""",
        (trace_id, interaction_id))
    return {"outcome": rows[0][0], "status": rows[0][1]} if rows else None


def payload_mentions(dg, trace_id: str, needle: str) -> list[dict]:
    """Spans of *trace_id* whose captured input or output carries *needle*
    (the cross-trace contamination probe), each named by the hop that
    carried it so a leak points at its source."""
    rows = dg.q("""
        SELECT attributes->>'lineage.self.id', attributes->>'lineage.direction',
               attributes->>'lineage.protocol', attributes->>'lineage.role',
               CASE WHEN attributes->>'input.value' LIKE %s THEN 'input' ELSE 'output' END
        FROM spans WHERE trace_id = %s
          AND (attributes->>'input.value' LIKE %s OR attributes->>'output.value' LIKE %s)
        ORDER BY seq""", (f"%{needle}%", trace_id, f"%{needle}%", f"%{needle}%"))
    return [{"self_id": r[0], "direction": r[1], "protocol": r[2], "role": r[3], "side": r[4]}
            for r in rows]
