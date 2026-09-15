"""Live tier scenarios L1–L10 (issue #161): real traffic through the cortex
lineage sidecar on the pinned travel_advisor app, asserted from the DG
tables. Run in file order (``-m live -x``); later scenarios address the
trace the baseline minted.

Every scenario's docstring is its expectation card, written before the run.
Assertions are shape first (the lineage forest, then the seven risk
invariants), rows second (the predicted decisions under the deployed test
catalog, ``catalog_e2e.json``), versions third. Counts are recorded, never
asserted: the LLM's plan varies, the shape does not.

Issue #161's scenario numbers are noted per test. Scenario 7 (an
interaction with only a response leg) is N/A by contract: "the request
leg always exists; the response leg only when the response span does —
its absence IS the in-flight signal" (docs/sidecar-wire-contract.md §7,
ADR-0030), so no producer can emit it and no test can ask for it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.live import driver, settle, shape
from tests.live.driver import (
    EXTERNAL_HOST, PSP_NOBODY_URL, SINK_HOST, SINK_URL, card_pii, credential, guest_turn,
    benign, mint_span_id, mint_trace_id, pi_only, probe, probes_concurrent, turn, turns_concurrent,
)

_CATALOG = json.loads((Path(__file__).parent / "catalog_e2e.json").read_text(encoding="utf-8"))
RULES = {r["rule_id"]: r for r in _CATALOG["rules"]}


def explanation(*rule_ids: str) -> str:
    """The compiled policy's combined explanation: the risk-axis winner's
    text, then the enforcement-axis winner's, joined by '; ' (one text when
    the same rule wins both) — rego.py's combining block."""
    return "; ".join(RULES[r]["rule_decision"]["explanation"] for r in rule_ids)


# The advisor ends its scripted turn by asking whether there is anything else,
# so the A2A task's terminal state is `input-required`, not `completed`
# (observed on the first live run, 2026-09-09). Either state with a non-empty
# answer artifact is a finished turn for lineage purposes.
TURN_DONE_STATES = {"completed", "input-required"}


def assert_turn_answered(reply):
    assert reply.state in TURN_DONE_STATES, f"turn did not finish: {reply.state} {reply.raw[:300]}"
    assert reply.answer.strip(), f"turn finished ({reply.state}) with no answer: {reply.raw[:300]}"


def _settle(env, dg, caps, trace_id):
    return settle.wait_drained(dg, trace_id, caps, timeout=env.settle_timeout,
                               quiet_seconds=env.quiet_seconds)


def _decision_of(dg, interaction_id):
    d = shape.latest_decision(dg, interaction_id)
    assert d is not None, f"no policy decision stored for {interaction_id}"
    return d


def _request_summary(record):
    s = record["summary"] or {}
    assert "request" in s, f"record has no request leg summary: {s}"
    return s["request"]


# --- L1 ------------------------------------------------------------------------


def test_L1_baseline_turn(env, kube, dg, capabilities, catalog, run_record, state):
    """L1 — one scripted turn under a minted trace id (#161 scenarios 1, 9).

    Expect: the reply finishes with an answer (state completed or
    input-required: the advisor ends by asking for more); the derived forest has one root
    per injected entry (the turn, the benign probe), zero orphans, zero request spans without their
    response twin, every inbound stamp-parented, the entries the only unstamped spans;
    demo-client started no other trace in the window; every stray trace in
    the window is a background MCP session from an agent to a tool; every
    live interaction has a current risk record; a trace record exists with
    both aggregation modes severity_max; a benign probe added to the trace
    is classified with zero findings at level PUBLIC and decided none/allow
    (a real turn's legs all carry findings, so this case is driven, not
    hoped for); after a quiet window nothing moves. Counts (spans,
    interactions, depth histogram, the turn's own finding counts) recorded.
    """
    rec = run_record.scenario("L1")
    T, p = mint_trace_id(), mint_span_id()
    state["T"] = T
    state["traces"] = {T}
    reply = turn(kube, trace_id=T, parent_id=p)
    rec.write("reply.json", reply.__dict__)
    assert_turn_answered(reply)
    p_benign = mint_span_id()
    r_benign = probe(kube, trace_id=T, parent_id=p_benign, label="benign", payload=benign())
    rec.write("probe_benign.json", r_benign.__dict__)
    assert r_benign.outcome in ("ok", "http_error"), r_benign

    s = _settle(env, dg, capabilities, T)
    rec.write("settle.json", s.__dict__)
    state["entries"] = 2  # the turn + the benign probe, each its own root
    f = shape.forest(dg, T, known=state["traces"])
    rec.write("forest.json", f.as_dict())
    shape.assert_lineage_forest(f, expect_entries=state["entries"])
    st = shape.strays(dg, T, known=state["traces"])
    rec.write("strays.json", st)
    shape.assert_strays_tolerated(dg, st)

    risk = shape.assert_risk_shape(dg, T, capabilities)
    rec.write("risk.json", risk)
    benign_iid = shape.interaction_of_probe(dg, T, p_benign)
    benign_rec = risk["records"][benign_iid]
    req = _request_summary(benign_rec)
    rec.write("benign_record.json", benign_rec)
    assert req.get("finding_count") == 0 and req.get("sensitivity_level") == "PUBLIC", req
    assert (benign_rec["risk_level"], benign_rec["enforcement_type"]) == ("none", "allow"), benign_rec
    turn_findings = sorted(
        v.get("finding_count") for r in risk["records"].values()
        for v in (r["summary"] or {}).values() if isinstance(v, dict) and "finding_count" in v)
    rec.write("turn_finding_counts.json", turn_findings)

    quiet = settle.quiet_recheck(dg, T, quiet_seconds=env.quiet_seconds)
    rec.write("quiet.json", quiet)
    state["L1_versions"] = quiet["records"]
    state["L1_multi_version"] = sorted(
        iid for iid, r in risk["records"].items() if r["version"] >= 2)
    rec.write("multi_version_interactions.json", state["L1_multi_version"])


# --- L2 ------------------------------------------------------------------------


def test_L2_ab_probe_in_trace(env, kube, dg, capabilities, catalog, run_record, state):
    """L2 — the same card payload to the same PSP, once as an external name
    and once as the internal short name, inside L1's trace (#161 scenarios 10,
    12, 13).

    Expect: both request legs classified identically ({PI, PII, PCI},
    RESTRICTED, identity bundle); external → critical/block, rules
    [DG-001, DG-004, E2E-INVERT], allowed_actions [redact], confidence
    0.95, explanation = DG-001's alone (it wins both axes; the advisory
    `warn` rule does not weaken `block`); internal → none/allow with no real
    rule; response summaries {"payload": null} (the PSP's plain-JSON reply
    is not parser-captured); trace rollup critical/block with both new
    record ids contributing; forest and risk shape hold.
    """
    T = state.get("T") or pytest.skip("L1 did not run")
    rec = run_record.scenario("L2")
    p_ext, p_int = mint_span_id(), mint_span_id()
    r_ext = probe(kube, trace_id=T, parent_id=p_ext, label="card_ext", payload=card_pii(),
                  host=EXTERNAL_HOST)
    r_int = probe(kube, trace_id=T, parent_id=p_int, label="card_int", payload=card_pii())
    rec.write("probes.json", [r_ext.__dict__, r_int.__dict__])
    assert r_ext.outcome == "ok" and r_int.outcome == "ok", (r_ext, r_int)
    state["entries"] += 2

    s = _settle(env, dg, capabilities, T)
    rec.write("settle.json", s.__dict__)
    ext = shape.interaction_of_probe(dg, T, p_ext)
    inn = shape.interaction_of_probe(dg, T, p_int)
    records = shape.current_records(dg, T)
    rec.write("records.json", {"ext": records[ext], "int": records[inn]})

    for iid in (ext, inn):
        req = _request_summary(records[iid])
        assert sorted(req["regulatory_tags"]) == ["PCI", "PI", "PII"], req
        assert req["sensitivity_level"] == "RESTRICTED", req
        assert req["contains_identity_bundle"] is True, req
        assert records[iid]["legs"] == ["request", "response"], records[iid]["legs"]
        assert (records[iid]["summary"] or {}).get("response") == {"payload": None}, (
            f"{iid}: the PSP reply was captured as a payload: {records[iid]['summary']}")
    assert (records[ext]["risk_level"], records[ext]["enforcement_type"]) == ("critical", "block")
    assert records[ext]["rules"] == ["DG-001", "DG-004", "E2E-INVERT"], records[ext]["rules"]
    d_ext = _decision_of(dg, ext)
    rec.write("decision_ext.json", d_ext)
    assert d_ext["allowed_actions"] == ["redact"] and d_ext["confidence"] == 0.95, d_ext
    assert d_ext["explanation"] == explanation("DG-001"), d_ext["explanation"]
    assert (records[inn]["risk_level"], records[inn]["enforcement_type"]) == ("none", "allow")
    assert shape._real(records[inn]["rules"]) == set(), records[inn]["rules"]

    trace = shape.current_trace(dg, T)
    rec.write("trace.json", trace)
    assert (trace["level"], trace["enforcement"]) == ("critical", "block")
    assert {records[ext]["id"], records[inn]["id"]} <= set(trace["contributing"])
    f = shape.forest(dg, T, known=state["traces"])
    rec.write("forest.json", f.as_dict())
    shape.assert_lineage_forest(f, expect_entries=state["entries"])
    rec.write("risk.json", shape.assert_risk_shape(dg, T, capabilities))
    state["L2"] = {"ext": ext, "int": inn, "p_int": p_int,
                   "int_history": shape.record_history(dg, inn),
                   "int_decisions": shape.decision_fingerprints(dg, inn)}


# --- L3 ------------------------------------------------------------------------


L3_EXPECT = {
    "card_ext": dict(risk="critical", enforcement="block", rules=["DG-001", "DG-004", "E2E-INVERT"],
                     allowed=["redact"], confidence=0.95, explanation=explanation("DG-001")),
    "card_int": dict(risk="none", enforcement="allow", rules=[], allowed=[], confidence=1.0,
                     explanation="No rules fired, falling back to default rule"),
    "pi_int": dict(risk="medium", enforcement="escalate", rules=["E2E-INT-DATA", "E2E-PI-INT"],
                   allowed=["log"], confidence=0.3,
                   explanation=explanation("E2E-PI-INT", "E2E-INT-DATA")),
    "cred_int": dict(risk="high", enforcement="require_approval", rules=["E2E-CRED-INT"],
                     allowed=["audit"], confidence=0.7, explanation=explanation("E2E-CRED-INT")),
    "cred_ext": dict(risk="critical", enforcement="block", rules=["DG-004", "E2E-CRED-EXT"],
                     allowed=[], confidence=0.95, explanation=explanation("DG-004")),
}


def test_L3_mixed_levels_in_one_trace(env, kube, dg, capabilities, catalog, run_record, state):
    """L3 — five probes fired at once into a fresh trace, under the test
    catalog (#161 scenarios 11, 13, 14).

    Expect, per probe (rules from interaction_risk_records, the rest from
    the stored decision): card→external critical/block [DG-001, DG-004,
    E2E-INVERT]; card→internal none/allow; PI-only→internal
    medium/escalate [E2E-INT-DATA, E2E-PI-INT] with the explanation of BOTH
    rules joined — the two combining axes won by different rules;
    credential→internal high/require_approval [E2E-CRED-INT];
    credential→external critical/block [DG-004, E2E-CRED-EXT] (no PII, so
    no DG-001). Three distinct rule-driven (risk, enforcement) pairs in one
    trace; the trace rolls up critical/block with the union of rules; the
    five probes are five interactions; shape holds.
    """
    rec = run_record.scenario("L3")
    T2 = mint_trace_id()
    state["T2"] = T2
    state["traces"].add(T2)
    specs = [
        dict(trace_id=T2, parent_id=mint_span_id(), label="card_ext", payload=card_pii(), host=EXTERNAL_HOST),
        dict(trace_id=T2, parent_id=mint_span_id(), label="card_int", payload=card_pii()),
        dict(trace_id=T2, parent_id=mint_span_id(), label="pi_int", payload=pi_only()),
        dict(trace_id=T2, parent_id=mint_span_id(), label="cred_int", payload=credential()),
        dict(trace_id=T2, parent_id=mint_span_id(), label="cred_ext", payload=credential(), host=EXTERNAL_HOST),
    ]
    results = probes_concurrent(kube, specs)
    rec.write("probes.json", [r.__dict__ for r in results])
    assert all(r.outcome == "ok" for r in results), results

    s = _settle(env, dg, capabilities, T2)
    rec.write("settle.json", s.__dict__)
    records = shape.current_records(dg, T2)
    seen: dict[str, dict] = {}
    for r in results:
        iid = shape.interaction_of_probe(dg, T2, r.parent_id)
        exp = L3_EXPECT[r.label]
        got = records[iid]
        dec = _decision_of(dg, iid)
        seen[r.label] = {"interaction": iid, "record": got, "decision": dec,
                         "request_summary": _request_summary(got)}
        assert (got["risk_level"], got["enforcement_type"]) == (exp["risk"], exp["enforcement"]), (
            f"{r.label}: {got['risk_level']}/{got['enforcement_type']}, "
            f"expected {exp['risk']}/{exp['enforcement']}; summary={got['summary']}")
        assert sorted(shape._real(got["rules"])) == exp["rules"], f"{r.label}: {got['rules']}"
        assert dec["allowed_actions"] == exp["allowed"], f"{r.label}: {dec}"
        assert dec["confidence"] == exp["confidence"], f"{r.label}: {dec}"
        assert dec["explanation"] == exp["explanation"], f"{r.label}: {dec['explanation']!r}"
    rec.write("per_probe.json", seen)
    assert len({v["interaction"] for v in seen.values()}) == 5, "five probes, five interactions"

    pairs = {(v["record"]["risk_level"], v["record"]["enforcement_type"]) for v in seen.values()}
    assert {("critical", "block"), ("medium", "escalate"), ("high", "require_approval")} <= pairs, pairs

    trace = shape.current_trace(dg, T2)
    rec.write("trace.json", trace)
    assert (trace["level"], trace["enforcement"]) == ("critical", "block")
    assert shape._real(trace["rules"]) == {
        "DG-001", "DG-004", "E2E-INVERT", "E2E-INT-DATA", "E2E-PI-INT", "E2E-CRED-INT", "E2E-CRED-EXT"}
    f = shape.forest(dg, T2, known=state["traces"])
    rec.write("forest.json", f.as_dict())
    shape.assert_lineage_forest(f, expect_entries=5)
    rec.write("risk.json", shape.assert_risk_shape(dg, T2, capabilities))


# --- L4 / L5 -------------------------------------------------------------------


def test_L4_incremental_legs_reversion(env, kube, dg, capabilities, catalog, sink, run_record, state):
    """L4 — a leg that arrives late re-versions the record (#161 scenario 4).

    Drive: the card payload to the never-answering sink (dotted external
    Host) with a 45 s client timeout. The request span is emitted on
    sight and its payload classified within seconds, so leg-ready delivers
    the request leg alone; the response span (abandoned) arrives 45 s
    later. Expect: the interaction has version 1 with legs_evidenced
    [request] and version 2 with [request, response]; at least two
    distinct evidence fingerprints in interaction_policy_decisions (OPA
    was consulted again); both versions critical/block from the request
    leg alone. Also recorded: how many of L1's own interactions carry
    ≥ 2 versions from the turn's natural leg timing.
    """
    T = state.get("T") or pytest.skip("L1 did not run")
    rec = run_record.scenario("L4")
    p = mint_span_id()
    t0 = time.monotonic()
    r = probe(kube, trace_id=T, parent_id=p, label="card_sink", payload=card_pii(),
              host=SINK_HOST, url=SINK_URL, timeout=45)
    rec.write("probe.json", r.__dict__)
    state["entries"] += 1
    assert r.outcome == "client_timeout", r
    assert time.monotonic() - t0 >= 44, "the sink answered — it must never answer"

    s = _settle(env, dg, capabilities, T)
    rec.write("settle.json", s.__dict__)
    iid = shape.interaction_of_probe(dg, T, p)
    hist = shape.record_history(dg, iid)
    rec.write("history.json", hist)
    assert len(hist) >= 2, f"late response leg did not re-version the record: {hist}"
    assert hist[0]["legs"] == ["request"], hist[0]
    assert hist[-1]["legs"] == ["request", "response"], hist[-1]
    assert all((h["risk_level"], h["enforcement_type"]) == ("critical", "block") for h in hist), hist
    fps = shape.decision_fingerprints(dg, iid)
    rec.write("fingerprints.json", {"distinct": fps})
    assert fps >= 2, "OPA was not re-consulted for the new evidence"
    rec.write("L1_multi_version.json", {"interactions": state.get("L1_multi_version", []),
                                        "count": len(state.get("L1_multi_version", []))})
    quiet = settle.quiet_recheck(dg, T, quiet_seconds=env.quiet_seconds)
    rec.write("quiet.json", quiet)
    state["L4"] = {"interaction": iid, "parent": p}


def test_L5_abandoned_exchange(env, dg, capabilities, catalog, run_record, state):
    """L5 — the abandoned exchange of L4 as a governance row (#161 scenario 6).

    Expect: the response span carries lineage.outcome=abandoned and no
    http.status_code; the response leg exists with error=true and no
    payload; the record's response summary is {"payload": null}; the
    verdict is the request leg's (critical/block). Shape holds.
    """
    T = state.get("T") or pytest.skip("L1 did not run")
    l4 = state.get("L4") or pytest.skip("L4 did not run")
    rec = run_record.scenario("L5")
    iid = l4["interaction"]
    legs = shape.legs_of(dg, iid)
    span = shape.response_span_of(dg, T, iid)
    rec.write("legs.json", legs)
    rec.write("response_span.json", span)
    assert span is not None and span["outcome"] == "abandoned", span
    assert span["status"] is None, span
    assert legs["response"]["error"] is True and legs["response"]["payload_hash"] is None, legs
    assert legs["request"]["payload_hash"] is not None, legs
    rec_ = shape.current_records(dg, T)[iid]
    assert (rec_["summary"] or {}).get("response") == {"payload": None}, rec_["summary"]
    assert (rec_["risk_level"], rec_["enforcement_type"]) == ("critical", "block")
    rec.write("risk.json", shape.assert_risk_shape(dg, T, capabilities))


# --- L6 ------------------------------------------------------------------------


def test_L6_no_payload_interaction(env, kube, dg, capabilities, catalog, run_record, state):
    """L6 — an exchange with no body either way (#161 scenario 8).

    Drive: GET a path the PSP does not serve (no body; the 404's plain-JSON
    reply is not parser-captured). Not /healthz: the sidecar bypasses health
    paths and emits nothing for them. Expect: both legs with payload_hash NULL, record
    none/allow, classification summary {"request": {"payload": null},
    "response": {"payload": null}}.
    """
    T = state.get("T") or pytest.skip("L1 did not run")
    rec = run_record.scenario("L6")
    p = mint_span_id()
    r = probe(kube, trace_id=T, parent_id=p, label="nobody_get", url=PSP_NOBODY_URL)
    rec.write("probe.json", r.__dict__)
    state["entries"] += 1
    assert r.outcome in ("ok", "http_error"), r
    s = _settle(env, dg, capabilities, T)
    rec.write("settle.json", s.__dict__)
    iid = shape.interaction_of_probe(dg, T, p)
    legs = shape.legs_of(dg, iid)
    rec.write("legs.json", legs)
    assert legs["request"]["payload_hash"] is None and legs["response"]["payload_hash"] is None, legs
    record = shape.current_records(dg, T)[iid]
    rec.write("record.json", record)
    assert record["summary"] == {"request": {"payload": None}, "response": {"payload": None}}, record
    assert (record["risk_level"], record["enforcement_type"]) == ("none", "allow")
    rec.write("risk.json", shape.assert_risk_shape(dg, T, capabilities))


# --- L7 ------------------------------------------------------------------------


def test_L7_concurrency(env, kube, dg, capabilities, catalog, run_record, state):
    """L7 — simultaneous legs and interleaved traces (#161 scenarios 2, 3, 5).

    Drive: four probes fired at once into L1's trace with distinct parent
    ids (card→external, PI→internal, credential→internal, no-body GET);
    then two full turns at once on two fresh traces with different guests,
    cities and accounts. Expect: four distinct interactions each with the
    L3-predicted verdict; both turns' forests hold; no span of one turn's
    trace carries the other guest's name in its captured input or output;
    no record id is shared across the two traces.
    """
    T = state.get("T") or pytest.skip("L1 did not run")
    rec = run_record.scenario("L7")
    specs = [
        dict(trace_id=T, parent_id=mint_span_id(), label="card_ext", payload=card_pii(), host=EXTERNAL_HOST),
        dict(trace_id=T, parent_id=mint_span_id(), label="pi_int", payload=pi_only()),
        dict(trace_id=T, parent_id=mint_span_id(), label="cred_int", payload=credential()),
        dict(trace_id=T, parent_id=mint_span_id(), label="nobody_get", url=PSP_NOBODY_URL),
    ]
    results = probes_concurrent(kube, specs)
    rec.write("probes.json", [r.__dict__ for r in results])
    state["entries"] += 4

    T3, T4 = mint_trace_id(), mint_trace_id()
    state["T3"], state["T4"] = T3, T4
    state["traces"] |= {T3, T4}
    turns = turns_concurrent(kube, [
        dict(trace_id=T3, parent_id=mint_span_id(), text=driver.USER_TURN),
        dict(trace_id=T4, parent_id=mint_span_id(),
             text=guest_turn(guest="Noa Levi", city="Kyoto", account="acct_002")),
    ])
    rec.write("turns.json", [t.__dict__ for t in turns])
    for t in turns:
        assert_turn_answered(t)

    for tid in (T, T3, T4):
        s = _settle(env, dg, capabilities, tid)
        rec.write(f"settle-{tid[:8]}.json", s.__dict__)

    records = shape.current_records(dg, T)
    ids = set()
    for r in results:
        iid = shape.interaction_of_probe(dg, T, r.parent_id)
        ids.add(iid)
        if r.label in L3_EXPECT:
            exp = L3_EXPECT[r.label]
            got = records[iid]
            assert (got["risk_level"], got["enforcement_type"]) == (exp["risk"], exp["enforcement"]), (
                f"{r.label}: {got}")
            assert sorted(shape._real(got["rules"])) == exp["rules"], f"{r.label}: {got['rules']}"
    assert len(ids) == 4, "four simultaneous probes, four interactions"

    for tid, own, other in ((T3, "Maya Park", "Noa Levi"), (T4, "Noa Levi", "Maya Park")):
        f = shape.forest(dg, tid, known=state["traces"])
        rec.write(f"forest-{tid[:8]}.json", f.as_dict())
        shape.assert_lineage_forest(f, expect_entries=1)
        shape.assert_strays_tolerated(dg, shape.strays(dg, tid, known=state["traces"]))
        rec.write(f"risk-{tid[:8]}.json", shape.assert_risk_shape(dg, tid, capabilities))
        own_hops = shape.payload_mentions(dg, tid, own)
        leak_hops = shape.payload_mentions(dg, tid, other)
        rec.write(f"crossover-{tid[:8]}.json", {"own": own, "own_hops": own_hops,
                                                "other": other, "leak_hops": leak_hops})
        assert own_hops, f"{tid}: own guest never captured"
        assert not leak_hops, (
            f"{tid}: the other turn's guest ({other!r}) appears in this trace's payloads at "
            f"{[(h['self_id'], h['direction'], h['protocol'], h['side']) for h in leak_hops]} — "
            f"PII from another session inside this session's hops")
    r3 = {r["id"] for r in shape.current_records(dg, T3).values()}
    r4 = {r["id"] for r in shape.current_records(dg, T4).values()}
    assert not r3 & r4, "a risk record is shared by two traces"
    fT = shape.forest(dg, T, known=state["traces"])
    rec.write("forest.json", fT.as_dict())
    shape.assert_lineage_forest(fT, expect_entries=state["entries"])
    rec.write("risk.json", shape.assert_risk_shape(dg, T, capabilities))


# --- L8 ------------------------------------------------------------------------


def test_L8_idempotency(env, kube, dg, capabilities, catalog, run_record, state):
    """L8 — unchanged evidence writes no new version (FR-DAS-014).

    Drive: the internal card probe again, byte-identical to L2's. Expect:
    L2's internal interaction has exactly the record history and decision
    fingerprints it had (untouched); the new interaction's version 1 equals
    it in risk, enforcement, rules and classification summary; every L1
    interaction still has the version count it had at L1; the trace has a
    new version only because its contributing set grew.
    """
    T = state.get("T") or pytest.skip("L1 did not run")
    l2 = state.get("L2") or pytest.skip("L2 did not run")
    rec = run_record.scenario("L8")
    p = mint_span_id()
    r = probe(kube, trace_id=T, parent_id=p, label="card_int_again", payload=card_pii())
    rec.write("probe.json", r.__dict__)
    state["entries"] += 1
    assert r.outcome == "ok", r
    s = _settle(env, dg, capabilities, T)
    rec.write("settle.json", s.__dict__)

    assert shape.record_history(dg, l2["int"]) == l2["int_history"], "L2's record re-versioned"
    assert shape.decision_fingerprints(dg, l2["int"]) == l2["int_decisions"], "L2 re-decided"
    new = shape.interaction_of_probe(dg, T, p)
    records = shape.current_records(dg, T)
    a, b = records[l2["int"]], records[new]
    rec.write("pair.json", {"l2": a, "new": b})
    assert b["version"] == 1
    for k in ("risk_level", "enforcement_type", "rules", "summary"):
        assert a[k] == b[k], f"{k}: {a[k]} != {b[k]}"
    now = settle.snapshot_versions(dg, T)["records"]
    drifted = {i: (v, now.get(i)) for i, v in state["L1_versions"].items() if now.get(i) != v}
    rec.write("l1_drift.json", drifted)
    assert not drifted, f"L1 interactions re-versioned without new evidence: {drifted}"
    trace = shape.current_trace(dg, T)
    assert b["id"] in trace["contributing"]
    rec.write("risk.json", shape.assert_risk_shape(dg, T, capabilities))


# --- L9 (opt-in, destructive) --------------------------------------------------


def test_L9_opa_outage_holds_the_cursor(env, kube, dg, capabilities, catalog, run_record, state):
    """L9 — OPA unreachable: the leg stream holds, nothing is skipped,
    catch-up writes exactly one version (#158 acceptance). Opt-in with
    E2E_DESTRUCTIVE=1: it scales the OPA deployment to zero.

    Expect while OPA is down: the probe's leg exists, no risk record for
    its interaction, leg-ready's backlog stays > 0 across three polls
    (held, not skipped); /risk/health (when served) reports the leg
    channel unhealthy. After scale-up: exactly one version for the
    interaction, and no interaction in the trace gained a duplicate
    version.
    """
    if not env.destructive:
        pytest.skip("E2E_DESTRUCTIVE=1 to run the OPA outage drill")
    T = state.get("T") or pytest.skip("L1 did not run")
    rec = run_record.scenario("L9")
    before = settle.snapshot_versions(dg, T)["records"]
    kube.run(["scale", "deploy/opa", "--replicas=0"], ns=env.dg_ns)
    try:
        kube.run(["rollout", "status", "deploy/opa", "--timeout=120s"], ns=env.dg_ns, timeout=150)
        p = mint_span_id()
        r = probe(kube, trace_id=T, parent_id=p, label="card_ext_outage", payload=card_pii(),
                  host=EXTERNAL_HOST)
        state["entries"] += 1
        assert r.outcome == "ok", r
        held = []
        for _ in range(3):
            time.sleep(6)
            held.append(settle.pending(dg, capabilities).get("leg_ready", 0))
        rec.write("held_polls.json", held)
        assert all(h > 0 for h in held), f"leg-ready did not hold while OPA was down: {held}"
        iid_rows = dg.q("""
            SELECT isp.interaction_id FROM spans s
            JOIN interaction_spans isp ON isp.trace_id = s.trace_id AND isp.span_id = s.span_id
            WHERE s.trace_id = %s AND s.parent_id = %s AND isp.role = 'anchor'""", (T, p))
        assert iid_rows, "probe interaction not derived while OPA was down"
        iid = iid_rows[0][0]
        (n,) = dg.one("SELECT count(*) FROM interaction_risk_records WHERE interaction_id = %s", (iid,))
        assert n == 0, "a risk record was written with OPA unreachable"
        if capabilities.health:
            import httpx
            h = httpx.get(f"{env.dg_api}/risk/health", timeout=10).json()
            rec.write("health_during.json", h)
    finally:
        kube.run(["scale", "deploy/opa", "--replicas=1"], ns=env.dg_ns)
        kube.run(["rollout", "status", "deploy/opa", "--timeout=180s"], ns=env.dg_ns, timeout=200)
    s = _settle(env, dg, capabilities, T)
    rec.write("settle.json", s.__dict__)
    hist = shape.record_history(dg, iid)
    rec.write("history.json", hist)
    assert len(hist) == 1 and (hist[0]["risk_level"], hist[0]["enforcement_type"]) == ("critical", "block")
    after = settle.snapshot_versions(dg, T)["records"]
    bumped = {i: (before[i], after[i]) for i in before if after.get(i) != before[i]}
    assert not bumped, f"outage catch-up re-versioned unchanged interactions: {bumped}"
    rec.write("risk.json", shape.assert_risk_shape(dg, T, capabilities))


# --- L10 (needs the alerts processor) ------------------------------------------


def test_L10_alert_supersession(env, kube, dg, capabilities, catalog, run_record, state):
    """L10 — alert lifecycle over a fresh trace (#104 acceptance). Skips
    unless the deployed branch runs the alerts processor.

    Drive: PI-only→internal (trace medium) → settle; card→external (trace
    critical) → settle; the same card probe again → settle. Expect after
    step 1: one open alert at medium; after step 2: one open alert at
    critical, the medium alert's superseded_by pointing at it and its
    status untouched; after step 3: still one head, duplicate_count 1.
    """
    if not capabilities.alerts:
        pytest.skip("no alerts processor on the deployed branch (capabilities.alerts=False)")
    rec = run_record.scenario("L10")
    T5 = mint_trace_id()
    state["T5"] = T5
    state["traces"].add(T5)

    probe(kube, trace_id=T5, parent_id=mint_span_id(), label="pi_int", payload=pi_only())
    _settle(env, dg, capabilities, T5)
    a1 = shape.trace_alerts(dg, T5)
    rec.write("alerts-1.json", a1)
    heads = [a for a in a1 if a["superseded_by"] is None]
    assert len(heads) == 1 and heads[0]["level"] == "medium", a1
    first_status = heads[0]["status"]

    probe(kube, trace_id=T5, parent_id=mint_span_id(), label="card_ext", payload=card_pii(),
          host=EXTERNAL_HOST)
    _settle(env, dg, capabilities, T5)
    a2 = shape.trace_alerts(dg, T5)
    rec.write("alerts-2.json", a2)
    heads = [a for a in a2 if a["superseded_by"] is None]
    assert len(heads) == 1 and heads[0]["level"] == "critical", a2
    prior = next(a for a in a2 if a["id"] == a1[0]["id"])
    assert prior["superseded_by"] == heads[0]["id"] and prior["status"] == first_status, a2

    probe(kube, trace_id=T5, parent_id=mint_span_id(), label="card_ext_again", payload=card_pii(),
          host=EXTERNAL_HOST)
    _settle(env, dg, capabilities, T5)
    a3 = shape.trace_alerts(dg, T5)
    rec.write("alerts-3.json", a3)
    heads = [a for a in a3 if a["superseded_by"] is None]
    assert len(heads) == 1 and heads[0]["duplicates"] == 1, a3
    rec.write("risk.json", shape.assert_risk_shape(dg, T5, capabilities, alert_heads=1))
