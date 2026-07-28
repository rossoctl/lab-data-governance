---
status: accepted
---

# Data lineage: intra-trace algorithm

Data lineage answers two questions about a payload: **where did it originate**
(data source), and **what entities/transformations did it pass through**. The
input is the interaction data (ADR-0025 `interactions` + `interaction_legs`,
each leg carrying a `payload_hash`); the output is per-payload **lineage
metadata**. This ADR records the intra-trace algorithm and the decisions that
shaped it. The human-owned spec lives at `docs/data _lineage_alg.md`; this ADR
captures the *why* and the settled boundaries.

## Lineage belongs to payloads; entities are nodes

The unit that carries lineage is the **payload on an interaction leg**, not the
entity. Entities (agent/tool/llm/user) are the nodes that *compute* lineage as
data flows through them. This is why the metadata is keyed by payload, and why
"which entities did this pass through" is a *field* of a payload's metadata
rather than a property of the entity.

## Lineage metadata

Per payload: (1) the set of **data sources** (origins), (2) a map
`data_source → set<transformation>` (order does not matter), (3) the ordered
**list of entities** the data passed through. Transformations are a finite
enumeration (anonymization, summarization, …) still being finalized with a
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
(`data _lineage_alg.md:70-101`); exact metadata-construction rules live there.

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
   (per the spec's `linear_lineage`, `data _lineage_alg.md:77-79`).

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
(b) — matching the spec's worked example (`data _lineage_alg.md:147` uses
`linear` for the agent's first outbound, `:149,:151` use `merge` once ≥2
priors exist). Exact metadata construction (transformation-set union, key-
collision merge) is in the spec (`data _lineage_alg.md:70-123`); this ADR does
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

### D7 — Matching runs at ingest; metadata is persisted

Lineage is computed at **ingest** with the configured matcher and persisted, so
API reads are pure lookups (no per-query matcher calls, which would be an
LLM/NER call per payload-pair over a trace's full history). This follows the
repo's derived-stream pattern.

**Deferred:** what happens when the matcher *implementation* changes —
re-derivation, matcher-versioning, and historical backfill are explicitly not
solved here.

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
  metadata triple (`data_sources`, `source_transformations` map,
  `entity_path`), `payload_hash` (secondary index, D5), `seq`. One row per
  interaction leg that received lineage. **Shipped** as migration
  `0011_lineage_metadata` (issue #117): the triple is `TEXT[]` /`JSONB` /
  `TEXT[]` respectively (JSONB for the map-to-set, arrays where order matters or
  does not), all `NOT NULL` — an origin's metadata is a real *empty* triple, and
  absence of the row is what means "not yet derived".
- **Trace-level `partial` flag (D6)** — where the `complete`/`partial` status +
  stop-`seq` live is **open**: either a small `lineage_trace_status`
  `(trace_id → status, stopped_at_seq)` table, or derived on read from the
  presence of a gap. Recorded as an open item, not settled here, and deliberately
  **not** shipped with #117 — absent-payload handling is its own ticket, and
  adding a column for it early would fix this open choice by accident.

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
  defer per case; taint/reachability cutoff instead of whole-trace stop.
- Trace-level status location (D6 / Schema): dedicated `lineage_trace_status`
  table vs derived-on-read.
