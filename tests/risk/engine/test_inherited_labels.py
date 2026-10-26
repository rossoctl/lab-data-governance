"""Inherited labels reach the decision behind ``RISK_INHERITED_LABELS``.

- ``off``: the engine never reads the label tables, and the OPA input, the
  fingerprint and the record are byte-identical to the engine without label
  lineage — built here from the same evidence.
- ``on``: the labels that reached a leg's payload join that flow as ONE
  document-level data item; only whole values whose origin is in the trace.
- ``all``: rejected at startup. Labels from other conversations have their own
  switch, ``RISK_INHERITED_LABELS_CROSS_CONVERSATIONS`` (slice 2, #273): with it on
  (and ``on`` here), whole values from any conversation are forwarded — a label
  carried in counts like one found here.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys

import psycopg
import pytest

from data_governance.processors.classification.verdict import Verdict
from data_governance.risk import config
from data_governance.risk.engine import compute, labels, utils
from data_governance.risk.engine.evidence import Evidence, gather_evidence

CLEAN = Verdict(sensitivity_level="PUBLIC", regulatory_tags=[], contains_identity_bundle=False,
                is_personalized=False, primary_domain=None, findings=[], model_version=1)
ANCHOR = utils.AnchorFacts(direction="outbound", peer_host="api.partner.example:8000", url_scheme="http",
                           url_path="/mcp", self_id="lab-a")


def _edge(**fields) -> labels.InheritedEdge:
    base = dict(trace_id="T", entity_type="SSN", regulatory_tags=("PII",), sensitivity_level="RESTRICTED",
                match_kind="whole", matched_text="123-45-6789", matched_start=20, matched_end=31)
    return labels.InheritedEdge(**{**base, **fields})


def _evidence(inherited: labels.Inherited | None) -> Evidence:
    return Evidence(
        interaction_id="ix", trace_id="T", caller_entity_id="lab-a", callee_entity_id="partner",
        legs=[utils.LegEvidence("request", "h-req"), utils.LegEvidence("response", "h-res")],
        span_ids=["s1"], classifications={"request": CLEAN, "response": CLEAN}, anchor=ANCHOR,
        inherited=inherited,
    )


def _artefacts(evidence: Evidence) -> tuple[str, str, tuple]:
    """The OPA input, the fingerprint and the record the engine would write,
    serialized — the bytes the flag must not move when off."""
    opa_input = utils.build_opa_input(
        legs=evidence.legs, span_ids=evidence.span_ids, classifications=evidence.classifications,
        caller_entity_id=evidence.caller_entity_id, callee_entity_id=evidence.callee_entity_id,
        anchor=evidence.anchor, internal_patterns=["*.svc"], inherited=evidence.inherited)
    fp = utils.fingerprint(evidence.legs, evidence.classifications, evidence.anchor, evidence.inherited)
    decision = utils.PolicyDecision("high", "block", [], "x", ["DG-001"], 0.9, "v1")
    record = compute._record_params(
        interaction_id=evidence.interaction_id, trace_id=evidence.trace_id, parent_interaction_id=None,
        caller_entity_id=evidence.caller_entity_id, callee_entity_id=evidence.callee_entity_id,
        legs=evidence.legs, classifications=evidence.classifications, decision=decision,
        inherited=evidence.inherited)
    return json.dumps(opa_input, sort_keys=True), fp, json.dumps(record, sort_keys=True, default=str)


def _base(evidence: Evidence) -> tuple[str, str, tuple]:
    """The same artefacts through the engine's calls as they were before label
    lineage: no inherited argument anywhere."""
    opa_input = utils.build_opa_input(
        legs=evidence.legs, span_ids=evidence.span_ids, classifications=evidence.classifications,
        caller_entity_id=evidence.caller_entity_id, callee_entity_id=evidence.callee_entity_id,
        anchor=evidence.anchor, internal_patterns=["*.svc"])
    fp = utils.fingerprint(evidence.legs, evidence.classifications, evidence.anchor)
    decision = utils.PolicyDecision("high", "block", [], "x", ["DG-001"], 0.9, "v1")
    record = compute._record_params(
        interaction_id=evidence.interaction_id, trace_id=evidence.trace_id, parent_interaction_id=None,
        caller_entity_id=evidence.caller_entity_id, callee_entity_id=evidence.callee_entity_id,
        legs=evidence.legs, classifications=evidence.classifications, decision=decision)
    return json.dumps(opa_input, sort_keys=True), fp, json.dumps(record, sort_keys=True, default=str)


# --- off ---------------------------------------------------------------------------------------------


def test_off_input_fingerprint_and_record_are_byte_identical_to_the_base() -> None:
    evidence = _evidence(None)
    assert _artefacts(evidence) == _base(evidence)


def test_off_mode_forwards_nothing_even_when_edges_are_handed_over() -> None:
    off = _evidence(labels.Inherited(mode="off", by_leg={"request": [_edge()]}))
    assert _artefacts(off) == _base(off)


def test_on_with_nothing_inherited_is_byte_identical_too() -> None:
    evidence = _evidence(labels.Inherited(mode="on", by_leg={}))
    assert _artefacts(evidence) == _base(evidence)


# --- on ----------------------------------------------------------------------------------------------


def test_on_adds_one_document_level_item_to_the_flow_the_labels_reached() -> None:
    inherited = labels.Inherited(mode="on", by_leg={"request": [
        _edge(),
        _edge(),  # the same unit through a second carrier: one entity
        _edge(entity_type="DOB", regulatory_tags=("PI",), sensitivity_level="INTERNAL",
              matched_text="1987-03-14", matched_start=40, matched_end=50),
    ]})
    opa_input = json.loads(_artefacts(_evidence(inherited))[0])
    request, response = opa_input["flows"]["request"], opa_input["flows"]["response"]
    assert len(response["data_items"]) == 1
    (own, item) = request["data_items"]
    assert own["regulatory_tags"] == []
    assert item["regulatory_tags"] == ["PI", "PII"] and item["classification_level"] == "RESTRICTED"
    assert item["list_of_entities"] == ["SSN", "DOB"]
    assert item["entities"][0] == {"entity_type": "SSN", "regulatory_tags": ["PII"],
                                   "classification_level": "RESTRICTED", "text": "123-45-6789",
                                   "start": 20, "end": 31}


def test_on_the_labels_move_the_fingerprint_and_join_the_record() -> None:
    evidence = _evidence(labels.Inherited(mode="on", by_leg={"request": [_edge()]}))
    _, fp, record = _artefacts(evidence)
    _, base_fp, _ = _base(evidence)
    assert fp != base_fp
    summary = json.loads(json.loads(record)[0]["classification_summary"])
    assert summary["request"]["inherited"] == {"regulatory_tags": ["PII"], "max_level": "RESTRICTED",
                                              "kinds": ["whole"], "recombined": False, "count": 1}
    assert "inherited" not in summary["response"]


@pytest.mark.parametrize("edge", [
    _edge(origin_trace_id="another-trace"),  # an origin in another conversation
    _edge(match_kind="fragment"),            # anything but a whole value
    _edge(match_kind="semantic", matched_text=None, matched_start=None, matched_end=None),
])
def test_on_forwards_only_whole_values_from_this_trace(edge: labels.InheritedEdge) -> None:
    assert not labels.forwarded(edge, "on")
    evidence = _evidence(labels.Inherited(mode="on", by_leg={"request": [edge]}))
    assert _artefacts(evidence) == _base(evidence)


def test_an_origin_named_by_its_own_trace_id_is_this_trace() -> None:
    assert labels.forwarded(_edge(origin_trace_id="T"), "on")


# --- all, and the cross-conversation switch ---------------------------------------------------------

EARLIER_MRN = _edge(entity_type="MRN", regulatory_tags=("PII", "PHI"), sensitivity_level="CONFIDENTIAL",
                    matched_text="MRN-12345678", matched_start=20, matched_end=32, origin_trace_id="an-earlier-trace")


def test_all_is_rejected_and_names_the_cross_conversation_switch() -> None:
    with pytest.raises(ValueError, match="RISK_INHERITED_LABELS_CROSS_CONVERSATIONS=on"):
        config.parse_inherited_labels("all")
    with pytest.raises(ValueError, match="must be off or on"):
        config.parse_inherited_labels("yes")
    assert config.parse_inherited_labels(None) == "off" and config.parse_inherited_labels(" ON ") == "on"


def test_the_cross_conversation_switch_needs_the_same_conversation_one() -> None:
    """What labels from other conversations would mean without this conversation's
    own is not decided: refused, not guessed."""
    assert config.parse_cross_conversations(None, "on") is False
    assert config.parse_cross_conversations(" ON ", "on") is True
    assert config.parse_cross_conversations("off", "off") is False
    with pytest.raises(ValueError, match="needs RISK_INHERITED_LABELS=on"):
        config.parse_cross_conversations("on", "off")
    with pytest.raises(ValueError, match="must be off or on"):
        config.parse_cross_conversations("yes", "on")


@pytest.mark.parametrize(("edge", "on", "cross"), [
    (_edge(), True, True),
    (_edge(origin_trace_id="T"), True, True),
    (_edge(origin_trace_id="an-earlier-trace"), False, True),
    (_edge(match_kind="fragment"), False, False),
    (_edge(match_kind="fragment", origin_trace_id="an-earlier-trace"), False, False),
    (_edge(match_kind="semantic", matched_text=None, matched_start=None, matched_end=None,
           origin_trace_id="an-earlier-trace"), False, False),
])
def test_what_each_mode_forwards(edge: labels.InheritedEdge, on: bool, cross: bool) -> None:
    """The cross-conversation switch widens ``on`` by the origin only: a whole
    value, whichever conversation it was found in. Never a fragment or a semantic
    match, and nothing at all with the mode off."""
    assert labels.forwarded(edge, "off") is False and labels.forwarded(edge, "off", True) is False
    assert labels.forwarded(edge, "on") is on
    assert labels.forwarded(edge, "on", True) is cross


def test_the_cross_switch_hands_the_flow_a_label_carried_in_from_an_earlier_conversation() -> None:
    """A record read alone in a later conversation (nothing found there) that label
    lineage links to the conversation that wrote it: under ``on`` alone the
    artefacts are the base's; with the cross-conversation switch its labels join
    the request flow and move the fingerprint, exactly as an inherited label of
    this trace does."""
    on = _evidence(labels.Inherited(mode="on", by_leg={"request": [EARLIER_MRN]}))
    assert _artefacts(on) == _base(on)
    every = _evidence(labels.Inherited(mode="on", by_leg={"request": [EARLIER_MRN]}, cross=True))
    opa_input, fp, _ = _artefacts(every)
    (own, item) = json.loads(opa_input)["flows"]["request"]["data_items"]
    assert own["regulatory_tags"] == [] and item["regulatory_tags"] == ["PHI", "PII"]
    assert item["classification_level"] == "CONFIDENTIAL" and item["list_of_entities"] == ["MRN"]
    assert fp != _base(every)[1]


@pytest.mark.parametrize(("raw", "cross"), [("all", "off"), ("yes", "off"), ("off", "on"), ("on", "yes")])
def test_a_bad_value_stops_leg_ready_with_the_message_before_any_db_work(raw: str, cross: str) -> None:
    env = {**os.environ, "RISK_INHERITED_LABELS": raw, "RISK_INHERITED_LABELS_CROSS_CONVERSATIONS": cross,
           "DATABASE_URL": "postgres://unused:1/x"}
    result = subprocess.run([sys.executable, "-m", "data_governance.processors.leg_ready"],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 2 and "RISK_INHERITED_LABELS" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_bad_value_does_not_break_what_merely_imports_the_config() -> None:
    """The API, the alerts processor and the rest import risk.config; only leg-ready
    and the engine path consult the mode."""
    env = {**os.environ, "RISK_INHERITED_LABELS": "all", "RISK_INHERITED_LABELS_CROSS_CONVERSATIONS": "yes"}
    result = subprocess.run(
        [sys.executable, "-c", "import data_governance.risk.config, data_governance.risk.engine.utils"],
        env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


# --- evidence from the database ----------------------------------------------------------------------


@pytest.fixture()
def derived_r4(configured_db: str) -> str:
    """R4 replayed into the database and derived by the label-lineage processor."""
    from data_governance.label_lineage import find
    from data_governance.processors.label_lineage import driver
    from tests.processors.label_lineage.seed import seed

    seed(configured_db, "R4")
    driver.drain(0, driver.Config(find.exact, "exact"))
    return configured_db


def _egress_interaction() -> str:
    from tests.label_lineage.fixtures import egress, trace_input

    return egress("R4", trace_input("R4")).interaction_id


def test_off_gather_evidence_never_reads_the_label_tables(derived_r4: str, monkeypatch) -> None:
    monkeypatch.setattr(config, "inherited_labels_mode", lambda: "off")
    with psycopg.connect(derived_r4) as conn:
        conn.execute("ALTER TABLE payload_label_edges RENAME TO payload_label_edges_away")
        conn.commit()
    evidence = gather_evidence(_egress_interaction())
    assert evidence.inherited is None


def test_on_gather_evidence_reads_the_edges_into_each_leg(derived_r4: str, monkeypatch) -> None:
    monkeypatch.setattr(config, "inherited_labels_mode", lambda: "on")
    evidence = gather_evidence(_egress_interaction())
    assert evidence.inherited is not None and evidence.inherited.mode == "on"
    request = evidence.inherited.forwarded("request")
    assert ("SSN", "123-45-6789") in {(e.entity_type, e.matched_text) for e in request}
    item = labels.data_item(request)
    assert "PII" in item["regulatory_tags"] and item["classification_level"] == "RESTRICTED"
    off = dataclasses.replace(evidence, inherited=None)
    assert _artefacts(off) == _base(off)


def test_the_cross_switch_reaches_the_evidence(derived_r4: str, monkeypatch) -> None:
    monkeypatch.setattr(config, "inherited_labels_mode", lambda: "on")
    assert gather_evidence(_egress_interaction()).inherited.cross is False
    monkeypatch.setattr(config, "inherited_cross_conversations", lambda: True)
    assert gather_evidence(_egress_interaction()).inherited.cross is True


def test_on_an_edge_derived_from_an_old_payload_is_not_forwarded(derived_r4: str, monkeypatch) -> None:
    """A leg rewritten in place keeps its label edges until label lineage re-derives
    it; the engine reads only the edges at the payload the leg carries now."""
    monkeypatch.setattr(config, "inherited_labels_mode", lambda: "on")
    interaction_id = _egress_interaction()
    with psycopg.connect(derived_r4) as conn:
        conn.execute("UPDATE interaction_legs SET payload_hash = 'rewritten' "
                     "WHERE interaction_id = %s AND leg_type = 'request'", (interaction_id,))
        conn.commit()
    evidence = gather_evidence(interaction_id)
    assert evidence.inherited.forwarded("request") == []
    assert evidence.inherited.forwarded("response")  # the response leg still carries its own payload


def test_on_the_engine_asks_opa_again_when_labels_land(derived_r4: str, monkeypatch) -> None:
    """The call site, not only ``utils.fingerprint``: the same evidence without and
    then with the inherited labels is two fingerprints, two OPA calls, and the
    second input carries the inherited item."""
    from data_governance.risk.engine.opa import OpaDecision

    class _Opa:
        def __init__(self):
            self.calls = []

        def evaluate(self, **kwargs):
            self.calls.append(kwargs)
            return OpaDecision(risk_level="low", enforcement_type=None, allowed_actions=[], explanation=None,
                               triggered_rules=[], confidence=0.5, policy_version="1")

    monkeypatch.setattr(config, "inherited_labels_mode", lambda: "on")
    evidence = gather_evidence(_egress_interaction())
    opa = _Opa()
    compute._get_or_refresh_decision(interaction_id=evidence.interaction_id, opa_client=opa,
                                     evidence=dataclasses.replace(evidence, inherited=None))
    compute._get_or_refresh_decision(interaction_id=evidence.interaction_id, opa_client=opa, evidence=evidence)
    assert len(opa.calls) == 2
    items = [opa.calls[i]["opa_input"]["flows"]["request"]["data_items"] for i in (0, 1)]
    assert len(items[1]) == len(items[0]) + 1
