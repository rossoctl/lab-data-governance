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

> **Renumbered from 0027.** This ADR was originally written as ADR-0027 on the
> data-lineage branch while ADR-0027 "Leg-readiness notification for governance
> consumers" (#125) was written independently on `main` — the same
> both-sides-took-the-next-free-number collision that produced the two alembic
> heads that merge revision `0014` resolves. The merge left two files numbered
> 0027 and no 0028. This one moved because leg-readiness was already merged to
> `main` and could be cited from there, so keeping its number stable was the
> lower-risk half. Citations meaning *this* ADR (including every `D<n>`
> reference) were retargeted to 0028; citations meaning leg-readiness were left
> alone. Pre-merge git history and commit messages still say "ADR-0027" for this
> document.

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

The spec now names these three `Sources` / `Transformations` / `Entities`
(`data_lineage_alg.md:44-49`). The persisted and wire names are still
`data_sources` / `source_transformations` / `entities`; the spec's names are not
final, so **this ADR deliberately does not rename anything yet** — the rename
follows once they settle, and D10 is the precedent for how that is done (name
moves with meaning, in one migration).

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

## The two-operation algebra

Every interaction maps to exactly one operation, applied while traversing the
trace's interactions in `seq` order:

- **`init_lineage(entity)`** — the payload **originates here** (a data source);
  trivial metadata rooted at this entity.
- **`merge_lineage(*(payload, metadata), output, entity, is_entity_source)`** —
  one *or more* inputs to one output; calls `match(input_k, output)` per input,
  unions the metadata of the matching inputs, attaching per-source
  transformations. Degrades to `init` when `match` returns false for **all**
  inputs (D3(2)).

Signatures follow the spec's generic (payload-carrying) form
(`data_lineage_alg.md:86-112`); exact metadata-construction rules live there.

**There is no `linear_lineage`.** The spec previously named a separate
single-payload operation and the algebra had three members; it now defines one
generic `merge_lineage` covering "a single or multiple payloads", and the worked
examples call `merge_lineage` for the one-input cases that used to read `linear`
(`data_lineage_alg.md:162,171-175`). The two were never distinguishable in
result — a merge over one input *is* a linear — so collapsing them removes a
selection branch rather than changing an outcome. See D11 for what this does and
does not change in the traversal.

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
This is what makes an agent a partial mixing bowl intra-trace, and it is why an
agent's `inbound(i)` grows past one. An LLM/tool does not accumulate, so it
always sees exactly one input. **Persistent** memory behaves identically
intra-trace; it differs *only* across executions and is therefore the bridge for
inter-trace lineage (Step II, deferred). Persistence does not affect the
intra-trace loop.

Memory is still what makes `|inbound(i)|` grow, but that size no longer selects
an *operation* — one generic `merge_lineage` handles both arities (D11). What
memory now determines is how many inputs that one operation receives, which is a
statement about the **inputs**, not about which op runs.

### D3 — `init` has two triggers, at two different times

An interaction produces `init`-shaped (origin) metadata when either:
1. **Structural, *before* op selection** — the interaction is first in the path:
   its input leg's payload was produced by **no earlier interaction** in the
   trace (a genuine trace root, e.g. user input). This is the condition D4
   branches on.
2. **Semantic, *inside* an op** — `match` returns `false` for **every** input,
   signalling the output is a new origin (an anonymization that severs lineage,
   or a read whose response is brand-new data). This is **not** a D4 branch — it
   is a runtime *result* of `merge_lineage`: a source whose `match` returns false
   contributes no lineage, and if no source matches at all the op degrades to
   `init` (`data_lineage_alg.md:101-104`, Example 3 at `:140-145`).

The two live at different times: (1) is checkable structurally before the op
runs; (2) can only be known after calling `match`. D4 encodes only (1); (2) is
handled inside the operation body (see the spec for exact internals).

**`is_entity_source` ignored in the degrade branch.** When every input fails to
match, the op inits at the entity *regardless* of `is_entity_source`
(`data_lineage_alg.md:103`). So a **target-only** entity that severs lineage
still becomes a data source. This is deliberate: the output payload exists and
came from somewhere, and the entity that produced it is the only origin left to
name — a lineage-severing anonymizer genuinely is where its output originates.
The spec keeps the alternative reading as an inline comment (`:104`, "if
`is_entity_source = false` there should be no meaningful output") but does not
adopt it; Example 3 states the rule for `false or true` alike.

**Origin is no longer only a degrade outcome.** D3 used to be the *whole* story of
how an entity becomes a data source mid-trace, which put all the weight on the
matcher: with `simple_match` always returning true, branch (2) was unreachable and
no mid-trace entity could ever be an origin. D12's `is_entity_source` adds a second,
**structural** route — an entity declared a source contributes itself *alongside*
the inherited sources, on a successful match. The two are independent and compose:
`matched` decides whether upstream lineage is inherited, `is_entity_source` decides
whether this entity also contributed content of its own. A tool that both consumes
its request and returns newly-read data reports both facts, which the old
`matched`-only model could not express (see D12).

### D4 — Op selection

Computed per interaction `i` in `seq` order. Two distinct terms, at two grains:

- **`output_leg`** — the single leg of `i` whose lineage we are computing (for a
  call, the callee's response is its output; the request is its input, D1).
- **`inbound(i)`** — the *set* of prior payloads available to `i`'s entity when
  it produced `output_leg` (D1 routing across earlier interactions). This is
  where memory lives (D2): a memoryless entity (LLM/tool) has exactly one
  inbound payload; an accumulating entity carries all its priors.

```
lineage[i], for each interaction i in seq order:
  # (a) structural init — D3(1): output_leg's payload has no producing interaction
  if inbound(i) is empty:
      init_lineage(entity)
  # (b) one or more inbound payloads — memory (D2) decides how many, not which op
  else:
      merge_lineage(*inbound(i), output_leg, entity, is_entity_source(entity))
      # per-input match; degrades to init if none match — D3(2)
```

**Selection is now two-way, not three.** `|inbound(i)|` no longer picks an
operation — it only sizes the argument list of the single generic op
(`data_lineage_alg.md:179-186`). The old branch (b)/(c) split on
`|inbound(i)| == 1` vs `>= 2` is gone, and with it the question of which side an
accumulating entity's *first* outbound falls on: it takes the same `merge_lineage`
as every other non-root leg, with one input.

`is_entity_source(entity)` is the entity-taxonomy lookup (D12) — a property of the
producing entity, not of the payloads, which is why it is a parameter of the op
rather than something the op could derive. Exact metadata construction
(transformation-set union, key-collision merge, the entity's own empty
transformation set) is in the spec (`data_lineage_alg.md:97-112`); this ADR does
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

#### Reading the status — the two rules every consumer needs

These two are load-bearing everywhere the status travels (the traversal's
`LineageStatus`, the `lineage_trace_status` row, the API envelope, the flow
view's banner, and every test that pins them). This is their single source; other
sites state what holds locally and cite **D6** rather than re-deriving them.

**1. Absence of the status row means *unknown*, never `complete`.** "Not yet
derived" and "derived, covers everything" are opposite claims. Collapsing them
would reintroduce silent truncation through the eventual-consistency window: every
trace P-data-lineage has not reached yet would present its (possibly truncated,
possibly empty) prefix as the full set of data sources — exactly the failure this
flag exists to prevent, reappearing on the traces most likely to be read, the
newest ones. So the read serves `status: null`, the API serves `null`, and the UI
says *unknown* rather than warning about nothing or reassuring about everything.

The trap is a future editor **defaulting** the missing value: `status or
"complete"`, a `COALESCE(status, 'complete')`, a `?? 'complete'`. Those look like
tidying and are silent in every test that only exercises derived traces. There is
no default. Unknown is a third value and it must stay expressible end to end.

Two things make this hard to get wrong at the storage layer, and they are why the
schema-level statements of it are worth keeping (migration 0012): `status` is
`NOT NULL`, and a CHECK constraint pairs it with `stopped_at_seq`. So a *present*
row always makes a definite claim, absence of the **row** is the only way to say
"unknown", and there is no in-band NULL for an editor to reinterpret. The
invariant is structural in the database; it is only defaultable in the code above
it, which is where the warnings belong.

One deliberate consequence: the driver writes **no status row at all** for a trace
whose legs have not landed (`process_leg` returns early when the trace has no
legs). Nothing was derived, so there is nothing to claim — and writing `complete`
there would assert full coverage of a trace we have not seen.

**2. `partial` is a *warning*, not an error state.** The derived prefix is
correct; it is simply a prefix. Nothing failed, no read should 500, and no
consumer should treat it as a broken trace. It is also **not** a statement about
*why* the payload is missing — one flag currently covers *not captured*,
*redacted*, *genuinely empty* and *in-flight* alike (see the deferrals below).

Note what `partial` truncates and what it does not: it truncates the **lineage**,
not the leg list. The read returns every leg of the trace, with `lineage: null` on
the legs at and after the gap (a LEFT JOIN, no `seq` filter). A consumer must not
expect post-gap legs to be *absent* from the response.

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
  At the time this decision was taken the driver re-derived a whole trace per
  arriving leg and upserted *without* deleting (D7's derived-stream pattern), so
  rows from an earlier, longer derivation outlived a later, shorter one. The very
  case the flag exists for — a trace that *was* complete and is now truncated —
  was the case where the stale rows hid the gap. (D9 below fixes that staleness;
  it does not revive derived-on-read, for the reason in the next bullet.)
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

- **A trace can now have no status row**, which this table makes the *only* way to
  say "unknown" — `status` is `NOT NULL`, so a present row always makes a definite
  claim. Absence therefore means unknown and never `complete`; see D6 "Reading the
  status" for why the two must not be collapsed, and for the defaulting trap.
- **The upsert alone is not enough for `lineage_metadata` any more.** Once a
  derivation can get *shorter*, the driver must also **delete** the trace's rows
  the derivation no longer covers — otherwise the read serves lineage for legs
  after the gap while the status says `partial`, which is self-contradictory
  rather than merely stale. That delete, and how it must be scoped, is D9 below.

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
(migration 0010's NOTIFY trigger covers UPDATE for exactly this reason — though
covering UPDATE only delivers the *wake*; making the rewritten leg reachable at all
took D13). If a
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

**Scope of the delete: set membership, never a `seq` threshold.** The condition is
"everything under the trace that this derivation did not produce" — not
`seq >= stopped_at_seq`. This is the one part of D9 that is easy to get backwards,
and getting it backwards is silent: **a `seq`-threshold delete would spare exactly
the rows it must remove.** `seq` is a re-allocated cursor value, not a stable
position. When P-interactions rewrites a leg in place it draws a *fresh* `seq` from
the sequence, so the gap leg's new `seq` sits *above* the stale rows that were
written under its old one — a `>= stop` predicate then matches the gap leg's own
(already correct, or absent) row and misses the stale tail entirely. Set membership
has no such failure mode: the derivation is the sole authority on which of a trace's
legs have lineage, so anything else under the trace goes.

Scoping by set membership also collects rows whose leg has disappeared from the
trace entirely — something neither an upsert nor a threshold can do — and
degenerates correctly to "delete every row of this trace" when the derived set is
empty, which is a real case: the gap landing on the trace's first leg.

The delete is named for what it removes, not for the condition it tests. Every row
it deletes is a lineage fact some previous derivation of this same trace asserted
and this one no longer does; nothing it deletes is current.

It is scoped through `interactions` because `lineage_metadata` carries no
`trace_id` (ADR-0025 keeps identity on the parent); mis-scoping it would delete
another trace's evidence.

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
trace-derived API — since named, and the same shape D14's `fanin`/`fanout` take:
derived from the trace *and* the metadata rather than from the triple alone. That is
the honest home for it: the trace has the leg `seq`
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

**The rename is NOT a backward-compatible migration — accepted, with an
operational consequence.** 0013 renames the column in one step, so a reader still
running pre-rename code fails hard: `column m.entity_path does not exist`. Not a
degraded read — a broken one. Observed in practice on the Kind cluster: applying
`90-data-lineage.yaml` ran its migrate init container to 0013 while the API pod
still queried `entity_path`, and every lineage read stayed broken until the other
deployments were restarted onto the new image.

This bites because *every* pod migrates to head (ADR-0002), so whichever pod
starts first drags the schema forward under all the others. The deploy order is
therefore **build images → roll every reader → let the migration land**, or accept
a window of failing lineage reads. `deploy/k8s/README.md` records the same.

Accepted rather than solved: this is a pre-release lab deployment where the window
is free, and expand–contract (add `entities`, dual-write, migrate readers, drop
`entity_path`) costs three migrations and a dual-read path to protect a table with
one reader. Against real traffic, expand–contract is the correct shape and this
decision should be revisited — a one-step rename of a column any live reader
selects is an outage by construction, not by accident.

### D11 — The algebra collapses to two operations; `linear_lineage` is deleted

**Shipped** (issue #131). `operations.linear_lineage`, `Operation.LINEAR` and D4's
three-way branch are gone; the docstrings that described the previous algebra were
updated with them.

The spec changed. `docs/data_lineage_alg.md` (human-owned, authoritative) replaced
its separate single-payload operation with **one generic `merge_lineage`** that
covers "a single or multiple payloads" (`:85-93`), and rewrote the worked examples
and the summary pseudocode to call it in the positions that previously read
`linear_lineage` (`:162`, `:171-175`, `:186`).

**Why this is a simplification and not a behaviour change.** A merge over one
input and a linear over that same input compute the identical triple: the union of
one source set is that set, the key-collision merge has nothing to collide, and the
entity extension is the same. The two operations were always the same function at
different arities. Keeping both forced a selection branch whose only job was to
pick between indistinguishable results.

What this removes:

- **`operations.linear_lineage`** and its dedicated tests.
- **`Operation.LINEAR`** from the traversal's recorded selection. `Operation` was
  introduced so a test could assert *why* a row looks the way it does; with a
  two-way selection the remaining members are `INIT` and `MERGE`.
- **The `|inbound(i)| == 1` branch** in `traversal._derive_leg` (D4).

What this deliberately does **not** remove:

- **`memory.accumulates` and `ACCUMULATING_KINDS`.** Memory still decides how many
  priors are retained and therefore how many inputs `merge_lineage` receives (D2).
  The predicate's *consumer* changes — it sizes an argument list instead of
  choosing an op — but the predicate itself is untouched. Deleting it would
  conflate "we no longer branch on the count" with "the count no longer matters".
- **The structural-init branch (D3(1)).** An empty `inbound(i)` is a genuinely
  different case: there is no input to match against, so `merge_lineage` has
  nothing to be called with. Two-way is the floor, not one-way.

Note the traversal keeps one behaviour the spec's summary does not spell out: a
leg whose producing entity is unknown is **skipped without truncating the trace**,
which is distinct from D6's absent-payload cutoff. That is unchanged by this
decision and is documented in `traversal.derive_trace_lineage`.

### D12 — `is_entity_source`: an entity can contribute itself as a source, independent of `match`

**Shipped** (issue #131), as `memory.SOURCE_KINDS` / `memory.is_entity_source`
beside `memory.accumulates`, threaded into `merge_lineage`. Kind defaults only —
reading the declared taxonomy table remains deferred. This changed persisted output
for every trace with a tool call, so it requires re-derivation via the D8/D9
recovery path (truncate `lineage_metadata` + `lineage_trace_status`, reset the
`data_lineage` cursor to 0, re-drain).

The spec added an **Entity Taxonomy** (`data_lineage_alg.md:24-35`) and threaded an
`is_entity_source: bool` parameter through `merge_lineage` (`:92`, `:95-96`,
`:107-108`).

**What it means.** On a successful match, an entity declared a *source* is added to
the output metadata **alongside** the inherited sources — it appears in `Sources`,
gains a key in `Transformations` with an **empty** transformation set, and is
extended into `Entities` (`:106-112`). The empty set is the point: the entity's own
contribution did not undergo the transformation that the *inherited* sources
underwent, so stamping the match's transformation onto it would be a false claim.

**Why a parameter and not a matcher verdict.** These are two orthogonal facts about
one leg:

- `matched` — *did the request's content survive into the response?*
- `is_entity_source` — *did this entity also contribute content of its own?*

A single boolean cannot carry both. The live `travel-advisor` trace has the
counterexample: `charge_card` receives a PAN in its request and returns an
`auth_code` that existed nowhere upstream. Under a `matched`-only model,
`matched=True` inherits the PAN but loses the ingress, while `matched=False` records
the ingress but discards the PAN's provenance — the most governance-critical edge in
the trace. With `is_entity_source` both are stated. The same applies to
`get_payment_info`, whose request tokens all reappear in its response (so a
content-comparing matcher reasonably returns `matched=True`) while it returns
freshly-read cardholder data.

**Where the value comes from.** A declared per-entity table is the eventual source
and **reading it is deferred** (`:26-27`). Until then, kind-based defaults
(`:31-35`): `tool` is source ✓ and target ✓; `LLM` and `agent` are neither. So
`is_entity_source` is a *second* kind-driven predicate beside `memory.accumulates`,
with the same trajectory — declared config later, one named place now. Both should
live together rather than in separate modules, since the deferred table supplies
both.

**Kinds the taxonomy does not name default to "not a source".** The spec's table
covers `llm` / `tool` / `agent`; the full kind set also has `user`, `client` and
`service` (CONTEXT.md **Entity**). Absence from `SOURCE_KINDS` means ✗, so those
three contribute nothing of their own. This costs nothing today for a trace root —
`user` / `client` legs already become origins **structurally** via D3(1), which
needs no taxonomy entry — and it keeps the safe direction for `service`, whose
data-contributing status is genuinely unknown rather than assumed.

Deliberately a *default*, not a decision about those kinds: adding a kind is a
one-line change to `SOURCE_KINDS`, and the declared table supersedes the whole
question. Recorded so a later reader does not mistake the omission for a finding
that `service` was analyzed and ruled out.

**Accepted consequence: tools over-report as sources under the trivial matcher.**
With `simple_match` always matching, the degrade branch never fires and every tool
leg adds its tool to `Sources`. Combined with an agent's accumulation (D2), the
source set grows monotonically down the trace — on the live trace the advisor's
final leg ends up rooted at the agent *and* every tool that fired. For the read
tools (`search_destinations`, `get_weather`, `get_payment_info`, `get_flights`) that
is the correct and previously-missing answer. For agent-delegation-shaped tools
(`delegate_to_booking_agent`, which is `kind='tool'` in `entities` but is pure
delegation carrying no new data) it is an over-report. Accepted until the declared
table lands: over-reporting an origin is the safe direction for a governance tool,
where the failure this replaces was *under*-reporting an external data ingress.

**`location` is a placeholder.** The taxonomy table carries an
internal/external column that no operation reads. It is recorded for future
use (inter-trace / Step II) and has no v1 semantics.

**`target` has since gained a consumer.** When this decision was taken neither
`target` nor `location` was read by anything. That is still true of `location`, but
D14's `list destinations` gives `target` its first reader — shipped in D15 as
`memory.TARGET_KINDS` / `memory.is_entity_target`, beside the other two predicates
because the same deferred table supplies all three columns. No operation in the
*algebra* reads it, which is what this decision was about; the read surface does.

Note `TARGET_KINDS` and `SOURCE_KINDS` are both `{"tool"}` today, so a tool is
usually both a source and a destination. They are independent taxonomy columns that
happen to share a default and diverge once the declared table distinguishes a read
tool from a write one — so `is_entity_target` must never be written as
`not is_entity_source(...)`, and agreement between the two lists is an artifact rather
than corroboration.

**`Entities` membership follows data flow, not source-hood — resolved.**
`Entities` answers "did data pass *through* this entity", which is a question about
flow and is **independent of `is_entity_source`**. So the entity is extended into
`Entities` unconditionally on the matched branch, whether or not it is also a
source. `is_entity_source` gates `Sources` and `Transformations` only
(`data_lineage_alg.md:126`, Example 1's Note, excludes the entity from exactly
those two).

This resolves what was previously recorded as an asymmetry against
`init_lineage`, which leaves `Entities` **empty** (`:78-83`). The two are
consistent once membership is read as a flow claim rather than an origin claim:

- **Matched branch** — data reached the entity from upstream and left transformed,
  so it genuinely passed *through*. It belongs in `Entities`.
- **`init_lineage`** — the payload originates here with no upstream at all. Nothing
  passed *through* anything, so an empty set is the truthful answer, and listing the
  originating entity would assert a transit that did not happen.

A source entity therefore lands in `Entities` when data passed through it, and only
then. Not a spec change — this is the reading the spec's own rules produce; it is
recorded because the earlier draft treated the difference as an unresolved
inconsistency rather than as two different facts.

### D13 — The drain needs a second, non-cursored arm to catch legs rewritten in place

D9 established that a re-derivation must delete stale rows as well as upsert, and
justified it by noting that legs are rewritten in place and that migration 0010's
trigger covers UPDATE "for exactly this reason". That is true about the *wake* and
was wrong about the *reach*: covering UPDATE means the consumer is notified, not that
it can still see the rewritten leg. It could not.

**Why the cursor cannot get there.** P-interactions preserves a leg's `seq` across a
re-derive — `seq` is omitted from the upsert's `DO UPDATE SET` so that replay and
crash recovery never reshuffle seqs a downstream consumer has already delivered
(ADR-0007). P-data-lineage drains `WHERE seq > cursor`. A rewritten leg therefore
sits *behind* the cursor: the trigger fires, the consumer drains, finds nothing past
the cursor, and does nothing. The poll backstop runs the same query, so it does not
help either. The lineage derived from the superseded payload survives while
`lineage_trace_status` still reports `complete` — D9's stale-row delete never runs,
because the derivation that would trigger it is never invoked.

This is strictly worse than the failure D9 addressed. There, a stale row was at least
accompanied by a `partial` status a reader could act on. Here the trace asserts full
coverage over lineage derived from a payload that no longer exists.

**The decision.** The drain gets two arms (`data_lineage/driver.py:_drain_spec`):

1. the existing cursor arm, `seq > cursor`, unchanged;
2. a staleness arm that re-derives any trace where
   `lineage_metadata.payload_hash IS DISTINCT FROM interaction_legs.payload_hash`.

The second arm **never advances the durable cursor**. A stale leg's `seq` is below the
cursor by definition, so advancing to it would drag the cursor backwards and re-drain
every leg in between — unboundedly, and in violation of the monotonic advance the
other consumers of the shared loop rely on. It does not need a cursor: the hash
comparison *is* its durable state, since re-deriving the trace is exactly what clears
the condition. A crash mid-pass leaves the predicate true and the next wake
re-detects it.

Both arms route through the same `process_item`, so the stale path inherits D9's
delete and D8's status upsert unchanged. The arm re-derives the whole *trace*, not
just the offending leg, because a rewritten payload changes what every later leg
inherits (D1).

**Rejected: bump `seq` on rewrite.** A one-line change to `state.py` would put the
rewritten leg above the cursor and need no new query. It trades this bug for a subtler
one in the recovery path — the determinism `seq` preservation exists to protect — and
perturbs a producer to fix a consumer's blind spot.

**Accepted cost.** No index can serve a predicate comparing two columns across two
tables, so the arm hash-joins `lineage_metadata` against `interaction_legs` on every
wake, including the common one where nothing is stale. Cost scales with table size
rather than with staleness. Acceptable at lab scale and strictly better than serving
stale provenance; a cheaper trigger (a dirty-trace queue written by the statement that
rewrites the leg, or an indexable generated column) is the shape to reach for against
real traffic. Recorded as an open item rather than guessed at.

### D14 — The read surface is five reads at three grains, all trace-scoped

The spec gained an **API** section (`data_lineage_alg.md` "API") naming five reads
where it previously named one ("given execution flow interactions, we can easily
compute the trace lineage"). Recorded here because most of them are a *different kind*
of read from the one that shipped, and because the section closes three scope
questions that were open.

**The reads, and what each is answerable from:**

| Read | Grain | Source |
| --- | --- | --- |
| per-leg lineage metadata | leg | `lineage_metadata` lookup — **shipped** (#118) |
| `lineage fanout(entity, source)` | entity | trace **and** metadata — **shipped** |
| `lineage fanin(entity, source)` | entity | trace **and** metadata — **shipped** |
| `list sources` | trace | union of the trace's `data_sources` — **shipped** |
| `list destinations` | trace | taxonomy `target`, kind defaults — **shipped** |

All five now ship. The four added after #118 live in
`retrieval/lineage_graph.py` behind two endpoints —
`GET /api/traces/{tid}/entities/{eid}/data-lineage-graph?direction=fanin|fanout`
`&source=<natural-key>` and `GET /api/traces/{tid}/data-lineage-summary` — and D15
below records what their implementation had to decide that this decision left open.

**Amended.** The two entity-grain reads were first recorded here, and shipped, as
`fanout(entity)` / `fanin(entity)`. The spec's **API** section was subsequently
rewritten (`data_lineage_alg.md`, commit `399f4fc`) to make them
`fanout(entity, source)` / `fanin(entity, source)` and to add the traversal rule quoted
in D15. The table above and the endpoint signature are corrected to match; D15 carries
the substance and records what the source-less version got wrong. Nothing else in this
decision changes — the grains, the two-table pairing and the three closed scope
questions all stand.

**The metadata triple cannot answer fanin/fanout alone, and the spec pairs the two
sources correctly.** `lineage_metadata` records *sets* — sources, transformations,
entities — per leg, and deliberately records no edges (D10: a merge unions branches
that reached an entity by different routes, and there is no truthful interleaving to
store). Ancestors/descendants is a *stronger* claim than the ordering D10 deferred, so
these reads need the leg structure in `interaction_legs` too. Hence the spec's phrase
"derived from the trace **and** metadata": **the trace supplies the candidate edges,
the metadata supplies whether lineage actually flowed along them.**

**That pairing is what makes these *lineage* fanin/fanout rather than a call-graph
walk** — the load-bearing sentence of the spec's section: "if there is no lineage
through an entity that Entity is the end of fanin or fanout". A structural walk would
report every entity the trace reached; these stop where provenance stops.

The rewritten spec sharpens *what* "no lineage through an entity" is measured against,
and it is not the mere existence of a metadata row: it is **whether the traced source is
in that row's set**, evaluated in **sequence order**. D15 records the operational rule.
The paragraph above is still the right reading of why two tables are needed; what it
under-specified is which bit of the metadata answers the question.

**Consequence, and it is the trade the triple already accepts:** these traversals
inherit matcher quality. Under the trivial `simple_match` nothing terminates early, so
fanout degenerates to the whole reachable call graph and fanin to the whole ancestry —
complete but full of maybes, exactly as the triple is. Not a new weakness, but a full
fanout must not be read as evidence that data genuinely reached everything it lists.

Partly bounded by the amendment: the sequence rule prunes **regardless of matcher
quality**, because it is a fact about the trace's own ordering rather than about
provenance. So even under `simple_match` a fanout is now the seq-*forward* reachable
graph rather than the whole of it, and fanin the seq-backward one. The source rule still
inherits matcher quality as described — `simple_match` propagates every upstream source
into every downstream leg, so membership rarely fails — and the caveat above stands
undiminished for it.

**No re-derivation on the read path, so D7 is intact.** These reads re-walk
*structure* and read *persisted* verdicts; they do not call the matcher. D7's
constraint is that matching runs at ingest — an LLM/NER call per payload pair over a
trace's history is what persisting avoids — and a structural walk over
already-decided lineage does not reintroduce it. An implementation that finds itself
needing a matcher call to answer fanin/fanout has violated D7 and should persist the
edge instead.

**`list sources` reads the triple, not the taxonomy.** The spec resolves it to "union
of data sources, scoped to trace" — buildable today against a table that already
exists, needing no taxonomy entry. It deliberately does *not* mean "entities declared
sources": that is a different set, and under D12's kind defaults the two diverge
exactly where a delegation-shaped tool over-reports.

**`list destinations` gives the taxonomy's `target` column its first consumer**,
retiring half of D12's "`target` is unread, `location` is a placeholder". Until the
declared table lands the answer is the kind default (`tool` ✓, `llm` ✗, `agent` ✗).
Note the v1 consequence: `SOURCE_KINDS` and the taxonomy's `target` both currently
resolve to `tool`, so `list sources` and `list destinations` return overlapping
membership for unrelated reasons. They diverge only once the declared table
distinguishes a read tool from a write one — so early agreement between the two reads
is an artifact of the defaults, not corroboration.

**Three scopes the section closes**, all explicit in the spec's Deferred block when this
decision was written: **deployment scope** (these are per-trace reads; an all-traces "what
are my sources" is deferred), **cross-trace** (fanin/fanout do not cross a trace
boundary — that is Step II, so "ancestors" means ancestors *within the trace*), and
**reading the entity-taxonomy table** (unchanged from D12). The first of those lines has
since been removed from the spec — see the note below on what that does and does not
mean.

**The deferred block changed with the amendment, in two ways.** It gained
**multi-source**, deferred verbatim — "Lineage fanout/fanin Given multiple sources -
semantics are not clear: Do we expect the exact set of sources? Any of them?" So these
reads take exactly **one** source; see D15 for why that makes the parameter required
rather than optional.

It also **dropped the "Deployment scope" line** (`399f4fc`). Read as a deletion rather
than a resolution: nothing in the spec now describes an all-traces read, and no such read
is implemented — every one of the five remains scoped through `interactions.trace_id`.
Recorded here so the earlier sentence above ("an all-traces 'what are my sources' is
deferred") is not later cited as spec text; it is this ADR's own reading, and it still
holds in practice. Reaching for a deployment-wide read should re-open the question with a
human rather than treat the removed line as permission.

### D15 — The traversal's edge is a leg carrying *this source*, crossed in sequence order

**Shipped**, implementing D14's four deferred reads (`retrieval/lineage_graph.py`).
D14 settled *what* the reads are and *which two tables* answer them; it deliberately
did not fix the edge rule. This records what the implementation had to decide, because
each choice is one a later editor could plausibly reverse.

**Amended** after `data_lineage_alg.md` commit `399f4fc` rewrote the spec's **API**
section (`ee3a493` is its parent, which reworked the surrounding algorithm text but left
`fanout(entity)` / `fanin(entity)` intact). The first shipped version of this decision had a two-conjunct edge rule and no
`source` parameter; that reading is superseded, and the section "**What the previous
edge rule got wrong**" below records it explicitly, because it is plausible enough to be
re-derived by a reader who assumes it was merely a coarser version of this one. It was
not — it was a different and false answer.

**The signature.** `fanout(entity, source)` / `fanin(entity, source)`, served as
`GET /api/traces/{tid}/entities/{eid}/data-lineage-graph?direction=fanin|fanout`
`&source=<natural-key>`. `source` is a **data source natural key** as stored in
`lineage_metadata.data_sources` — lineage stores keys, not entity ids (ADR-0027, D5) —
so the accepted values are exactly what `list sources` returns. The two reads are keyed
the same way deliberately: list, then drill in.

**`source` is required, not optional.** It is half the question. An entity handles
content from several sources at once — on the live corpus a mid-trace agent leg
routinely carries four or five — and each has its own fanout, so there is no default
that answers what was asked. The only candidate default, the union over all of them, is
precisely the **multi-source read the spec defers** ("Given multiple sources - semantics
are not clear: Do we expect the exact set of sources? Any of them?"), so serving it
silently would ship a guess at an open design question under the name of a settled one.
A missing or empty `source` is therefore a `400` carrying the same error shape a bad
`direction` already does (`retrieval.MissingSource`, mirroring `UnknownDirection`) —
both are malformed questions, as against answerable ones with empty answers.

*Rejected: optional, defaulting to the old source-less walk.* It would have kept
existing callers working, but that walk is not a weaker version of this one (see below),
so leaving it reachable behind a default would make the wrong answer the easiest to ask
for.

**An *unknown* source is a valid empty answer, NOT a 404.** A key matching no
`data_sources` value anywhere in the trace returns `200` with `state="no-adjacent"` and
empty lists, exactly as an unknown seed entity does. Three reasons:

- it is the truthful reply — the walk genuinely computed "no eligible edge exists",
  whereas a 404 would claim the *question* was malformed, which is a different claim;
- **a 404 would have to be inferred from absence, and absence is not yet knowledge
  here.** Deciding "this key is unknown to this trace" means scanning the trace's
  derived rows, and a mid-derivation trace has few or none — so the same request would
  404 now and 200 later. That is exactly the collapse D6's three-valued `status` and
  this read's `state`/`pending_frontier` exist to prevent: *"we don't know yet" must
  never be served as "there is nothing"*. The tri-state already handles it correctly —
  an undelivered trace answers `pending` with a frontier, which a 404 would destroy;
- it matches the convention one field over: unknown trace and unknown entity are both
  200-with-empty here, and `state` is how the caller tells the cases apart.

**The edge rule.**

```
Arriving at entity A at sequence position s, a hop A -> B is followed iff:
  1. the trace has an Interaction leg whose per-leg direction runs A -> B;
  2. that leg has a derived lineage_metadata row;
  3. `source` is a MEMBER of that row's stored `data_sources`; and
  4. the leg's `seq` is strictly later than s (fanout) / earlier (fanin).
```

Clauses 1-2 are D14's "the trace supplies the candidate edges, the metadata supplies
whether lineage actually flowed along them", made operational; neither table can answer
alone. Clauses 3-4 are the spec's own sentence, which is the whole of the amendment:

> we should traverse an edge towards the next/previous entity based iff the source is
> part of the edge/interaction metadata sources
>
> the interaction sequence number governs the edges to be considered and their order
> (fanout - larger numbers, fanin - smaller numbers)

**Clause 3: the source is held CONSTANT for the whole walk.** It is the thing being
*traced*, not a per-hop comparison against the previously-visited entity. A leg whose
`data_sources` omits it is a leg this source's content demonstrably did not travel on,
so the walk must not cross it even though *some other* source's content did.

**Clause 3 is a set-membership test, never a re-derivation — D7 is intact.** The read
path reads `m.data_sources` and asks `source in data_sources`. No matching, no
normalisation, no prefix or fuzzy comparison: the natural keys were written by the
ingest-time derivation and are compared verbatim. This is the clause where D7 is easiest
to violate — an implementation that finds itself wanting to *decide* whether a source
belongs to a leg has violated it, and the fix is to persist the attribution at ingest,
not to compute it here.

**Clause 4: `seq` governs eligibility AND order.** Data cannot flow backwards in time,
so an entity's downstream is what happened *after* the content arrived there. `seq` is
the per-leg execution cursor (ADR-0025 puts it on the leg; the parent `interactions` row
has none), and it does two jobs: it *gates* which edges may be crossed, and it *orders*
the departures within an entity, which is what the spec's "and their order" asks for.
Ordering does not change which entities are reachable; it makes the walk deterministic
and gives the earliest-in-time route the claim on a given hop count.

**Strict (`>`), not non-strict (`>=`), and the corpus settles it rather than taste.** A
request and its response are two *different* legs at two different `seq`s — ADR-0025
splits them, and on live trace `e62610bec7e8c1f4372aacc392eb9be5` the
`search_destinations` call is seq 2 request / seq 3 response — so a genuine round trip is
always expressible under `>`. Two legs can never share a `seq`, it being drawn from a
sequence, so `>=` could only ever re-admit the very leg just arrived on: data flowing
straight back where it came from in zero elapsed time. That is a false hop. Relaxing
this is also not a free widening — it removes the termination argument below.

**The seed is unconstrained.** It has not arrived *on* a leg, so it may depart on any
eligible one. Pinning it to its earliest/latest touching leg would silently narrow the
question to "downstream of that particular arrival" when the caller asked about the
entity.

**Per-leg direction, never the interaction's caller→callee.** A response leg runs
callee → caller, matching `traversal._producer_id`/`_consumer_id` and the UI's
`legDirection`. This is the subtle half: keying on the parent's fixed direction would
drop every response, and an agent's data mostly *arrives* as the responses to calls it
made (ADR-0025) — so that reading would silently lose the majority of real inbound
flow. A consequence worth stating because it defeats an intuition: a leaf tool's
`fanout` is **not** empty, since its response delivers data back to its caller.

**Absence of a lineage row ends the walk but is reported, not swallowed.** The result
carries a `pending_frontier` naming the entities the walk reached but could not
continue through, because the onward leg has no derived row *yet*. Without it "provenance
genuinely ends here" and "P-data-lineage has not got here yet" would be the same empty
tail — the same collapse D6 forbids for coverage, one level down. For the same reason
the result carries a three-valued `state` (`derived` / `pending` / `no-adjacent`)
rather than letting an empty entity list speak: an empty list has three unrelated
causes and only `no-adjacent` is a complete answer.

**Clause 3 splits "no hop" into two facts, and they must never collapse.** This is the
crux of the tri-state discipline once the read is source-scoped:

| The leg | Meaning | Disclosure |
| --- | --- | --- |
| no derived row yet | *ask again later* — the answer may grow | on `pending_frontier` |
| derived, `source` not in `data_sources` | **final**: this source did not flow here | silently not an edge |

Merging them is a lie in either direction. Treating the undelivered case as final
under-reports a still-arriving answer; treating the source-absent case as pending sends a
caller back to poll for something no amount of waiting will deliver. The `LEFT JOIN` plus
null-probe in `_fetch_legs` exists precisely to keep them apart, and the pair is asserted
together in the tests so the two branches cannot be "simplified" into one.

**Clause 4 also filters the frontier; clause 3 deliberately does not.** The asymmetry
follows from where each fact lives. A leg's `seq` is on `interaction_legs` and is known
**whether or not** the lineage row has landed, so an undelivered leg on the wrong side of
the arrival is *already* a settled "never an edge" — naming its entity as pending would
promise growth no derivation can deliver. Its `data_sources`, by contrast, is precisely
what has not landed, so membership is genuinely unknown and the entity is honestly named.
Filtering the frontier by source would under-promise; not filtering it by seq would
over-promise. Both directions are pinned by paired tests.

A source-absent dead end therefore shares `no-adjacent` with "nothing there" rather than
getting a fourth `state` value. *Rejected: a `source-absent` state.* It would name the
distinction without being useful — every such case is the same actionable fact ("this is
the end of the fanout"), and a caller can do nothing different with them. The distinction
that *does* change caller behaviour, final versus not-yet, is already carried.

**In-Python BFS over an adjacency map, not a recursive CTE.** One flat query fetches
the trace's legs with a `LEFT JOIN` lineage probe; the walk runs in Python. The
recursive-CTE precedent (`processors/interactions/state.py`) walks a *single*
self-referential FK with no filter; here an edge is derived from two tables plus the
direction rule, a source-membership test and a `seq` comparison against the *arrival* —
encoding that into a recursive join condition would bury the edge rule in SQL and put
the frontier logic out of reach. Traces are bounded and `get_interactions` already scans
one three times.

**The query selects `m.data_sources`, not a `has_lineage` boolean.** This is the root
fix, not a refinement of one. Reducing the lineage row to `(m.seq IS NOT NULL)` makes
clause 3 *unaskable* at every layer above the query: with only a boolean the walk cannot
test whether *this* source is in the leg's set, so it can only fall back on "this leg has
some lineage row" — which is the false rule the previous version shipped.
`source_transformations` and `entities` are deliberately **not** selected: the spec's
hop rule names only *sources*, and `entities` would be actively wrong to test against,
being an unordered "passed through here" claim (D10) that would let the walk hop to
anything the metadata ever mentioned.

**Clause 4 makes `visited: set[str]` unsound, and this is the subtle consequence.** With
the seq gate an entity can be legitimately **re-entered** at a different position, and a
different arrival opens edges the first one could not take: an agent reached at seq 30 may
only leave on seq > 30, while the same agent reached at seq 10 may also leave on seq 20.
A plain "seen it, skip it" set keeps whichever arrival happened to be dequeued first and
silently drops every entity reachable only past the better one. This is the ordinary shape
of the corpus — a coordinating agent is re-entered on every tool response it receives —
not a corner case.

The replacement is `best_arrival[entity]`: the most **permissive** arrival seq seen, and an
entity is re-enqueued iff a new arrival is strictly more permissive. An arrival opens
exactly the legs on its permissive side, so a strictly more permissive one opens a
superset and anything else a subset. Note the sign, which is the opposite of the direction
of travel and is the easiest thing here to get backwards: fanout departs on `seq >
arrival`, so an *earlier* arrival is the more permissive one; fanin is the mirror.

**Depth is deliberately not part of that test, and `hops` is tracked separately.** This is
the trap one refinement in from the `visited` bug, and it is easy to walk straight into: a
`(depth, arrival)` dominance test reading "shallower, or equal depth and more permissive"
looks natural and is wrong. A **deeper** arrival can be **more permissive** — reached the
long way round but earlier in the trace — and it then opens edges the shallow arrival
cannot; rejecting it loses everything beyond it, the same class of silent under-reporting
merely rarer. So permissiveness alone gates expansion, while `hops` keeps its own map and
is written with `min` so a deeper permissive revisit records reachability without
lengthening the reported distance.

**Termination, and it no longer rests on the cycle guard.** `agent → tool → agent` is
still the ordinary shape of every tool call, and the graph is still genuinely cyclic — but
what makes it finite now is **clause 4**, not the visited bookkeeping: every traversal
must strictly advance `seq`, and a trace has finitely many legs. `best_arrival` is a
pruning optimisation; the seq monotonicity is the termination argument. Formally,
`best_arrival[entity]` is only ever replaced by a strictly more permissive value drawn
from the trace's **finite** set of leg seqs, so it can improve at most `|legs|` times per
entity; every enqueue is either an entity's first or a strict improvement, bounding total
enqueues at `|entities| × (|legs| + 1)`.

That reassignment matters for a future editor: relaxing clause 4 to `>=` would remove the
termination guarantee, not merely widen the answer. The span-tree CTEs in `state.py`
still have no guard because a tree cannot cycle; do not read their absence as precedent.

**`hops` is now sequence-aware, and plain hop-BFS no longer yields it.** `hops` is the
fewest hops along a path that respects *both* clauses, and the shortest **structural**
route may be closed to this source or run backwards in time while a longer route is open.
Two properties make the reported number right: the queue is processed in non-decreasing
depth order (a plain FIFO, every enqueue at `depth + 1` — the standard BFS invariant,
which survives the seq gate because the gate only ever *removes* edges), and a shallower
route already found is never overwritten by a deeper one. So the first depth at which an
entity becomes reachable *at all* is the depth recorded.

**Bounds are disclosed.** Hop and entity caps set `truncated` rather than silently
returning a prefix, and a walk that merely *ends* on the boundary does not set it — a
flag that cried truncation on complete answers would be trained away. A cap is also not
tripped by legs the seq rule had already excluded: reporting `truncated` there would tell
the caller a wider bound reveals more, which is false.

The spec's "and their order" earns its keep at exactly this boundary. Ordering departures
by `seq` does not change *reachability*, so it is easy to dismiss as cosmetic — but it
decides which prefix a truncated answer returns, and "the earliest flows" is the only
prefix a reader can interpret. An insertion-order walk would return an arbitrary one.

**`truncated` and `pending_frontier` are different claims and must not be merged.**
`pending_frontier` means *not derived yet — ask again later*; `truncated` means
*derived, but this answer declined to return it all*. An entity skipped by the entity
cap therefore lands in `truncated` only: putting it on the frontier would send a
caller back to poll for something no amount of waiting delivers, since only a wider
bound produces it. The two are independent and a single walk can legitimately report
both.

**Still no matcher, so D7 holds.** These reads re-walk structure and read persisted
verdicts. An implementation that finds itself wanting a matcher call to answer a hop
has violated D7; the edge should have been persisted instead. Clause 3 does not change
this: it reads a persisted set, it does not decide one.

#### What the previous edge rule got wrong, and why it looked plausible

Recorded at length because the superseded reading is *reasonable-sounding* and a future
reader could re-derive it believing it equivalent to this one. It is not equivalent. It
is false, and the failure is silent — it returns a large, confident, well-formed answer.

The shipped implementation had clauses 1-2 only. It reduced the lineage row to
`(m.seq IS NOT NULL) AS has_lineage`, never selecting `data_sources`, and it carried
`seq` on the edge but used it *solely to sort the reported legs* — the walk's state was
`(entity, depth)`, so an entity departed on edges that had fired **before** the one it
arrived on. Two consequences, both reproduced on live trace
`e62610bec7e8c1f4372aacc392eb9be5`, seeding the leaf tool `search_destinations`
(`6e400213-…`), which touches exactly two legs: seq 2 inbound, seq 3 outbound.

**1. `fanin` and `fanout` were byte-identical, and returned everything.** Both answered
with **all 10 other entities and all 50 legs** — not merely the same membership but the
same `hops` for every entity. The mechanism is worth stating because it is not obvious:
`_adjacency` builds fan-in as the exact edge-*reversal* of fan-out, and a trace's
aggregate request+response edge set is symmetric (every request has a response running
the other way). Reversing a symmetric relation is a no-op — so once time is discarded
there is nothing left for the reversal to distinguish. **The direction reversal itself was
correct; it just had no purchase.** What separates upstream from downstream is *when*, and
that was the discarded information.

**2. A leaf tool's ancestry included an entity from 36 legs later.** `charge_card`
appeared at 4 hops in the seed's `fanin`, though its only legs are seq 39/40 — long after
the seed finished — and its `data_sources` never mention `search_destinations` at all. So
*both* new clauses independently exclude it, and neither existed.

**The same seed under the amended rule**, on the same trace, for comparison:

| | entities | legs | `charge_card` |
| --- | --- | --- | --- |
| before, `fanout` | 10 | 50 | 4 hops |
| before, `fanin` | 10 | 50 | 4 hops |
| after, `fanout` | 7 | 36 | absent |
| after, `fanin` | 0 | 0 | absent |

The empty `fanin` is correct and is the sharpest illustration: `search_destinations` is
where that source *originates*, so it has no ancestors, and the two legs carrying it are
both later than the seed's own arrival.

**Why it looked plausible.** The reading "a leg with a derived lineage row is a lineage
edge" is a faithful rendering of D14's own sentence, *"the metadata supplies whether
lineage actually flowed along them"* — if you read "whether lineage flowed" as a property
of the leg rather than of the (leg, source) pair. Under the trivial `simple_match` it
even looks corroborated: every leg gets a row, so the walk returns the whole call graph,
which is *exactly* what a correct implementation also returns when nothing prunes it.
D14's own "accepted degeneracy" paragraph then explains the full fanout away. The bug and
the documented limitation are indistinguishable from the output alone.

That is the trap. **A full fanout under the old rule was not "matcher too weak to prune";
it was "no pruning implemented".** The two are testable apart only by checking that
`fanin != fanout` on a seq-asymmetric trace, which is why that inequality is now a named
regression test at both the pure and DB-backed layers.

**The UI's refusal was correct, and this decision is what retires it.** The
now-deleted `ui/src/lib/lineageGraph.ts` had a clause 3 (that module's numbering,
unrelated to the edge rule's clauses above) declining to walk transitively, because
"a transitive claim the backend never derived would be the UI inventing lineage".
That reasoning was right *while the backend derived nothing*, and it is exactly what
this decision removes **for the server**: the backend now derives the multi-hop
claim, so it is citable.

The client-side roll-up is therefore **deleted rather than kept beside the served
answer** — two competing notions of "the lineage highlight" is the duplication that
would let two tabs disagree about what is true. Its successor is
`ui/src/lib/lineageReachability.ts`, which renders a served answer rather than
composing one and whose module docstring carries this reasoning forward. A UI
consuming these endpoints must also pass a `source`, since the server no longer has
a question to answer without one.

## Outputs

- **API** — trace-scoped reads at three grains (D14, edge rule in D15), all
  **shipped**: per-leg lineage metadata, `GET /api/traces/{tid}/data-lineage` (#118),
  carrying the D6 coverage status on the envelope (#120); `lineage fanin`/`fanout` per
  entity **per data source**,
  `GET /api/traces/{tid}/entities/{eid}/data-lineage-graph?direction=…&source=…`
  (both parameters required — D15); and `list sources` / `list destinations`,
  `GET /api/traces/{tid}/data-lineage-summary`.
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
- **Reading the declared entity-taxonomy table** (D12) — source/target/persistent-
  storage/location per entity. Kind-based defaults stand in; the table's own shape
  and population are out of scope here. The spec's API section names this deferral
  too (D14), so `list destinations` runs on the `target` kind default until it lands.
- **The `location` (internal/external) dimension** of the taxonomy (D12) — carried
  in the spec's table, read by nothing, reserved for Step II. (`target` is no longer
  in this position: D14's `list destinations` is its first consumer.)
- **Deployment-wide and cross-trace scope** for the entity- and trace-grain reads
  (D14). The reads themselves shipped (D15), scoped to one trace; an all-traces
  "what are my sources" and a walk that crosses a trace boundary (Step II) are not
  in them.
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
  v1 default), so keying later changes what the derivation computes rather than
  requiring it to be redesigned — inbound payloads already pool per *node*, so a
  keyed policy only has to return a distinct node. The node is a
  derivation-time value and is never persisted, so this says nothing either way
  about schema churn: `lineage_metadata` records the sources, transformations and
  entities that resulted, not the memory nodes they were pooled through. Also
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

- D13's staleness arm is blind to a `lineage_metadata` row whose **leg has been
  deleted**: with nothing to compare against, the inner join cannot see it, so the
  orphaned provenance claim survives until that trace is re-derived for some other
  reason. Latent today — P-interactions only ever upserts legs, it does not hard-delete
  them — and deliberately not fixed by widening the join, which would make the two arms
  race for the same unconsumed leg. If leg deletion is ever introduced, this needs a
  sweep of its own.

- A cheaper staleness trigger than D13's per-wake hash join. The predicate cannot
  be indexed (it compares two columns across two tables), so its cost tracks table
  size and is paid even when nothing is stale. Candidates: a dirty-trace queue
  written by the same statement that rewrites a leg (moves the cost to the writer,
  which knows precisely what changed), or an indexable generated/denormalised
  column. Not chosen now because the right shape depends on write volume this
  deployment has not yet seen.

- Distinguishing a **data-contributing** tool from a **pass-through** one (D12).
  The kind default makes every `tool` a source, which over-reports for
  delegation-shaped tools. The declared table is the intended fix; inferring it
  from tool names, descriptions or payload-size heuristics was considered and
  rejected — a governance claim derived from a free-text naming convention has no
  provenance, and the signals are unevenly present (in the live corpus only 1 of 8
  tools carries `tool.description`).
- **Per-entity vs per-leg source semantics** (D12). `is_entity_source` is a
  property of the entity, so it applies to every leg that entity produces. Whether
  a *specific* call was a read or a write (the same tool can do both — `create_booking`
  accepts data and returns a new booking id) is not expressible today.

**Resolved** (kept for the record):

- The trace-level status *location* (D6 / Schema) — settled by **D8** in favour of
  the dedicated `lineage_trace_status` table over derived-on-read.
- Whether a mid-trace entity can be a data source **without** the matcher refusing
  a match — settled by **D12**'s `is_entity_source`. Previously the only route was
  D3(2)'s degrade, which the trivial matcher makes unreachable, so no tool could
  ever appear as an origin.
- Whether a *source* entity also belongs in `Entities` — settled in **D12**:
  yes, when data passed through it. `Entities` is a flow claim, independent of
  source-hood; `is_entity_source` gates `Sources` and `Transformations` only. This
  was previously recorded as an unresolved asymmetry against `init_lineage`'s empty
  set; the two are consistent once membership is read as transit rather than origin.
- Kinds absent from the taxonomy table (`user` / `client` / `service`) — **D12**
  defaults them to "not a source". A default that a later entry or the declared
  table overrides, not a finding about those kinds.
- Where the deferred **ordering** read lives (D10) — **D14** names it. D10 routed
  "in what order did the data pass through these entities" to "a future
  trace-derived API" without saying what that API was; the spec's API section
  supplies the shape, and `fanin`/`fanout` are derived from the trace *and* the
  metadata for exactly D10's reason: the triple has no edges to walk.
- Whether the read path may re-derive (D7) — **D14**: no. The entity- and
  trace-grain reads re-walk structure and read persisted verdicts; needing a
  matcher call to answer one means the edge should have been persisted instead.
