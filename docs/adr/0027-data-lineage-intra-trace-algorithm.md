---
status: accepted
---

# Data lineage: intra-trace algorithm

Data lineage answers two questions about a payload: **where did it originate**
(data source), and **what entities/transformations did it pass through**. The
input is the interaction data (ADR-0025 `interactions` + `interaction_legs`,
each leg carrying a `payload_hash`); the output is per-payload **lineage
metadata**. This ADR records the intra-trace algorithm and the decisions that
shaped it. The human-owned spec lives at `docs/data_lineage_alg.md`; this ADR
captures the *why* and the settled boundaries.

## Lineage belongs to payloads; entities are nodes

The unit that carries lineage is the **payload on an interaction leg**, not the
entity. Entities (agent/tool/llm/user) are the nodes that *compute* lineage as
data flows through them. This is why the metadata is keyed by payload, and why
"which entities did this pass through" is a *field* of a payload's metadata
rather than a property of the entity.

## Lineage metadata

Per payload: (1) the set of **data sources** (origins), (2) a map
`data_source → set<transformation>` (order does not matter), (3) the **set of
entities** the data passed through — unordered (see D10). Transformations are a
finite enumeration (anonymization, summarization, …) still being finalized with a
human.

## Semantic matching is a pluggable, deferred capability

Lineage is built on `match(payload_a, payload_b) → {matched, transformation,
…evidence}` — a black box that decides whether two payloads are related and
what transformation connects them. Lineage does **not** know how it decides.

- The **default matcher** is trivial: `simple_match → {true, null}` — always
  matched, no transformation. With it, lineage is **complete but full of
  maybes** (every structural edge is treated as real flow).
- Better matchers (value-based, confidential-aware) prune the maybes. Lineage
  quality is bounded by matcher quality, but lineage is **computable today**
  with the default matcher.
- Matching is deliberately **out of scope** of lineage itself — it is its own
  component with its own roadmap.

## The three-operation algebra

Every interaction maps to exactly one operation, applied while traversing the
trace's interactions in `seq` order:

- **`init_lineage(entity)`** — the payload **originates here** (a data source);
  trivial metadata rooted at this entity.
- **`linear_lineage(input, output, entity)`** — a single input payload processed
  to one output; calls `match(input, output)`, inherits/extends the input's
  metadata. Degrades to `init` when `match` returns false (D3(2)).
- **`merge_lineage(*inputs, output, entity)`** — multiple inputs to one output;
  calls `match(input_k, output)` per input, unions the metadata of the matching
  inputs, attaching per-source transformations.

Signatures follow the spec's generic (payload-carrying) forms
(`data_lineage_alg.md:70-101`); exact metadata-construction rules live there.

## Decisions

### D1 — "Prior" and inbound routing are structural, from the interaction legs

Traversal is **`seq` order, per trace**. A payload is **inbound to entity E**
iff, in an interaction with lower `seq`, E is the **callee** and the payload is
the **request** leg, OR E is the **caller** and the payload is the **response**
leg. That single rule generates `inbound(i)` from the interaction table —
requests are inbound to the callee, responses are inbound to the caller.

### D2 — Transient/session memory is assumed always present; persistence is only the cross-trace bridge

Within a trace, every accumulating entity (e.g. an agent) retains its prior
inbound payloads — **transient/session memory is assumed to always exist**.
This is what makes an agent a partial mixing bowl intra-trace and drives
`merge`. An LLM/tool does not accumulate, so it always sees one input and uses
`linear`. **Persistent** memory behaves identically intra-trace; it differs
*only* across executions and is therefore the bridge for inter-trace lineage
(Step II, deferred). Persistence does not affect the intra-trace loop.

### D3 — `init` has two triggers, at two different times

An interaction produces `init`-shaped (origin) metadata when either:
1. **Structural, *before* op selection** — the interaction is first in the path:
   its input leg's payload was produced by **no earlier interaction** in the
   trace (a genuine trace root, e.g. user input). This is the condition D4
   branches on.
2. **Semantic, *inside* an op** — `match` returns `false` comparing input vs
   output payload mid-path, signalling the output is a new origin (an
   anonymization that severs lineage, or a fresh external read whose response is
   brand-new data). This is **not** a D4 branch — it is a runtime *result* of
   `linear`/`merge`: when `match` returns false for a source, that source
   contributes no lineage, and if no source matches the op degrades to `init`
   (per the spec's `linear_lineage`, `data_lineage_alg.md:77-79`).

The two live at different times: (1) is checkable structurally before the op
runs; (2) can only be known after calling `match`. D4 encodes only (1); (2) is
handled inside the operation bodies (see the spec for exact internals).

### D4 — Op selection

Computed per interaction `i` in `seq` order. Two distinct terms, at two grains:

- **`output_leg`** — the single leg of `i` whose lineage we are computing (for a
  call, the callee's response is its output; the request is its input, D1).
- **`inbound(i)`** — the *set* of prior payloads available to `i`'s entity when
  it produced `output_leg` (D1 routing across earlier interactions). This is
  where memory lives (D2): a memoryless entity (LLM/tool) has exactly one
  inbound payload; an accumulating entity carries all its priors, so op
  selection is really on `|inbound(i)|`.

```
lineage[i], for each interaction i in seq order:
  # (a) structural init — D3(1): output_leg's payload has no producing interaction
  if output_leg's payload originates outside the trace:
      init_lineage(entity)
  # (b) |inbound(i)| == 1: memoryless entity, or an accumulating entity's first inbound
  else if inbound(i) has exactly one payload p:
      linear_lineage(p, output_leg, entity)          # may degrade to init — D3(2)
  # (c) |inbound(i)| >= 2: accumulating entity (D2) with retained priors
  else:
      merge_lineage(*inbound(i), output_leg, entity)  # per-source match — D3(2)
```

The memory predicate is folded into `inbound(i)`: because transient memory is
always present (D2), an accumulating entity's `inbound(i)` grows to ≥2 and hits
branch (c); an LLM/tool never accumulates, so it stays at one and hits (b). An
accumulating entity's **first** outbound legitimately has one inbound and uses
(b) — matching the spec's worked example (`data_lineage_alg.md:147` uses
`linear` for the agent's first outbound, `:149,:151` use `merge` once ≥2
priors exist). Exact metadata construction (transformation-set union, key-
collision merge) is in the spec (`data_lineage_alg.md:70-123`); this ADR does
not restate it.

### D5 — Metadata is keyed per leg, not per payload

The canonical key is the interaction **leg** `(interaction_id, leg_type)` — the
grain at which a lineage fact is unique. `payload_hash` is **not** the key:
payloads are content-addressed and deduped, so identical bytes can appear at
different positions (across traces, or even within one trace when an entity
echoes an input verbatim) with **completely different lineage**. Keying on the
hash would collide those distinct facts into one row. The hash says *what the
content is*, not *where it came from*; lineage is about the latter.

`payload_hash` is kept as a **secondary index** to support the deferred reverse
lookup ("where did this content go / come from"), not as the primary key.

### D6 — Absent payload: positional prefix cutoff (interim)

If a leg's `payload_hash` is absent, lineage is computed **up to that point
only** — a positional prefix in **leg `seq`** order (ADR-0025 put `seq` on
`interaction_legs`; the parent `interactions` row has no `seq`, so ordering is
by leg). Processing stops at the first leg with an absent payload; legs with
lower `seq` get lineage, legs from that point on get none. The trace's lineage
is then marked **`partial`** (vs `complete`), recording the leg `seq` at which
it stopped — so a governance consumer never reads a truncated prefix as the full
set of sources. Silent truncation is the failure mode this flag exists to
prevent.

Per ADR-0025 the request leg always exists; the realistic trigger is a
**missing response payload** mid-trace.

This is an **interim** rule. The finer handling — distinguishing *not captured*
/ *redacted* (data flowed, opaque) from *genuinely empty* from *response
in-flight*, and choosing per case between break-chain, conservative
pass-through (`transformation: unknown`), and defer — is **deferred**. In
particular it does **not** yet do taint/reachability cutoff (poison only the
paths through the gap); it stops the whole trace at the gap.

**Shipped** with issue #120: the cutoff in `traversal.derive_trace_lineage`
(which now returns a trace-level `TraceLineage`), the status in
`lineage_trace_status` (migration `0012`), on
`GET /api/traces/{tid}/data-lineage` beside `legs`, and as a warning at the top
of the flow view.

### D7 — Matching runs at ingest; metadata is persisted

Lineage is computed at **ingest** with the configured matcher and persisted, so
API reads are pure lookups (no per-query matcher calls, which would be an
LLM/NER call per payload-pair over a trace's full history). This follows the
repo's derived-stream pattern.

**Deferred:** what happens when the matcher *implementation* changes —
re-derivation, matcher-versioning, and historical backfill are explicitly not
solved here.

### D8 — Trace-level status lives in a dedicated `lineage_trace_status` table

Closes the open item D6/Schema left: a dedicated `(trace_id → status,
stopped_at_seq)` table, **not** derived on read.

Derived-on-read was the tempting option — no migration, and the gap looks like
something a `SELECT` could spot. It cannot, in either form:

- **From the metadata rows** ("no lineage row past leg N") it is not detectable.
  The driver re-derives a whole trace per arriving leg and upserts *without*
  deleting (D7's derived-stream pattern), so rows from an earlier, longer
  derivation outlive a later, shorter one. The very case the flag exists for — a
  trace that *was* complete and is now truncated — is the case where the stale
  rows hide the gap.
- **From `interaction_legs.payload_hash IS NULL`** it is detectable, but that
  puts a second implementation of D6's cutoff rule in read-path SQL, free to
  drift from the traversal that actually produced the rows. Two answers to "is
  this trace complete?" is worse than one, and for a governance claim the
  authoritative answer must be the one the derivation reached.

The dedicated table's idempotency story is the smallest available: PK `trace_id`
means exactly one row per trace, ever, so the driver's upsert *is* the whole
story — and the **partial → complete** transition (a late payload arrives) is a
plain overwrite of that one row, with no longer, earlier answer left behind to
shadow it. Recovery is the established derived-table one, shared with
`lineage_metadata`: truncate, reset the `data_lineage` cursor to 0, re-drain.
Both tables are written in the transaction that advances the cursor, so a trace's
metadata and its coverage claim cannot disagree.

Two consequences worth stating, because both are load-bearing:

- **Absence of the status row means *unknown*, never `complete`.** "Not derived
  yet" and "derived, covers everything" are opposite claims; collapsing them
  would reintroduce silent truncation through the eventual-consistency window.
  The read serves `status: null` and the UI warns about nothing.
- **The upsert alone is not enough for `lineage_metadata` any more.** Once a
  derivation can get *shorter*, the driver must also **delete** the trace's rows
  the derivation no longer covers — otherwise the read serves lineage for legs
  after the gap while the status says `partial`, which is self-contradictory
  rather than merely stale. The delete is scoped to "not in this derivation's
  output" rather than `seq >= stopped_at_seq`, because `seq` is re-allocated when
  a leg is rewritten in place and a threshold would spare exactly the rows it
  must remove. This is D9.

### D9 — Re-derivation is upsert **plus** a stale-row delete, not upsert alone

The derived-stream pattern this repo uses elsewhere (`payload_classifications`,
the P-interactions `flush`) re-derives by upsert and never deletes, because those
derivations only ever grow: a payload gets classified, a trace gains
interactions. `lineage_metadata` broke that assumption the moment D6's cutoff
landed, so it needs one operation more than its siblings. Recorded as its own
decision because it is a deliberate departure from the pattern — and from the
"idempotent re-derive by upsert" the lineage table was originally specified with
— rather than an incidental implementation detail.

**Why a derivation shrinks.** Lineage is re-derived per arriving leg, and legs
are themselves rewritten in place when P-interactions re-derives a trace
(migration 0010's NOTIFY trigger covers UPDATE for exactly this reason). If a
re-derivation leaves a leg without a payload, D6 truncates: the trace that
previously produced a row per leg now produces only the prefix before the gap.
An upsert rewrites the prefix and is silent about the rest, so the rows past the
gap survive from the earlier, longer derivation.

**Why that is not merely untidy.** Those surviving rows are *positive provenance
claims* — "this payload came from these sources" — about legs whose payloads are
no longer available to support them. A consumer reading them gets a confident
answer built on evidence that has gone, while the trace's own status says
`partial`. Without the delete, D6's flag would be decorative: the truncation it
announces would not actually be reflected in what the read serves.

**Scope of the delete.** Everything under the trace that this derivation did not
produce — which also collects rows whose leg has disappeared from the trace
entirely, something an upsert can never do. The derivation is the sole authority
on which of a trace's legs have lineage. It is scoped through `interactions`
because `lineage_metadata` carries no `trace_id` (ADR-0025 keeps identity on the
parent); mis-scoping it would delete another trace's evidence.

`lineage_metadata` deliberately does **not** gain a `trace_id` column to avoid
that join. Its source table `interaction_legs` has none either, the join is
already the established shape in the read path, and denormalising would make a
wrong-trace row *expressible* where today the join cannot lie — the wrong
trade for a governance claim. If the deferred reverse lookup (D5's
`payload_hash` index, "where did this content go") lands and wants cross-trace
queries, that is the point to revisit.

### D10 — The third metadata element is an unordered **set**, named `entities`

The spec changed. `docs/data_lineage_alg.md` (human-owned, authoritative) now
defines the third element of the triple as a set and says so explicitly:

> 3. the set of entities - through which entities the data passed through
>    Note: this is unordered. In case an order is needed - it will need to be
>    derived from the trace using an API.

and the operation rules read in kind: "A new **set** of entities which is empty"
(rule 1(3)), "create a copy of the entity **set** and extend it with the entity
name" (rule 2(3)), "the **set** of entities is merged and extended with the
entity" (rule 3(3)). It previously said "list", and the implementation carried an
ordered, deduplicated tuple named `entity_path`.

**The field is renamed to `entities`** — Python `DataLineage.entities:
frozenset[str]`, column `lineage_metadata.entities` (migration
`0013_lineage_entities_rename`), JSON key `entities`, TS `DataLineage.entities`.
The name had to move with the meaning: *path* promises a sequence a consumer may
legitimately read hop-by-hop, and a field that no longer carries one must not keep
advertising it. A stale name on a governance claim is worse than a rename.

**Ordering is deliberately deferred, not lost.** The spec routes it to a future
trace-derived API, and that is the honest home for it: the trace has the leg `seq`
order that could answer "in what order", whereas the metadata triple does not. A
`merge` unions two branches that reached the entity through different routes, and
there is no single truthful interleaving of them to store — the old implementation
could only offer whichever first-arrival order its traversal happened to produce.
Deriving order from the trace on demand can be correct; baking one into a merged
set cannot.

**`_extend_path` is deleted.** Its dedup existed to reconstruct set behaviour
inside a tuple; set union does it natively, and rules 2(3)/3(3) are now literally
`| {entity_name}`. Fewer moving parts, and the type (`frozenset`) now *refuses* to
hold an order the algebra cannot justify.

**The persisted sort is serialization only.** `entities` is written to `TEXT[]`
sorted, exactly as `source_transformations` already sorts its sets (`driver._upsert`):
a re-derivation of identical lineage then produces byte-identical rows, which is
what makes idempotency observable. Order is insignificant, so pinning it is free —
but it is *not* meaning, and nothing (SQL consumer, API client, UI) may read flow
order out of array position. The same applies on the wire: `entities` is a JSON
array only because JSON has no set type.

**Consequence for the UI.** `DataLineageView` previously rendered the field as an
`a → b → c` arrow chain, which asserted precisely the order the spec disclaims. It
now renders a `LabelGroup` of labels — the same unordered presentation the
transformation sets beside it use. The null / empty-set / populated three-state
handling is unchanged and unrelated: "not yet derived", "derived, passed through
nothing" and "derived, passed through these" remain three distinct readings.

Migration 0011 is left as it shipped. It recorded the shape that was correct at the
time; rewriting applied history to look like it always knew better would hide that
the spec moved.

## Outputs

- **API** — given a trace's interaction flow, compute/serve trace lineage;
  return lineage metadata for any interaction/payload.
- **Tables** — a map `(interaction_id, leg_type) → lineage metadata` (D5), so
  "what are the data sources" is a read, not a recompute; `payload_hash` is a
  secondary index for the deferred reverse lookup.

## Schema

Concrete shape, following the repo's derived-table pattern (own `seq` cursor, no
FKs, idempotent re-derive):

- **`lineage_metadata`** — PK `(interaction_id, leg_type)` (D5). Columns: the
  metadata triple (`data_sources`, `source_transformations` map, `entities`),
  `payload_hash` (secondary index, D5), `seq`. One row per interaction leg that
  received lineage. **Shipped** as migration `0011_lineage_metadata` (issue #117):
  the triple is `TEXT[]` /`JSONB` / `TEXT[]` respectively (JSONB for the
  map-to-set, arrays for the two sets — Postgres has no set type), all `NOT NULL`
  — an origin's metadata is a real *empty* triple, and absence of the row is what
  means "not yet derived". The third column shipped as `entity_path` and was
  renamed to `entities` by migration `0013_lineage_entities_rename` when the spec
  redefined it as unordered (D10).
- **`lineage_trace_status`** — PK `trace_id` (D8). Columns: `status`
  (`lineage_status` ENUM: `complete` | `partial`, `NOT NULL`) and
  `stopped_at_seq` (`BIGINT`, nullable). **Shipped** as migration
  `0012_lineage_trace_status` (issue #120). A CHECK constraint pairs the two —
  `partial` requires a stop position, `complete` forbids one — so a
  "partial, but I won't say from where" row cannot be stored. Absence of the row
  means *not yet derived*, matching `lineage_metadata`'s convention; `status` is
  therefore `NOT NULL` (a present row always makes a definite claim). No `seq`
  cursor column: nothing drains this table, and a trace's coverage is not a stream
  of events.

This section fixes the keys and the fact that a trace-level status must exist,
matching how ADR-0024/0025 name their PKs.

## Deliberately out of scope

- **Step II — inter-trace lineage** (flow through shared persistent storage:
  one trace writes, another reads). Metadata shape is designed to carry
  cross-trace sources unchanged, but the mechanism is deferred.
- **Matcher implementation and its versioning/re-derivation** (D7).
- **The transformation enumeration** (finalized with a human).
- **Map `persisting-entity → payload`** (the reverse data-source index).
- **Dependencies** (config, code/model versions) — non-data inputs to a
  transformation are not tracked in v1.

## Open items

- Memory granularity for stateful entities — **blob vs keyed** (per
  session/user/thread) is deferred; driven by declared config; matters for
  Step II (unkeyed memory would reintroduce the mixing bowl *across* traces —
  a false cross-user data-flow claim for a governance tool). To keep the choice
  genuinely open, the recommended seam is to model the memory node as
  `(entity_id, memory_key)` with `memory_key = NULL` meaning unkeyed/blob (the
  v1 default), so keying later is a value change, not a migration. Also
  unresolved: **shared vs partitioned** stores (a shared knowledge base is
  legitimately unkeyed; cross-user flow through it is real).
- Absent-payload finer handling (D6): classify *not-captured* / *redacted* /
  *empty* / *in-flight* and choose break-chain vs conservative pass-through vs
  defer per case. #120 shipped the interim positional-prefix rule and
  deliberately did **not** narrow this — one `partial` flag covers every reason a
  payload is missing, so a redacted-but-flowing payload and a never-captured one
  are currently indistinguishable to a consumer. `lineage_trace_status` has no
  reason column for exactly that reason: adding one now would fix this open
  choice by accident.
- Taint/reachability cutoff instead of whole-trace stop (D6): poison only the
  paths *through* the gap rather than truncating the trace at it. Still open —
  #120 stops the whole trace, so a branch that never touched the missing payload
  loses its lineage too. This would be per-leg state, not the trace-level row
  D8 added.

**Resolved** (kept for the record): the trace-level status *location* (D6 /
Schema) — settled by **D8** in favour of the dedicated `lineage_trace_status`
table over derived-on-read.
