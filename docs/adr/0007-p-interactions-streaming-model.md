# P-interactions: streaming, eventually-consistent, no completion flag

`P-interactions` derives **Entities** and **Interactions** from stored
**Spans**. The natural shapes for that derivation are batch (re-derive
the world from `spans` periodically) or streaming (cursor `spans` by
`seq` and incrementally update derived tables).

We chose **streaming with eventual consistency**: a long-running
processor cursors `spans` by `seq`, writes/updates `entities`,
`interactions`, `entity_spans`, `interaction_spans`, and
`interaction_payloads` incrementally, and re-evaluates earlier rows
when a late parent arrives or a span is finalized. **Interactions
have no `complete` / `status` column** — consumers see whatever state
is in the table at query time.

## The rule

The processor runs as a single long-lived consumer. Per-span work
follows a fixed nine-step procedure (entity inference → late-parent
re-evaluation → anchor rule firing → interaction-tree placement →
attachment → aggregate updates), wrapped in one transaction; the
durable cursor in `processor_state.last_processed_seq` advances only
on transaction commit. Restarts re-process the last in-flight span
(idempotent — upserts on entities and interactions, role transitions
on interaction_spans, content-addressed payload dedup all collapse
on retry).

Interactions are **mutable in place**: a `client → agent` interaction
synthesised from an orphan SERVER span is mutated to `agent → agent`
when the parent CLIENT POST arrives; an interaction's `error`,
`request_payload_hash`, `response_payload_hash`, `started_at`, and
`ended_at` may all be updated as more spans arrive. Interaction `id`
is stable across mutations; `seq` advances on each mutation,
mirroring ADR-0004 one layer up.

The processor wakes on `LISTEN dg_spans_inserted` (a coalesced NOTIFY
fired by a Postgres trigger on `spans` insert), with a ~5-10s polling
backstop in case a notification is missed.

## Why

- **Batch-rebuild is the wrong shape for an audit-grade
  interaction graph.** Consumers (the UI, downstream lineage
  processors) want low-latency visibility into in-progress traces —
  not "yesterday's interactions". Periodic full rebuild also discards
  any cross-trace stable identity built up incrementally, or forces
  the rebuild to re-derive identity from scratch each time.
- **Eventually-consistent matches the receiver's stance** (ADR-0004:
  spans themselves are append-and-finalize, eventually consistent).
  `P-interactions` is one layer up, with the same eventual-consistency
  contract: a `seq` watermark on `interactions`, mutations re-bump it,
  consumers cursor over it.
- **A `complete` flag would be lying.** A span finalising later
  (ADR-0004) can in principle add attributes that change an
  interaction's payload or error. Any "complete" stamp can be
  invalidated by a future event we do not control. Better to have no
  flag than a flag that is sometimes wrong.
- **One transaction per span is the simplest correctness story.**
  Per-step commits (advance the cursor halfway through processing a
  span) would let a crash leave the derived tables inconsistent with
  the cursor. Transaction-scoped per-span work plus idempotent rules
  collapses the recovery story to "re-process from
  `last_processed_seq + 1`".

## Considered alternatives

- **Per-trace batch (re-derive a trace's interactions when its
  spans stop arriving for N seconds).** Rejected: requires a "trace
  is quiet" detector — same wall-clock-watermark problem, harder to
  get right at scale, and forfeits in-progress visibility.
- **Stateless query-time derivation** (no `interactions` table; the
  UI derives interactions from `spans` on each read). Rejected: the
  derivation is non-trivial (six anchor rules, late-parent
  re-evaluation, payload extraction, content-addressed dedup) and
  re-running it on every query is wasteful and inconsistent across
  consumers.
- **`interactions.status ∈ {open, complete}` flag with a wall-clock
  watchdog that flips it.** Rejected: the watchdog adds operational
  surface (a sweep job, a tunable timeout) for a guarantee
  (`status = complete` ⟹ this interaction will not change) we
  cannot honour under finalization.
- **Polling instead of LISTEN/NOTIFY.** Rejected for hot-path
  latency reasons; kept as a heartbeat backstop because LISTEN is
  best-effort.

## Consequences

- **Consumers see eventual consistency.** A trace queried twice may
  return a different graph as more spans arrive. Documented at the
  `Interaction` term in CONTEXT.md.
- **Provisional entities accumulate.** When an interaction's caller
  swaps from a synthesised `client` to a real `agent`, the
  synthesised entity is left orphaned in `entities`. v2 does not
  garbage-collect; the cost is a small number of unreferenced rows
  per trace.
- **A new `interactions.seq` watermark.** Same shape as `spans.seq`
  per ADR-0004; same monotonicity rules; advances on creation and on
  mutation. Future stream consumers of `interactions` cursor on it.
- **The receiver gains one trigger** (`AFTER INSERT ON spans`)
  firing `NOTIFY dg_spans_inserted` with no payload. The receiver
  itself stays semantically unaware — the trigger lives in the
  database, not the receiver code path.
- **Restart cost is bounded** by "re-process the last span in
  flight". The cursor advances only on commit; recovery has no
  visible effect on derived tables.
- **The §6 high-water-mark gap problem now has a `P-interactions`
  cursor too.** Same shape as receiver-side gaps. Addressed by the
  same v1.x mechanism when it lands.
