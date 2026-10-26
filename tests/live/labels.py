"""What label lineage derived for a live trace, read from migration 0024's tables:
per leg the ``leg_inherited_labels`` summary, per edge where the unit was found
(the origin's callee and leg, and its trace when another) and how it arrived (the
carrier's callee and leg, and its trace when the edge crosses from another
conversation). Recorded to the run record as ``labels.json`` and asserted by the
label rows of the two test modules, only when the deployment carries label
lineage (``capabilities.labels``)."""

from __future__ import annotations

from typing import Any

from tests.live.fleet import name_of

_LEGS_SQL = """
    SELECT m.interaction_id, m.leg_type::text, ee.kind::text, ee.natural_key, m.classified,
           m.inherited_tags_whole, m.inherited_tags_fragment, m.max_inherited_level, m.origin_count,
           m.recombined, m.edge_count, m.payload_hash
    FROM leg_inherited_labels m JOIN interactions i ON i.id = m.interaction_id
    JOIN entities ee ON ee.id = i.callee_entity_id
    WHERE m.trace_id = %s"""

_EDGES_SQL = """
    SELECT e.to_interaction_id, e.to_leg_type::text, te.kind::text, te.natural_key,
           e.entity_type, e.unit_value, e.unit_text, e.regulatory_tags, e.sensitivity_level,
           e.match_kind, e.matched_text, e.recombined,
           oe.kind::text, oe.natural_key, e.origin_leg_type::text, e.origin_trace_id,
           fe.kind::text, fe.natural_key, e.from_leg_type::text, e.from_trace_id
    FROM payload_label_edges e
    JOIN interactions ti ON ti.id = e.to_interaction_id JOIN entities te ON te.id = ti.callee_entity_id
    JOIN interactions oi ON oi.id = e.origin_interaction_id JOIN entities oe ON oe.id = oi.callee_entity_id
    JOIN interactions fi ON fi.id = e.from_interaction_id JOIN entities fe ON fe.id = fi.callee_entity_id
    WHERE e.trace_id = %s"""


def read(dg, trace_id: str) -> dict[str, Any]:
    legs = [{"interaction": r[0], "leg": r[1], "callee": name_of(r[2], r[3]), "classified": r[4],
             "whole": sorted(r[5] or []), "fragment": sorted(r[6] or []), "level": r[7], "origins": r[8],
             "recombined": r[9], "edges": r[10], "payload_hash": r[11]}
            for r in dg.q(_LEGS_SQL, (trace_id,))]
    edges = [{"to": r[0], "to_leg": r[1], "to_callee": name_of(r[2], r[3]), "entity_type": r[4],
              "value": r[5], "unit_text": r[6], "tags": sorted(r[7] or []), "level": r[8], "kind": r[9],
              "matched": r[10], "recombined": r[11], "origin": name_of(r[12], r[13]), "origin_leg": r[14],
              "origin_trace": r[15], "from": name_of(r[16], r[17]), "from_leg": r[18], "from_trace": r[19]}
             for r in dg.q(_EDGES_SQL, (trace_id,))]
    return {"legs": legs, "edges": edges}


def write(run_record, name: str, lab: dict[str, Any]) -> None:
    run_record.scenario(name).write("labels.json", lab)


def into(lab: dict[str, Any], interaction_id: str, leg: str = "request") -> list[dict[str, Any]]:
    """The edges into one leg."""
    return [e for e in lab["edges"] if e["to"] == interaction_id and e["to_leg"] == leg]


def leg_row(lab: dict[str, Any], interaction_id: str, leg: str = "request") -> dict[str, Any] | None:
    return next((x for x in lab["legs"] if x["interaction"] == interaction_id and x["leg"] == leg), None)


def whole_from(edges: list[dict[str, Any]], origin: str, origin_leg: str = "response") -> list[tuple[str, str]]:
    """(entity type, value) of the units found whole whose origin is *origin*'s leg
    in THIS conversation (an origin in another trace is lineage information,
    not what the row's own lineage carried)."""
    return sorted({(e["entity_type"], e["value"]) for e in edges
                   if e["origin"] == origin and e["origin_leg"] == origin_leg and e["kind"] == "whole"
                   and e["origin_trace"] is None})


def inherited_tags(edges: list[dict[str, Any]]) -> list[str]:
    """What the leg inherited: whole values whose origin is in this conversation.
    An edge whose origin lies in another trace is lineage information for
    following the value's path, never something the decision sees."""
    return sorted({t for e in edges if e["kind"] == "whole" and e["origin_trace"] is None for t in e["tags"]})


def crossing(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The edges that cross from another conversation (the carrier is a leg of another trace)."""
    return [e for e in edges if e["from_trace"] is not None]
