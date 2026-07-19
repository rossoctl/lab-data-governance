"""Debug tool: materialise the graph algorithm's INTERMEDIATE graphs.

Usage:
  python -m data_governance.processors.interactions.graph.cli <trace_id>

Reads a trace's spans from Postgres, runs the graph extractor, and writes the
intermediate graph tables so the algorithm's coloring/inference can be eyeballed.
This is a DEV/DEBUG aid only — it is NOT the production path. Production derivation
of entities/interactions is done by
:mod:`data_governance.processors.interactions.graph_driver` (selected by
``INTERACTIONS_ALGORITHM=graph``), which writes the real ``entities`` /
``interactions`` / ``interaction_spans`` / ``interaction_payloads`` tables via
``state.flush``. The final ``proto_*`` output tables this tool used to write are
gone (superseded by those production tables); ``_DDL`` still drops any left over
from earlier versions so a debug run starts clean.

Intermediate scratch tables written (dropped + recreated per run):
    proto_base_nodes, proto_base_edges
        — base graph after Step 1 (white nodes + traceparent edges)
    proto_colored_nodes, proto_colored_edges
        — execution graph BEFORE entity formation (Step 2): Blue/Teal nodes,
          additive edge colors, inferred nodes/edges, combined-span duplicates,
          the Step 2.d node+edge merge, and between-boundary flag annotations
    proto_entity_nodes, proto_entity_edges
        — entity graph AFTER Step 3: one node per combined group (Step 3.a),
          one interaction per Teal transport chain (Step 3.b)
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
  -- node. Per ADR-0025: this column is the sole sanctioned signal for
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
  -- Per ADR-0025 Step 3.a: true iff every absorbed node was inferred.
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

        # The final entities / interactions / payloads / interaction_spans are NOT
        # written here anymore: the production `graph_driver` derives them into the
        # real `entities` / `interactions` / ... tables via `state.flush` (the
        # single source of truth). This tool writes only the INTERMEDIATE graph
        # tables (base / colored / entity), for eyeballing the algorithm's coloring
        # and inference — the leftover final `proto_*` tables from earlier versions
        # are still dropped by `_DDL` above so a debug run leaves a clean slate.


def process_trace(trace_id: str, *, progress=None) -> ExtractResult:
    """Fetch a trace's spans, run the graph extractor, write the intermediate
    graph tables. Returns the full ``ExtractResult`` (its final entities /
    interactions are also available in memory for callers that want them).

    Debug-only core behind :func:`main`. Single-trace: :func:`_write_results`
    runs ``_DDL`` which drops + recreates the intermediate ``proto_*`` graph
    tables, so each call holds exactly one trace. Production derivation of the
    final entities/interactions is the graph_driver's job, not this tool's.

    ``db.configure(...)`` must already have been called by the caller. ``progress``
    is an optional ``callable(str)`` for phase reporting.
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

        print("\n--- base graph ---")
        print(f"  {len(result.base_graph.nodes)} nodes  {len(result.base_graph.edges)} edges")

        print("\n--- colored graph ---")
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

        print("\n--- entity graph ---")
        print(f"  {len(result.entity_graph.nodes)} entities  "
              f"{len(result.entity_graph.edges)} edges")

        print(f"\n--- entities ({len(result.entities)}) ---")
        for e in sorted(result.entities, key=lambda x: x.natural_key):
            marker = " (inferred)" if e.inferred else ""
            print(f"  {e.natural_key:40}{marker} scopes={e.scope_name}")

        print(f"\n--- interactions ({len(result.interactions)}) ---")
        for ix in sorted(result.interactions, key=lambda r: r.order):
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
