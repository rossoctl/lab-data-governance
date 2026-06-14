"""CLI driver for the P-interactions prototype. THROWAWAY.

Usage:
  python -m data_governance.processors.p_interactions_proto.cli [<trace_id>] [--scramble]
  python -m data_governance.processors.p_interactions_proto.cli [--since <dur>]

Reads spans for the given trace from Postgres in seq order, runs the
per-span procedure (procedure.py), drops + recreates scratch
tables (schema mirrors the Q21 / ADR-0007 layout), inserts results.

If `<trace_id>` is omitted, runs over **every** trace in the DB: the
scratch tables are reset once, then each trace is extracted and inserted
into the same tables (entities dedupe on natural_key, so traces
accumulate). Per-trace failures are logged and skipped; a final summary
reports how many traces succeeded/failed.

`--since <dur>` (e.g. `24h`, `90m`, `7d`; bare number = seconds) narrows
the all-traces run to traces whose root span *started* within the last
`<dur>`. Cannot be combined with an explicit `<trace_id>`.

`--scramble` reverses span order to torture-test the late-parent re-eval
path (M8 in the rewrite plan). It requires an explicit `<trace_id>`.
"""

from __future__ import annotations

import datetime as dt
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


def _reset_tables() -> None:
    """Drop + recreate the scratch tables (run once before any insert)."""
    with db.transaction() as txn:
        txn.execute(_DDL)


def _write_results(trace_id: str, result: ExtractResult) -> None:
    """Reset the scratch tables, then write a single trace's results."""
    _reset_tables()
    _insert_results(trace_id, result)


def _insert_results(trace_id: str, result: ExtractResult) -> None:
    """Insert one trace's results into the (already-created) scratch tables.

    Idempotent across traces: entities dedupe on ``natural_key``, so this can
    be called once per trace to accumulate every trace into the same tables.
    """
    with db.transaction() as txn:
        # Entities first (interactions reference them). The same logical entity
        # (natural_key) recurs across traces with a fresh per-run uuid, so on
        # conflict we keep the already-stored row and map this run's id onto the
        # canonical one. Every later entity_id reference is remapped through it,
        # otherwise the FK to entities breaks when traces accumulate.
        entity_id_map: dict[str, str] = {}
        for e in result.entities:
            row = txn.fetch_one(
                "INSERT INTO entities(id, kind, natural_key, display_name, "
                "project_name, detected_from, seq, original_seq, retracted_at, trace_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (natural_key) DO UPDATE SET natural_key = EXCLUDED.natural_key "
                "RETURNING id",
                (
                    e.id, e.kind, e.natural_key, e.display_name, e.project_name,
                    e.detected_from, e.seq, e.original_seq, e.retracted_at, trace_id,
                ),
            )
            entity_id_map[e.id] = str(row[0]) if row else e.id

        def _ent(eid: str | None) -> str | None:
            return entity_id_map.get(eid, eid) if eid is not None else None

        for es in result.entity_spans:
            txn.execute(
                "INSERT INTO entity_spans(entity_id, trace_id, span_id, role) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (_ent(es.entity_id), es.trace_id, es.span_id, es.role),
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
                    ix.id, ix.trace_id, _ent(ix.caller_entity_id), _ent(ix.callee_entity_id),
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


_SINCE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_since(spec: str) -> dt.datetime:
    """Parse a `--since` duration (e.g. `24h`, `90m`, `7d`) to an absolute UTC
    lower bound: ``now - duration``. Bare integers are seconds."""
    spec = spec.strip().lower()
    unit = spec[-1] if spec and spec[-1] in _SINCE_UNITS else "s"
    number = spec[:-1] if spec and spec[-1] in _SINCE_UNITS else spec
    try:
        seconds = int(number) * _SINCE_UNITS[unit]
    except (ValueError, KeyError):
        raise ValueError(f"invalid --since duration: {spec!r} (use e.g. 24h, 90m, 7d)")
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)


def _enumerate_trace_ids(time_from: dt.datetime | None = None) -> list[str]:
    """Distinct trace_ids via paginated root_only listing.

    With ``time_from`` set, only traces whose listing root started at/after that
    instant are returned (the window filters on ``started_at``)."""
    ids: list[str] = []
    cursor: int | None = None
    while True:
        result = retrieval.get_spans(
            cursor=cursor, limit=500, root_only=True, time_from=time_from
        )
        if not result.spans:
            break
        ids.extend(s.trace_id for s in result.spans)
        cursor = result.spans[-1].seq
        if len(result.spans) < 500:
            break
    # De-dup defensively while preserving listing order.
    seen: set[str] = set()
    return [t for t in ids if not (t in seen or seen.add(t))]


def _run_one_trace(trace_id: str, scramble: bool) -> ExtractResult:
    spans = _fetch_trace_spans(trace_id)
    return extract(spans, scramble_for_late_parent=scramble)


def _run_many_traces(time_from: dt.datetime | None = None) -> int:
    """Extract a set of traces into the scratch tables (reset once, accumulate).

    Without ``time_from``, processes every trace in the DB; with it, only traces
    whose root started within the window. Per-trace failures are logged and
    skipped; prints a compact one-line-per-trace progress and an ok/failed
    summary.
    """
    trace_ids = _enumerate_trace_ids(time_from=time_from)
    total = len(trace_ids)
    scope = "all" if time_from is None else f"since {time_from.isoformat()}"
    print(f"found {total} traces ({scope}); resetting scratch tables...")
    _reset_tables()

    ok = 0
    failed = 0
    for i, trace_id in enumerate(trace_ids, start=1):
        try:
            result = _run_one_trace(trace_id, scramble=False)
            _insert_results(trace_id, result)
            n_ent = sum(1 for e in result.entities if e.retracted_at is None)
            n_ix = sum(1 for ix in result.interactions if ix.retracted_at is None)
            print(f"[{i:4}/{total}] {trace_id[:16]}  {n_ent:3} ent  {n_ix:3} ix")
            ok += 1
        except Exception as exc:  # prototype: never let one trace abort the sweep
            print(f"[{i:4}/{total}] {trace_id[:16]}  ERROR: {exc}", file=sys.stderr)
            failed += 1
    print(f"done: {ok} ok, {failed} failed")
    return 0 if failed == 0 else 1


def main() -> int:
    args = sys.argv[1:]
    scramble = False
    if "--scramble" in args:
        scramble = True
        args.remove("--scramble")

    since: str | None = None
    if "--since" in args:
        i = args.index("--since")
        if i + 1 >= len(args):
            print("ERROR: --since requires a duration (e.g. 24h)", file=sys.stderr)
            return 2
        since = args[i + 1]
        del args[i : i + 2]

    if len(args) > 1:
        print(__doc__)
        return 2
    trace_id = args[0] if args else None
    if scramble and trace_id is None:
        print("ERROR: --scramble requires a trace_id", file=sys.stderr)
        return 2
    if since is not None and trace_id is not None:
        print("ERROR: --since cannot be combined with a trace_id", file=sys.stderr)
        return 2

    time_from: dt.datetime | None = None
    if since is not None:
        try:
            time_from = _parse_since(since)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    import os
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1
    db.configure(dsn)
    try:
        if trace_id is None:
            return _run_many_traces(time_from=time_from)
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
