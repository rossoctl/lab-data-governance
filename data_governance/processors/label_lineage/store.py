"""Writing a :class:`~data_governance.label_lineage.summary.Derivation` to the four
tables of migration 0024, and reading back what risk needs to know changed.

The rows mirror the dataclasses: an edge row is the edge's two occurrences
flattened, a leg row is a :class:`LegSummary`, the status row is the trace's
:class:`TraceStatus`. Keys are deterministic — ``edge_id`` is a uuid5 of the edge's
natural key, the leg row is keyed by its leg — so writing one derivation twice is
the same rows, and the trace-scoped delete is by set membership, never by seq.
"""

from __future__ import annotations

import json
import uuid

from data_governance import db
from data_governance.label_lineage.summary import Derivation, Edge, LegSummary

#: The namespace edge ids are minted in (uuid5 of "data-governance:label-lineage:edge"
#: under the URL namespace). Fixed: changing it renames every stored edge.
NS_LABELS = uuid.UUID("2472ae9a-b655-531d-a17a-b966d624ad0d")


def edge_id(edge: Edge) -> str:
    """The edge's id: trace / to leg / origin / from leg / held run / span."""
    o, v = edge.unit.origin, edge.unit.value
    span = "-" if edge.to.span is None else f"{edge.to.span[0]}:{edge.to.span[1]}"
    natural = "/".join([
        edge.to.at.trace_id,
        ":".join(edge.to_key),
        ":".join([o.trace_id, o.interaction_id, o.leg_type, o.payload_hash, str(v.start), str(v.end),
                  edge.unit.entity_type]),
        ":".join(edge.from_key),
        str(edge.to.held_from),
        str(edge.to.held_to),
        span,
    ])
    return str(uuid.uuid5(NS_LABELS, natural))


_SUMMARY_COLUMNS = ("inherited_tags_whole, inherited_tags_fragment, max_inherited_level, "
                    "origin_count, recombined, edge_count")


def _summary(leg: LegSummary) -> tuple:
    """The part of a leg row risk reads: what reached the leg, not where from."""
    return (list(leg.inherited_tags_whole), list(leg.inherited_tags_fragment), leg.max_inherited_level,
            leg.origin_count, leg.recombined, leg.edge_count)


def exposed(tx: db.Transaction, trace_id: str) -> frozenset[tuple]:
    """What another trace's priors read of this one, as stored: its edges (by id)
    and each derived leg with the payload and classification it was derived
    with. A change in this set is what makes the traces that read it stale."""
    edges = tx.fetch_all("SELECT edge_id FROM payload_label_edges WHERE trace_id = %s", (trace_id,))
    legs = tx.fetch_all("SELECT interaction_id, leg_type::text, payload_hash, classified "
                        "FROM leg_inherited_labels WHERE trace_id = %s", (trace_id,))
    return frozenset(("edge", r[0]) for r in edges) | frozenset(("leg", *r) for r in legs)


def exposed_by(derivation: Derivation) -> frozenset[tuple]:
    """:func:`exposed` as *derivation* will store it."""
    return frozenset(("edge", edge_id(e)) for e in derivation.edges) | frozenset(
        ("leg", *key, leg.payload_hash, leg.classified) for key, leg in derivation.legs.items())


def read_summaries(tx: db.Transaction, trace_id: str) -> dict[tuple[str, str], tuple]:
    rows = tx.fetch_all(
        f"SELECT interaction_id, leg_type::text, {_SUMMARY_COLUMNS} FROM leg_inherited_labels "
        "WHERE trace_id = %s",
        (trace_id,),
    )
    return {(r[0], r[1]): tuple(r[2:]) for r in rows}


_EDGE_SQL = """
INSERT INTO payload_label_edges (
    edge_id, trace_id, to_interaction_id, to_leg_type, to_payload_hash,
    from_interaction_id, from_leg_type, from_payload_hash, from_trace_id,
    origin_interaction_id, origin_leg_type, origin_payload_hash, origin_trace_id,
    unit_start, unit_end, unit_text, unit_value, entity_type, regulatory_tags, sensitivity_level, category,
    matched_text, matched_start, matched_end, matcher, match_kind, score, held_from, held_to, recombined, seq
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (edge_id) DO UPDATE SET
    to_payload_hash = EXCLUDED.to_payload_hash, from_payload_hash = EXCLUDED.from_payload_hash,
    unit_text = EXCLUDED.unit_text, unit_value = EXCLUDED.unit_value,
    regulatory_tags = EXCLUDED.regulatory_tags, sensitivity_level = EXCLUDED.sensitivity_level,
    category = EXCLUDED.category, matched_text = EXCLUDED.matched_text, matcher = EXCLUDED.matcher,
    match_kind = EXCLUDED.match_kind, score = EXCLUDED.score, recombined = EXCLUDED.recombined,
    seq = EXCLUDED.seq
"""

_LEG_SQL = f"""
INSERT INTO leg_inherited_labels (
    interaction_id, leg_type, trace_id, payload_hash, seq, matcher, classified,
    {_SUMMARY_COLUMNS}, recombined_units
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (interaction_id, leg_type) DO UPDATE SET
    trace_id = EXCLUDED.trace_id, payload_hash = EXCLUDED.payload_hash, seq = EXCLUDED.seq,
    matcher = EXCLUDED.matcher, classified = EXCLUDED.classified,
    inherited_tags_whole = EXCLUDED.inherited_tags_whole,
    inherited_tags_fragment = EXCLUDED.inherited_tags_fragment,
    max_inherited_level = EXCLUDED.max_inherited_level, origin_count = EXCLUDED.origin_count,
    recombined = EXCLUDED.recombined, recombined_units = EXCLUDED.recombined_units,
    edge_count = EXCLUDED.edge_count
"""

_STATUS_SQL = """
INSERT INTO label_lineage_trace_status (
    trace_id, status, unclassified_legs, unanchored_findings, derived_legs, edge_count, matcher, derived_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (trace_id) DO UPDATE SET
    status = EXCLUDED.status, unclassified_legs = EXCLUDED.unclassified_legs,
    unanchored_findings = EXCLUDED.unanchored_findings, derived_legs = EXCLUDED.derived_legs,
    edge_count = EXCLUDED.edge_count, matcher = EXCLUDED.matcher, derived_at = now()
"""


def _edge_row(edge: Edge, matcher: str, seq: int) -> tuple:
    o, u, to = edge.unit.origin, edge.unit, edge.to
    start, end = to.span if to.span is not None else (None, None)
    return (
        edge_id(edge), to.at.trace_id, *edge.to_key, to.at.payload_hash,
        *edge.from_key, edge.came_from.at.payload_hash, edge.from_trace_id,
        o.interaction_id, o.leg_type, o.payload_hash,
        None if o.trace_id == to.at.trace_id else o.trace_id,
        u.value.start, u.value.end, u.text, u.value.canonical, u.entity_type, list(u.regulatory_tags),
        u.sensitivity_level, u.category,
        to.matched_text, start, end, matcher, to.match_kind, round(to.score, 3), to.held_from, to.held_to,
        edge.recombined, seq,
    )


def write(tx: db.Transaction, derivation: Derivation, matcher: str) -> set[str]:
    """Persist *derivation* within *tx* and return the interactions whose inherited
    summary differs from the one stored before: a leg that had a row and now reads
    differently, a leg with no row before that now has edges (a decision may
    already have been made without them), or a leg that lost its row while
    something had reached it."""
    trace_id = derivation.trace_id
    before = read_summaries(tx, trace_id)
    seq_of = {key: leg.seq for key, leg in derivation.legs.items()}
    edges = {edge_id(e): e for e in derivation.edges}
    tx.execute_many(_EDGE_SQL, [_edge_row(e, matcher, seq_of[e.to_key]) for e in edges.values()])
    tx.execute_many(_LEG_SQL, [
        (*key, trace_id, leg.payload_hash, leg.seq, matcher, leg.classified, *_summary(leg),
         json.dumps(list(leg.recombined_units)))
        for key, leg in derivation.legs.items()
    ])
    tx.execute(
        "DELETE FROM payload_label_edges WHERE trace_id = %s AND NOT (edge_id = ANY(%s::text[]))",
        (trace_id, list(edges)),
    )
    keys = list(derivation.legs)
    tx.execute(
        "DELETE FROM leg_inherited_labels m WHERE m.trace_id = %s AND NOT EXISTS ("
        "  SELECT 1 FROM unnest(%s::text[], %s::text[]) AS kept(interaction_id, leg_type)"
        "  WHERE kept.interaction_id = m.interaction_id AND kept.leg_type = m.leg_type::text)",
        (trace_id, [k[0] for k in keys], [k[1] for k in keys]),
    )
    s = derivation.status
    tx.execute(_STATUS_SQL, (trace_id, s.status, s.unclassified_legs, s.unanchored_findings,
                             s.derived_legs, s.edge_count, matcher))
    changed = {key[0] for key, leg in derivation.legs.items()
               if (before[key] != _summary(leg) if key in before else leg.edge_count > 0)}
    changed |= {key[0] for key, row in before.items() if key not in derivation.legs and row[-1]}
    return changed
