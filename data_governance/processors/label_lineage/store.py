"""Writing a :class:`~data_governance.label_lineage.summary.Derivation` to the four
tables of migration 0024 and the four of 0031 and 0032, reading back what risk needs to know
changed, and queueing the conversations a changed occurrence bears on (:func:`correct`).

The rows mirror the dataclasses: an edge row is the edge's two occurrences
flattened, a leg row is a :class:`LegSummary`, the status row is the trace's
:class:`TraceStatus`. Keys are deterministic — ``edge_id`` is a uuid5 of the edge's
natural key, the leg row is keyed by its leg — so writing one derivation twice is
the same rows, and the trace-scoped delete is by set membership, never by seq.

A crossing edge is an edge row like any other; its level, the values whose edges were
capped at a leaving leg and the limits that cut it are ``label_crossings`` rows, what the trace's
persistent entities received ``label_occurrences`` rows, the legs they sent
``label_sends`` rows, and every edge whose value rests on a crossing has a
``label_crossed_edges`` row with how surely it is the label's there. A derivation
without the cross-conversation switch produces none, so writing it deletes the trace's
rows there and writes none.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from data_governance import db
from data_governance.label_lineage.cross import Holding, core, core_hash
from data_governance.label_lineage.summary import Derivation, Edge, LegSummary

#: The namespace edge ids are minted in (uuid5 of "data-governance:label-lineage:edge"
#: under the URL namespace). Fixed: changing it renames every stored edge.
NS_LABELS = uuid.UUID("2472ae9a-b655-531d-a17a-b966d624ad0d")

#: The namespace occurrence and crossing ids are minted in (uuid5 of
#: "data-governance:label-lineage:crossing" under the URL namespace). Fixed.
NS_CROSSINGS = uuid.uuid5(uuid.NAMESPACE_URL, "data-governance:label-lineage:crossing")


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
    from_trace_id = EXCLUDED.from_trace_id,
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


def value_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def occurrence_id(h: Holding) -> str:
    """The occurrence's id: entity / leg / origin unit / span / held run."""
    o = h.occurrence
    return str(uuid.uuid5(NS_CROSSINGS, "/".join([
        "occurrence", h.holder_entity_id, ":".join(o.at.leg), ":".join(map(str, o.unit.key)),
        f"{o.span[0]}:{o.span[1]}", str(o.held_from), str(o.held_to)])))


_OCCURRENCE_SQL = """
INSERT INTO label_occurrences (
    occurrence_id, holder_entity_id, trace_id, interaction_id, leg_type, payload_hash,
    value_text, value_hash, core_hash, word_count, span_start, span_end, record_start, record_end,
    origin_trace_id, origin_interaction_id, origin_leg_type, origin_payload_hash,
    unit_start, unit_end, unit_text, unit_value, entity_type, regulatory_tags, sensitivity_level, category,
    held_from, held_to, label_basis, level, valid_from, valid_to
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (occurrence_id) DO UPDATE SET
    trace_id = EXCLUDED.trace_id,
    payload_hash = EXCLUDED.payload_hash, record_start = EXCLUDED.record_start, record_end = EXCLUDED.record_end,
    unit_text = EXCLUDED.unit_text, regulatory_tags = EXCLUDED.regulatory_tags,
    sensitivity_level = EXCLUDED.sensitivity_level, category = EXCLUDED.category,
    label_basis = EXCLUDED.label_basis, level = EXCLUDED.level,
    valid_from = EXCLUDED.valid_from, valid_to = EXCLUDED.valid_to
"""

_CROSSING_SQL = """
INSERT INTO label_crossings (
    crossing_id, trace_id, interaction_id, leg_type, value_hash, conversations, edge_id, occurrence_id, level, cut
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (crossing_id) DO UPDATE SET
    conversations = EXCLUDED.conversations, occurrence_id = EXCLUDED.occurrence_id, level = EXCLUDED.level
"""


def _occurrence_row(h: Holding) -> tuple:
    o, u = h.occurrence, h.occurrence.unit
    record = h.record or (None, None)
    held_core, words = core(h.value)       # never None: the fold holds no value without a word
    return (
        occurrence_id(h), h.holder_entity_id, o.at.trace_id, *o.at.leg, o.at.payload_hash,
        h.value, value_hash(h.value), core_hash(held_core), words, *o.span, *record,
        u.origin.trace_id, u.origin.interaction_id, u.origin.leg_type, u.origin.payload_hash,
        u.value.start, u.value.end, u.text, u.value.canonical, u.entity_type, list(u.regulatory_tags),
        u.sensitivity_level, u.category, o.held_from, o.held_to, h.label_basis, h.level, h.valid_from, h.valid_to,
    )


def _crossing_rows(derivation: Derivation) -> list[tuple]:
    t = derivation.trace_id
    rows = []
    for e in derivation.edges:
        c = derivation.levels.get(e.key)
        if c is not None:
            eid = edge_id(e)
            rows.append((str(uuid.uuid5(NS_CROSSINGS, f"crossing/{eid}")), t, *e.to_key,
                         value_hash(c.holding.value), c.conversations, eid, occurrence_id(c.holding), c.level, None))
    for m in derivation.capped:
        vh = value_hash(m.value)
        rows.append((str(uuid.uuid5(NS_CROSSINGS, f"capped/{t}/{':'.join(m.leg)}/{vh}")), t, *m.leg,
                     vh, m.conversations, None, None, None, None))
    for leg, limit in derivation.cuts:
        rows.append((str(uuid.uuid5(NS_CROSSINGS, f"cut/{t}/{':'.join(leg)}/{limit}")), t, *leg,
                     None, None, None, None, None, limit))
    return rows


_SEND_SQL = """
INSERT INTO label_sends (interaction_id, leg_type, entity_id, trace_id, occurred_at) VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (interaction_id, leg_type) DO UPDATE SET
    entity_id = EXCLUDED.entity_id, trace_id = EXCLUDED.trace_id, occurred_at = EXCLUDED.occurred_at
"""


def _write_sends(tx: db.Transaction, derivation: Derivation) -> None:
    rows = [(*leg, entity_id, derivation.trace_id, t) for entity_id, leg, t in derivation.sends]
    tx.execute_many(_SEND_SQL, rows)
    tx.execute(
        "DELETE FROM label_sends s WHERE s.trace_id = %s AND NOT EXISTS ("
        "  SELECT 1 FROM unnest(%s::text[], %s::text[]) AS kept(interaction_id, leg_type)"
        "  WHERE kept.interaction_id = s.interaction_id AND kept.leg_type = s.leg_type::text)",
        (derivation.trace_id, [r[0] for r in rows], [r[1] for r in rows]),
    )


def occurrences(tx: db.Transaction, trace_id: str) -> set[tuple]:
    """The trace's ``label_occurrences`` rows as (entity, valid from, valid to, the whole
    row): what :func:`correct` compares before and after a derivation."""
    return set(tx.fetch_all("SELECT holder_entity_id, valid_from, valid_to, to_jsonb(o)::text FROM label_occurrences o "
                            "WHERE trace_id = %s", (trace_id,)))


def taken_over(tx: db.Transaction, derivation: Derivation) -> set[tuple]:
    """The rows *derivation* is about to write that another trace's derivation wrote, in
    the shape of :func:`occurrences`. An occurrence whose origin lies outside the
    interactions that moved to another trace keeps its id, and the upsert takes its row
    over. What the row was before belongs to the comparison of :func:`affected`: a leg
    sent inside its old interval only is queued by nothing else."""
    ids = [occurrence_id(h) for h in derivation.holdings]
    return set(tx.fetch_all("SELECT holder_entity_id, valid_from, valid_to, to_jsonb(o)::text FROM label_occurrences o "
                            "WHERE occurrence_id = ANY(%s) AND trace_id <> %s", (ids, derivation.trace_id)))


# A send is read with the trace its row was written with. When its interaction has moved to another trace since,
# the move itself queued both traces, and the new one is derived after this, reading what is held then.
_AFFECTED_SQL = """
SELECT DISTINCT s.trace_id
  FROM label_sends s
  JOIN unnest(%s::text[], %s::timestamptz[], %s::timestamptz[]) AS c(entity_id, valid_from, valid_to)
    ON s.entity_id = c.entity_id AND s.occurred_at > c.valid_from AND s.occurred_at < c.valid_to
 WHERE s.trace_id <> %s
 ORDER BY s.trace_id
"""


def correct(tx: db.Transaction, trace_id: str, before: set[tuple]) -> list[str]:
    """Queue, in *tx*, what :func:`affected` returns. Returns the queued traces."""
    traces = affected(tx, trace_id, before)
    queue(tx, traces)
    return traces


def queue(tx: db.Transaction, traces) -> None:
    """Queue *traces* for derivation again, in sorted order: one transaction that queues
    several takes their queue rows in the order every other queue writer takes them."""
    tx.execute_many("SELECT dg_queue_label_trace(%s)", [(t,) for t in sorted(set(traces))])


def affected(tx: db.Transaction, trace_id: str, before: set[tuple]) -> list[str]:
    """Every other conversation the derivation just written can change:
    one with a leg sent by the entity of an occurrence that was added, removed or
    changed (*before* against the rows now), strictly inside that occurrence's
    interval (R1). Everything a match at such a leg reads from other conversations is
    the entity's occurrences valid at its time, so this is every leg whose result can
    differ, and no other. An unchanged derivation changes none."""
    changed = before ^ occurrences(tx, trace_id)
    if not changed:
        return []
    rows = sorted({r[:3] for r in changed})
    return [r[0] for r in tx.fetch_all(_AFFECTED_SQL, ([r[0] for r in rows], [r[1] for r in rows],
                                                       [r[2] for r in rows], trace_id))]


_CARRIED_SQL = """
INSERT INTO label_crossed_edges (edge_id, trace_id, level, ambiguous) VALUES (%s, %s, %s, %s)
ON CONFLICT (edge_id) DO UPDATE SET trace_id = EXCLUDED.trace_id, level = EXCLUDED.level,
    ambiguous = EXCLUDED.ambiguous
"""


def _carried_rows(derivation: Derivation) -> list[tuple]:
    return [(edge_id(e), derivation.trace_id, c.level, c.ambiguous)
            for e in derivation.edges if (c := derivation.carried.get(e.key)) is not None]


def _carried_by_interaction(rows) -> dict[str, set[tuple]]:
    out: dict[str, set[tuple]] = {}
    for interaction_id, *row in rows:
        out.setdefault(interaction_id, set()).add(tuple(row))
    return out


def _replace(tx: db.Transaction, table: str, key: str, trace_id: str, rows: list[tuple], sql: str) -> None:
    tx.execute_many(sql, rows)
    tx.execute(f"DELETE FROM {table} WHERE trace_id = %s AND NOT ({key} = ANY(%s::text[]))",
               (trace_id, [r[0] for r in rows]))


def write(tx: db.Transaction, derivation: Derivation, matcher: str) -> set[str]:
    """Persist *derivation* within *tx* and return the interactions whose inherited
    summary differs from the one stored before: a leg that had a row and now reads
    differently, a leg with no row before that now has edges (a decision may
    already have been made without them), a leg that lost its row while
    something had reached it, or one whose carried crossed labels changed."""
    trace_id = derivation.trace_id
    before = read_summaries(tx, trace_id)
    carried_before = _carried_by_interaction(tx.fetch_all(
        "SELECT e.to_interaction_id, c.edge_id, c.level, c.ambiguous FROM label_crossed_edges c "
        "JOIN payload_label_edges e ON e.edge_id = c.edge_id WHERE c.trace_id = %s", (trace_id,)))
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
    _replace(tx, "label_occurrences", "occurrence_id", trace_id,
             [_occurrence_row(h) for h in derivation.holdings], _OCCURRENCE_SQL)
    _replace(tx, "label_crossings", "crossing_id", trace_id, _crossing_rows(derivation), _CROSSING_SQL)
    _write_sends(tx, derivation)
    carried = _carried_rows(derivation)
    _replace(tx, "label_crossed_edges", "edge_id", trace_id, carried, _CARRIED_SQL)
    s = derivation.status
    tx.execute(_STATUS_SQL, (trace_id, s.status, s.unclassified_legs, s.unanchored_findings,
                             s.derived_legs, s.edge_count, matcher))
    changed = {key[0] for key, leg in derivation.legs.items()
               if (before[key] != _summary(leg) if key in before else leg.edge_count > 0)}
    changed |= {key[0] for key, row in before.items() if key not in derivation.legs and row[-1]}
    # how surely a carried label is the value's is what a decision reads too (it moves without the summary)
    to_of = {edge_id(e): e.to_key[0] for e in derivation.edges}
    carried_after = _carried_by_interaction((to_of[r[0]], r[0], r[2], r[3]) for r in carried)
    changed |= {i for i in carried_before.keys() | carried_after.keys()
                if carried_before.get(i) != carried_after.get(i)}
    return changed
