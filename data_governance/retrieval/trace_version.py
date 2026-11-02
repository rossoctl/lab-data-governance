"""The **Trace version** — one opaque token that changes whenever a trace's
derived picture changes (ADR-0034).

A live trace page needs to know *whether* its trace has changed since it last
read it, cheaply, on every wake. It does not need to know *what* changed: every
derived read is a whole-trace read (``get_interactions``, ``get_entities``,
``get_data_lineage`` …), so "something changed" is already the full instruction
("read again"). The token is therefore a digest of the write-side high-water
marks, not a diff:

- ``legs``: the highest ``interaction_legs.seq`` in the trace. Covers every leg
  write, and — because the sidecar derivation re-inserts the whole trace per
  arriving span (ADR-0030) — every re-derivation too, even one that leaves the
  row count unchanged.
- ``ix``: the number of ``interactions`` rows. Belt and braces for a derivation
  that could delete without re-inserting.
- ``es``: the number of ``entity_spans`` rows. An **Entity** can be attributed
  to a trace before any of its interactions exist (``identified_via`` a span),
  and ``entity_spans`` carries no ``seq``, so the count is the only mark.
- ``lin``: the highest ``lineage_metadata.seq`` in the trace, plus the trace's
  lineage ``status``/``stopped_at_seq``. The lineage processor writes after the
  interactions processor and the status can flip with no new metadata row.

The token is a string the REST layer hands to the client verbatim and the
client compares for *equality only* — it must never be parsed, ordered or
arithmetically compared. A trace with nothing derived yet has a token too
(all marks zero), so a page opened before derivation starts can still wait on
it. Before the derived-table migrations have run the token is the same
all-zero value: never an error, like every other retrieval read.
"""

from __future__ import annotations

from data_governance import db

__all__ = ["get_trace_version"]


def _existing_tables(tx: db.Transaction, names: tuple[str, ...]) -> set[str]:
    rows = tx.fetch_all(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name = ANY(%s)",
        (list(names),),
    )
    return {r[0] for r in rows}


def get_trace_version(trace_id: str) -> str:
    """Return the trace's current version token (see the module docstring)."""
    with db.transaction() as tx:
        present = _existing_tables(
            tx, ("interactions", "lineage_metadata", "lineage_trace_status")
        )
        legs = ix = es = 0
        lin = 0
        status = ""
        stopped = 0
        if "interactions" in present:
            row = tx.fetch_one(
                "SELECT "
                "  COALESCE((SELECT MAX(l.seq) FROM interaction_legs l "
                "            JOIN interactions i ON i.id = l.interaction_id "
                "            WHERE i.trace_id = %(tid)s), 0), "
                "  (SELECT COUNT(*) FROM interactions WHERE trace_id = %(tid)s), "
                "  (SELECT COUNT(*) FROM entity_spans WHERE trace_id = %(tid)s)",
                {"tid": trace_id},
            )
            assert row is not None
            legs, ix, es = int(row[0]), int(row[1]), int(row[2])
        if "lineage_metadata" in present:
            row = tx.fetch_one(
                "SELECT COALESCE(MAX(m.seq), 0) FROM lineage_metadata m "
                "JOIN interactions i ON i.id = m.interaction_id "
                "WHERE i.trace_id = %s",
                (trace_id,),
            )
            assert row is not None
            lin = int(row[0])
        if "lineage_trace_status" in present:
            row = tx.fetch_one(
                "SELECT status::text, stopped_at_seq FROM lineage_trace_status "
                "WHERE trace_id = %s",
                (trace_id,),
            )
            if row is not None:
                status = str(row[0])
                stopped = int(row[1] or 0)
    return f"legs={legs};ix={ix};es={es};lin={lin};st={status}@{stopped}"
