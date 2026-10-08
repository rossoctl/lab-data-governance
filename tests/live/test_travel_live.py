"""Live tier scenarios L1–L15 (issue #161): real traffic through the cortex
lineage sidecar on the pinned travel_advisor app, asserted from the DG
tables. Run in file order (``-m live``, no ``-x``: one red scenario must
not stop the rest); later scenarios address the trace the baseline minted.

Three groups, in the order they run:

A. **Lineage and the plugin** (L1–L6): nothing is read from the risk
   tables. One turn, then the app's own ledger against the tables, then
   the plugin's edge behaviours (abandoned exchange, no body, simultaneous
   legs) and two users at once. Every real turn in this group is checked
   by shape AND by the ledger.
B. **Classification and risk** (L7–L13): known bytes under the deployed
   test catalog, ``catalog_e2e.json``; versions, idempotency, the outage
   drill, alerts.
C. **The app's behaviour** (L14–L15): the rogue partner variant, then the
   cut turn, which runs last because its upstream keeps working for
   minutes.

Every property a scenario examines is one row of its table
(tests/live/checks.py: ``[case, id, property, expected, actual, status,
comment]``, written to ``<run>/<case>/checks.json`` as it happens and
concatenated into ``<run>/checks.csv``). Ids are stable names and are the
join key for ``report.py diff <run A> <run B>``. Every scenario's docstring
is its expectation card, written before the run. Counts the model's plan
decides are ``recorded``, never asserted.

Issue #161's scenario numbers are noted per test. Scenario 7 (an
interaction with only a response leg) is N/A by contract: "the request
leg always exists; the response leg only when the response span does —
its absence IS the in-flight signal" (docs/sidecar-wire-contract.md §7,
ADR-0030), so no producer can emit it and no test can ask for it.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from pathlib import Path

import pytest

from tests.live import audit, checks, driver, ledger, settle, shape
from tests.live.checks import Checks
from tests.live.driver import (
    EXTERNAL_HOST, PSP_NOBODY_URL, REFLECT_HOST, REFLECT_URL, SINK_HOST, SINK_URL, card_pii,
    credential, guest_turn, benign, mint_span_id, mint_trace_id, pi_only, probe, probes_concurrent,
    turn, turn_cut, turns_concurrent,
)

# A live session for another app skips this module (see test_lab_live.py).
if __import__("os").environ.get("E2E_KUBE_CONTEXT") and __import__("os").environ.get("E2E_APP", "travel_advisor") != "travel_advisor":
    pytest.skip("travel_advisor scenarios; E2E_APP selects another app", allow_module_level=True)

_CATALOG = json.loads((Path(__file__).parent / "catalog_e2e.json").read_text(encoding="utf-8"))
RULES = {r["rule_id"]: r for r in _CATALOG["rules"]}


def explanation(*rule_ids: str) -> str:
    """The compiled policy's combined explanation: the risk-axis winner's
    text, then the enforcement-axis winner's, joined by '; ' (one text when
    the same rule wins both) — rego.py's combining block."""
    return "; ".join(RULES[r]["rule_decision"]["explanation"] for r in rule_ids)


# The advisor ends its scripted turn by asking whether there is anything else,
# so the A2A task's terminal state is `input-required`, not `completed`.
# Either state with a non-empty answer artifact is a finished turn for
# lineage purposes.
TURN_DONE_STATES = {"completed", "input-required"}


def _turn_answered(ch: Checks, reply, id: str = "turn") -> None:
    ch.check(f"{id}.state", "the turn finished", sorted(TURN_DONE_STATES), reply.state,
             ok=reply.state in TURN_DONE_STATES, comment=reply.raw[:200] if reply.state not in TURN_DONE_STATES else None)
    ch.check(f"{id}.answer", "the turn answered", "non-empty", f"{len(reply.answer.strip())} chars",
             ok=bool(reply.answer.strip()))


def _settle(ch: Checks, env, dg, caps, trace_id, rec, *, tag="") -> None:
    s = settle.wait_drained(dg, trace_id, caps, timeout=env.settle_timeout, quiet_seconds=env.quiet_seconds)
    rec.write(f"settle{tag}.json", s.__dict__)
    ch.record(f"settle{tag}", "settled", f"{s.elapsed:.1f} s · {s.polls} polls · {s.spans} spans",
              comment=f"absent streams {s.streams_absent or 'none'}")


def _forest(ch: Checks, dg, rec, trace_id, state, *, tag="", **kw):
    f = shape.forest(dg, trace_id, known=state["traces"])
    rec.write(f"forest{tag}.json", f.as_dict())
    ch.record(f"forest{tag}.interactions", "interactions derived", f.interactions)
    checks.forest(ch, f, expect_entry=shape.ENTRY_SELF_ID, **kw)
    return f


def _audit(ch: Checks, env, dg, fleet, attach, rec, trace_id, *, known, markers=(), reply=None, tag="",
           quiet_reread=True, probe_parents=frozenset(), with_risk=True):
    """Every property of the derived tables for one trace (tests/live/audit.py),
    every check a row; the declared gaps read KNOWN. Fails once, with the
    table of real failures. ``with_risk=False`` keeps the risk axis out."""
    au, extra = audit.run(dg, trace_id, fleet, known=known, markers=list(markers), reply=reply,
                          quiet_seconds=env.quiet_seconds, attach=attach, quiet_reread=quiet_reread,
                          probe_parents=frozenset(probe_parents), with_risk=with_risk)
    rec.write(f"audit{tag}.json", au.as_dict())
    rec.write(f"audit{tag}.md", au.markdown(), raw=True)
    rec.write(f"census{tag}.json", extra)
    ch.absorb_audit(au, prefix=f"audit{tag}")
    assert not au.failed, "\n" + au.markdown(failed_only=True)
    return au


def _ledger_join(ch: Checks, env, kube, dg, fleet, rec, trace_id, state, *, tag=""):
    """Tier C on one trace: the app's ledger lines for the run window joined
    to the trace's request spans (tests/live/ledger.py); every residual a
    row. The caller skips or guards on ``capabilities.ledger``."""
    since = state.get("t_start") or (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=30))
    records, malformed = ledger.collect(kube, env, sorted(fleet.workloads), since)
    rec.write(f"ledger_records{tag}.jsonl", "\n".join(json.dumps(r) for r in records), raw=True)
    c = ledger.compare(dg, trace_id, records, fleet, malformed=malformed)
    rec.write(f"ledger_compare{tag}.json", c)
    ledger.assert_consistent(c, checks=ch)
    return c


def _decision_of(dg, interaction_id):
    d = shape.latest_decision(dg, interaction_id)
    assert d is not None, f"no policy decision stored for {interaction_id}"
    return d


def _request_summary(record):
    s = record["summary"] or {}
    assert "request" in s, f"record has no request leg summary: {s}"
    return s["request"]


def _verdict(rec: dict) -> str:
    return f"{rec['risk_level']}/{rec['enforcement_type']}"


# =============================================================================
# A. Lineage and the plugin — nothing read from the risk tables
# =============================================================================


# --- L1 ------------------------------------------------------------------------


def test_L1_baseline_turn(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """L1 — one scripted turn under a minted trace id, and nothing else
    (#161 scenario 1).

    Expect: the reply finishes with an answer (state completed or
    input-required: the advisor ends by asking for more); the derived forest
    has exactly one root (the turn), zero orphans, zero request spans without
    their response twin, every inbound stamp-parented, the entry the only
    unstamped span; demo-client started no other trace in the window; the
    audit holds on the four lineage axes — soundness, completeness,
    fidelity, invariance — with the app's declared gaps (the frameworks'
    own-trace MCP sessions, the stale toolset parent, the held content-kind
    fix) reported as KNOWN by id, never excused silently; after a quiet
    window nothing moves. The risk axis and the risk tables are not read:
    they are group B's (L7 onwards). Counts (spans, interactions, depth
    histogram) recorded.
    """
    rec = run_record.scenario("L1")
    with Checks("L1", rec) as ch:
        state["t_start"] = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=30)
        T, p = mint_trace_id(), mint_span_id()
        state["T"] = T
        state["traces"] = {T}
        state["probes"] = set()
        reply = turn(kube, trace_id=T, parent_id=p, text=driver.fresh_turn())
        rec.write("reply.json", reply.__dict__)
        _turn_answered(ch, reply)
        state["reply"] = reply.answer

        _settle(ch, env, dg, capabilities, T, rec)
        state["entries"] = 1  # the turn is the only root until L3 adds probes
        _forest(ch, dg, rec, T, state, expect_entries=state["entries"])
        _audit(ch, env, dg, fleet, attach, rec, T, known=state["traces"], markers=["Maya Park", "acct_001"],
               reply=reply.answer, with_risk=False)
        quiet = settle.quiet_recheck(dg, T, quiet_seconds=env.quiet_seconds)
        rec.write("quiet.json", quiet)


# --- L2 ------------------------------------------------------------------------


def test_L2_ledger_matches_the_tables(env, kube, dg, capabilities, catalog, fleet, run_record, state):
    """L2 — the app's own ledger against the derived tables, on L1's trace
    (Tier C), before any probe is added to it.

    Needs the app pods to run with ROSSOCTL_LEDGER=stdout (skips otherwise).
    Reads every workload's ledger lines for the run window and joins each
    outbound/inbound exchange the app made to the sidecar's request span of
    the same pod, host and method, one to one; compares the captured
    input/output with the digest of what the app sent/received where the
    two reductions are defined to coincide; checks the span sits inside the
    app's send window; lists the dark hops (store acts) and the strays the
    frameworks report about themselves (an outbound call with no ambient
    trace naming another trace). Asserts: no undeclared ledger-only
    exchange, no table-only exchange, no digest mismatch, no timing
    violation, every pod in the tree wrote a ledger line for the trace. An
    empty ledger is red, not green.
    """
    if not capabilities.ledger:
        pytest.skip("the app pods do not run a ledger (ROSSOCTL_LEDGER=stdout not set on every workload)")
    T = state.get("T") or pytest.skip("L1 did not run")
    rec = run_record.scenario("L2")
    with Checks("L2", rec) as ch:
        _ledger_join(ch, env, kube, dg, fleet, rec, T, state)


# --- L3 ------------------------------------------------------------------------


def test_L3_abandoned_exchange(env, kube, dg, capabilities, catalog, sink, run_record, state):
    """L3 — an exchange the far end never answers (#161 scenarios 4 and 6,
    the lineage half; the record's versions are L9's).

    Drive: the card payload to the never-answering sink under a dotted
    external Host, 45 s client timeout. The request span is emitted on
    sight; the response span arrives when the client gives up. Expect: the
    response span carries lineage.outcome=abandoned and no http.status_code;
    the response leg exists with error=true and no payload; the request leg
    has a payload; the forest of T holds with one more root.
    """
    T = state.get("T") or pytest.skip("L1 did not run")
    rec = run_record.scenario("L3")
    with Checks("L3", rec) as ch:
        p = mint_span_id()
        t0 = time.monotonic()
        r = probe(kube, trace_id=T, parent_id=p, label="card_sink", payload=card_pii(),
                  host=SINK_HOST, url=SINK_URL, timeout=45)
        rec.write("probe.json", r.__dict__)
        state["entries"] += 1
        state["probes"].add(p)
        ch.check("probe.outcome", "the client gave up on the sink", "client_timeout", r.outcome)
        held = time.monotonic() - t0
        ch.check("probe.held", "the sink never answered (client held ≥ 44 s)", "≥ 44 s", f"{held:.1f} s", ok=held >= 44)

        _settle(ch, env, dg, capabilities, T, rec)
        iid = shape.interaction_of_probe(dg, T, p)
        legs = shape.legs_of(dg, iid)
        span = shape.response_span_of(dg, T, iid)
        rec.write("legs.json", legs)
        rec.write("response_span.json", span)
        ch.check("span.present", "a response span exists for the abandoned exchange", "present",
                 "present" if span else "MISSING", ok=span is not None)
        ch.check("span.outcome", "response span lineage.outcome", "abandoned", span["outcome"])
        ch.check("span.status", "response span http.status_code", None, span["status"])
        ch.check("legs.response.error", "response leg error flag", True, legs["response"]["error"])
        ch.check("legs.response.payload", "response leg payload hash", None, legs["response"]["payload_hash"])
        ch.check("legs.request.payload", "request leg has a payload", "present",
                 "present" if legs["request"]["payload_hash"] else "none", ok=legs["request"]["payload_hash"] is not None)
        _forest(ch, dg, rec, T, state, expect_entries=state["entries"])
        state["sink"] = {"interaction": iid, "parent": p}


# --- L4 ------------------------------------------------------------------------


def test_L4_no_payload_interaction(env, kube, dg, capabilities, catalog, run_record, state):
    """L4 — an exchange with no body either way (#161 scenario 8, the
    lineage half; its record is read in L7).

    Drive: GET a path the PSP does not serve (no body; the 404's plain-JSON
    reply is not parser-captured). Not /healthz: the sidecar bypasses health
    paths and emits nothing for them. Expect: both legs with payload_hash
    NULL; the forest holds.
    """
    T = state.get("T") or pytest.skip("L1 did not run")
    rec = run_record.scenario("L4")
    with Checks("L4", rec) as ch:
        p = mint_span_id()
        r = probe(kube, trace_id=T, parent_id=p, label="nobody_get", url=PSP_NOBODY_URL)
        rec.write("probe.json", r.__dict__)
        state["entries"] += 1
        state["probes"].add(p)
        ch.check("probe.outcome", "the GET was answered (404 is fine)", ["ok", "http_error"], r.outcome,
                 ok=r.outcome in ("ok", "http_error"))
        _settle(ch, env, dg, capabilities, T, rec)
        iid = shape.interaction_of_probe(dg, T, p)
        legs = shape.legs_of(dg, iid)
        rec.write("legs.json", legs)
        ch.check("legs.request.payload", "request leg payload hash", None, legs["request"]["payload_hash"])
        ch.check("legs.response.payload", "response leg payload hash", None, legs["response"]["payload_hash"])
        _forest(ch, dg, rec, T, state, expect_entries=state["entries"])
        state["nobody"] = {"interaction": iid, "parent": p}


# --- L5 ------------------------------------------------------------------------


def test_L5_simultaneous_legs(env, kube, dg, capabilities, catalog, run_record, state):
    """L5 — simultaneous legs inside one trace (#161 scenarios 2, 3).

    Drive: four probes fired at once into L1's trace with distinct parent
    ids (card→external, PI→internal, credential→internal, no-body GET).
    Expect: four distinct interactions, each found by its own parent; the
    forest holds with four more roots. Derivation under concurrent arrival
    within ONE trace; the verdicts of these payloads are L8's, the
    cross-trace property is L6's.
    """
    T = state.get("T") or pytest.skip("L1 did not run")
    rec = run_record.scenario("L5")
    with Checks("L5", rec) as ch:
        specs = [
            dict(trace_id=T, parent_id=mint_span_id(), label="card_ext", payload=card_pii(), host=EXTERNAL_HOST),
            dict(trace_id=T, parent_id=mint_span_id(), label="pi_int", payload=pi_only()),
            dict(trace_id=T, parent_id=mint_span_id(), label="cred_int", payload=credential()),
            dict(trace_id=T, parent_id=mint_span_id(), label="nobody_get", url=PSP_NOBODY_URL),
        ]
        results = probes_concurrent(kube, specs)
        rec.write("probes.json", [r.__dict__ for r in results])
        state["entries"] += 4
        state["probes"] |= {sp["parent_id"] for sp in specs}
        ch.record("probes.outcomes", "the four probes' outcomes", {r.label: r.outcome for r in results})

        _settle(ch, env, dg, capabilities, T, rec)
        ids = {shape.interaction_of_probe(dg, T, r.parent_id) for r in results}
        rec.write("interactions.json", sorted(ids))
        ch.check("probes.interactions", "four simultaneous probes derive four distinct interactions", 4, len(ids))
        _forest(ch, dg, rec, T, state, expect_entries=state["entries"])


# --- L6 ------------------------------------------------------------------------


def test_L6_cross_trace_isolation(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """L6 — two users at once stay two traces (#161 scenario 5).

    Drive: two full turns at once on two fresh traces with different
    guests, cities and accounts. Expect: both turns' forests and lineage
    audits hold; both turns join their ledgers (when the app runs one); no
    span of one turn's trace carries the other guest's name in its captured
    input or output (isolation, I2); no record id is shared across the two
    traces (the one risk-table read here, an identity check, not a
    verdict). Red on app commits before agent-examples-snp #17: the runtime's
    peer tool cached its A2A continuation under a constant, so every
    delegation to a peer shared one context id and the peer's history held
    every caller's turns; one guest's name reached the other's prompt. The
    leak rows are raised last, after both traces' forests, audits and ledger joins.
    """
    if not state.get("T"):
        pytest.skip("L1 did not run")
    rec = run_record.scenario("L6")
    with Checks("L6", rec, raise_at_exit=True) as ch:
        T3, T4 = mint_trace_id(), mint_trace_id()
        state["T3"], state["T4"] = T3, T4
        state["traces"] |= {T3, T4}
        turns = turns_concurrent(kube, [
            dict(trace_id=T3, parent_id=mint_span_id(), text=driver.fresh_turn()),
            dict(trace_id=T4, parent_id=mint_span_id(),
                 text=guest_turn(guest="Noa Levi", city="Kyoto", account="acct_002",
                                 base=driver.fresh_turn())),
        ])
        rec.write("turns.json", [t.__dict__ for t in turns])
        for t, tag in zip(turns, ("maya", "noa")):
            _turn_answered(ch, t, id=f"turn.{tag}")

        for tid in (T3, T4):
            _settle(ch, env, dg, capabilities, tid, rec, tag=f"-{tid[:8]}")

        for tid, own, other in ((T3, "Maya Park", "Noa Levi"), (T4, "Noa Levi", "Maya Park")):
            tag = f"-{tid[:8]}"
            _forest(ch, dg, rec, tid, state, tag=tag, expect_entries=1)
            _audit(ch, env, dg, fleet, attach, rec, tid, known=state["traces"], markers=[own], tag=tag,
                   quiet_reread=False, with_risk=False)
            if capabilities.ledger:
                _ledger_join(ch, env, kube, dg, fleet, rec, tid, state, tag=tag)
            own_hops = shape.payload_mentions(dg, tid, own)
            leak_hops = shape.payload_mentions(dg, tid, other)
            rec.write(f"crossover{tag}.json", {"own": own, "own_hops": own_hops,
                                               "other": other, "leak_hops": leak_hops})
            ch.check(f"isolation{tag}.own", f"own guest ({own}) captured in this trace", "≥ 1 hop",
                     f"{len(own_hops)} hops", ok=bool(own_hops))
            ch.check(f"isolation{tag}.leak", f"the other turn's guest ({other}) in this trace's payloads", [],
                     sorted({(h["self_id"], h["direction"], h["protocol"], h["side"]) for h in leak_hops}),
                     comment="PII from another session inside this session's hops" if leak_hops else None)
        r3 = {r["id"] for r in shape.current_records(dg, T3).values()}
        r4 = {r["id"] for r in shape.current_records(dg, T4).values()}
        ch.check("isolation.shared_record_ids", "risk record ids shared by the two traces", [], sorted(r3 & r4))


# =============================================================================
# B. Classification and risk — known bytes under the test catalog
# =============================================================================


# --- L7 ------------------------------------------------------------------------


def test_L7_known_payloads_to_the_psp(env, kube, dg, capabilities, catalog, fleet, run_record, state):
    """L7 — the first read of the risk tables: known payloads to the PSP
    inside L1's trace (#161 scenarios 9, 10, 12, 13), and the risk shape of
    everything group A left in T.

    Drive: a benign body, then the same card payload once as an external
    name and once as the internal short name. Expect: the benign probe is
    classified with zero findings at level PUBLIC and decided none/allow (a
    real turn's legs all carry findings, so the zero case is driven, not
    hoped for); both card request legs classified identically ({PI, PII,
    PCI}, RESTRICTED, identity bundle); external → critical/block, rules
    [DG-001, DG-004, E2E-INVERT], allowed_actions [redact], confidence
    0.95, explanation = DG-001's alone (it wins both axes; the advisory
    `warn` rule does not weaken `block`); internal → none/allow with no
    real rule; response summaries {"payload": null} (the PSP's plain-JSON
    reply is not parser-captured); L4's no-body exchange decides none/allow
    with {"payload": null} both sides; trace rollup critical/block with
    both card record ids contributing. Then the risk axis (R1) and the risk
    shape of T: one current record per live interaction, a trace record
    with both aggregation modes, contributing ids = current record set,
    rules union, no unclassified leg, no pending record, entity set equal —
    the open stale-callee defect, a KNOWN row, never green.
    """
    T = state.get("T") or pytest.skip("L1 did not run")
    rec = run_record.scenario("L7")
    with Checks("L7", rec, raise_at_exit=True) as ch:
        p_benign, p_ext, p_int = mint_span_id(), mint_span_id(), mint_span_id()
        state["probes"] |= {p_benign, p_ext, p_int}
        r_benign = probe(kube, trace_id=T, parent_id=p_benign, label="benign", payload=benign())
        r_ext = probe(kube, trace_id=T, parent_id=p_ext, label="card_ext", payload=card_pii(),
                      host=EXTERNAL_HOST)
        r_int = probe(kube, trace_id=T, parent_id=p_int, label="card_int", payload=card_pii())
        rec.write("probes.json", [r_benign.__dict__, r_ext.__dict__, r_int.__dict__])
        ch.check("probes.outcomes", "the three probes were answered", ["ok|http_error", "ok", "ok"],
                 [r_benign.outcome, r_ext.outcome, r_int.outcome],
                 ok=r_benign.outcome in ("ok", "http_error") and r_ext.outcome == "ok" and r_int.outcome == "ok")
        state["entries"] += 3

        _settle(ch, env, dg, capabilities, T, rec)
        ext = shape.interaction_of_probe(dg, T, p_ext)
        inn = shape.interaction_of_probe(dg, T, p_int)
        records = shape.current_records(dg, T)
        rec.write("records.json", {"ext": records[ext], "int": records[inn]})

        benign_rec = records[shape.interaction_of_probe(dg, T, p_benign)]
        req = _request_summary(benign_rec)
        rec.write("benign_record.json", benign_rec)
        ch.check("benign.findings", "benign request leg findings", 0, req.get("finding_count"))
        ch.check("benign.level", "benign request leg sensitivity level", "PUBLIC", req.get("sensitivity_level"))
        ch.check("benign.verdict", "benign record verdict", "none/allow", _verdict(benign_rec))

        for name, iid in (("ext", ext), ("int", inn)):
            req = _request_summary(records[iid])
            ch.check(f"{name}.tags", f"card {name}: request leg regulatory tags", ["PCI", "PI", "PII"], sorted(req["regulatory_tags"]))
            ch.check(f"{name}.level", f"card {name}: request leg sensitivity level", "RESTRICTED", req["sensitivity_level"])
            ch.check(f"{name}.bundle", f"card {name}: identity bundle", True, req["contains_identity_bundle"])
            ch.check(f"{name}.legs", f"card {name}: legs evidenced", ["request", "response"], records[iid]["legs"])
            ch.check(f"{name}.response_summary", f"card {name}: response summary (plain JSON is not parser-captured)",
                     {"payload": None}, (records[iid]["summary"] or {}).get("response"))
        ch.check("ext.verdict", "card external: verdict", "critical/block", _verdict(records[ext]))
        ch.check("ext.rules", "card external: triggered rules", ["DG-001", "DG-004", "E2E-INVERT"], records[ext]["rules"])
        d_ext = _decision_of(dg, ext)
        rec.write("decision_ext.json", d_ext)
        ch.check("ext.allowed_actions", "card external: allowed actions", ["redact"], d_ext["allowed_actions"])
        ch.check("ext.confidence", "card external: confidence", 0.95, d_ext["confidence"])
        ch.check("ext.explanation", "card external: explanation = DG-001's alone", explanation("DG-001"), d_ext["explanation"])
        ch.check("int.verdict", "card internal: verdict", "none/allow", _verdict(records[inn]))
        ch.check("int.rules", "card internal: real rules", [], sorted(shape._real(records[inn]["rules"])))

        nobody = records[state["nobody"]["interaction"]]
        rec.write("nobody_record.json", nobody)
        ch.check("nobody.summary", "L4's no-body exchange: classification summary",
                 {"request": {"payload": None}, "response": {"payload": None}}, nobody["summary"])
        ch.check("nobody.verdict", "L4's no-body exchange: verdict", "none/allow", _verdict(nobody))

        trace = shape.current_trace(dg, T)
        rec.write("trace.json", trace)
        ch.check("trace.rollup", "trace rollup", "critical/block", f"{trace['level']}/{trace['enforcement']}")
        ch.check("trace.contributing", "both card records contribute to the rollup", "both present",
                 "both present" if {records[ext]["id"], records[inn]["id"]} <= set(trace["contributing"]) else "missing",
                 ok={records[ext]["id"], records[inn]["id"]} <= set(trace["contributing"]))
        _forest(ch, dg, rec, T, state, expect_entries=state["entries"])
        state["L7"] = {"ext": ext, "int": inn, "p_int": p_int,
                       "int_history": shape.record_history(dg, inn),
                       "int_decisions": shape.decision_fingerprints(dg, inn)}

        # The risk axis of the audit, then the risk shape. The known DG defect
        # (the stale peer.host callee on a record, #279) is a KNOWN row:
        # it hides nothing and starves no later scenario of T.
        au = audit.risk_only(dg, T, fleet, probe_parents=frozenset(state["probes"]))
        rec.write("audit-risk.json", au.as_dict())
        rec.write("audit-risk.md", au.markdown(), raw=True)
        ch.absorb_audit(au, prefix="audit")
        risk = shape.assert_risk_shape(dg, T, capabilities, entity_set="record", checks=ch)
        rec.write("risk.json", risk)
        turn_findings = sorted(
            v.get("finding_count") for r in risk["records"].values()
            for v in (r["summary"] or {}).values() if isinstance(v, dict) and "finding_count" in v)
        rec.write("turn_finding_counts.json", turn_findings)
        ch.record("turn.finding_counts", "finding counts across the turn's legs", turn_findings)
        quiet = settle.quiet_recheck(dg, T, quiet_seconds=env.quiet_seconds)
        rec.write("quiet.json", quiet)
        state["T_versions"] = quiet["records"]
        state["T_multi_version"] = sorted(iid for iid, r in risk["records"].items() if r["version"] >= 2)
        rec.write("multi_version_interactions.json", state["T_multi_version"])
        ch.record("turn.multi_version", "interactions of T with ≥ 2 record versions from natural leg timing",
                  len(state["T_multi_version"]))
        if au.failed:
            raise AssertionError("\n" + au.markdown(failed_only=True))


# --- L8 ------------------------------------------------------------------------


L8_EXPECT = {
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


def test_L8_mixed_levels_in_one_trace(env, kube, dg, capabilities, catalog, run_record, state):
    """L8 — five probes fired at once into a fresh trace, under the test
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
    rec = run_record.scenario("L8")
    with Checks("L8", rec, raise_at_exit=True) as ch:
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
        ch.check("probes.outcomes", "the five probes were answered", ["ok"] * 5, [r.outcome for r in results])
        _settle(ch, env, dg, capabilities, T2, rec)

        records = shape.current_records(dg, T2)
        seen = {}
        for r in results:
            iid = shape.interaction_of_probe(dg, T2, r.parent_id)
            got = records[iid]
            d = _decision_of(dg, iid)
            seen[r.label] = {"interaction": iid, "record": got, "decision": d}
            exp = L8_EXPECT[r.label]
            ch.check(f"{r.label}.verdict", f"{r.label}: verdict", f"{exp['risk']}/{exp['enforcement']}", _verdict(got))
            ch.check(f"{r.label}.rules", f"{r.label}: triggered rules", exp["rules"], sorted(shape._real(got["rules"])))
            ch.check(f"{r.label}.allowed_actions", f"{r.label}: allowed actions", exp["allowed"], d["allowed_actions"])
            ch.check(f"{r.label}.confidence", f"{r.label}: confidence", exp["confidence"], d["confidence"])
            ch.check(f"{r.label}.explanation", f"{r.label}: explanation", exp["explanation"], d["explanation"])
        rec.write("per_probe.json", seen)
        ch.check("probes.interactions", "five probes, five interactions", 5, len({v["interaction"] for v in seen.values()}))
        pairs = sorted({(v["record"]["risk_level"], v["record"]["enforcement_type"]) for v in seen.values()})
        ch.check("probes.distinct_pairs", "distinct (risk, enforcement) pairs in one trace", "≥ 3", pairs, ok=len(pairs) >= 3)

        trace = shape.current_trace(dg, T2)
        rec.write("trace.json", trace)
        ch.check("trace.rollup", "trace rollup", "critical/block", f"{trace['level']}/{trace['enforcement']}")
        want = {"DG-001", "DG-004", "E2E-INVERT", "E2E-INT-DATA", "E2E-PI-INT", "E2E-CRED-INT", "E2E-CRED-EXT"}
        ch.check("trace.rules_union", "trace rules ⊇ the union of the five probes' rules", sorted(want),
                 sorted(set(trace["rules"]) & want), ok=set(trace["rules"]) >= want)
        _forest(ch, dg, rec, T2, state, expect_entries=5, expect_children=False)  # five probes, no turn
        rec.write("risk.json", shape.assert_risk_shape(dg, T2, capabilities, entity_set="record", checks=ch))


# --- L9 ------------------------------------------------------------------------


def test_L9_incremental_legs_reversion(env, dg, capabilities, catalog, run_record, state):
    """L9 — a leg that arrives late re-versions the record (#161 scenario 4,
    the risk half of L3's exchange).

    Nothing new is driven: L3's request span was classified within seconds,
    so leg-ready delivered the request leg alone; the abandoned response
    span arrived 45 s later. Expect: the interaction has version 1 with
    legs_evidenced [request] and version 2 with [request, response]; at
    least two distinct evidence fingerprints in interaction_policy_decisions
    (OPA was consulted again); both versions critical/block from the
    request leg alone; the record's response summary is {"payload": null}.
    Also recorded: how many of T's interactions carry ≥ 2 versions from the
    turn's natural leg timing.
    """
    T = state.get("T") or pytest.skip("L1 did not run")
    sink = state.get("sink") or pytest.skip("L3 did not run")
    rec = run_record.scenario("L9")
    with Checks("L9", rec, raise_at_exit=True) as ch:
        iid = sink["interaction"]
        hist = shape.record_history(dg, iid)
        rec.write("history.json", hist)
        ch.check("versions.count", "record versions (the late leg re-versioned it)", "≥ 2", len(hist), ok=len(hist) >= 2)
        ch.check("versions.first_legs", "version 1 legs evidenced", ["request"], hist[0]["legs"])
        ch.check("versions.last_legs", "last version legs evidenced", ["request", "response"], hist[-1]["legs"])
        ch.check("versions.verdicts", "every version's verdict (from the request leg alone)", "critical/block",
                 sorted({f"{h['risk_level']}/{h['enforcement_type']}" for h in hist}),
                 ok=all((h["risk_level"], h["enforcement_type"]) == ("critical", "block") for h in hist))
        fps = shape.decision_fingerprints(dg, iid)
        rec.write("fingerprints.json", {"distinct": fps})
        ch.check("decisions.fingerprints", "distinct evidence fingerprints (OPA re-consulted)", "≥ 2", fps, ok=fps >= 2)
        rec_ = shape.current_records(dg, T)[iid]
        ch.check("record.response_summary", "record response summary", {"payload": None},
                 (rec_["summary"] or {}).get("response"))
        rec.write("T_multi_version.json", {"interactions": state.get("T_multi_version", []),
                                           "count": len(state.get("T_multi_version", []))})
        ch.record("turn.multi_version", "interactions of T with ≥ 2 versions from natural leg timing",
                  len(state.get("T_multi_version", [])))


# --- L10 -----------------------------------------------------------------------


def test_L10_idempotency(env, kube, dg, capabilities, catalog, run_record, state):
    """L10 — unchanged evidence writes no new version (FR-DAS-014).

    Drive: the internal card probe again, byte-identical to L7's. It IS a
    new interaction, a new classification and a new decision (a new
    exchange id). Expect: L7's internal interaction has exactly the record
    history and decision fingerprints it had (untouched); the new
    interaction's version 1 equals it in risk, enforcement, rules and
    classification summary; every interaction of T still has the version
    count it had at L7; the trace has a new version only because its
    contributing set grew. Evidence for one interaction never re-versions
    another; identical evidence yields an identical verdict.
    """
    T = state.get("T") or pytest.skip("L1 did not run")
    l7 = state.get("L7") or pytest.skip("L7 did not run")
    rec = run_record.scenario("L10")
    with Checks("L10", rec, raise_at_exit=True) as ch:
        p = mint_span_id()
        r = probe(kube, trace_id=T, parent_id=p, label="card_int_again", payload=card_pii())
        rec.write("probe.json", r.__dict__)
        state["entries"] += 1
        state["probes"].add(p)
        ch.check("probe.outcome", "the probe was answered", "ok", r.outcome)
        _settle(ch, env, dg, capabilities, T, rec)

        ch.check("old.history_untouched", "L7's internal record history unchanged", len(l7["int_history"]),
                 len(shape.record_history(dg, l7["int"])), ok=shape.record_history(dg, l7["int"]) == l7["int_history"])
        ch.check("old.fingerprints_untouched", "L7's internal decision fingerprints unchanged", l7["int_decisions"],
                 shape.decision_fingerprints(dg, l7["int"]))
        new = shape.interaction_of_probe(dg, T, p)
        records = shape.current_records(dg, T)
        a, b = records[l7["int"]], records[new]
        rec.write("pair.json", {"l7": a, "new": b})
        ch.check("new.version", "the new interaction's record version", 1, b["version"])
        for k in ("risk_level", "enforcement_type", "rules", "summary"):
            ch.check(f"new.{k}", f"new interaction's {k} equals L7's", a[k], b[k])
        now = settle.snapshot_versions(dg, T)["records"]
        drifted = {i: (v, now.get(i)) for i, v in state["T_versions"].items() if now.get(i) != v}
        rec.write("t_drift.json", drifted)
        ch.check("trace.no_drift", "interactions of T re-versioned without new evidence", {}, drifted)
        trace = shape.current_trace(dg, T)
        ch.check("trace.contributing", "the new record contributes to the rollup", "present",
                 "present" if b["id"] in trace["contributing"] else "missing", ok=b["id"] in trace["contributing"])
        rec.write("risk.json", shape.assert_risk_shape(dg, T, capabilities, entity_set="record", checks=ch))


# --- L11 -----------------------------------------------------------------------


def test_L11_response_only_pii_is_not_sent(env, kube, dg, capabilities, catalog, sink, run_record, state):
    """L11 — data that only comes BACK from an external host was not sent to it
    (#271, fixed by #274: one flow per leg).

    Drive: a tools/call with benign arguments to the test reflector under an
    external Host; its JSON-RPC result carries PII (name, email, SSN, phone,
    DOB). Expect: the request leg classifies with zero findings; the response
    leg classifies PII at a restricted level; the record fires no
    external-sharing rule (DG-001 needs PII in a flow TO the external
    destination, and the only such flow is the benign request) — decided
    none/allow under the test catalog, whose only PII rule is likewise
    external-sharing. Shape holds. Before #274 the response's PII was scored
    as sent to the destination and this exchange read critical/block.
    """
    T = state.get("T") or pytest.skip("L1 did not run")
    rec = run_record.scenario("L11")
    with Checks("L11", rec, raise_at_exit=True) as ch:
        p = mint_span_id()
        r = probe(kube, trace_id=T, parent_id=p, label="reflect_pii", payload=benign(),
                  url=REFLECT_URL, host=REFLECT_HOST)
        rec.write("probe.json", r.__dict__)
        state["entries"] += 1
        state["probes"].add(p)
        ch.check("probe.outcome", "the reflector answered 200", "ok/200", f"{r.outcome}/{r.status}",
                 ok=r.outcome == "ok" and r.status == 200)
        _settle(ch, env, dg, capabilities, T, rec)
        iid = shape.interaction_of_probe(dg, T, p)
        legs = shape.legs_of(dg, iid)
        rec.write("legs.json", legs)
        ch.check("legs.payloads", "both legs carry a payload", "both", "both" if legs["request"]["payload_hash"] and legs["response"]["payload_hash"] else "not both",
                 ok=bool(legs["request"]["payload_hash"] and legs["response"]["payload_hash"]))
        record = shape.current_records(dg, T)[iid]
        rec.write("record.json", record)
        summary = record["summary"] or {}
        req, resp = summary.get("request") or {}, summary.get("response") or {}
        ch.check("request.findings", "request leg findings (benign arguments)", 0, req.get("finding_count"))
        ch.check("response.pii", "response leg classifies PII", "PII in tags, findings > 0",
                 {"findings": resp.get("finding_count"), "tags": resp.get("regulatory_tags")},
                 ok=(resp.get("finding_count") or 0) > 0 and "PII" in (resp.get("regulatory_tags") or []))
        pii_rules = [x for x in record["rules"] if x == "DG-001" or (x.startswith("E2E-") and "PII" in x)]
        ch.check("record.no_external_pii_rule", "external-sharing PII rules fired (#271: response-only PII is not sent)",
                 [], pii_rules, comment="scored as sent to the external destination" if pii_rules else None)
        ch.check("record.verdict", "record verdict", "none/allow", _verdict(record))
        rec.write("risk.json", shape.assert_risk_shape(dg, T, capabilities, entity_set="record", checks=ch))


# --- L12 (opt-in, destructive) -------------------------------------------------


def test_L12_opa_outage_holds_the_cursor(env, kube, dg, capabilities, catalog, run_record, state):
    """L12 — OPA unreachable: the leg stream holds, nothing is skipped,
    catch-up writes exactly one version (#158 acceptance). Opt-in with
    E2E_DESTRUCTIVE=1: it scales the OPA deployment to zero, a shared
    component of whatever cluster the tier runs against.

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
    rec = run_record.scenario("L12")
    with Checks("L12", rec) as ch:
        before = settle.snapshot_versions(dg, T)["records"]
        kube.run(["scale", "deploy/opa", "--replicas=0"], ns=env.dg_ns)
        try:
            kube.run(["rollout", "status", "deploy/opa", "--timeout=120s"], ns=env.dg_ns, timeout=150)
            p = mint_span_id()
            r = probe(kube, trace_id=T, parent_id=p, label="card_ext_outage", payload=card_pii(),
                      host=EXTERNAL_HOST)
            state["entries"] += 1
            state["probes"].add(p)
            ch.check("probe.outcome", "the probe was answered while OPA was down", "ok", r.outcome)
            held = []
            for _ in range(3):
                time.sleep(6)
                held.append(settle.pending(dg, capabilities).get("leg_ready", 0))
            rec.write("held_polls.json", held)
            ch.check("outage.backlog_held", "leg-ready backlog across three polls (held, not skipped)", "> 0 each",
                     held, ok=all(h > 0 for h in held))
            iid_rows = dg.q("""
                SELECT isp.interaction_id FROM spans s
                JOIN interaction_spans isp ON isp.trace_id = s.trace_id AND isp.span_id = s.span_id
                WHERE s.trace_id = %s AND s.parent_id = %s AND isp.role = 'anchor'""", (T, p))
            ch.check("outage.derived", "the probe's interaction derived while OPA was down", "present",
                     "present" if iid_rows else "MISSING", ok=bool(iid_rows))
            iid = iid_rows[0][0]
            (n,) = dg.one("SELECT count(*) FROM interaction_risk_records WHERE interaction_id = %s", (iid,))
            ch.check("outage.no_record", "risk records written with OPA unreachable", 0, n)
            if capabilities.health:
                import httpx
                h = httpx.get(f"{env.dg_api}/risk/health", timeout=10).json()
                rec.write("health_during.json", h)
                ch.record("outage.health", "/risk/health while OPA was down", h)
        finally:
            kube.run(["scale", "deploy/opa", "--replicas=1"], ns=env.dg_ns)
            kube.run(["rollout", "status", "deploy/opa", "--timeout=180s"], ns=env.dg_ns, timeout=200)
        _settle(ch, env, dg, capabilities, T, rec)
        hist = shape.record_history(dg, iid)
        rec.write("history.json", hist)
        ch.check("catchup.versions", "versions after catch-up", 1, len(hist))
        ch.check("catchup.verdict", "verdict after catch-up", "critical/block",
                 f"{hist[0]['risk_level']}/{hist[0]['enforcement_type']}" if hist else "no record")
        after = settle.snapshot_versions(dg, T)["records"]
        bumped = {i: (before[i], after[i]) for i in before if after.get(i) != before[i]}
        ch.check("catchup.no_duplicates", "interactions re-versioned by the catch-up without new evidence", {}, bumped)
        rec.write("risk.json", shape.assert_risk_shape(dg, T, capabilities, entity_set="record", checks=ch))


# --- L13 (needs the alerts processor) ------------------------------------------


def test_L13_alert_supersession(env, kube, dg, capabilities, catalog, run_record, state):
    """L13 — alert lifecycle over a fresh trace (#104 acceptance). Skips
    unless the deployed branch runs the alerts processor. A fresh trace
    because the assertions are a sequence: T is already critical, so a
    medium head superseded by critical could never be observed there.

    Drive: PI-only→internal (trace medium) → settle; card→external (trace
    critical) → settle; the same card probe again → settle. Expect after
    step 1: one open alert at medium; after step 2: one open alert at
    critical, the medium alert's superseded_by pointing at it and its
    status untouched; after step 3: still one head, duplicate_count 1.
    """
    if not capabilities.alerts:
        pytest.skip("no alerts processor on the deployed branch (capabilities.alerts=False)")
    rec = run_record.scenario("L13")
    with Checks("L13", rec) as ch:
        T5 = mint_trace_id()
        state["T5"] = T5
        state["traces"].add(T5)

        probe(kube, trace_id=T5, parent_id=mint_span_id(), label="pi_int", payload=pi_only())
        _settle(ch, env, dg, capabilities, T5, rec, tag="-1")
        a1 = shape.trace_alerts(dg, T5)
        rec.write("alerts-1.json", a1)
        heads = [a for a in a1 if a["superseded_by"] is None]
        ch.check("step1.heads", "after the PI probe: open alert heads", 1, len(heads))
        ch.check("step1.level", "after the PI probe: head level", "medium", heads[0]["level"] if heads else None)
        first_status = heads[0]["status"]

        probe(kube, trace_id=T5, parent_id=mint_span_id(), label="card_ext", payload=card_pii(),
              host=EXTERNAL_HOST)
        _settle(ch, env, dg, capabilities, T5, rec, tag="-2")
        a2 = shape.trace_alerts(dg, T5)
        rec.write("alerts-2.json", a2)
        heads = [a for a in a2 if a["superseded_by"] is None]
        ch.check("step2.heads", "after the card probe: open alert heads", 1, len(heads))
        ch.check("step2.level", "after the card probe: head level", "critical", heads[0]["level"] if heads else None)
        prior = next(a for a in a2 if a["id"] == a1[0]["id"])
        ch.check("step2.superseded", "the medium alert's superseded_by points at the critical head",
                 heads[0]["id"] if heads else None, prior["superseded_by"])
        ch.check("step2.prior_status", "the superseded alert's status untouched", first_status, prior["status"])

        probe(kube, trace_id=T5, parent_id=mint_span_id(), label="card_ext_again", payload=card_pii(),
              host=EXTERNAL_HOST)
        _settle(ch, env, dg, capabilities, T5, rec, tag="-3")
        a3 = shape.trace_alerts(dg, T5)
        rec.write("alerts-3.json", a3)
        heads = [a for a in a3 if a["superseded_by"] is None]
        ch.check("step3.heads", "after the repeat: open alert heads", 1, len(heads))
        ch.check("step3.duplicates", "after the repeat: duplicate count on the head", 1, heads[0]["duplicates"] if heads else None)
        rec.write("risk.json", shape.assert_risk_shape(dg, T5, capabilities, alert_heads=1, checks=ch))


# =============================================================================
# C. The app's behaviour
# =============================================================================


# --- L14 (#258) ----------------------------------------------------------------


def test_L14_rogue_partner_notification(env, kube, dg, capabilities, catalog, fleet, run_record, state):
    """L14 — #258, the risk demo: the booking agent's instructions decide what
    crosses to the travel partner. Runs only when booking-agent carries
    ROSSOCTL_VARIANT (rogue | fixed); asserts per variant.

    Drive: one scripted turn (the request also carries the guest's email
    and phone; travel-advisor's delegation forwards neither, so the booking
    agent only ever holds the name). Expect, rogue: a notify-partner →
    api.travel-partner.example exchange whose arguments carry the guest's
    name, whose request leg classifies PII and whose record is
    critical/block [DG-001]. Expect, fixed: the same exchange with
    `dates` and `note` only — no PII, none/allow. Whether the model makes
    the call at all is its own choice on every turn (measured on the app
    commit: rogue about 2 of 5 live turns, fixed 5 of 5); a turn without
    the call xfails and asserts nothing about the verdict. The booking
    statuses are recorded first: the instructions trigger on `confirmed`.
    The exchange may sit in the turn's trace or, under a pod-lifetime MCP
    session (a declared gap), in its own trace: it is found by callee and
    time window, and where it landed is recorded.
    """
    dep = kube.json(["get", "deploy", "booking-agent"], ns=env.app_ns)
    variant = next((e.get("value") for c in dep["spec"]["template"]["spec"]["containers"]
                    for e in c.get("env", []) if e["name"] == "ROSSOCTL_VARIANT"), None)
    if variant not in ("rogue", "fixed"):
        pytest.skip("booking-agent runs the stock instructions (ROSSOCTL_VARIANT unset)")
    rec = run_record.scenario("L14")
    with Checks("L14", rec) as ch:
        T, p = mint_trace_id(), mint_span_id()
        state["traces"].add(T)
        since = _dt.datetime.now(_dt.timezone.utc)
        reply = turn(kube, trace_id=T, parent_id=p, text=driver.rogue_turn())
        rec.write("reply.json", reply.__dict__)
        _turn_answered(ch, reply)
        _settle(ch, env, dg, capabilities, T, rec)
        statuses = [(json.loads(r[0] or "{}") or {}).get("status") for r in dg.q(
            "SELECT attributes->>'output.value' FROM spans WHERE trace_id = %s "
            "AND name = 'booking-agent mcp create_booking response' ORDER BY started_at", (T,))]
        ch.record("booking.statuses", "create_booking statuses in the turn (the notification triggers on confirmed)",
                  statuses)
        rows = dg.q("""
            SELECT i.trace_id, i.id, r.risk_level, r.enforcement_type, r.triggered_rule_ids, r.classification_summary,
                   a.attributes->>'input.value'
            FROM interactions i JOIN entities e ON e.id = i.callee_entity_id
            JOIN interaction_spans s ON s.interaction_id = i.id AND s.role = 'anchor'
            JOIN spans a ON a.span_id = s.span_id AND a.trace_id = i.trace_id
            LEFT JOIN LATERAL (SELECT * FROM interaction_risk_records x WHERE x.interaction_id = i.id ORDER BY version DESC LIMIT 1) r ON TRUE
            WHERE e.natural_key LIKE %s AND a.started_at >= %s AND a.attributes->>'lineage.self.id' = 'notify-partner'
            ORDER BY a.started_at""", ("%api.travel-partner.example%", since))
        def _fields(v):     # the sidecar keeps the JSON-RPC arguments as the tool span's input
            try:
                return sorted(json.loads(v or "{}"))
            except ValueError:
                return None
        found = [{"trace": r[0], "in_turn_trace": r[0] == T, "interaction": r[1], "risk": r[2], "enforcement": r[3],
                  "rules": sorted(set(r[4] or []) - {"0000"}) if isinstance(r[4], list) else r[4],
                  "summary": r[5] if isinstance(r[5], dict) else json.loads(r[5] or "{}"),
                  "fields": _fields(r[6]), "input_head": (r[6] or "")[:200]} for r in rows]
        rec.write("partner_exchanges.json", {"variant": variant, "exchanges": found})
        ch.record("variant", "booking-agent variant", variant)
        ch.record("partner.exchanges", "notify-partner → partner exchanges in the window", len(found),
                  comment=("in the turn's trace" if found and found[-1]["in_turn_trace"] else "in another trace") if found else None)
        if not found:
            pytest.xfail(f"the booking agent did not call notify_partner this turn ({variant}; booking statuses {statuses}): "
                         "the model's choice, measured at about 2 of 5 live turns for rogue and 5 of 5 for fixed — "
                         "recorded, not a platform failure")
        x = found[-1]
        req = (x["summary"] or {}).get("request") or {}
        tags = set(req.get("regulatory_tags") or [])
        ch.record("partner.fields", "the arguments that reached the partner", x["fields"])
        if variant == "rogue":
            ch.check("partner.name", "rogue: the guest's name is among the arguments", "guest_name sent",
                     x["fields"], ok="guest_name" in (x["fields"] or []))
            ch.check("partner.pii", "rogue: the partner request carries PII", "PII in tags", sorted(tags), ok="PII" in tags)
            ch.check("partner.verdict", "rogue: the partner record verdict", "critical/block", f"{x['risk']}/{x['enforcement']}")
            ch.check("partner.rule", "rogue: DG-001 fired", "DG-001 in rules", x["rules"], ok="DG-001" in (x["rules"] or []))
        else:
            ch.check("partner.fields_only", "fixed: dates and the note, nothing else", "non-empty subset of [dates, note]",
                     x["fields"], ok=bool(x["fields"]) and set(x["fields"]) <= {"dates", "note"})
            ch.check("partner.pii", "fixed: no personal data reached the partner", "no PII in tags", sorted(tags), ok="PII" not in tags)
            ch.check("partner.verdict", "fixed: the partner record verdict", "none/allow", f"{x['risk']}/{x['enforcement']}")


# --- L15 (runs last) -----------------------------------------------------------


def test_L15_cut_stream_is_not_ok(env, kube, dg, capabilities, catalog, fleet, run_record, state):
    """L15 — a caller that walks away mid-turn.

    Drive: a full turn sent to travel-advisor whose client closes the
    connection after 20 s without reading a byte; the upstream keeps working
    for minutes. Expect: the entry exchange's response span does not read
    ``ok`` (abandoned, or error with no 2xx); its response leg carries
    error=true; the record's response summary has no payload. The rest of
    the turn still derives under the same root, and the app's ledger joins
    it (when the app runs one). Runs last: the abandoned turn finishes in
    the background and would pollute later windows.
    """
    rec = run_record.scenario("L15")
    with Checks("L15", rec, raise_at_exit=True) as ch:
        T, p = mint_trace_id(), mint_span_id()
        state["traces"].add(T)
        cut = turn_cut(kube, trace_id=T, parent_id=p, hold=20.0)
        rec.write("cut.json", cut)
        ch.check("cut.bytes_before", "bytes the client read before closing (the turn must still be running)", 0, cut["bytes_before_cut"],
                 comment=f"held {cut['held']} s")
        _settle(ch, env, dg, capabilities, T, rec)
        iid = shape.interaction_of_probe(dg, T, p)
        span = shape.response_span_of(dg, T, iid)
        legs = shape.legs_of(dg, iid)
        rec.write("response_span.json", span)
        rec.write("legs.json", legs)
        ch.check("span.present", "the entry exchange has a response span after settling", "present",
                 "present" if span else "MISSING", ok=span is not None)
        ok_read = span is not None and (span["outcome"] == "ok" or (span["status"] and 200 <= int(span["status"]) < 300))
        ch.check("span.not_ok", "the cut stream does not read ok", "not ok (abandoned, or error with no 2xx)",
                 f"outcome {span['outcome']!r} status {span['status']!r}" if span else "no span", ok=span is not None and not ok_read,
                 comment="a cut stream reads ok" if ok_read else None)
        ch.check("legs.response.error", "the entry response leg error flag", True, legs["response"]["error"])
        record = shape.current_records(dg, T).get(iid)
        rec.write("record.json", record)
        ch.check("record.response_summary", "the entry record's response summary", {"payload": None},
                 (record["summary"] or {}).get("response") if record else "no record")
        _forest(ch, dg, rec, T, state, expect_entries=1)
        if capabilities.ledger:
            _ledger_join(ch, env, kube, dg, fleet, rec, T, state)
        rec.write("risk.json", shape.assert_risk_shape(dg, T, capabilities, entity_set="record", checks=ch))
