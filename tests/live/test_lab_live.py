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

Label rows (the catalogue's ``expected_label_propagation``) run only when
the deployment carries label lineage (``capabilities.labels``): what the
derivation says reached the egress leg, recorded as ``labels.json``, and —
with ``capabilities.inherited_labels`` (leg-ready's RISK_INHERITED_LABELS=on)
— that the egress record carries those labels, or, off, that it carries
none. The exact finder is byte equality: a re-spaced, split or redacted value
is not the unit, and the cards say so.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.live import audit, lab_expect, settle, shape
from tests.live import labels as L
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
STORE = "lab-store"
TOOL_X = "lab-tool-x"
# alice's record as the store returns it, by unit: what an exact match can carry
RECORD_UNITS = [("DOB", "1987-03-14"), ("EMAIL", "alice.moreno@example.com"), ("PHONE", "+1 212 555 0147"),
                ("PN", "Alice Moreno"), ("SSN", "123-45-6789")]


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
                                   "expected_depth": exp.depth, "pod_lifetime": exp.pod_lifetime,
                                   "observed_lifecycle": sorted(map(str, obs["lifecycle"]))})
    ch.record("forest.expected_calls", "calls the plan predicts (with multiplicity)", len(exp.calls),
              comment=f"depth {exp.depth}; on a pod-lifetime session {len(exp.pod_lifetime)}")
    ch.check("forest.missing_calls", "predicted calls missing from the tables", {}, d["missing_calls"])
    ch.check("forest.extra_calls", "calls in the tables the plan did not predict", {}, d["extra_calls"])
    ch.check("forest.missing_lifecycle", "predicted lifecycle exchanges missing", [], d["missing_lifecycle"])
    ch.check("forest.extra_lifecycle", "lifecycle exchanges not predicted", [], d["extra_lifecycle"])
    ch.check("forest.wrong_parent", "calls under the wrong parent", {}, d["wrong_parent"])
    ch.check("forest.entities", "entity set = the plan's", "equal",
             "equal" if not d["missing_entities"] and not d["extra_entities"] else {"missing": d["missing_entities"], "extra": d["extra_entities"]},
             ok=not d["missing_entities"] and not d["extra_entities"])
    ch.check("forest.equal", "the forest EQUALS the plan's", True, d["equal"])
    # The switch: a call on a session the agent opened at startup rides the
    # turn since cortex #1333 (before it, the call sat in its own trace). A
    # kit that loses the bridge turns this row red, with the call missing
    # above and the census reporting the stray.
    for caller, callee, tool in exp.pod_lifetime:
        ch.check(f"forest.rides_turn.{caller}>{callee}/{tool}",
                 f"a pod-lifetime session's call derives under the turn: {caller}→{callee}/{tool}",
                 "under the turn", "under the turn" if (caller, callee, "mcp", tool) in obs["calls"] else "absent",
                 ok=(caller, callee, "mcp", tool) in obs["calls"],
                 comment=("lifecycle in the turn: " + ("yes" if (caller, callee) in obs["lifecycle"] else "no (ran at startup)"))
                 if (caller, callee, "mcp", tool) in obs["calls"] else "the call left the turn: a kit without the MCP context bridge?")
    # 2. the audit, with no gaps to hide behind
    au, extra = audit.run(dg, T, fleet, known=state["traces"], quiet_seconds=env.quiet_seconds, attach=attach,
                          quiet_reread=False)
    rec.write("audit.json", au.as_dict())
    rec.write("audit.md", au.markdown(), raw=True)
    rec.write("census.json", extra)
    failed = list(au.failed)
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


def _labels(ch, dg, capabilities, run_record, name, T, eg):
    """Read and record what label lineage derived for the trace; return the edges
    into the egress request leg, or ``None`` when label lineage is not deployed.
    Asserts the flag's half that every row shares: on, the egress record names
    the labels that reached it; off, it names none (the engine never read them)."""
    if not capabilities.labels:
        ch.record("labels", "label lineage", "not deployed")
        return None
    lab = L.read(dg, T)
    L.write(run_record, name, lab)
    edges = L.into(lab, eg["id"])
    row = L.leg_row(lab, eg["id"])
    ch.check("labels.egress_row", "label lineage derived the egress request leg", "a row", row, ok=row is not None)
    ch.record("labels.egress", "units into the egress request leg (type, value, kind, origin/leg, via)",
              sorted({(e["entity_type"], e["value"], e["kind"], f"{e['origin']}/{e['origin_leg']}",
                       f"{e['from']}/{e['from_leg']}") for e in edges}))
    ch.check("labels.whole_only", "the exact finder writes whole only", ["whole"] if edges else [],
             sorted({e["kind"] for e in edges}))
    ch.check("labels.recombined", "nothing recombines in one conversation's exact match", False,
             bool(row and row["recombined"]))
    inherited = (eg["request"] or {}).get("inherited")
    if capabilities.inherited_labels:
        expected = L.inherited_tags(edges)
        ch.check("labels.decision_carries", "the egress record carries the labels that reached it (flag on)",
                 expected, (inherited or {}).get("regulatory_tags", []))
    else:
        ch.check("labels.decision_off", "flag off: the egress record carries no inherited labels", None, inherited)
    return edges


def _record_reaches(ch, edges, origin=STORE, expected=RECORD_UNITS, id="labels.record_whole"):
    if edges is None:
        return
    reached = L.whole_from(edges, origin)
    ch.check(id, f"every unit of the record reaches the egress whole from {origin}'s response",
             expected, [u for u in expected if u in reached], comment=f"all from {origin}: {reached}")


# --- R2 / R3 ------------------------------------------------------------------------------------


def test_R2_forward_pii_to_external_partner(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R2 — PII read from the store leaves to the external partner unchanged.
    Expect: forest = plan; the partner request leg carries PII at RESTRICTED;
    the partner interaction is critical/block with DG-001 (and DG-004).
    Labels: every unit of the record (SSN 123-45-6789, PN Alice Moreno, EMAIL,
    PHONE +1 212 555 0147, DOB 1987-03-14) reaches the partner request leg whole,
    origin lab-store's response, carried by lab-b's inbound a2a request (where
    the classifier reads the suffix as a card code: the labels arrive anyway).
    Decision: unchanged, critical/block [DG-001, DG-004]; with inherited labels
    on, the record's request summary carries the inherited PII; off, nothing."""
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
    edges = _labels(ch, dg, capabilities, run_record, "R2", T, eg[0])
    _record_reaches(ch, edges)
    if edges is not None:
        ch.check("labels.via_lab_b", "the units arrive through lab-b's inbound request", ["lab-b/request"],
                 sorted({f"{e['from']}/{e['from_leg']}" for e in edges}))
    ch.finish()


def test_R3_forward_pii_to_internal_partner(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R3 — the same flow to the partner by its internal name: same classification, allowed.
    Precondition: the record classifies above INTERNAL (alice's SSN ⇒ RESTRICTED), so the
    test catalog's INTERNAL-level rules (E2E-INT-DATA, E2E-PI-INT) do not fire either.
    Labels: as R2 — the record's units reach the internal partner's request leg
    whole from lab-store's response. Decision: allowed either way; the inherited
    labels form one document-level item at RESTRICTED, so no INTERNAL-level rule
    fires on them either."""
    plan = {"mark": "R3", "steps": [read("alice", "rec"),
                                    {"op": "a2a", "agent": "lab-b", "args": {"rec": "$rec"},
                                     "plan": {"steps": [send("$args.rec", "sent", host=INTERNAL)]}, "mark": "b"}]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R3", plan)
    eg = _egress(legs, INTERNAL)
    ch.check("partner.calls", "partner calls", 1, len(eg))
    ch.check("partner.tags", "partner request leg tags include PII", "PII", _tags(eg[0]["request"]), ok="PII" in _tags(eg[0]["request"]))
    ch.check("partner.verdict", "partner verdict (internal)", "none/allow", _verdict(eg[0]))
    ch.check("partner.rules", "real rules fired", [], eg[0]["rules"])
    _record_reaches(ch, _labels(ch, dg, capabilities, run_record, "R3", T, eg[0]))
    ch.finish()


# --- R4 … R8: the transformations ----------------------------------------------------------------


def test_R4_annotate_keeps_it_risky(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R4 — annotate: PII read from the store leaves with a note added; still PII, still blocked.
    Labels: the record's units reach the partner request leg whole from
    lab-store's response, through lab-a's memory of both the store read and
    lab-tool-x's annotated answer. Decision: unchanged, critical/block; with
    inherited labels on, the record carries the inherited PII."""
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
    _record_reaches(ch, _labels(ch, dg, capabilities, run_record, "R4", T, eg))
    ch.finish()


def test_R5_combine_two_harmless_halves_into_an_ssn(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R5 — two non-risky payloads become one risky payload. The halves are
    read separately; the tool joins them; the SSN leaves. Expect: neither
    half classifies as PII; the tool's response and the partner request do;
    the partner interaction is critical/block.
    Labels: the SSN reaches the partner request leg whole, origin lab-tool-x's
    response (the classifier's finding of the combined number is a new origin;
    the suffix that reached the tool is only the tail of that number, so exact
    match does not find it there); Alice Moreno whole from lab-store's first
    read. Decision: unchanged, critical/block."""
    plan = {"mark": "R5", "steps": [read("alice", "a", fields=["name", "ssn_prefix"]), read("alice", "b", fields=["ssn_suffix"]),
                                    transform("lab_tool_x", "combine", {"a": "$a", "b": "$b"}, "t"), send("$t", "sent")]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R5", plan)
    ch.check("app.joined_ssn", "the app joined the halves into the SSN", "123-45-6789", values["t"].get("ssn"))
    halves = _calls(legs, "lab-store")
    ch.check("halves.no_ssn", "neither half's response classifies as SSN", [], [h["id"] for h in halves if "SSN" in str(h["response"])])
    eg = _egress(legs, EXTERNAL)[0]
    ch.check("partner.tags", "partner request leg tags include PII", "PII", _tags(eg["request"]), ok="PII" in _tags(eg["request"]))
    ch.check("partner.verdict", "partner verdict", "critical/block", _verdict(eg))
    edges = _labels(ch, dg, capabilities, run_record, "R5", T, eg)
    if edges is not None:
        ch.check("labels.ssn_from_the_tool", "the combined SSN reaches the egress whole from lab-tool-x's response",
                 [("SSN", "123-45-6789")], [u for u in L.whole_from(edges, TOOL_X) if u[0] == "SSN"])
        ch.check("labels.name_from_the_store", "Alice Moreno reaches the egress whole from lab-store's response",
                 True, ("PN", "Alice Moreno") in L.whole_from(edges, STORE))
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


def test_R6b_split_of_a_name_free_record(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R6b — the record read without its name (ssn, public_note), split by
    lab-tool-x into {public_note, ssn_prefix} and {ssn_suffix}, both parts sent
    externally in one payload. No NER-visible unit of the record survives whole
    in either part.
    Labels: the origin carries the SSN (lab-store's response holds the unit
    123-45-6789, which reaches lab-tool-x's request whole); the egress inherits
    nothing from it yet — no SSN edge into the partner request, no PII among
    its inherited tags, recombined false (fragments that recombine are later
    work). What may reach is only the classifier's own reading of the parts
    (the last four as a card code), tags the egress's own classification carries.
    Decision: no PII reaches it through lineage, so DG-001 does not fire from
    labels; the classifier's own reading of the parts decides, as on R8
    (recorded)."""
    plan = {"mark": "R6b", "steps": [read("alice", "rec", fields=["ssn", "public_note"]),
                                     transform("lab_tool_x", "split", "$rec", "t"),
                                     send("$t.parts", "sent_both"),
                                     {"op": "set", "value": "$t", "mark": "parts"}]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R6b", plan)
    parts = values["parts"]["parts"]
    ch.check("app.parts", "the app split the name-free record into two parts",
             [{"public_note": "prefers aisle seats", "ssn_prefix": "123-45"}, {"ssn_suffix": "6789"}], parts)
    eg = _egress(legs, EXTERNAL)
    ch.check("partner.calls", "partner calls", 1, len(eg))
    run_record.scenario("R6b").write("egress_record.json", eg[0])
    ch.check("partner.no_pii", "the classifier sees no PII in the two parts", "no PII", _tags(eg[0]["request"]),
             ok="PII" not in _tags(eg[0]["request"]))
    ch.record("partner.verdict", "what the classifier made of the two parts (recorded, as R8)",
              f"{_verdict(eg[0])} {eg[0]['rules']} tags {_tags(eg[0]['request'])}")
    edges = _labels(ch, dg, capabilities, run_record, "R6b", T, eg[0])
    if edges is not None:
        lab = L.read(dg, T)
        tool_req = [e for x in _calls(legs, TOOL_X) for e in L.into(lab, x["id"])]
        ch.check("labels.origin_carries_ssn", "the store's SSN is a unit that reaches lab-tool-x's request whole",
                 True, ("SSN", "123-45-6789") in L.whole_from(tool_req, STORE))
        ch.check("labels.egress_no_ssn", "no SSN edge into the partner request (nothing exact survives the split)",
                 [], [e["value"] for e in edges if e["entity_type"] == "SSN"])
        ch.check("labels.egress_no_pii", "no PII among the egress's inherited tags", "no PII",
                 L.inherited_tags(edges), ok="PII" not in L.inherited_tags(edges))
        ch.check("labels.nothing_new", "what reaches is only tags the egress's own classification carries",
                 f"⊆ {_tags(eg[0]['request'])}", L.inherited_tags(edges),
                 ok=set(L.inherited_tags(edges)) <= set(_tags(eg[0]["request"])))
        ch.check("labels.no_dg001_from_labels", "DG-001 does not fire (no PII, detected or inherited)",
                 "no DG-001", eg[0]["rules"], ok="DG-001" not in eg[0]["rules"])
    ch.finish()


def test_R7_paraphrase_keeps_the_ssn(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R7 — the SSN re-formatted ('social security no. 123 45 6789'). Prediction:
    still PII; a miss is a finding about the recogniser, recorded either way.
    Labels: nothing from the store's SSN reaches the egress (re-spacing is a
    structural transformation, which exact match does not read); the tool's
    own re-reading reaches the egress (the classifier's finding of
    '123 45 6789' on lab-tool-x's response, a new origin, arrives whole) when
    the classifier makes that reading. The unchanged fields (Alice Moreno, the
    DOB, the phone) reach whole from lab-store's response; the spelled-out
    email does not. Decision: unchanged (the egress's own classification)."""
    plan = {"mark": "R7", "steps": [read("alice", "rec"), transform("lab_tool_x", "paraphrase_keep", "$rec", "t"), send("$t", "sent")]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R7", plan)
    eg = _egress(legs, EXTERNAL)[0]
    run_record.scenario("R7").write("egress_record.json", eg)
    ch.check("partner.classified", "the partner request leg was classified", "finding_count present",
             eg["request"].get("finding_count") if eg["request"] else None, ok=bool(eg["request"]) and eg["request"].get("finding_count") is not None)
    edges = _labels(ch, dg, capabilities, run_record, "R7", T, eg)
    if edges is not None:
        from_store = L.whole_from(edges, STORE)
        ch.check("labels.store_ssn_absent", "nothing from the store's SSN reaches the egress", [],
                 [u for u in from_store if u[0] == "SSN"])
        ch.check("labels.unchanged_fields", "the unchanged fields reach whole from the store",
                 [("DOB", "1987-03-14"), ("PHONE", "+1 212 555 0147"), ("PN", "Alice Moreno")],
                 [u for u in from_store if u[0] in ("DOB", "PHONE", "PN")])
        ch.check("labels.email_absent", "the spelled-out email is not the store's EMAIL", [],
                 [u for u in from_store if u[0] == "EMAIL"])
        tool = _calls(legs, TOOL_X)
        findings = dg.q("SELECT pc.findings FROM interaction_legs l JOIN payload_classifications pc "
                        "ON pc.content_hash = l.payload_hash WHERE l.interaction_id = %s AND l.leg_type = 'response'",
                        (tool[0]["id"],)) if tool else []
        reread = any(f.get("entity_type") == "SSN" for (fs,) in findings for f in (fs or []))
        respaced = [u for u in L.whole_from(edges, TOOL_X) if u[0] == "SSN"]
        ch.check("labels.tool_rereading", "the tool's own re-reading of the SSN reaches the egress (when the classifier made it)",
                 "present" if reread else "n/a", "present" if respaced else "absent",
                 ok=bool(respaced) or not reread, comment=f"tool response SSN reading: {reread}; {respaced}")
    if "PII" not in _tags(eg["request"]):
        ch.check("partner.tags", "the re-formatted SSN is recognised as PII", "PII", _tags(eg["request"]), ok=False,
                 known="classifier finding: the spaced SSN is not recognised")
        pytest.xfail(f"the re-formatted SSN was not recognised as PII: {eg['request']} — a classifier finding, recorded")
    ch.check("partner.tags", "the re-formatted SSN is recognised as PII", "PII", _tags(eg["request"]), ok=True)
    ch.check("partner.verdict", "partner verdict", "critical/block", _verdict(eg))
    ch.finish()


def test_R8_redaction_makes_it_harmless(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R8 — the record redacted to the SSN's last four leaves externally: no PII, allowed.
    Labels: nothing — no SSN edge into the partner request (the last four are
    not the unit); what may reach is only the classifier's own reading of the
    four digits as a card code, tags the egress's own classification already
    carries. Decision: unchanged by labels (today's xfail on the classifier's
    PCI reading stands)."""
    plan = {"mark": "R8", "steps": [read("alice", "rec"), transform("lab_tool_x", "redact", "$rec", "t"), send("$t", "sent")]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R8", plan)
    ch.check("app.redacted", "the app redacted the record", {"public_note": "prefers aisle seats", "ssn_last4": "6789", "redacted": True}, values["t"])
    eg = _egress(legs, EXTERNAL)[0]
    run_record.scenario("R8").write("egress_record.json", eg)
    ch.check("partner.no_pii", "the redacted record carries no PII", "no PII", _tags(eg["request"]), ok="PII" not in _tags(eg["request"]))
    edges = _labels(ch, dg, capabilities, run_record, "R8", T, eg)
    if edges is not None:
        ch.check("labels.egress_no_ssn", "no SSN edge into the partner request", [],
                 [e["value"] for e in edges if e["entity_type"] == "SSN"])
        ch.check("labels.nothing_new", "what reaches is only tags the egress's own classification carries",
                 f"⊆ {_tags(eg['request'])}", L.inherited_tags(edges),
                 ok=set(L.inherited_tags(edges)) <= set(_tags(eg["request"])))
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
    The llm request leg carries PII; what leaves is measured.
    Labels: every identifier of the record the model repeated byte for byte
    reaches the partner request leg whole from lab-store's response (the
    SSN iff the summary repeats it verbatim). Decision: unchanged (blocked iff
    the classifier sees PII); with inherited labels on, the record carries what
    reached."""
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
    edges = _labels(ch, dg, capabilities, run_record, "R9", T, eg)
    verbatim = [u for u in RECORD_UNITS if u[1] in values["sum"]]
    _record_reaches(ch, edges, expected=verbatim, id="labels.repeated_whole")
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


def test_R11_pod_lifetime_session_rides_the_turn(env, kube, dg, capabilities, catalog, fleet, attach, run_record, state):
    """R11 — the frameworks' failure mode as a switch: the same tool call on a
    session the agent opened at startup (session=pod_lifetime) derives under
    the turn exactly like a per-turn call. Before cortex #1333 (the MCP
    context bridge, lab-dg #287) the call rode the startup context into its
    own trace; the kit now carries the turn across the session's post task.
    The switch stays: a kit that loses the bridge turns forest.rides_turn red."""
    plan = {"mark": "R11", "steps": [read("public", "rec"),
                                     {"op": "mcp", "tool": "lab_tool_x", "args": {"op": "forward", "payload": "$rec"},
                                      "session": "pod_lifetime", "mark": "t"}]}
    T, values, legs, ch = _run(env, kube, dg, capabilities, fleet, attach, run_record, state, "R11", plan)
    ch.check("app.forwarded", "the tool forwarded the record (the call happened)", values["rec"], values["t"])
    ch.finish()
