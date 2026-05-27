"""CLI driver for the P-interactions prototype. THROWAWAY.

Usage:
  python -m data_governance.processors.p_interactions_proto.cli <trace_id>

Reads spans for the given trace from Postgres in seq order, runs the
extractor, drops + recreates scratch tables (proto_*) and inserts results.
"""

from __future__ import annotations

import dataclasses
import json
import sys

from data_governance import db, retrieval

from .extractor import (
    ExtractResult,
    ProtoEntity,
    ProtoInteraction,
    ProtoInteractionSpan,
    ProtoPayload,
    extract,
)


_DDL = """
DROP TABLE IF EXISTS proto_interaction_spans CASCADE;
DROP TABLE IF EXISTS proto_interactions CASCADE;
DROP TABLE IF EXISTS proto_interaction_payloads CASCADE;
DROP TABLE IF EXISTS proto_entities CASCADE;

CREATE TABLE proto_entities (
  id           uuid PRIMARY KEY,
  kind         text NOT NULL,
  natural_key  text NOT NULL UNIQUE,
  display_name text NOT NULL,
  detected_from text NOT NULL,
  trace_id     text NOT NULL
);

CREATE TABLE proto_interaction_payloads (
  content_hash text PRIMARY KEY,
  content_kind text NOT NULL,
  content      jsonb NOT NULL,
  byte_size    int NOT NULL
);

CREATE TABLE proto_interactions (
  id                     uuid PRIMARY KEY,
  caller_entity_id       uuid NOT NULL REFERENCES proto_entities(id),
  callee_entity_id       uuid NOT NULL REFERENCES proto_entities(id),
  started_at             timestamptz NOT NULL,
  ended_at               timestamptz NULL,
  error                  boolean NULL,
  request_payload_hash   text NULL REFERENCES proto_interaction_payloads(content_hash),
  response_payload_hash  text NULL REFERENCES proto_interaction_payloads(content_hash),
  summary                text NOT NULL,
  trace_id               text NOT NULL
);
CREATE INDEX ON proto_interactions(trace_id);

CREATE TABLE proto_interaction_spans (
  interaction_id uuid NOT NULL REFERENCES proto_interactions(id) ON DELETE CASCADE,
  trace_id       text NOT NULL,
  span_id        text NOT NULL,
  is_anchor      boolean NOT NULL DEFAULT false,
  PRIMARY KEY (interaction_id, trace_id, span_id)
);
CREATE INDEX ON proto_interaction_spans(trace_id, span_id);
"""


def _fetch_trace_spans(trace_id: str) -> list[retrieval.Span]:
    """Fetch all spans for a trace, paginating by seq."""
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
        # Run DDL — split on ';' since execute() expects single statements is fine
        # in psycopg 3 actually; pass the whole script.
        txn.execute(_DDL)
        for e in result.entities:
            txn.execute(
                "INSERT INTO proto_entities(id, kind, natural_key, display_name, detected_from, trace_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (e.id, e.kind, e.natural_key, e.display_name, e.detected_from, trace_id),
            )
        for p in result.payloads:
            txn.execute(
                "INSERT INTO proto_interaction_payloads(content_hash, content_kind, content, byte_size) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (content_hash) DO NOTHING",
                (p.content_hash, p.content_kind, json.dumps(p.content, default=str), p.byte_size),
            )
        for ix in result.interactions:
            txn.execute(
                "INSERT INTO proto_interactions(id, caller_entity_id, callee_entity_id, "
                "started_at, ended_at, error, request_payload_hash, response_payload_hash, "
                "summary, trace_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    ix.id,
                    ix.caller_entity_id,
                    ix.callee_entity_id,
                    ix.started_at,
                    ix.ended_at,
                    ix.error,
                    ix.request_payload_hash,
                    ix.response_payload_hash,
                    ix.summary,
                    trace_id,
                ),
            )
        for ev in result.interaction_spans:
            txn.execute(
                "INSERT INTO proto_interaction_spans(interaction_id, trace_id, span_id, is_anchor) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (ev.interaction_id, ev.trace_id, ev.span_id, ev.is_anchor),
            )


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    trace_id = sys.argv[1]

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

        print("running extractor...")
        result = extract(spans)

        print("\n--- entities ---")
        for e in result.entities:
            print(f"  [{e.kind:16}] {e.display_name:40} ({e.detected_from})")

        print(f"\n--- interactions ({len(result.interactions)}) ---")
        for ix in sorted(result.interactions, key=lambda r: r.started_at):
            ent_by_id = {e.id: e for e in result.entities}
            caller = ent_by_id.get(ix.caller_entity_id)
            callee = ent_by_id.get(ix.callee_entity_id)
            err = "ERR" if ix.error else "ok " if ix.error is False else "?  "
            print(f"  {err}  {ix.summary}")

        print(f"\n--- payloads ({len(result.payloads)}) ---")
        kind_counts: dict[str, int] = {}
        for p in result.payloads:
            kind_counts[p.content_kind] = kind_counts.get(p.content_kind, 0) + 1
        for k, v in sorted(kind_counts.items()):
            print(f"  {k:24} {v}")

        print("\n--- notes ---")
        for n in result.notes:
            print(f"  - {n}")

        print("\nwriting scratch tables...")
        _write_results(trace_id, result)
        print("done.")
    finally:
        db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
