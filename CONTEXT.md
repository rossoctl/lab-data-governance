# Data Governance

The data-governance extension of Kagenti: ingests OTEL spans from Kagenti agents,
tools, and services, stores them verbatim in Postgres, and exposes typed retrieval
for a UI and future processors. v1 is span-shaped only — semantic concepts
(interactions, lineage, classifications) are deliberately deferred.

## Language

**Span**:
A single OTEL span row in the `spans` table, identified by the composite key
`(trace_id, span_id)`. Stored verbatim with payloads inline in `attributes`.
A row may be inserted once and updated exactly once on completion (see
**Finalization**); the row is otherwise immutable.

**Finalization**:
The conditional UPDATE applied when an OTLP sender flushes a span before its
end and later sends the completed version (same `(trace_id, span_id)`,
populated `ended_at`). Mutable columns are overwritten; `seq` advances to a
fresh sequence value; `arrival_seq`, `started_at`, `parent_id`,
`service_name`, `observed_at` are preserved. See ADR-0004.

**Arrival seq** (`arrival_seq`):
The original `seq` value assigned to a **Span** at INSERT time. Stable per
row — never updated, even on **Finalization**. Used by consumers that need a
durable per-row identifier (recovery checkpoints, idempotence keys). Distinct
from `seq`, which is a watermark and may advance once per row.

**Trace**:
The set of all **Spans** sharing a `trace_id`. Has no inherent root or boundary
beyond the shared id; "tree shape" is derived from `parent_id` links at query
time.
_Avoid_: "request", "session" — those imply semantics v1 does not assign.

**Listing root**:
The one **Span** chosen to represent a **Trace** in a UI listing row. Defined as
the trace's earliest **Real root** if one exists, otherwise its earliest
**Orphan span**. Computed at query time, not stored — see ADR-0001.
_Avoid_: "root span" alone (ambiguous between Real root and Listing root).

**Real root**:
A **Span** whose `parent_id IS NULL`. The OTEL-defined start of a trace.

**Orphan span**:
A **Span** whose `parent_id` is set but whose referenced
`(trace_id, parent_id)` does not exist in `spans` at query time. The parent may
have been dropped by the §3.1 blocklist, lost in transit, evicted by a future
retention policy, or simply not yet ingested. Orphan-ness is **eventually
consistent**: a span that is an orphan at T1 may cease to be one at T2 when its
parent arrives.

**In-window**:
A property of a **Span** in a query result, true iff its `started_at`
(trace-clock, OTLP-supplied) falls within the request's `(time_from, time_to)`.
False on out-of-window spans returned because they are **Listing roots** of
in-window traces. Carried as the `in_time_window` field on returned `Span`
objects; defaults to true when the caller supplied no window. Window basis is
trace-clock, not receiver-clock — so `observed_at` (when the receiver saw the
span) does not affect window membership.

**Listing root fallback**:
The rule by which a **Trace** with no **Real root** still gets a **Listing
root**: pick the earliest **Orphan span**. Lets the UI show every trace with
in-window activity, even when the OTEL root never arrived.

**Blocklist**:
The hardcoded set of span-name patterns (exact or `prefix*`) that the
`P-otel-receiver` drops at the OTLP socket before writing. See PROJECT.md §3.1.

**P-otel-receiver**:
The single v1 ingestion processor. Owns the OTLP socket (gRPC and HTTP),
applies the **Blocklist**, and writes accepted **Spans** verbatim to the
`spans` table. Stays semantically unaware — no classification, normalization,
correlation, or payload extraction.

**Retrieval API**:
The read-only Python library that consumers (UI backend, future processors)
call to read **Spans**. Exposes typed methods only — there is no SQL
escape hatch. Built on the **db module** (Layer 1) per ADR-0005. Future
processors that need bespoke SQL use the db module directly alongside the
retrieval API.

**db module** (Layer 1):
The thin generic Postgres connection/transaction wrapper above
psycopg 3 + psycopg_pool that owns connection lifecycle, transaction
boundaries, and parameterized query execution. Consumed by domain
functions (Layer 2): `write_span` (receiver), `get_spans` (UI backend
+ future processors), the §3.1 blocklist tally writer, and any future
processor's storage operations. See ADR-0005.

**TraceListingEntry**:
One row of the recent-traces UI view — a derived display of a **Trace**,
anchored on its current **Listing root**. Rendered from a `GET /spans` call
with `root_only=true` (each returned `Span` is one trace's listing root).
Eventually consistent: the listing root, and therefore the row's display
fields, may change as late spans arrive or **Finalization** advances a
span's `seq`. Identity is `trace_id`; everything else is derived. The UI
dedupes by `trace_id` across paginated responses to collapse anchor flips
into a single row, keeping the highest-`seq` anchor.

**Entity**:
A derived node in the lineage graph: one row in the `entities` table per
distinct `(service_name, semantic_kind, sub_kind)` tuple. Derived from **Spans**
by the **Graph-builder**, not supplied by the source. Identity is a
deterministic `entity_id` over the tuple, so re-derivation is idempotent. See
ADR-0007.

**Edge**:
A derived directed boundary in the lineage graph: one row in the `edges` table
per cross-entity parent→child **Span** boundary (caller→callee). Keyed by the
**child** `(trace_id, span_id)`, so a **Span** has at most one edge.
Same-**Entity** parent/child pairs and **Real roots** produce no edge. Carries
no timing or payload — those join back to the child **Span** (ADR-0006). See
ADR-0007.

**Semantic kind**:
The classification of a **Span**'s **Entity** — `LLM`, `TOOL`, `AGENT`,
`CHAIN`, `RETRIEVER`, `SERVER`/`CLIENT`, `PRODUCER`/`CONSUMER`, or `UNKNOWN` —
derived by a fixed ladder (`openinference.span.kind` → any `llm.*` key present →
any `gen_ai.*` key → OTLP span `kind` → `UNKNOWN`).
_Avoid_: conflating it with the OTLP `kind` column, which is only the ladder's
fallback rung.

**Sub-kind**:
The refinement discriminator on an **Entity**: the model name when **Semantic
kind** is `LLM`, the tool name when `TOOL`, else `NULL`. Keeps distinct models
and distinct tools from collapsing into one **Entity**.

**Graph-builder**:
The Layer-2 processor that reads **Spans** via the **db module** and writes
**Entities** and **Edges** idempotently — the planned read+write processor of
ADR-0005 / PROJECT.md §6. Separate from **P-otel-receiver**, which stays
semantically unaware. See ADR-0007.
_Avoid_: implying the receiver classifies spans — derivation is the
**Graph-builder**'s job, not the receiver's.

**Orphan boundary**:
An **Edge** whose parent **Span** is absent at derivation time (an **Orphan
span**'s boundary), recorded with `from_entity = NULL` and `edge_kind =
UNKNOWN_*` rather than dropped. **Eventually consistent**: the next
**Graph-builder** pass flips `from_entity` to the real **Entity** when the
parent arrives. The graph view renders it as a single `(external / uncaptured)`
source node. Mirrors the **Listing root fallback** for orphans.

## Relationships

- A **Trace** contains one or more **Spans**, all sharing its `trace_id`.
- A **Span** has at most one parent **Span** within the same **Trace** (or no
  parent, making it a **Real root**, or a missing parent, making it an
  **Orphan span**).
- A **Trace** has zero or more **Real roots** (typically one) and zero or more
  **Orphan spans**. It always has exactly one **Listing root** at any given
  moment, chosen by the **Listing root fallback** rule.
- A **TraceListingEntry** is a derived view of one **Trace**, anchored on its
  current **Listing root**.
- The **Retrieval API** is the only sanctioned read path over **Spans**; the UI
  backend composes its REST endpoints from it.
- A **Span** maps to exactly one **Entity** (by its `(service_name,
  semantic_kind, sub_kind)`); an **Entity** aggregates many **Spans**.
- An **Edge** connects two **Entities** (`from_entity` → `to_entity`), derived
  from one child **Span**'s boundary with its parent; an **Orphan boundary** has
  a NULL `from_entity`.
- The **Graph-builder** derives **Entities** and **Edges** from **Spans**, just
  as the **Retrieval API** reads **Spans** — both are Layer-2 consumers of the
  **db module** (ADR-0005), and the **Graph-builder** is also a writer.

## Example dialogue

> **Dev:** "If a trace's `parent_id IS NULL` span hasn't arrived yet, does
> `GET /spans?root_only=true` skip the trace?"
> **Designer:** "No. The **Listing root fallback** picks the earliest **Orphan
> span** as the **Listing root** so the trace still shows up. The UI flags it
> by checking whether the returned listing root's `parent_id` is null
> (**Real root**) or not (orphan acting as listing root)."

> **Dev:** "What if the real root arrives later?"
> **Designer:** "Then the **Listing root** flips on the next query. The
> **TraceListingEntry**'s display fields update — that's the eventual
> consistency we accept in v1. See ADR-0001."

## Flagged ambiguities

- **"Root"** was used loosely to mean both `parent_id IS NULL` (OTEL sense) and
  "the span shown in the listing" (UI sense). Resolved: **Real root** vs
  **Listing root** — distinct terms.
- **"Trace"** vs **"TraceListingEntry"** — the OTEL set of spans vs the UI row
  derived from it. They share `trace_id` but the row's other fields are
  derived and eventually consistent; the trace itself is just the set.
