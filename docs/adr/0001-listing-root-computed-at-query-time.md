# Listing root computed at query time, not stored

The recent-traces listing needs one row per trace, anchored on a "listing root"
span. We considered maintaining a `traces` summary table populated by the
ingestion path so the listing root is stable and pagination is straightforward.
We chose instead to compute the listing root at query time over the `spans`
table, accepting that the listing root — and therefore the row's display
fields and ordering position — may change as late spans arrive.

## Why

- The receiver (§3) is intentionally trivial: append-only, one transaction per
  span, no cross-row coordination. A `traces` table would force the receiver
  to reason about whether each incoming span changes a trace's listing root,
  reintroducing the kind of fix-pass logic the design explicitly rejected.
- v1 is a development / demo deployment (§5). Eventual-consistency on the
  recent-traces listing is acceptable at that scale; the UI tolerates it.
- A refreshable materialized view is a strictly additive remediation if
  profiling shows the listing query dominates UI latency at production scale.
  No schema change, no receiver change.

## Consequences

- **`TraceListingEntry` is eventually consistent.** A trace's listing root
  may flip from "earliest orphan" to "real root" when a late span arrives.
  `GET /spans?root_only=true` paginates using a composite `(started_at,
  span_id)` keyset cursor aligned with the `started_at DESC, span_id ASC`
  sort, so every listing root that satisfies the filter appears on exactly
  one page of a complete walk (skips impossible). The same trace may still
  appear with two different anchor spans across pages if its listing root
  flips between pages as late spans arrive; the UI dedupes by `trace_id`
  client-side (duplicates possible, skips impossible). The REST `cursor`
  parameter is always the `seq` of the last span on the previous page; the
  server resolves it to `(started_at, span_id)` internally before applying
  the composite predicate.
- **`root_only=True` on the retrieval API requires a `NOT EXISTS` check** to
  identify orphans (parent referenced but not present). The
  `(trace_id, parent_id)` index from §3 makes this cheap; dropping it would
  regress this query.
- **A future `traces` summary table is reserved for v1.x.** It would be a pure
  performance optimization, not a semantic change — listing-root identity
  stays computed-from-spans even if it gets cached.
