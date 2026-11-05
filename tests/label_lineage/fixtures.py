"""The replayed lab traces (``tests/fixtures/label_lineage/R*.json``): one trace each,
exported from a live DG database after lab runs — entities,
interactions, legs with their wire time, payloads and the classifier's real
findings. Loaded here into the fold's input; the driver tests load the same files
into a migrated database."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from data_governance.label_lineage import fold
from data_governance.processors.data_lineage.traversal import Entity

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "label_lineage"


def load(row: str) -> dict:
    return json.loads((FIXTURES / f"{row}.json").read_text())


def trace_input(row: str) -> fold.TraceInput:
    d = load(row)
    by_ix = {i["id"]: i for i in d["interactions"]}
    legs = [
        fold.WireLeg(
            interaction_id=leg["interaction_id"], leg_type=leg["leg_type"], seq=int(leg["seq"]),
            caller_entity_id=by_ix[leg["interaction_id"]]["caller_entity_id"],
            callee_entity_id=by_ix[leg["interaction_id"]]["callee_entity_id"],
            payload_hash=leg["payload_hash"],
            occurred_at=dt.datetime.fromisoformat(leg["occurred_at"]) if leg["occurred_at"] else None,
        )
        for leg in d["legs"]
    ]
    return fold.TraceInput(
        trace_id=d["trace_id"],
        legs=legs,
        entities={e["id"]: Entity(id=e["id"], natural_key=e["natural_key"], kind=e["kind"])
                  for e in d["entities"]},
        payloads={p["content_hash"]: p["content"] for p in d["payloads"]},
        findings={c["content_hash"]: c["findings"] for c in d["classifications"]},
    )


def name(t: fold.TraceInput, entity_id: str) -> str:
    """An entity's short name: ``lab-a``, ``lab-store``, ``api.partner.example:8000``."""
    key = t.entities[entity_id].natural_key
    return key.split(":", 1)[1].split("/")[-1]


def kind(row: str, payload_hash: str) -> str:
    return next(p["content_kind"] for p in load(row)["payloads"] if p["content_hash"] == payload_hash)


def legs_to(row: str, t: fold.TraceInput, callee: str, leg_type: str = "request") -> list[fold.WireLeg]:
    """The tool-call (or a2a, or LLM) legs to *callee*, MCP plumbing left out, in wire order."""
    return [
        leg for leg in fold.wire_order(t.legs)
        if leg.leg_type == leg_type and leg.payload_hash and name(t, leg.callee_entity_id) == callee
        and not kind(row, leg.payload_hash).startswith(("mcp_", "tool_discovery"))
    ]


def egress(row: str, t: fold.TraceInput) -> fold.WireLeg:
    partner = "lab-partner:8000" if row == "R3" else "api.partner.example:8000"
    (leg,) = legs_to(row, t, partner)
    return leg
