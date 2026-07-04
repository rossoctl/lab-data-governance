"""CLI driver for the P-interactions graph prototype. THROWAWAY.

Usage:
  python -m data_governance.processors.p_interactions_proto.cli <trace_id>

Reads spans for the given trace from Postgres, runs the graph-based extractor,
drops + recreates scratch tables (proto_*) and inserts results.

Scratch tables written:
  Intermediate graphs (for evaluation):
    proto_base_nodes, proto_base_edges
        — base graph after Step 1 (white nodes + traceparent edges)
    proto_colored_nodes, proto_colored_edges
        — execution graph BEFORE entity formation (Step 2): Blue/Teal nodes,
          additive edge colors, inferred nodes/edges, combined-span duplicates,
          the Step 2.d node+edge merge, and between-boundary flag annotations
    proto_entity_nodes, proto_entity_edges
        — entity graph AFTER Step 3: one node per combined group (Step 3.a),
          one interaction per Teal transport chain (Step 3.b)

  Final output (same shape as linear-pass prototype for comparison):
    proto_entities
    proto_interaction_payloads
    proto_interactions
    proto_interaction_spans
"""

from __future__ import annotations

import json
import sys

from data_governance import db, retrieval

from .builder import _node_is_boundary
from .extractor import ExtractResult, extract


_DDL = """
DROP TABLE IF EXISTS proto_interaction_spans CASCADE;
DROP TABLE IF EXISTS proto_interactions CASCADE;
DROP TABLE IF EXISTS proto_interaction_payloads CASCADE;
DROP TABLE IF EXISTS proto_entities CASCADE;

DROP TABLE IF EXISTS proto_entity_edges CASCADE;
DROP TABLE IF EXISTS proto_entity_node_spans CASCADE;
DROP TABLE IF EXISTS proto_entity_nodes CASCADE;

DROP TABLE IF EXISTS proto_colored_edges CASCADE;
DROP TABLE IF EXISTS proto_colored_nodes CASCADE;

DROP TABLE IF EXISTS proto_base_edges CASCADE;
DROP TABLE IF EXISTS proto_base_nodes CASCADE;

-- Legacy table cleanup (replaced by proto_base_*, proto_colored_*, proto_entity_*).
DROP TABLE IF EXISTS proto_xscope_node_spans CASCADE;
DROP TABLE IF EXISTS proto_xscope_edges CASCADE;
DROP TABLE IF EXISTS proto_xscope_nodes CASCADE;
DROP TABLE IF EXISTS proto_merged_node_spans CASCADE;
DROP TABLE IF EXISTS proto_merged_edges CASCADE;
DROP TABLE IF EXISTS proto_merged_nodes CASCADE;
DROP TABLE IF EXISTS proto_graph_node_spans CASCADE;
DROP TABLE IF EXISTS proto_graph_edges CASCADE;
DROP TABLE IF EXISTS proto_graph_nodes CASCADE;

-- Step 1 base graph (white)
CREATE TABLE proto_base_nodes (
  id         text PRIMARY KEY,
  span_id    text NOT NULL,
  scope      text NOT NULL,
  attributes jsonb NOT NULL DEFAULT '{}',
  trace_id   text NOT NULL
);

CREATE TABLE proto_base_edges (
  id           text PRIMARY KEY,
  from_node_id text NOT NULL REFERENCES proto_base_nodes(id),
  to_node_id   text NOT NULL REFERENCES proto_base_nodes(id),
  trace_id     text NOT NULL
);

-- Step 2 colored execution graph (before fuse)
CREATE TABLE proto_colored_nodes (
  id                  text PRIMARY KEY,
  span_id             text NOT NULL,
  scope               text NOT NULL,
  color               text NOT NULL,            -- white | blue | teal
  is_boundary         boolean NOT NULL DEFAULT false,
  is_target_duplicate boolean NOT NULL DEFAULT false,
  -- Set by Step 2.c / Step 2.d on the materialised inferred (e.g. unobserved-peer)
  -- node. Per ADR-0007: this column is the sole sanctioned signal for
  -- "inferred node"; do not parse the `label` column for that purpose.
  is_inferred         boolean NOT NULL DEFAULT false,
  flagged             boolean NOT NULL DEFAULT false,
  label               text NULL,
  attributes          jsonb NOT NULL DEFAULT '{}',
  trace_id            text NOT NULL
);

CREATE TABLE proto_colored_edges (
  id           text PRIMARY KEY,
  from_node_id text NOT NULL REFERENCES proto_colored_nodes(id),
  to_node_id   text NOT NULL REFERENCES proto_colored_nodes(id),
  colors       text NOT NULL,                   -- comma-joined subset of {white,blue,teal}
  kind         text NOT NULL,                   -- highest applied color (for display)
  trace_id     text NOT NULL
);

-- Step 3 entity graph (after fuse)
CREATE TABLE proto_entity_nodes (
  id                 text PRIMARY KEY,
  label              text NULL,
  attributes         jsonb NOT NULL DEFAULT '{}',
  contains_boundary  boolean NOT NULL DEFAULT false,
  contains_blue      boolean NOT NULL DEFAULT false,
  contains_teal      boolean NOT NULL DEFAULT false,
  -- Per ADR-0007 Step 3.a: true iff every absorbed node was inferred.
  -- This column is the sole sanctioned signal for "inferred entity"; do not
  -- parse the `label` column for that purpose.
  inferred           boolean NOT NULL DEFAULT false,
  scopes             text NOT NULL DEFAULT '',
  trace_id           text NOT NULL
);

CREATE TABLE proto_entity_node_spans (
  node_id text NOT NULL REFERENCES proto_entity_nodes(id),
  span_id text NOT NULL,
  PRIMARY KEY (node_id, span_id)
);

CREATE TABLE proto_entity_edges (
  id           text PRIMARY KEY,
  from_node_id text NOT NULL REFERENCES proto_entity_nodes(id),
  to_node_id   text NOT NULL REFERENCES proto_entity_nodes(id),
  trace_id     text NOT NULL
);

-- Final output
CREATE TABLE proto_entities (
  id             text PRIMARY KEY,
  -- natural_key carries the kind as a typed prefix (`llm:` / `tool:` /
  -- `agent:`) per ADR-0007 "Natural-key prefixes (an implementation construct,
  -- not a spec vocabulary)". Consumers split on `:` rather than reading a
  -- separate kind column.
  natural_key    text NOT NULL,
  display_name   text NOT NULL,
  detected_from  text NOT NULL,
  scope_name     text NOT NULL,
  anchor_span_id text NULL,
  -- Per ADR-0007: inferred identity is a typed boolean, never derived
  -- from label/natural_key parsing. UI and downstream queries filter on
  -- this column.
  inferred       boolean NOT NULL DEFAULT false,
  trace_id       text NOT NULL
);

CREATE TABLE proto_interaction_payloads (
  content_hash text PRIMARY KEY,
  content_kind text NOT NULL,
  content      jsonb NOT NULL,
  byte_size    int NOT NULL
);

CREATE TABLE proto_interactions (
  id                     text PRIMARY KEY,
  caller_entity_id       text NOT NULL REFERENCES proto_entities(id),
  callee_entity_id       text NOT NULL REFERENCES proto_entities(id),
  started_at             timestamptz NOT NULL,
  ended_at               timestamptz NULL,
  error                  boolean NULL,
  request_payload_hash   text NULL REFERENCES proto_interaction_payloads(content_hash),
  response_payload_hash  text NULL REFERENCES proto_interaction_payloads(content_hash),
  summary                text NOT NULL,
  -- Intra-turn ordering tiebreak (ADR-0007 "Inferred interaction ordering").
  -- "order" is a SQL reserved word, so it is always double-quoted. Consumers
  -- sort by (started_at, "order").
  "order"                integer NOT NULL DEFAULT 0,
  trace_id               text NOT NULL
);
CREATE INDEX ON proto_interactions(trace_id);

CREATE TABLE proto_interaction_spans (
  interaction_id text NOT NULL REFERENCES proto_interactions(id) ON DELETE CASCADE,
  trace_id       text NOT NULL,
  span_id        text NOT NULL,
  is_anchor      boolean NOT NULL DEFAULT false,
  PRIMARY KEY (interaction_id, trace_id, span_id)
);
CREATE INDEX ON proto_interaction_spans(trace_id, span_id);
"""


def _fetch_trace_spans(trace_id: str) -> list[retrieval.Span]:
    out: list[retrieval.Span] = []
    cursor: int | None = None
    while True:
        result = retrieval.get_spans(cursor=cursor, limit=500, trace_id=trace_id, order="asc")
        if not result.spans:
            break
        out.extend(result.spans)
        cursor = result.spans[-1].seq
        if len(result.spans) < 500:
            break
    return out


def _scopes_for_entity(entity_node, span_by_id) -> str:
    scopes: list[str] = []
    for sid in entity_node.span_ids:
        s = span_by_id.get(sid)
        if s is None:
            continue
        scope = (s.scope or {}).get("name") or ""
        if scope and scope not in scopes:
            scopes.append(scope)
    return ",".join(scopes)


def _write_results(trace_id: str, result: ExtractResult, span_by_id) -> None:
    with db.transaction() as txn:
        txn.execute(_DDL)

        # --- Step 1 base graph ---
        for node in result.base_graph.nodes:
            txn.execute(
                "INSERT INTO proto_base_nodes(id, span_id, scope, attributes, trace_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (node.id, node.span_id, node.scope,
                 json.dumps(node.attributes, default=str), trace_id),
            )
        for edge in result.base_graph.edges:
            txn.execute(
                "INSERT INTO proto_base_edges(id, from_node_id, to_node_id, trace_id) "
                "VALUES (%s, %s, %s, %s)",
                (edge.id, edge.from_node_id, edge.to_node_id, trace_id),
            )

        # --- Steps 2–4 colored execution graph (before fuse) ---
        for node in result.colored_graph.nodes:
            txn.execute(
                "INSERT INTO proto_colored_nodes("
                "id, span_id, scope, color, is_boundary, is_target_duplicate, "
                "is_inferred, flagged, label, attributes, trace_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (node.id, node.span_id, node.scope, node.color,
                 _node_is_boundary(node, span_by_id), node.is_target_duplicate,
                 node.is_inferred, node.flagged, node.label,
                 json.dumps(node.attributes, default=str), trace_id),
            )
        for edge in result.colored_graph.edges:
            colors_csv = ",".join(sorted(edge.colors))
            txn.execute(
                "INSERT INTO proto_colored_edges("
                "id, from_node_id, to_node_id, colors, kind, trace_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (edge.id, edge.from_node_id, edge.to_node_id,
                 colors_csv, edge.kind, trace_id),
            )

        # --- Step 3 entity graph (after fuse) ---
        for node in result.entity_graph.nodes:
            scopes = _scopes_for_entity(node, span_by_id)
            txn.execute(
                "INSERT INTO proto_entity_nodes("
                "id, label, attributes, contains_boundary, contains_blue, "
                "contains_teal, inferred, scopes, trace_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (node.id, node.label, json.dumps(node.attributes, default=str),
                 node.contains_boundary, node.contains_blue, node.contains_teal,
                 node.inferred, scopes, trace_id),
            )
            for sid in node.span_ids:
                txn.execute(
                    "INSERT INTO proto_entity_node_spans(node_id, span_id) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (node.id, sid),
                )
        for edge in result.entity_graph.edges:
            txn.execute(
                "INSERT INTO proto_entity_edges(id, from_node_id, to_node_id, trace_id) "
                "VALUES (%s, %s, %s, %s)",
                (edge.id, edge.from_node_id, edge.to_node_id, trace_id),
            )

        # --- Final output ---
        for e in result.entities:
            txn.execute(
                "INSERT INTO proto_entities("
                "id, natural_key, display_name, detected_from, "
                "scope_name, anchor_span_id, inferred, trace_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (e.id, e.natural_key, e.display_name, e.detected_from,
                 e.scope_name, e.anchor_span_id, e.inferred, trace_id),
            )
        for p in result.payloads:
            txn.execute(
                "INSERT INTO proto_interaction_payloads("
                "content_hash, content_kind, content, byte_size) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (content_hash) DO NOTHING",
                (p.content_hash, p.content_kind, json.dumps(p.content, default=str), p.byte_size),
            )
        for ix in result.interactions:
            txn.execute(
                "INSERT INTO proto_interactions("
                "id, caller_entity_id, callee_entity_id, started_at, ended_at, "
                "error, request_payload_hash, response_payload_hash, summary, "
                '"order", trace_id) '
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (ix.id, ix.caller_entity_id, ix.callee_entity_id, ix.started_at, ix.ended_at,
                 ix.error, ix.request_payload_hash, ix.response_payload_hash, ix.summary,
                 ix.order, trace_id),
            )
        for ev in result.interaction_spans:
            txn.execute(
                "INSERT INTO proto_interaction_spans("
                "interaction_id, trace_id, span_id, is_anchor) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (ev.interaction_id, ev.trace_id, ev.span_id, ev.is_anchor),
            )


def process_trace(trace_id: str, *, progress=None) -> ExtractResult:
    """Fetch a trace's spans, run the graph extractor, write scratch tables.

    The reusable core shared by the CLI (:func:`main`) and the UI's
    ``POST /proto/process/<trace_id>`` endpoint. Single-trace semantics,
    identical to the CLI: :func:`_write_results` runs ``_DDL`` which drops
    and recreates every ``proto_*`` table, so each call holds exactly one
    trace.

    ``db.configure(...)`` must already have been called by the caller.

    ``progress`` is an optional ``callable(str)`` used by the CLI for its
    phase reporting; the API caller passes nothing.
    """
    def _say(msg: str) -> None:
        if progress is not None:
            progress(msg)

    _say(f"fetching spans for {trace_id}...")
    spans = _fetch_trace_spans(trace_id)
    _say(f"  fetched {len(spans)} spans")

    _say("running graph extractor...")
    result = extract(spans)
    span_by_id = {s.span_id: s for s in spans}

    _say("writing scratch tables...")
    _write_results(trace_id, result, span_by_id)
    return result


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
        result = process_trace(trace_id, progress=print)

        print(f"\n--- base graph ---")
        print(f"  {len(result.base_graph.nodes)} nodes  {len(result.base_graph.edges)} edges")

        print(f"\n--- colored graph ---")
        n_blue = sum(1 for n in result.colored_graph.nodes if n.color == "blue")
        n_teal = sum(1 for n in result.colored_graph.nodes if n.color == "teal")
        n_dup = sum(1 for n in result.colored_graph.nodes if n.is_target_duplicate)
        n_inferred = sum(1 for n in result.colored_graph.nodes if n.is_inferred)
        n_flag = sum(1 for n in result.colored_graph.nodes if n.flagged)
        # Boundary count is reported in the notes (it needs span facts to
        # re-derive for observed nodes); the colored-graph note line has it.
        print(f"  {n_blue} blue, {n_teal} teal "
              f"({n_dup} target duplicates, {n_inferred} inferred)  "
              f"{len(result.colored_graph.edges)} edges  {n_flag} flagged")

        print(f"\n--- entity graph ---")
        print(f"  {len(result.entity_graph.nodes)} entities  "
              f"{len(result.entity_graph.edges)} edges")

        print(f"\n--- entities ({len(result.entities)}) ---")
        for e in sorted(result.entities, key=lambda x: x.natural_key):
            inferred = " (inferred)" if e.inferred else ""
            print(f"  {e.natural_key:40}{inferred} scopes={e.scope_name}")

        print(f"\n--- interactions ({len(result.interactions)}) ---")
        for ix in sorted(result.interactions, key=lambda r: (r.started_at, r.order)):
            err = "ERR" if ix.error else "ok " if ix.error is False else "?  "
            print(f"  {err}  {ix.summary}")

        print(f"\n--- payloads ({len(result.payloads)}) ---")
        kind_counts: dict[str, int] = {}
        for p in result.payloads:
            kind_counts[p.content_kind] = kind_counts.get(p.content_kind, 0) + 1
        for k, v in sorted(kind_counts.items()):
            print(f"  {k:30} {v}")

        print("\n--- notes ---")
        for n in result.notes:
            print(f"  - {n}")

        print("\ndone.")
    finally:
        db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
