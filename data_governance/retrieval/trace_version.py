"""The **Trace version** — one opaque token that changes whenever a trace's
derived picture changes (ADR-0034).

A live trace page needs to know *whether* its trace has changed since it last
read it, cheaply, on every wake. It does not need to know *what* changed: every
derived read is a whole-trace read (``get_interactions``, ``get_entities``,
``get_data_lineage`` …), so "something changed" is already the full instruction
("read again"). The token is therefore a digest of the write-side marks, not a
diff:

- ``legs``: the highest ``interaction_legs.seq`` in the trace. A leg's ``seq``
  is assigned once, at its first INSERT, so this moves on every new leg —
  including a leg deleted and re-inserted by a re-derivation, which keeps the
  row count but takes a fresh ``seq``.
- ``es``: the number of ``entity_spans`` rows. An **Entity** can be attributed
  to a trace before any of its interactions exist (``identified_via`` a span),
  and ``entity_spans`` carries no ``seq``, so the count is the only mark.
- ``dig``: an md5 over the trace's interactions ``(id, parent_interaction_id,
  caller_entity_id, callee_entity_id)`` and its legs ``(interaction_id,
  leg_type, occurred_at, payload_hash, error, content_kind)`` — the inputs the
  causal leg order (ADR-0035) and the derived views read.
- ``lin``: the highest ``lineage_metadata.seq`` in the trace and an md5 of the
  trace's lineage rows (the triple each read returns), plus the trace's
  lineage ``status``/``stopped_at_seq``. The lineage processor writes after the
  interactions processor and the status can flip with no new metadata row.
- ``lab``: the trace's label-lineage status, edge count and ``derived_at``
  (migration 0024). Label lineage writes after classification, and every
  derivation stamps ``derived_at``. Only present once the trace has a status row,
  so a token from before label lineage reads as it always did.
- ``irr`` / ``trr``: the numbers of ``interaction_risk_records`` and
  ``trace_risk_records`` rows in the trace. Both tables are insert-only and
  versioned (migration 0016): a recompute is a new row, never an update, so
  the count moves on every risk write. The risk processors write after
  derivation, so a trace's decision is often its last write.
- ``al``: a digest of the trace's ``alerts`` rows, whole. An alert is the one
  risk write that is updated in place — a duplicate bumps ``duplicate_count``,
  a supersession sets ``superseded_by``, an operator changes ``status`` — and
  no count or high-water mark sees an update. A trace has few alerts (one per
  trace risk version at most), so hashing them is as cheap as counting.

**Why ``dig`` exists: ``seq`` cannot see an in-place correction.** Both
interaction writers re-derive a trace by *upserting* its rows, and a leg's
``seq`` is deliberately absent from the upsert's ``DO UPDATE`` (it is the
ingestion cursor, preserved for replay determinism — ADR-0035). So a re-derive
that re-parents an interaction, retargets its caller or callee, or rewrites a
leg's time, payload, outcome or content kind moves no ``seq`` and no row
count, yet it can reorder the trace and change every view. Only the values
themselves can show that, so the token carries a digest of them. Its rendering
is fixed so that equal values always give equal tokens: rows in key order,
each as a record (where NULL and ``''`` render differently), and
``occurred_at`` as its epoch in seconds — the same instant to the microsecond
whatever the session's ``TimeZone`` or ``DateStyle``. A re-derive that writes
identical values back therefore leaves the token alone.

The lineage rows have the same property one step later: the lineage
processor writes each row under its leg's preserved ``seq``, so re-deriving a
corrected trace rewrites rows in place with ``MAX(seq)`` unchanged and the
status often unchanged too. The interactions-side correction has already
moved ``dig`` by then, but a page that re-read before the lineage write landed
would keep the old lineage, so ``lin`` carries a digest of the rows as well.

The digest replaces an earlier ``ix`` mark, the number of ``interactions``
rows: the digest lists every interaction of the trace by id, so it already
changes whenever that number does.

The token is a string the REST layer hands to the client verbatim and the
client compares for *equality only* — it must never be parsed, ordered or
arithmetically compared. A trace with nothing derived yet has a token too
(all marks zero), so a page opened before derivation starts can still wait on
it. Before the derived-table migrations have run the token is the same
all-zero value: never an error, like every other retrieval read. A table or
column that a later migration adds or renames is probed first: an added one
reads as absent until it exists, a renamed one under its earlier name.
"""

from __future__ import annotations

from data_governance import db

__all__ = ["get_trace_version"]

#: The tables the token reads beyond ``entity_spans`` (which lands with
#: ``interactions``), probed so an unmigrated DB reads as all zeros.
_TABLES = (
    "interactions",
    "interaction_legs",
    "lineage_metadata",
    "lineage_trace_status",
    "label_lineage_trace_status",
    "trace_risk_records",
)

#: Added by migration 0022, after its table.
_LEG_CONTENT_KIND = "interaction_legs.content_kind"
#: Named ``entity_path`` when 0013 creates the table; 0015 renames it.
_LINEAGE_ENTITIES = "lineage_metadata.entities"

#: Every column the token reads that a migration after its table's creation
#: adds or renames. The rest date from their table's creation (0004, 0009,
#: 0013, 0014).
_COLUMNS = (_LEG_CONTENT_KIND, _LINEAGE_ENTITIES)


def _existing_schema(tx: db.Transaction) -> set[str]:
    """The probed tables that exist, plus ``table.column`` for each probed
    column that does. One round trip.

    Each column is matched as its own ``table_name``/``column_name`` pair:
    the catalog view filters those before it joins, while a match on the
    joined text runs about four times slower, on every wake of every page.
    """
    pairs = [column.split(".") for column in _COLUMNS]
    rows = tx.fetch_all(
        "SELECT table_name::text FROM information_schema.tables "
        "WHERE table_name = ANY(%s) "
        "UNION ALL "
        "SELECT table_name::text || '.' || column_name::text "
        "FROM information_schema.columns "
        "WHERE "
        + " OR ".join(["(table_name = %s AND column_name = %s)"] * len(pairs)),
        (list(_TABLES), *(name for pair in pairs for name in pair)),
    )
    return {r[0] for r in rows}


def _derivation_marks_sql(present: set[str]) -> str:
    """One statement for ``es``, ``legs`` and ``dig``.

    Both aggregates scope through ``interactions.trace_id`` (legs carry no
    trace id, ADR-0025). The legs are reached as ``interaction_id =
    ANY(ARRAY(<the trace's ids>))`` rather than as a join: with a join the
    planner hash-joins a long trace against a sequential scan of every leg in
    the database (measured: 1000 legs of 161k, 44 ms), where the array form
    probes the legs' ``interaction_id`` index for exactly the trace's legs
    (4.5 ms) — and this runs on every wake of every open page.

    ``dig`` is NULL — rendered ``0`` — when the trace has no interactions,
    which is also when it has no legs. The ``'#'`` between the two row lists
    keeps them apart: a record's text cannot end early, so the joined string
    decodes one way only.
    """
    if "interaction_legs" in present:
        content_kind = (
            "l.content_kind" if _LEG_CONTENT_KIND in present else "NULL::text"
        )
        legs = (
            "SELECT COALESCE(MAX(l.seq), 0) AS max_seq, "
            "  string_agg(ROW(l.interaction_id, l.leg_type, "
            "                 extract(epoch FROM l.occurred_at), l.payload_hash, "
            f"                 l.error, {content_kind})::text, "
            "             '|' ORDER BY l.interaction_id, l.leg_type) AS rows "
            "FROM interaction_legs l "
            "WHERE l.interaction_id = ANY(ARRAY("
            "  SELECT id FROM interactions WHERE trace_id = %(tid)s))"
        )
    else:
        legs = "SELECT 0 AS max_seq, NULL::text AS rows"
    return (
        "SELECT "
        "  (SELECT COUNT(*) FROM entity_spans WHERE trace_id = %(tid)s), "
        "  legs.max_seq, "
        "  md5(ix.rows || '#' || COALESCE(legs.rows, '')) "
        "FROM "
        "  (SELECT string_agg(ROW(i.id, i.parent_interaction_id, "
        "                         i.caller_entity_id, i.callee_entity_id)::text, "
        "                     '|' ORDER BY i.id) AS rows "
        "   FROM interactions i WHERE i.trace_id = %(tid)s) AS ix, "
        f"  ({legs}) AS legs"
    )


def get_trace_version(trace_id: str) -> str:
    """Return the trace's current version token (see the module docstring)."""
    with db.transaction() as tx:
        present = _existing_schema(tx)
        legs = es = 0
        digest = "0"
        lin = 0
        lin_digest = "0"
        status = ""
        stopped = 0
        irr = trr = 0
        alerts = "0"
        if "interactions" in present:
            row = tx.fetch_one(_derivation_marks_sql(present), {"tid": trace_id})
            assert row is not None
            es, legs = int(row[0]), int(row[1])
            if row[2] is not None:
                digest = str(row[2])
        if "lineage_metadata" in present:
            # The rows' own content, not only MAX(seq): the lineage processor
            # writes each row under its leg's preserved seq, so re-deriving a
            # corrected trace rewrites rows in place with no seq moving.
            # Arrays and JSONB render canonically as text; key order fixed.
            # Before 0015 the entity set is ``entity_path``: the same values.
            entities = (
                "m.entities" if _LINEAGE_ENTITIES in present else "m.entity_path"
            )
            row = tx.fetch_one(
                "SELECT COALESCE(MAX(m.seq), 0), "
                "  md5(string_agg(ROW(m.interaction_id, m.leg_type, m.seq, "
                "                     m.data_sources, m.source_transformations, "
                f"                     {entities})::text, "
                "                 '|' ORDER BY m.interaction_id, m.leg_type)) "
                "FROM lineage_metadata m "
                "WHERE m.interaction_id = ANY(ARRAY("
                "  SELECT id FROM interactions WHERE trace_id = %s))",
                (trace_id,),
            )
            assert row is not None
            lin = int(row[0])
            if row[1] is not None:
                lin_digest = str(row[1])
        if "lineage_trace_status" in present:
            row = tx.fetch_one(
                "SELECT status::text, stopped_at_seq FROM lineage_trace_status "
                "WHERE trace_id = %s",
                (trace_id,),
            )
            if row is not None:
                status = str(row[0])
                stopped = int(row[1] or 0)
        # The three risk tables arrive together (migration 0016).
        if "trace_risk_records" in present:
            row = tx.fetch_one(
                "SELECT "
                "  (SELECT COUNT(*) FROM interaction_risk_records "
                "   WHERE trace_id = %(tid)s), "
                "  (SELECT COUNT(*) FROM trace_risk_records WHERE trace_id = %(tid)s), "
                "  (SELECT md5(string_agg(a::text, ',' ORDER BY a.alert_id)) "
                "   FROM alerts a WHERE a.trace_id = %(tid)s)",
                {"tid": trace_id},
            )
            assert row is not None
            irr, trr = int(row[0]), int(row[1])
            alerts = row[2] or "0"
        lab = ""
        if "label_lineage_trace_status" in present:
            row = tx.fetch_one(
                "SELECT status, edge_count, derived_at FROM label_lineage_trace_status "
                "WHERE trace_id = %s",
                (trace_id,),
            )
            if row is not None:
                lab = f";lab={row[0]}:{int(row[1])}@{row[2].isoformat()}"
    return (
        f"legs={legs};es={es};dig={digest};"
        f"lin={lin}:{lin_digest};st={status}@{stopped};"
        f"irr={irr};trr={trr};al={alerts}{lab}"
    )
