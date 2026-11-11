"""A memoryless tool that calls a service: its answer inherits what the service
returned (invocation scope), not only its own request."""

from __future__ import annotations

import datetime as dt

from data_governance.label_lineage import find, fold
from data_governance.processors.data_lineage.traversal import Entity

T0 = dt.datetime(2026, 11, 8, 9, 0, tzinfo=dt.timezone.utc)
ENTITIES = {
    "a": Entity(id="a", natural_key="agent:lab/a", kind="agent"),
    "t2": Entity(id="t2", natural_key="tool:lab/t2", kind="tool"),
    "t3": Entity(id="t3", natural_key="tool:lab/t3", kind="tool"),
}
MRN = "4471820"
FINDING = {"entity_type": "MRN", "text": MRN, "start": 9, "end": 16, "regulatory_tags": ["PII"], "sensitivity_level": "RESTRICTED"}


def _leg(ix: str, lt: str, seq: int, caller: str, callee: str, h: str | None, parent: str | None, t: int) -> fold.WireLeg:
    return fold.WireLeg(ix, lt, seq, caller, callee, h, occurred_at=T0 + dt.timedelta(seconds=t),
                        parent_interaction_id=parent)


def _trace() -> fold.TraceInput:
    legs = [
        _leg("ix1", "request", 1, "a", "t2", "h-q1", None, 0),       # agent asks the tool
        _leg("ix2", "request", 2, "t2", "t3", "h-q2", "ix1", 1),     # the tool asks a service
        _leg("ix2", "response", 3, "t2", "t3", "h-r2", "ix1", 2),    # the service answers with the MRN
        _leg("ix1", "response", 4, "a", "t2", "h-r1", None, 3),      # the tool answers the agent with the MRN
    ]
    payloads = {"h-q1": {"q": "lookup"}, "h-q2": {"q": "fetch"},
                "h-r2": {"mrn": MRN, "name": "Rosa Alvarez"}, "h-r1": {"external_id": MRN}}
    findings = {"h-q1": [], "h-q2": [], "h-r2": [FINDING], "h-r1": []}
    return fold.TraceInput("t", legs, ENTITIES, payloads, findings)


def test_tool_answer_inherits_what_its_child_service_returned() -> None:
    d = fold.derive(_trace(), find.exact)
    edges = {(e.from_key, e.to_key, e.unit.value.canonical if hasattr(e.unit, "value") else None) for e in d.edges}
    assert any(f == ("ix2", "response") and t == ("ix1", "response") for f, t, _ in edges), d.edges


def test_scope_adds_nothing_for_an_accumulating_agent() -> None:
    legs = [_leg("ix1", "request", 1, "u", "a", "h-q1", None, 0)]
    inbound = {("ix1", "request"): ()}
    out = fold.with_invocation_scope(legs, inbound, ENTITIES)
    assert out == inbound
