# Span finalization: conditional update with seq as watermark

OTLP allows a sender to flush a span before it ends — emitting one
"start" version with no `ended_at` and a later "end" version with
the same `(trace_id, span_id)` and a populated `ended_at`. The naive
choices are first-writer-wins (drop the end version) and naive UPSERT
(let any later arrival overwrite). Both are wrong in different ways.

We chose a **conditional update on completion**, with `seq` advancing
on update so stream consumers see the finalization, and a separate
`arrival_seq` column preserving the original insert rank for stable
per-row identity.

## The rule

On `INSERT ... ON CONFLICT (trace_id, span_id)`:

- **DO UPDATE** iff the incoming row has `ended_at IS NOT NULL`
  AND the existing row has `ended_at IS NULL`. The UPDATE
  overwrites every mutable column (`kind`, `name`, `attributes`,
  `events`, `links`, `otlp`, `scope`, `resource_attributes`,
  `error`, `status_message`, `ended_at`) and **assigns a fresh
  `seq`** from the same sequence used for inserts.
- **DO NOTHING** otherwise. Idempotent for end-version retries
  (existing row already has `ended_at`); also drops "second
  partial-flush before completion" cases (neither row has
  `ended_at`, no clear winner).

`arrival_seq` is set on INSERT to the same value as `seq`. It is
**never updated**. `started_at`, `parent_id`, `service_name`,
`observed_at` are also preserved on UPDATE — the original arrival's
values stand.

## Why

- **First-writer-wins (the prior §3 stance) silently drops
  finalizations.** A span emitted as "start" then "end" by a
  sender that flushes early would be permanently incomplete in
  storage, with no `ended_at`, no error status, partial attributes.
- **Naive UPSERT loses arrival ordering.** If every conflict
  overwrites, an OTLP retry after a network blip rewrites the row
  with identical content — harmless but no longer truly idempotent
  (two retries produce two `seq` advances, both visible to stream
  consumers as "events"). The conditional clause restricts UPDATE
  to genuine completions.
- **`seq` advancing on update is the only mechanism by which a
  `seq`-cursored stream consumer ever observes a completion.**
  Without it, completion events are invisible to streams. The v1.x
  high-water-mark mechanism (§6) and any future processor that
  cares about completed spans both need this property.
- **`arrival_seq` preserves stable per-row identity.** Recovery
  checkpoints, per-row idempotence keys, and any consumer that wants
  "I observed this exact row at this position" use `arrival_seq`.
  It never moves; it is the row's stable handle.

## Considered alternatives

- **First-writer-wins with documented assumption.** Rejected: we'd
  be encoding an assumption about senders we don't fully control.
  Future Kagenti instrumentations (different language SDKs, new
  agent frameworks) may early-flush by default, and a silent-data-loss
  failure mode is exactly the wrong place to be lenient.
- **Naive UPSERT.** Rejected: every retry advances seq, polluting
  stream consumers with phantom "events" that carry no new
  information.
- **Conditional UPDATE with seq stable.** Rejected: would prevent
  stream consumers from ever observing completions, requiring a
  parallel finalization channel (separate column, separate index).
  Strictly more machinery for less expressiveness.
- **`completion_seq` as a separate column, `seq` stable.** Rejected
  in favor of the simpler "seq is watermark, arrival_seq is stable"
  split — same expressiveness, one column instead of two, and
  `arrival_seq` is a more useful stable handle than `seq` would
  have been (a stable INSERT-time rank is what most consumers
  actually want).

## Consequences

- **The §3 "single append-only table" framing softens.** It is now
  an **append-and-finalize** table: rows are inserted once, may be
  updated exactly once on completion, and never deleted in v1.
- **`seq` is a watermark, not an identifier.** Anything that needs
  per-row stable identity uses `arrival_seq` or
  `(trace_id, span_id)`. Cursors over `seq` continue to deliver
  monotonically-increasing watermarks, but a row's `seq` may move
  forward exactly once during its lifetime.
- **Stream consumers see each row up to twice.** Once at INSERT
  (partial or complete), and once at completion if it was originally
  partial. Consumers must dedupe by `(trace_id, span_id)` and treat
  the second visit as the authoritative version. This is the right
  semantics; it is also now the consumer's responsibility.
- **The high-water-mark gap problem (§6, ADR-0001's deferral) gets
  one more dimension.** In addition to insert-allocation gaps,
  there are now update-allocation gaps where a row's seq is mid-move.
  Both are deferred to v1.x and addressed together by the
  high-water-mark mechanism.
- **§5.1 metrics gain `spans_finalized_total`.** `spans_inserted_total`
  counts only initial INSERTs; `spans_finalized_total` counts the
  UPDATE path; `spans_duplicate_total` counts the DO NOTHING hits.
  All three are cleanly separable.
- **Recent-traces listing ordering (sorted by listing-root
  `started_at desc`, Q14) is unaffected by finalization** — a span's
  `started_at` does not change on update, only its `seq` and other
  mutable columns.
