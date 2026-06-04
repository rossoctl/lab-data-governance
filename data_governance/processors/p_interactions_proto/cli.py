"""CLI driver for the P-interactions prototype. THROWAWAY.

Usage:
  python -m data_governance.processors.p_interactions_proto.cli <trace_id> [--scramble]

Reads spans for the given trace from Postgres in seq order, runs the
per-span procedure (procedure.py), drops + recreates scratch
tables (schema mirrors the Q21 / ADR-0007 layout), inserts results.

`--scramble` reverses span order to torture-test the late-parent re-eval
path (M8 in the rewrite plan).
"""

from __future__ import annotations

import json
import sys

from data_governance import db, retrieval

from .procedure import ExtractResult, extract


_DDL = """
DROP TABLE IF EXISTS interaction_spans CASCADE;
DROP TABLE IF EXISTS entity_spans CASCADE;
DROP TABLE IF EXISTS interactions CASCADE;
DROP TABLE IF EXISTS interaction_payloads CASCADE;
DROP TABLE IF EXISTS entities CASCADE;
DROP TABLE IF EXISTS processor_state CASCADE;

-- Q21 schema, mirrored as scratch tables.
-- Type-checked here as text rather than ENUMs to avoid alembic friction;
-- the production schema will use proper ENUMs (entity_kind,
-- entity_span_role, interaction_span_role).

CREATE TABLE entities (
  id            uuid PRIMARY KEY,
  kind          text NOT NULL,
  natural_key   text NOT NULL UNIQUE,
  display_name  text NOT NULL,
  project_name  text NULL,
  detected_from text NOT NULL,
  seq           bigint NOT NULL,
  original_seq  bigint NOT NULL,        -- ADR-0011: preserved at creation
  retracted_at  timestamptz NULL,        -- ADR-0011 tombstone
  trace_id      text NOT NULL  -- prototype-only column for cleanup
);

CREATE TABLE entity_spans (
  entity_id  uuid NOT NULL REFERENCES entities(id),
  trace_id   text NOT NULL,
  span_id    text NOT NULL,
  role       text NOT NULL,  -- discovered_via | identified_via
  PRIMARY KEY (entity_id, trace_id, span_id, role)
);

CREATE TABLE interaction_payloads (
  content_hash  text PRIMARY KEY,
  content_kind  text NOT NULL,
  content       jsonb NOT NULL,
  byte_size     int  NOT NULL
);

CREATE TABLE interactions (
  id                     uuid PRIMARY KEY,
  trace_id               text NOT NULL,
  parent_interaction_id  uuid NULL REFERENCES interactions(id),
  caller_entity_id       uuid NOT NULL REFERENCES entities(id),
  callee_entity_id       uuid NOT NULL REFERENCES entities(id),
  started_at             timestamptz NULL,
  ended_at               timestamptz NULL,
  error                  boolean NULL,
  request_payload_hash   text NULL REFERENCES interaction_payloads(content_hash),
  response_payload_hash  text NULL REFERENCES interaction_payloads(content_hash),
  summary                text NOT NULL,
  seq                    bigint NOT NULL,
  original_seq           bigint NOT NULL,        -- ADR-0011: preserved at creation
  retracted_at           timestamptz NULL,        -- ADR-0011 tombstone
  anchor_rule            text NOT NULL  -- prototype-only debug column
);
CREATE INDEX ON interactions(trace_id);
CREATE INDEX ON interactions(parent_interaction_id);

CREATE TABLE interaction_spans (
  interaction_id  uuid NOT NULL REFERENCES interactions(id) ON DELETE CASCADE,
  trace_id        text NOT NULL,
  span_id         text NOT NULL,
  role            text NOT NULL,  -- anchor | info | connector
  PRIMARY KEY (interaction_id, trace_id, span_id),
  -- ADR-0011 §3 schema invariant: each span belongs to at most one interaction.
  UNIQUE (trace_id, span_id)
);
CREATE INDEX ON interaction_spans(trace_id, span_id);

CREATE TABLE processor_state (
  processor_name      text PRIMARY KEY,
  last_processed_seq  bigint NOT NULL,
  updated_at          timestamptz NOT NULL DEFAULT now()
);
"""


def _fetch_trace_spans(trace_id: str) -> list[retrieval.Span]:
    out: list[retrieval.Span] = []
    cursor: int | None = None
    while True:
        result = retrieval.get_spans(
            cursor=cursor, limit=500, trace_id=trace_id, order="asc"
        )
        if not result.spans:
            break
        out.extend(result.spans)
        cursor = result.spans[-1].seq
        if len(result.spans) < 500:
            break
    return out


def _write_results(trace_id: str, result: ExtractResult) -> None:
    with db.transaction() as txn:
        txn.execute(_DDL)

        # Entities first (interactions reference them).
        for e in result.entities:
            txn.execute(
                "INSERT INTO entities(id, kind, natural_key, display_name, "
                "project_name, detected_from, seq, original_seq, retracted_at, trace_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (natural_key) DO NOTHING",
                (
                    e.id, e.kind, e.natural_key, e.display_name, e.project_name,
                    e.detected_from, e.seq, e.original_seq, e.retracted_at, trace_id,
                ),
            )

        for es in result.entity_spans:
            txn.execute(
                "INSERT INTO entity_spans(entity_id, trace_id, span_id, role) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (es.entity_id, es.trace_id, es.span_id, es.role),
            )

        for p in result.payloads:
            txn.execute(
                "INSERT INTO interaction_payloads(content_hash, content_kind, content, byte_size) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (content_hash) DO NOTHING",
                (p.content_hash, p.content_kind, json.dumps(p.content, default=str), p.byte_size),
            )

        # Two-pass insert for interactions to satisfy the parent_interaction_id
        # self-FK: insert all rows with parent NULL, then UPDATE the parent links.
        for ix in result.interactions:
            txn.execute(
                "INSERT INTO interactions(id, trace_id, parent_interaction_id, "
                "caller_entity_id, callee_entity_id, started_at, ended_at, error, "
                "request_payload_hash, response_payload_hash, summary, seq, "
                "original_seq, retracted_at, anchor_rule) "
                "VALUES (%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    ix.id, ix.trace_id, ix.caller_entity_id, ix.callee_entity_id,
                    ix.started_at, ix.ended_at, ix.error,
                    ix.request_payload_hash, ix.response_payload_hash,
                    ix.summary, ix.seq, ix.original_seq, ix.retracted_at,
                    ix.anchor_rule,
                ),
            )
        for ix in result.interactions:
            if ix.parent_interaction_id is not None:
                txn.execute(
                    "UPDATE interactions SET parent_interaction_id = %s WHERE id = %s",
                    (ix.parent_interaction_id, ix.id),
                )

        for row in result.interaction_spans:
            txn.execute(
                "INSERT INTO interaction_spans(interaction_id, trace_id, span_id, role) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (interaction_id, trace_id, span_id) DO UPDATE "
                "SET role = EXCLUDED.role",
                (row.interaction_id, row.trace_id, row.span_id, row.role),
            )

        txn.execute(
            "INSERT INTO processor_state(processor_name, last_processed_seq) "
            "VALUES ('p_interactions_proto', %s) "
            "ON CONFLICT (processor_name) DO UPDATE SET last_processed_seq = EXCLUDED.last_processed_seq, "
            "updated_at = now()",
            (result.last_processed_seq,),
        )


def _print_report(result: ExtractResult) -> None:
    # Default view: filter retracted (tombstoned) rows. The durable writer
    # commits the full set; display only shows active rows.
    active_entities = [e for e in result.entities if e.retracted_at is None]
    active_interactions = [ix for ix in result.interactions if ix.retracted_at is None]

    print("\n--- entities ---")
    for e in sorted(active_entities, key=lambda r: (r.kind, r.natural_key)):
        proj = f" [{e.project_name}]" if e.project_name else ""
        print(f"  [{e.kind:7}] {e.display_name:40}{proj}  ({e.detected_from})")

    print(f"\n--- interactions ({len(active_interactions)}) ---")
    ent_by_id = {e.id: e for e in active_entities}
    # Print as forest (parent -> children).
    children_of: dict[str | None, list] = {}
    for ix in active_interactions:
        children_of.setdefault(ix.parent_interaction_id, []).append(ix)

    def _line(ix, depth: int) -> None:
        caller = ent_by_id.get(ix.caller_entity_id)
        callee = ent_by_id.get(ix.callee_entity_id)
        err = "ERR" if ix.error else "ok " if ix.error is False else "?  "
        indent = "  " * (depth + 1)
        cn = caller.display_name if caller else "(?)"
        ce = callee.display_name if callee else "(?)"
        print(f"{indent}{err}  [{ix.anchor_rule:18}] {cn} → {ce}")

    def _walk(parent_id, depth):
        # Sort by seq only — started_at can be None or mixed types.
        kids = sorted(children_of.get(parent_id, []), key=lambda r: r.seq)
        for ix in kids:
            _line(ix, depth)
            _walk(ix.id, depth + 1)

    _walk(None, 0)

    print(f"\n--- payloads ({len(result.payloads)}) ---")
    counts: dict[str, int] = {}
    for p in result.payloads:
        counts[p.content_kind] = counts.get(p.content_kind, 0) + 1
    for k, v in sorted(counts.items()):
        print(f"  {k:24} {v}")

    print("\n--- entity_spans summary ---")
    by_role: dict[str, int] = {}
    for es in result.entity_spans:
        by_role[es.role] = by_role.get(es.role, 0) + 1
    for k, v in sorted(by_role.items()):
        print(f"  {k:18} {v}")

    print("\n--- interaction_spans roles ---")
    by_role = {}
    for r in result.interaction_spans:
        by_role[r.role] = by_role.get(r.role, 0) + 1
    for k, v in sorted(by_role.items()):
        print(f"  {k:18} {v}")

    print(f"\nlast_processed_seq = {result.last_processed_seq}")
    print("\n--- notes ---")
    for n in result.notes:
        print(f"  - {n}")


def main() -> int:
    args = sys.argv[1:]
    scramble = False
    if "--scramble" in args:
        scramble = True
        args.remove("--scramble")
    if len(args) != 1:
        print(__doc__)
        return 2
    trace_id = args[0]

    import os
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1
    db.configure(dsn)
    try:
        print(f"fetching spans for {trace_id}...")
        spans = _fetch_trace_spans(trace_id)
        print(f"  fetched {len(spans)} spans")
        print(f"running per-span procedure (scramble={scramble})...")
        result = extract(spans, scramble_for_late_parent=scramble)
        _print_report(result)
        print("\nwriting scratch tables...")
        _write_results(trace_id, result)
        print("done.")
    finally:
        db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
