"""The controlled app: lineage_lab (E2E_APP=lineage_lab). Each scenario is a
row of tests/live/catalog/scenarios.yaml: the driver sends the plan, the
expected forest is computed from it (lab_expect), and the tables must EQUAL
it — calls, parents, entities, lifecycle presence, declared absences — then
the audit holds with no gaps, and the per-leg classifications and decisions
of the egress legs match the row's prediction. The risk predictions assume
the SHIPPED catalog: external + PII ⇒ DG-001 (critical/block), external +
RESTRICTED ⇒ DG-004; internal ⇒ allow.

Every property is a row of the scenario's table (tests/live/checks.py);
``_run`` records the frame (reply, equality, audit, risk shape) and returns
the recorder for the row's own per-leg rows.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.live import audit, lab_expect, settle, shape
from tests.live.checks import Checks
from tests.live.driver import mint_span_id, mint_trace_id, turn

# A live session for the other app skips this module; outside a live session
# (no E2E_KUBE_CONTEXT) it is collected and deselected by its marker, so the
# default suite's summary carries no skip from here.
if os.environ.get("E2E_KUBE_CONTEXT") and os.environ.get("E2E_APP") != "lineage_lab":
    pytest.skip("lineage_lab scenarios; run with E2E_APP=lineage_lab", allow_module_level=True)

PARTNER_URL = "http://lab-partner:8000/mcp"
EXTERNAL = "api.partner.example:8000"
INTERNAL = "lab-partner:8000"
LAB_A = "http://lab-a:8080/"


def read(key, mark, fields=None):
    args = {"op": "read", "key": key}
    if fields:
        args["fields"] = fields
    return {"op": "mcp", "tool": "lab_store", "args": args, "mark": mark}


def transform(tool, op, payload, mark):
    return {"op": "mcp", "tool": tool, "args": {"op": op, "payload": payload}, "mark": mark}


def send(payload, mark, host=EXTERNAL):
    return {"op": "http", "url": PARTNER_URL, "host": host, "args": {"payload": payload}, "mark": mark}


def _run(env, kube, dg, capabilities, fleet, attach, run_record, state, name, plan):
    rec = run_record.scenario(name)
    ch = Checks(name, rec)
    T, p = mint_trace_id(), mint_span_id()
    state.setdefault("traces", set()).add(T)
    reply = turn(kube, trace_id=T, parent_id=p, text=json.dumps(plan), url=LAB_A)
    rec.write("plan.json", plan)
    rec.write("reply.json", reply.__dict__)
    ch.check("turn.answer", "lab-a answered", "non-empty", f"{len(reply.answer.strip())} chars", ok=bool(reply.answer.strip()))
    values = json.loads(reply.answer).get("values", {})
    rec.write("values.json", values)
    ch.check("turn.no_error", "the plan ran without error", "no error key", "error" if "error" in values else "no error key",
             ok="error" not in values, comment=str(values.get("error"))[:200] if "error" in values else None)
    bad = [k for k, v in values.items() if isinstance(v, str) and v.startswith("Error:")]
    ch.check("turn.no_step_error", "no step value is an error", [], bad,
             comment=json.dumps({k: values[k][:120] for k in bad}) if bad else None)
    s = settle.wait_drained(dg, T, capabilities, timeout=env.settle_timeout, quiet_seconds=env.quiet_seconds)
    rec.write("settle.json", s.__dict__)
    ch.record("settle", "settled", f"{s.elapsed:.1f} s · {s.polls} polls · {s.spans} spans")
    # 1. the forest equals the plan's
    exp = lab_expect.expected_forest(plan, fleet)
    obs = lab_expect.observed(dg, T, fleet)
    d = lab_expect.diff(exp, obs)
    rec.write("forest_diff.json", {**d, "expected_calls": {str(k): v for k, v in exp.calls.items()},
                                   "observed_calls": {str(k): v for k, v in obs["calls"].items()},
                                   "expected_depth": exp.depth, "absent": exp.absent})
    ch.record("forest.expected_calls", "calls the plan predicts (with multiplicity)", len(exp.calls),
              comment=f"depth {exp.depth}; absent (pod_lifetime) {len(exp.absent)}")
    ch.check("forest.missing_calls", "predicted calls missing from the tables", {}, d["missing_calls"])
    ch.check("forest.extra_calls", "calls in the tables the plan did not predict", {}, d["extra_calls"])
    ch.check("forest.missing_lifecycle", "predicted lifecycle exchanges missing", [], d["missing_lifecycle"])
    ch.check("forest.extra_lifecycle", "lifecycle exchanges not predicted", [], d["extra_lifecycle"])
    ch.check("forest.wrong_parent", "calls under the wrong parent", {}, d["wrong_parent"])
    ch.check("forest.entities", "entity set = the plan's", "equal",
             "equal" if not d["missing_entities"] and not d["extra_entities"] else {"missing": d["missing_entities"], "extra": d["extra_entities"]},
             ok=not d["missing_entities"] and not d["extra_entities"])
    ch.check("forest.equal", "the forest EQUALS the plan's", True, d["equal"])
    for caller, callee, tool in exp.absent:
        ch.check(f"forest.absent.{caller}>{callee}/{tool}", f"a pod_lifetime call stays out of the turn: {caller}→{callee}/{tool}",
                 "absent", "present" if (caller, callee, "mcp", tool) in obs["calls"] else "absent",
                 ok=(caller, callee, "mcp", tool) not in obs["calls"])
    # 2. the audit, with no gaps to hide behind
    au, extra = audit.run(dg, T, fleet, known=state["traces"], quiet_seconds=env.quiet_seconds, attach=attach,
                          quiet_reread=False)
    rec.write("audit.json", au.as_dict())
    rec.write("audit.md", au.markdown(), raw=True)
    rec.write("census.json", extra)
    # A plan with pod_lifetime steps PREDICTS its own strays: the census may
    # list traces whose workloads are exactly those calls' (caller, tool);
    # anything else outside the turn is a real escape.
    failed = list(au.failed)
    if exp.absent:
        predicted = {(c, t) for c, t, _ in exp.absent}
        c5 = next((c for c in failed if c.id == "C5"), None)

        def is_predicted(row) -> bool:
            selfs = set((row.get("self_ids") or "").split(",")) - {""}
            peers = {p.split(".")[0].removesuffix("-mcp") for p in (row.get("peers") or "").split(",") if p}
            protos = set((row.get("protocols") or "").split(",")) - {""}
            return protos <= {"mcp"} and any(selfs <= {c, t} and peers <= {t} for c, t in predicted)
        if c5 is not None:
            outside = (c5.detail or {}).get("outside") or []
            if outside and all(is_predicted(r) for r in outside):
                failed.remove(c5)
                c5.ok = True  # the predicted strays are exactly the census's "outside"
                rec.write("predicted_strays.json", outside)
                ch.record("audit.C5.predicted_strays", "strays the plan predicted (pod_lifetime calls)", len(outside))
    ch.absorb_audit(au)
    ch.check("audit.no_known_gaps", "the lab declares no gaps: KNOWN checks", [], [c.id for c in au.known])
    assert not failed and not au.known, "\n" + au.markdown(failed_only=True)
    # 3. risk shape and the records, by callee. The trace record's entity set
    # is a KNOWN row (a record keeps the callee it was computed with after a
    # re-key, filed separately), so it hides none of the per-leg rows below.
    risk = shape.assert_risk_shape(dg, T, capabilities, entity_set="record", checks=ch)
    rec.write("risk.json", risk)
    legs = _legs_by_callee(dg, T)
    rec.write("legs_by_callee.json", legs)
    ch.record("legs", "interactions by callee with verdicts",
              [f"{leg['callee']}{'/' + leg['tool'] if leg.get('tool') else ''} {leg['risk']}/{leg['enforcement']} {leg['rules']}"
               for leg in legs if leg.get("risk")])
    return T, values, legs, ch


def _legs_by_callee(dg, trace_id):
    """Per interaction: callee name, request/response classification summary and verdict."""
    from tests.live.fleet import name_of
    rows = dg.q("""
        SELECT i.id, ee.kind::text, ee.natural_key, ce.natural_key, r.risk_level, r.enforcement_type,
               r.triggered_rule_ids, r.classification_summary,
               a.attributes->>'mcp.method', a.attributes->>'mcp.tool', a.attributes->>'lineage.protocol'
        FROM interactions i JOIN entities ee ON ee.id = i.callee_entity_id JOIN entities ce ON ce.id = i.caller_entity_id
        JOIN interaction_spans s ON s.interaction_id = i.id AND s.role = 'anchor'
        JOIN spans a ON a.span_id = s.span_id AND a.trace_id = i.trace_id
        LEFT JOIN LATERAL (SELECT * FROM interaction_risk_records x WHERE x.interaction_id = i.id
                           ORDER BY version DESC LIMIT 1) r ON TRUE
        WHERE i.trace_id = %s""", (trace_id,))
    out = []
    for iid, ek, ekey, ckey, lvl, enf, rules, summ, mm, mt, proto in rows:
        rules = rules if isinstance(rules, list) else json.loads(rules or "[]")
        summ = summ if isinstance(summ, dict) else json.loads(summ or "{}")
        out.append({"id": iid, "callee": name_of(ek, ekey), "caller": ckey, "risk": lvl, "enforcement": enf,
                    "rules": sorted(set(rules) - {"0000"}), "request": summ.get("request"), "response": summ.get("response"),
                    "protocol": proto, "mcp_method": mm, "tool": mt})
    return out


def _calls(legs, callee):
    """The tools/call (or non-mcp) interactions to *callee*: a lifecycle exchange
    (initialize, tools/list) is not the hop under test."""
    return [leg for leg in legs if leg["callee"] == callee.split(":")[0]
            and (leg["protocol"] != "mcp" or leg["mcp_method"] == "tools/call")]


def _egress(legs, host):
    return _calls(legs, host)


def _tags(summary):
    return sorted((summary or {}).get("regulatory_tags") or [])


def _verdict(leg) -> str:
    return f"{leg['risk']}/{leg['enforcement']}"


# --- R2 / R3 ------------------------------------------------------------------------------------


def test_R2_forward_pii_to_external_partner(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R2 — PII read from the store leaves to the external partner unchanged.
    Expect: forest = plan; the partner request leg carries PII at RESTRICTED;
    the partner interaction is critical/block with DG-001 (and DG-004)."""
    # the record is handed to lab-b through args: a sub-plan's marks are the peer's own
    plan = {"mark": "R2", "steps": [read("alice", "rec"),
                                    {"op": "a2a", "agent": "lab-b", "args": {"rec": "$rec"},
                                     "plan": {"steps": [send("$args.rec", "sent")]}, "mark": "b"}]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R2", plan)
    eg = _egress(legs, EXTERNAL)
    ch.check("partner.calls", "partner calls", 1, len(eg))
    ch.check("partner.tags", "partner request leg tags include PII", "PII", _tags(eg[0]["request"]), ok="PII" in _tags(eg[0]["request"]))
    ch.check("partner.level", "partner request leg level", "RESTRICTED", eg[0]["request"]["sensitivity_level"])
    ch.check("partner.verdict", "partner verdict", "critical/block", _verdict(eg[0]))
    ch.check("partner.rule", "DG-001 fired", "DG-001", eg[0]["rules"], ok="DG-001" in eg[0]["rules"])
    ch.finish()


def test_R3_forward_pii_to_internal_partner(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R3 — the same flow to the partner by its internal name: same classification, allowed.
    Precondition: the record classifies above INTERNAL (alice's SSN ⇒ RESTRICTED), so the
    test catalog's INTERNAL-level rules (E2E-INT-DATA, E2E-PI-INT) do not fire either."""
    plan = {"mark": "R3", "steps": [read("alice", "rec"),
                                    {"op": "a2a", "agent": "lab-b", "args": {"rec": "$rec"},
                                     "plan": {"steps": [send("$args.rec", "sent", host=INTERNAL)]}, "mark": "b"}]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R3", plan)
    eg = _egress(legs, INTERNAL)
    ch.check("partner.calls", "partner calls", 1, len(eg))
    ch.check("partner.tags", "partner request leg tags include PII", "PII", _tags(eg[0]["request"]), ok="PII" in _tags(eg[0]["request"]))
    ch.check("partner.verdict", "partner verdict (internal)", "none/allow", _verdict(eg[0]))
    ch.check("partner.rules", "real rules fired", [], eg[0]["rules"])
    ch.finish()


# --- R4 … R8: the transformations ----------------------------------------------------------------


def test_R4_annotate_keeps_it_risky(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R4 — annotate: PII read from the store leaves with a note added; still PII, still blocked."""
    plan = {"mark": "R4", "steps": [read("alice", "rec"), transform("lab_tool_x", "annotate", "$rec", "t"),
                                    send("$t", "sent")]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R4", plan)
    eg = _egress(legs, EXTERNAL)[0]
    ch.check("partner.tags", "partner request leg tags include PII", "PII", _tags(eg["request"]), ok="PII" in _tags(eg["request"]))
    ch.check("partner.verdict", "partner verdict", "critical/block", _verdict(eg))
    tool = _calls(legs, "lab-tool-x")
    ch.check("tool.calls", "lab-tool-x tools/call hops", "≥ 1", len(tool), ok=bool(tool))
    ch.check("tool.request_pii", "the tool request carries PII", "PII", _tags(tool[0]["request"]), ok="PII" in _tags(tool[0]["request"]))
    ch.check("tool.response_pii", "the tool response carries PII", "PII", _tags(tool[0]["response"]), ok="PII" in _tags(tool[0]["response"]))
    ch.finish()


def test_R5_combine_two_harmless_halves_into_an_ssn(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R5 — two non-risky payloads become one risky payload. The halves are
    read separately; the tool joins them; the SSN leaves. Expect: neither
    half classifies as PII; the tool's response and the partner request do;
    the partner interaction is critical/block."""
    plan = {"mark": "R5", "steps": [read("alice", "a", fields=["name", "ssn_prefix"]), read("alice", "b", fields=["ssn_suffix"]),
                                    transform("lab_tool_x", "combine", {"a": "$a", "b": "$b"}, "t"), send("$t", "sent")]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R5", plan)
    ch.check("app.joined_ssn", "the app joined the halves into the SSN", "123-45-6789", values["t"].get("ssn"))
    halves = _calls(legs, "lab-store")
    ch.check("halves.no_ssn", "neither half's response classifies as SSN", [], [h["id"] for h in halves if "SSN" in str(h["response"])])
    eg = _egress(legs, EXTERNAL)[0]
    ch.check("partner.tags", "partner request leg tags include PII", "PII", _tags(eg["request"]), ok="PII" in _tags(eg["request"]))
    ch.check("partner.verdict", "partner verdict", "critical/block", _verdict(eg))
    ch.finish()


def test_R6_split_a_risky_record_into_two_harmless_parts(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R6 — one risky payload becomes two non-risky payloads, each sent externally.
    Today's per-leg judgement: each part alone is allowed. (#272's column:
    the SSN's label must reach both parts.)"""
    plan = {"mark": "R6", "steps": [read("alice", "rec"), transform("lab_tool_x", "split", "$rec", "t"),
                                    send("$t.parts", "sent_both"),
                                    {"op": "set", "value": "$t", "mark": "parts"}]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R6", plan)
    parts = values["parts"]["parts"]
    ch.check("app.parts", "the app split the record into two parts, the SSN only in the suffix part", "2 parts, [no ssn, {ssn_suffix: 6789}]",
             parts, ok=len(parts) == 2 and "ssn" not in parts[0] and parts[1] == {"ssn_suffix": "6789"})
    eg = _egress(legs, EXTERNAL)
    ch.check("partner.calls", "partner calls", 1, len(eg))
    run_record.scenario("R6").write("egress_record.json", eg[0])
    ch.record("partner.verdict", "what the classifier made of the two parts together (the label column is empty today)",
              f"{_verdict(eg[0])} {eg[0]['rules']} tags {_tags(eg[0]['request'])}")
    ch.finish()


def test_R7_paraphrase_keeps_the_ssn(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R7 — the SSN re-formatted ('social security no. 123 45 6789'). Prediction:
    still PII; a miss is a finding about the recogniser, recorded either way."""
    plan = {"mark": "R7", "steps": [read("alice", "rec"), transform("lab_tool_x", "paraphrase_keep", "$rec", "t"), send("$t", "sent")]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R7", plan)
    eg = _egress(legs, EXTERNAL)[0]
    run_record.scenario("R7").write("egress_record.json", eg)
    ch.check("partner.classified", "the partner request leg was classified", "finding_count present",
             eg["request"].get("finding_count") if eg["request"] else None, ok=bool(eg["request"]) and eg["request"].get("finding_count") is not None)
    if "PII" not in _tags(eg["request"]):
        ch.check("partner.tags", "the re-formatted SSN is recognised as PII", "PII", _tags(eg["request"]), ok=False,
                 known="classifier finding: the spaced SSN is not recognised")
        pytest.xfail(f"the re-formatted SSN was not recognised as PII: {eg['request']} — a classifier finding, recorded")
    ch.check("partner.tags", "the re-formatted SSN is recognised as PII", "PII", _tags(eg["request"]), ok=True)
    ch.check("partner.verdict", "partner verdict", "critical/block", _verdict(eg))
    ch.finish()


def test_R8_redaction_makes_it_harmless(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R8 — the record redacted to the SSN's last four leaves externally: no PII, allowed."""
    plan = {"mark": "R8", "steps": [read("alice", "rec"), transform("lab_tool_x", "redact", "$rec", "t"), send("$t", "sent")]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R8", plan)
    ch.check("app.redacted", "the app redacted the record", {"public_note": "prefers aisle seats", "ssn_last4": "6789", "redacted": True}, values["t"])
    eg = _egress(legs, EXTERNAL)[0]
    run_record.scenario("R8").write("egress_record.json", eg)
    ch.check("partner.no_pii", "the redacted record carries no PII", "no PII", _tags(eg["request"]), ok="PII" not in _tags(eg["request"]))
    if (eg["risk"], eg["enforcement"]) != ("none", "allow"):
        ch.check("partner.verdict", "the redacted record is allowed", "none/allow", f"{_verdict(eg)} {eg['rules']}", ok=False,
                 known="classifier finding: the last four digits read as PCI",
                 comment=f"tags {_tags(eg['request'])} on {{public_note, ssn_last4, redacted}}")
        pytest.xfail(f"the redacted record still decides {eg['risk']}/{eg['enforcement']} {eg['rules']}: "
                     f"the classifier tags {_tags(eg['request'])} on {{public_note, ssn_last4, redacted}} — "
                     f"a classifier finding (last four digits read as PCI), recorded")
    ch.check("partner.verdict", "the redacted record is allowed", "none/allow", _verdict(eg))
    ch.finish()


def test_R9_llm_summary_leaves(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R9 — the record goes to the model, the model's summary leaves externally.
    The llm request leg carries PII; what leaves is measured."""
    plan = {"mark": "R9", "steps": [read("alice", "rec"),
                                    {"op": "llm", "prompt": "Summarize this customer record in one sentence, keeping every identifier exactly as written: {payload}",
                                     "args": {"payload": "$rec"}, "mark": "sum"},
                                    send({"summary": "$sum"}, "sent")]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R9", plan)
    llm = [leg for leg in legs if leg["protocol"] == "inference"]
    ch.check("llm.calls", "inference hops", "≥ 1", len(llm), ok=bool(llm))
    ch.check("llm.request_pii", "the inference request carries PII", "PII", _tags(llm[0]["request"]) if llm else [], ok=bool(llm) and "PII" in _tags(llm[0]["request"]))
    eg = _egress(legs, EXTERNAL)[0]
    run_record.scenario("R9").write("egress_record.json", {"summary": values["sum"], "record": eg})
    repeated = "123-45-6789" in values["sum"]
    ch.record("summary.repeats_ssn", "the model's summary repeats the SSN verbatim", repeated)
    pii_out = "PII" in _tags(eg["request"])
    ch.check("partner.verdict_follows_classification", "what leaves is judged by what the classifier saw: blocked iff PII",
             f"blocked iff PII ({'PII' if pii_out else 'no PII'})", _verdict(eg),
             ok=((eg["risk"], eg["enforcement"]) == ("critical", "block")) == pii_out)
    ch.finish()


def test_R10_fan_in_two_agents_one_tool(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R10 — lab-b and lab-c, in parallel, each read a record and forward it to
    lab-tool-y while both are open; lab-a then sends both externally. Expect: the
    two lab-tool-y interactions are attributed to their own callers (forest
    equality says so), two egress interactions, both critical/block."""
    sub = lambda key, m: {"steps": [read(key, m), transform("lab_tool_y", "forward", f"${m}", m + "t")]}
    plan = {"mark": "R10", "steps": [
        {"op": "parallel", "branches": [[{"op": "a2a", "agent": "lab-b", "plan": sub("alice", "ra"), "mark": "b"}],
                                        [{"op": "a2a", "agent": "lab-c", "plan": sub("bob", "rb"), "mark": "c"}]]},
        send("$b.values", "sent_b"), send("$c.values", "sent_c")]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R10", plan)
    eg = _egress(legs, EXTERNAL)
    ch.check("partner.calls", "partner calls", 2, len(eg))
    ch.check("partner.verdicts", "both partner calls blocked", ["critical/block", "critical/block"], [_verdict(e) for e in eg])
    ch.finish()


def test_R11_pod_lifetime_session_is_its_own_trace(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R11 — the frameworks' failure mode as a switch: the same tool call with
    session=pod_lifetime must NOT appear in the turn's tree (it rides the
    startup context into its own trace), while a per_turn call does."""
    plan = {"mark": "R11", "steps": [read("public", "rec"),
                                     {"op": "mcp", "tool": "lab_tool_x", "args": {"op": "forward", "payload": "$rec"},
                                      "session": "pod_lifetime", "mark": "t"}]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R11", plan)
    ch.check("app.forwarded", "the tool forwarded the record (the call happened, outside the trace)", values["rec"], values["t"])
    ch.finish()
