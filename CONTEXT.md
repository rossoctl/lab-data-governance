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

**Entity**:
A participant in a Kagenti **Interaction**. One of six kinds: `user` (interacts
with an agent via the Kagenti UI), `external_client` (invokes an agent from
outside the platform), `agent`, `tool`, `external_service` (called by a tool),
`llm` (large-language-model endpoint, identified by host + model).
Identity is the kind plus a natural key per kind, derived by the
**Caller inference rule** from span attributes (and, when available, from
Kagenti platform-stamped attributes — currently absent). Entities are
cross-trace stable: the same agent or tool resolves to the same `entities` row
across every trace it appears in.
_Avoid_: "service" alone — `service.name` is one input to entity identity, not
a synonym for it.

**Caller inference rule**:
The processor-side rule by which `P-interactions` derives **Entity** identity
(kind + natural key) from a **Span**'s attributes. Hybrid by design: prefer
Kagenti platform-stamped attributes (`kagenti.*`) when present, fall back to
generic OTEL / OpenInference attributes (HTTP server attributes,
`client.address`, `user_agent`, `peer.service`, `service.name`, OpenInference
LLM/tool keys). External callers/services with no richer evidence are
identified by network-layer info — IP or hostname. The rule is load-bearing
for correctness of the entity graph; documented in its own ADR.

**Interaction**:
A single directed call from one **Entity** to another, evidenced by one or
more **Spans**. Produced by `P-interactions`, not by the receiver. The
caller side may have no span of its own (e.g. a user-from-UI call before
the platform annotates) — in that case the interaction is anchored on the
callee span and the caller is synthesised by the **Caller inference rule**.

**P-interactions**:
The processor that reads stored **Spans** and derives **Entities**,
**Interactions**, and **Payloads**, writing them to the `entities`,
`interactions`, `interaction_spans`, and `interaction_payloads` tables. Runs
after `P-otel-receiver`; semantically aware where the receiver is not. Out of
scope for v1 ingestion; introduced as a later increment (v2-shaped — it
crosses PROJECT.md §4's "no payload extraction in v1" line deliberately).
Streaming consumer of `spans` cursored by `seq` (§6 pattern): processes each
span as it lands, writes **Interactions** incrementally, re-encounters spans
on **Finalization** when their `seq` advances. Interactions follow their own
append-and-finalize policy mirroring ADR-0004 one layer up.

**Payload**:
The request or response body of an **Interaction**, canonicalized by
`P-interactions` into a normalized form, content-addressed by hashing that
canonical form, and stored once in `interaction_payloads`. An **Interaction**
references its request and response payloads (if any) via nullable
`request_payload_hash` / `response_payload_hash` columns. The source bytes
still live in `spans.attributes` (the receiver doesn't extract); the payload
row is the processor's *decided* representation, not the raw attribute value.
Dedup is per-hash over the canonical form: two raw representations that
canonicalize the same resolve to one row.
_Avoid_: "payload" as a synonym for "attributes" — every span attribute is
not a payload; only the request/response bodies that the **Payload extraction
rule** identifies and canonicalizes are.

**Payload extraction rule**:
The processor-side rule by which `P-interactions`, for a given
**Interaction**, (1) identifies which span attributes carry the request and
response bodies, (2) canonicalizes each into a normalized form per its
**Content kind**, and (3) hashes the canonical form to produce the row's
`content_hash`. Load-bearing for dedup quality and analytics correctness.

**Content kind**:
The semantic shape of a **Payload**. Closed enum defined by `P-interactions`
(initial members: `llm_chat_prompt`, `llm_completion`, `tool_call_arguments`,
`tool_call_result`, `http_request_body`, `http_response_body`,
`agent_message`, `unknown`); adding a kind is a code + migration change. Each
non-`unknown` kind has its own canonicalization rule under the
**Payload extraction rule**. `unknown` is reserved for payload-shaped
attributes the processor did not recognise: the **Payload** is still stored
(raw bytes, no canonicalization, hash over the raw bytes) so the
**Interaction** still carries a `request_payload_hash` /
`response_payload_hash` and analytics can measure classifier coverage by
filtering `content_kind = 'unknown'`. Stored on
`interaction_payloads.content_kind`.

**TraceListingEntry**:
One row of the recent-traces UI view — a derived display of a **Trace**,
anchored on its current **Listing root**. Rendered from a `GET /spans` call
with `root_only=true` (each returned `Span` is one trace's listing root).
Eventually consistent: the listing root, and therefore the row's display
fields, may change as late spans arrive or **Finalization** advances a
span's `seq`. Identity is `trace_id`; everything else is derived. The UI
dedupes by `trace_id` across paginated responses to collapse anchor flips
into a single row, keeping the highest-`seq` anchor.

**Span role**:
The abstract protocol classification assigned to a **Span** by a scope-specific
classifier within `P-interactions`. One of three values:
- **Send** — the span represents the sender side of a cross-entity call (local entity initiates, remote entity receives).
- **Receive** — the span represents the receiver side of a cross-entity call (remote entity called, local entity handles).
- **Internal** — the span represents local work within a single entity; no cross-entity boundary is crossed. Unknown spans with no recognisable semantics are always classified Internal.
_Avoid_: "call event", "non-call event" — those are the algorithm doc's construction-time terms, not the domain vocabulary. Use Send / Receive / Internal.

**Scope graph**:
The directed graph built by `P-interactions` for a single OTel instrumentation
scope (e.g. the starlette+httpx HTTP layer, or the openinference+a2a agentic
layer). Nodes are entity nodes and event nodes; edges are gray (ordering within
an entity) or black (cross-entity call). Black edges partition the graph into
entity subgraphs: every node reachable within a subgraph (connected only by
gray edges) belongs to the same entity and collapses to a single entity node in
Step 1.b. No key matching is needed — entity boundaries are structural, defined
solely by black edges. Built from **Span role** classifications — one graph per
scope layer. See also **Layered graphs**, **Cross-scope merge**.

**Layered graphs**:
The collection of **Scope graphs**, one per instrumentation scope, produced from
the same **Trace** before cross-scope merging. Each layer may capture a
different protocol view of the same underlying calls (e.g. HTTP layer vs
agentic layer). Intermediate output of `P-interactions`; persisted to scratch
tables for evaluation.

**Cross-scope merge**:
The Step 2 operation in `P-interactions` that unifies nodes across **Layered
graphs** into a single merged graph. Primary signal is graph structure
similarity (Send nodes merge with Send nodes, Receive with Receive — never
Send with Receive); secondary signal is shared attributes (URL, host, address).
Nodes that exist in only one scope layer are retained as-is. The merged graph
is the source from which final **Entities** and **Interactions** are derived.

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
