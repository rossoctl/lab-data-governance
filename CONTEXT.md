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
call to read the stored and derived governance data. Exposes typed methods
only — there is no SQL escape hatch. Built on the **db module** (Layer 1) per
ADR-0005. The `data_governance.retrieval` package is the sanctioned read path,
split by seam into typed submodules re-exported from its root: `spans`
(**Span** reads — `get_spans`), **Interaction retrieval** (the derived
**Interaction**/**Entity** forest — `get_interactions`, `get_entities`, and
their span-evidence sub-reads), and `payloads` (a content-addressed **Payload**
read that inlines the **Classification** verdict — `get_payload`). Each returns
frozen dataclasses; the REST layer maps them mechanically to the wire, so all
derivation (leg **Duration**, aggregated `error`, chronological ordering, the
nullable-classification eventual-consistency shape, the not-yet-migrated empty
shape) lives behind the interface, not in the HTTP handlers. Future processors
that need bespoke SQL still use the db module directly alongside the retrieval
API.

**Interaction retrieval**:
The typed read path over the derived **Interaction**/**Entity** forest,
sibling to the span-only `get_spans` and part of the same **Retrieval API**
package (`data_governance.retrieval.interactions`). Exposes
`get_interactions(trace_id)` (parent identity rows with their nested
**Interaction leg**s, computed leg **Duration** and aggregated `error`, span
and anchor counts, ordered by the request leg's `occurred_at`),
`get_entities(trace_id)` (the **Entities** the processor recorded provenance
for in the trace, reached via the trace-scoped `entity_spans`), and the
per-row span-evidence sub-reads `get_interaction_spans` /
`get_entity_spans`. Reads are trace-scoped and eventually consistent (they
reflect whatever `P-interactions` has materialised so far); before the
interactions migration has run they return an **empty typed result**, never an
error — so a fresh DB serves an empty flow, not a 500. Distinct from `payloads`
retrieval, whose key is a `content_hash` and whose lifecycle is write-once and
cross-trace.
_Avoid_: reading the `interactions` / `entities` tables with inline SQL from
the REST layer — that split (query in the handler, data in the db module) is
what **Interaction retrieval** exists to close.

**db module** (Layer 1):
The thin generic Postgres connection/transaction wrapper above
psycopg 3 + psycopg_pool that owns connection lifecycle, transaction
boundaries, and parameterized query execution. Consumed by domain
functions (Layer 2): `write_span` (receiver), `get_spans` (UI backend
+ future processors), the §3.1 blocklist tally writer, and any future
processor's storage operations. See ADR-0005.

**Entity**:
A participant in a Kagenti **Interaction**. One of seven kinds: `user`
(interacts with an agent via the Kagenti UI), `client` (invokes an agent
from outside the platform with no platform-stamped user identity), `agent`,
`tool` (in-process, hosted by an agent), `tool` (deployed as its own
service, e.g. an MCP server — same kind, distinguished by natural-key
shape), `llm` (large-language-model endpoint, identified by host + model),
`service` (an external HTTP service called by an agent or tool). Identity
is the kind plus a **Natural key** per kind, derived by the **Caller
inference rule** from span attributes. Entities are cross-trace stable:
the same agent or tool resolves to the same `entities` row across every
trace it appears in. Entities carry `seq` (advances on every mutation —
display-name updates, evidence-span additions, retract events) and
`original_seq` (preserved at creation), mirroring **Interaction** and
ADR-0004 spans. Stream consumers cursor on `seq`; `original_seq`
distinguishes first-emission rows from mutations.
_Avoid_: `service.name` (the OTEL attribute) and `Entity.kind = service`
(the external-callee entity) read alike but are unrelated. The OTEL
`service.name` is one input to **agent** and deployed-**tool** identity;
it is *not* the input to `Entity.kind = service` identity. Always qualify.

**Natural key**:
The stable, kind-specific identity string for an **Entity**. One row per
`(kind, natural_key)` in `entities`. Format per kind:
- `user` → `user:<kagenti.user.id>`
- `client` → `client:<host-or-ip>` (preferring `peer.service`, then
  `client.address` host, then `client.address` IP)
- `agent` → `agent:(<project_name>,<canonical_service_name>)`
- `tool` (in-process) →
  `tool:<owning_agent_natural_key>:<tool_name>`
- `tool` (deployed) →
  `tool:(<project_name>,<canonical_service_name>)`
- `llm` → `llm:<host>/<model>` (`<host>` = `(unknown)` when absent —
  unresolved-host calls collapse onto a single `llm:(unknown)/<model>`
  entity per model, cross-trace stable. Per-interaction retarget when a
  paired CLIENT POST surfaces a host: the individual interaction moves
  to `llm:<host>/<model>` while other unresolved interactions remain
  attached. The unresolved entity is destructively retracted only when
  its last attached interaction retargets away. Per ADR-0011.)
- `service` → `service:<hostname>` (port out of scope for v2)

**Canonical service name**:
A **Span**'s `service.name` with its `openinference.project.name` stripped
as a prefix (with optional trailing `-` or `_` separator), if both
attributes are present and the prefix matches. Otherwise the literal
`service.name`. Computable per span. Used as the second component of an
`agent` or deployed-`tool` natural key — paired with the project name so
that two services with the same canonical name but different project names
are distinct entities.

**Caller inference rule**:
The processor-side rule by which `P-interactions` derives **Entity** identity
(kind + natural key) from a **Span**'s attributes. Hybrid by design: prefer
Kagenti platform-stamped attributes (`kagenti.*`) when present, fall back to
generic OTEL / OpenInference attributes (HTTP server attributes,
`client.address`, `user_agent`, `peer.service`, `service.name`, OpenInference
LLM/tool keys). External callers/services with no richer evidence are
identified by network-layer info — IP or hostname. The rule is load-bearing
for correctness of the entity graph; documented in its own ADR.

The **caller** of any leg is the **nearest enclosing entity** on the
span's same-service ancestor walk — the innermost in-process **tool** or
deployed-**tool** span that contains the call, else the owning **agent**
(an enclosing `llm` span is the call's own transport and is absorbed, never
a caller). This is what makes the in-framework delegate's outbound A2A leg
attribute to the delegate `tool` (the call physically issues from inside the
tool's span), exactly as the deployed-MCP tool's outbound call attributes to
that deployed `tool` — the difference is lineage position, not which service
owns the span (ADR-0016, refining ADR-0010).

**Interaction**:
A single directed call from one **Entity** to another, evidenced by one or
more **Spans**. Produced by `P-interactions`, not by the receiver. The
caller side may have no span of its own (e.g. a user-from-UI call before
the platform annotates) — in that case the interaction is anchored on the
callee span and the caller is synthesised by the **Caller inference rule**.
Eventually consistent: an interaction's `caller_entity_id`,
`callee_entity_id`, payload hashes, and aggregated `error` may be mutated
in place as more spans arrive (late parents, **Finalization**,
**Interaction reconciliation**). Identity (`id`) is stable across
mutations. Per ADR-0025 an interaction is a **parent identity row**
(`interactions`) plus one or two **Interaction leg**s (`interaction_legs`):
the payload hash, `occurred_at`, `error`, and `seq` live on
the *leg*: each leg carries its **own distinct** `seq` (DB-owned `nextval`,
request leg inserted first → lower seq; ADR-0027 Reversal), so a leg finalizes
and cursors on its own `seq`; the parent carries only shared identity and has
no `seq`. Stream consumers cursor `interaction_legs` on `seq`. (A leg has no
`original_seq`: unlike an **Entity**, a leg's `seq` is DB-owned and never
mutates on re-derive, so a frozen-vs-mutating pair would carry no information —
issue #133 dropped it.)
There is no "complete" flag —
consumers see the current state at query time. Anchor rules set
identity (`caller_entity_id`, `callee_entity_id`) on creation only;
re-fires of the same anchor rule on **Finalization** do not re-assert
identity. Identity is mutated only by a more-informed anchor (e.g.
late-arriving CLIENT parent retargeting the caller, ADR-0007) or by
**Interaction reconciliation** (cross-anchor evidence retargeting the
callee, ADR-0011). The general invariant: more-informed values are
never replaced with less-informed ones.

**Interaction leg** (`leg_type`):
A single directed call (**Interaction**) is recorded as a **parent identity
row** (`interactions`) plus **one or two leg rows** (`interaction_legs`), keyed
`(interaction_id, leg_type)` with `leg_type ∈ {request, response}`. This is a
**two-table** split (ADR-0025), not the single-`(id, direction)`-table shape
first sketched here: identity that is identical across legs lives *once* on the
parent, and only the leg-dependent, independently-finalizing fields live on the
leg. So leg disagreement on identity is inexpressible by construction, not
merely by convention.
- **On the parent** (`interactions`, one row per call): `id`, `trace_id`,
  `parent_interaction_id`, `caller_entity_id`, `callee_entity_id`, `summary`.
  Both legs necessarily share these — a response is the return value of the
  caller→callee call, not a new callee→caller call. The parent has **no `seq`**
  and is not independently cursorable (identity is immutable once decided); it
  is a join target for identity.
- **On the leg** (`interaction_legs`): `leg_type`, `occurred_at` (the request
  leg's is the call-start time, the response leg's the completion time),
  `payload_hash` (request vs response body), `error`, and its own `seq`
  (no `original_seq` — issue #133). Each leg finalizes independently and advances its own `seq` —
  this per-leg cursor is the mechanism that lets a stream consumer see "response
  landed" as a distinct event from "request sent". `interaction_legs_seq`
  replaces the retired `interactions_seq` as the cursorable stream.
The **Interaction tree** (`parent_interaction_id`) and the unique span-ownership
invariant (ADR-0011) both key on the parent `interaction_id`, not on the leg —
so the split leaves ADR-0008 and ADR-0011's `(trace_id, span_id)` uniqueness
untouched (`interaction_spans` gains a `leg_type` column so a span attributes to
a specific leg, but its PK stays `(trace_id, span_id)`). **Duration** is
computed on read (`response.occurred_at − request.occurred_at`), **null when the
response leg is absent** — the "response in flight" signal — never stored. A
parent is never leg-less: it is created together with its request leg. Drivers:
request/response are temporally asymmetric, have independent lifecycles
(streaming responses), draw as two arrows on an execution-flow diagram, and
carry independent governance policy (classification, retention, redaction,
`error`) per leg.
_Avoid_: treating a persisted `response` leg as a callee→caller edge — in the
schema the orientation is identical on both legs (caller/callee live on the
shared parent); only `leg_type` distinguishes them. (The graph algorithm does
form a callee→caller edge *internally* per call, but `graph_adapter` folds it
into the parent's response leg — see "Leg provenance".)
A leg becomes actionable for a governance consumer only at **Leg readiness**
(written *and* its payload, if any, classified) — see that term and ADR-0027;
"leg written" and "leg ready" are different instants for a payload-bearing leg.

**Leg provenance** (derived vs. observed):
Whether an **Interaction leg**'s timing is independently observed or projected
from a single span. This varies by which P-interactions algorithm wrote the legs:

- **Streaming algorithm — derived legs (Case-X).** One span carries both request
  and response payloads, known at once. `state.flush` projects its one internal
  interaction into a request leg (`occurred_at = started_at`) and a **derived**
  response leg (`occurred_at = ended_at`) — two legs of a synchronous call
  bracketed `started_at → ended_at`, sharing the one span as evidence. Each leg
  still gets its **own** DB-owned `seq` (`nextval`, request inserted first → lower
  seq; ADR-0027 Reversal), so a leg-readiness consumer can order and cursor them
  on a single seq even though they derive from one span. Honest (those timestamps
  genuinely bound the call) but *not* an independent lifecycle.
- **Graph algorithm — observed-style response legs.** The graph forms a
  bidirectional interaction per call (a request edge and a structurally-
  reconstructed response edge), each with its OWN anchor span and its own global
  `order`. Its adapter (`graph_adapter`) supplies explicit per-leg rows
  (`ProductionRows.legs_by_ix`), so the response leg's `occurred_at`/`error`/
  `payload_hash` come from the **responding endpoint's own span** — for an A2A
  delegation the responding agent's wrapper span, distinct from the request's
  call-site span. Each leg's `seq` is its edge's `order`, so request-before-
  response and nested-call LIFO ordering survive into the schema even when the
  two edges happen to share one anchor span. The parent stays oriented
  caller→callee (see "Interaction leg" — orientation is on the parent, not the
  leg); only the leg's timing/payload/error/`seq` are per-leg.

A future Case-Y source (two spans, distinct `span_id`s, shared exchange id,
arriving at different times) produces fully **observed** legs finalizing
independently. Consumers reading a `response` leg's `occurred_at` as "when the
response actually happened" are correct for the graph's observed-style and the
future observed legs, and approximately correct (= call return time) for the
streaming algorithm's derived ones. The split into legs is a **boundary
projection**: the verified `--scramble`-gated streaming algorithm holds one
interaction internally and is unchanged (ADR-0025); the graph algorithm owns its
per-leg projection in `graph_adapter`.
_Avoid_: assuming every `response` leg was observed from its own span — the
streaming algorithm's derived legs share the request span.

**Anchor span**:
A **Span** whose presence triggered the creation of an **Interaction**
under one of the anchor rules in `P-interactions`. Each interaction has
either one anchor (single-side rules: `client→agent`, `agent→llm`,
`agent→tool` (in-process), `agent→service` (external HTTP)) or two anchors
(boundary-crossing rules: cross-service parent/child, or deployed-MCP tool
call). Anchor identity is recorded on `interaction_spans` with role
`anchor`. Anchor rules fire on span structure (kind, parent, attributes);
the kinds of the **Entities** at either end are decided independently by
the **Caller inference rule** — the same anchor rule fires whether the
caller is an `agent`, a `tool`, or a `client`.

**Interaction tree**:
The parent-child structure on **Interactions** within a **Trace**. The
parent of interaction I is the innermost enclosing interaction whose
territory contains I's anchor(s); the trace root span itself is *not* an
interaction. A trace's interaction tree is a forest — one tree per
top-level interaction, with `parent_interaction_id IS NULL` at each root.
Stored on `interactions.parent_interaction_id` (nullable, self-referential).

**Interaction-span role**:
The role a **Span** plays on an **Interaction** it is attached to. Closed
enum: `anchor` (creation evidence), `info` (non-anchor span that
contributed payload, error, or exception evidence), `connector`
(non-anchor span in the interaction's territory that contributed nothing
classifiable, kept for traceability). The role can be promoted from
`connector` to `info` if a later **Finalization** reveals payload or
error attributes, or if **Interaction reconciliation** transfers a span
carrying payload/error onto the surviving interaction. Stored on
`interaction_spans.role`. **Schema invariant** (ADR-0011):
`interaction_spans` is unique on `(trace_id, span_id)` — each **Span**
in a **Trace** belongs to exactly one **Interaction**. This materialises
ADR-0008's innermost-territory rule as a hard constraint; ownership
conflicts surface as commit-time errors rather than silent
`min(started_at)` distortions. Reconciliation rules that retract an
interaction must transfer or clear its span ownerships in the same
transaction.

**Entity-span role**:
The role a **Span** plays on an **Entity** it evidences. Closed enum:
`discovered_via` (the span that first revealed the entity, exactly one row
per entity) and `identified_via` (subsequent spans that re-confirmed
identity). Stored on `entity_spans.role`. A single span can produce
entity_spans rows for multiple entities (e.g. a SERVER span identifying
both its caller and its callee).

**Provisional entity** _(legacy under **Sufficiency-gated emission**,
ADR-0012 — that model never emits a provisional entity, since identity is
decided only when final)_:
An **Entity** synthesised when the streaming model anchors an
**Interaction** before complete identity information is available — for
example, a SERVER span that arrives before its parent CLIENT span. The
entity's natural key uses the best evidence currently available (e.g.
`client:10.0.5.42` from `client.address`); when a later span reveals a
better key (`client:demo-client` from `peer.service`, or replaces the
caller entirely with an `agent` entity), the **Interaction**'s endpoint
foreign key swaps to the better entity and the provisional entity is
**destructively retracted** if no other interaction references it
(ADR-0011, superseding ADR-0007's earlier "left orphaned" stance).

**Sufficiency-gated emission**:
The emission discipline by which `P-interactions` materialises an
**Entity** or **Interaction** *only* at the arrival of the **Span** that
makes the already-arrived span set sufficient to decide it **finally** —
no future span can change it. Emit-once-when-decidable: never an eager
emit that is later corrected, and never an end-of-trace pass. Every anchor
rule fires on a *positive* completing span (an OpenInference
`AGENT`/`LLM`/`CHAIN`/`TOOL` span, a `/mcp` `SERVER` span, a paired
endpoint), because a *negative* conclusion ("no such span will ever
arrive") has no triggering event. A late parent resolves on the parent's
own arrival by look-back over already-arrived spans. Per ADR-0012;
production-directional, superseding **Interaction reconciliation**'s
emit-then-retract model.
_Avoid_: conflating with **Interaction reconciliation** — that is the
superseded eager-emit-then-retract pass; this is the lazy
emit-when-decidable discipline that makes retraction unnecessary.

**Interaction reconciliation** _(superseded by **Sufficiency-gated
emission**, ADR-0012)_:
A streaming post-anchor pass over already-emitted **Interactions** that
recognises when two **Anchor rules** fired on different spans for the
same logical call and collapses the redundancy. Distinct from the
**Caller inference rule** (per-span identity) and from anchor rules
(per-span structure): a reconciliation rule operates *per interaction*,
across anchors. Each rule names a pairing condition (descendant span
chain, time-window containment, attribute equivalence, etc.), a
preserved-interaction choice, and a **destructive retract** of the
redundant interaction(s) plus any provisional entities they leave
orphaned. When a retracted interaction had children in the
**Interaction tree**, those children's `parent_interaction_id` is
recomputed by the ADR-0008 walk (skipping retracted anchors); a child
may become a new top-level interaction if no surviving ancestor
remains. Reconciliation runs on every span arrival (a newly-arrived
span may complete a pairing) and is idempotent. Pairing candidates
are looked up against the durable `interactions` and `spans` tables
(scoped to the current trace), not in-memory processor state — this
is what makes reconciliation correct across cursor replay and
processor restart. Pairing search is
structural-first, scoped within a single trace: on CLIENT-side anchor
arrival, walk the span's ancestors and pair against an unresolved
interaction anchored on an ancestor; on LLM-side anchor arrival, scan
already-arrived descendant spans in the same trace for a paired
CLIENT-side anchor. Sibling-case pairing (service + time-window
overlap) is consulted only when no descendant pairing matches; on
multi-match the rule fails closed (the parallel-call ambiguity stays
unresolved, surfaced as instrumentation signal). Initial member:
LLM/HTTP-transport pairing — an `openinference-llm` interaction and an
`external-http` interaction emitted on the same logical call collapse
to one (ADR-0011). Future members: in-process-tool / deployed-tool
merge (left open by ADR-0010).

**Destructive retract** _(legacy under **Sufficiency-gated emission**,
ADR-0012 — that model emits only final rows, so the retract path is never
exercised)_:
The mechanism by which an already-emitted **Interaction** or
**Provisional entity** is removed from the consumer-visible world. Wire
format is a tombstone: a nullable `retracted_at TIMESTAMP` column on
both `interactions` and `entities`, with `seq` advancing on the
retraction event. Default reads filter `retracted_at IS NULL` — to a
default-query consumer, the row is gone (entity IDs may 404 between
reads). Stream consumers cursoring on `seq` see the retraction as a
`seq`-ordered mutation by reading the column directly, preserving the
ADR-0007 cursor model. Interactions are only ever retracted by
**Interaction reconciliation**; they are never deleted otherwise (other
mutations — retarget, identity-lock, payload/role updates, parent
recomputation — leave the row visible and bump `seq`). Entities are
retracted only when their last attached **Interaction** detaches (the
unresolved-LLM orphan case, generalised: ADR-0011 supersedes ADR-0007's
"left orphaned" stance for all provisional entities). Entity identity
fields are never edited in place — host-fill-in for an
`llm:(unknown)/MODEL` happens by per-interaction retarget onto a
distinct `llm:<host>/<model>` row, not by mutating the unresolved row.

**P-interactions**:
The processor that reads stored **Spans** and derives **Entities**,
**Interactions**, and **Payloads**, writing them to the `entities`,
`entity_spans`, `interactions`, `interaction_spans`, and
`interaction_payloads` tables. Implemented by the
`data_governance.processors.interactions` module (the module name drops the
`P-` prefix, mirroring how `P-otel-receiver` is the `otlp_receiver` module).
Runs after `P-otel-receiver`; semantically
aware where the receiver is not. Out of scope for v1 ingestion; introduced
as a later increment (v2-shaped — it crosses PROJECT.md §4's "no payload
extraction in v1" line deliberately). Streaming consumer of `spans`
cursored by `seq` (§6 pattern), woken by `LISTEN dg_spans_inserted` with a
~5-10s polling backstop: processes each span as it lands, writes
**Interactions** incrementally, re-encounters spans on **Finalization**
when their `seq` advances, re-evaluates earlier-arrived children when a
late parent arrives. Cursor is durable
(`processor_state.last_processed_seq`); per-span work is one transaction.
Interactions follow their own append-and-finalize policy mirroring
ADR-0004 one layer up — see ADR-0007.

**Payload**:
The request or response body of an **Interaction**, canonicalized by
`P-interactions` into a normalized form, content-addressed by hashing that
canonical form, and stored once in `interaction_payloads`. Each **Interaction
leg** references its payload (if any) via its nullable `payload_hash` column
(the request leg the request body, the response leg the response body; ADR-0025
moved these off the parent `interactions` row). The source bytes
still live in `spans.attributes` (the receiver doesn't extract); the payload
row is the processor's *decided* representation, not the raw attribute value.
Dedup is per-hash over the canonical form: two raw representations that
canonicalize the same resolve to one row.
_Avoid_: "payload" as a synonym for "attributes" — every span attribute is
not a payload; only the request/response bodies that the **Payload extraction
rule** identifies and canonicalizes are.

**Leg readiness**:
When an **Interaction leg** is *ready* to be acted on by a governance consumer
(**risk**, data-lineage, the **Policy Decision Point**). A leg is ready once it
has been written **and** either it has no payload (`payload_hash IS NULL` —
nothing to classify) **or** its payload has a **Classification** (a
`payload_classifications` row exists for its `content_hash`). A payload-bearing
leg is deliberately *not* ready before its verdict lands: the verdict is the
governance input, so acting earlier would gate a call on absent information.
Readiness is a **latch** — classifications are write-once (ADR-0024), so once a
leg is ready it stays ready (the only exception is the deferred
re-classification hook, which would transiently un-ready legs; ADR-0027). The
readiness event is surfaced on the **`dg_interaction_leg_ready`** channel, but
that notification is *latency-only*: reliability comes from the consumer
draining a **Readiness cursor**, not from the notify. See ADR-0027.
_Avoid_: conflating "leg written" (a row exists in `interaction_legs`) with
"leg ready" (written *and* its payload, if any, classified) — the whole point
of the readiness signal is that these are different instants for a
payload-bearing leg.

**Readiness cursor** (contiguous-prefix):
The durable drain watermark a **Leg readiness** consumer advances over
`interaction_legs`. Unlike the ordinary `seq > cursor` stream cursor's
unconditional max-advance (spans, payloads, entities), it advances only across
the **leading unbroken run of ready legs** and stops at the first unready one —
because the readiness predicate is non-monotonic in `seq` (a low-`seq` leg with
an unclassified payload can sit behind a high-`seq` ready leg, and a max-seq
advance would strand it forever). Each leg carries its **own distinct** `seq`
(DB-owned `nextval`, request leg inserted first → lower seq), so a plain single
`seq` totally orders the legs: within one **Interaction** the `request` leg is
delivered before the `response` leg simply because its seq is lower — no
`leg_type` tiebreaker, and the watermark is a plain `BIGINT` that persists
directly in `processor_state.last_processed_seq` (the composite `(seq, leg_type)`
watermark of earlier shared-seq drafts is retired; ADR-0027 Reversal). Across
different interactions no order is guaranteed beyond the seq order itself. The
cost of the contiguous prefix is head-of-line blocking: one slow classification
holds the watermark until it lands. See ADR-0027.

**Ready channels** (`dg_entity_ready`, `dg_interaction_leg_ready`):
The two consumer-facing notification channels a governance consumer `LISTEN`s
on — `dg_entity_ready` (a new **Entity** was first detected) and
`dg_interaction_leg_ready` (a leg reached **Leg readiness**). Named for the
*semantic event they signal*, deliberately distinct from the internal
`_inserted` drain-wake channels (`dg_spans_inserted`, `dg_payloads_inserted`),
which name a *physical table write*. Both `_ready` channels are **latency-only**
(ADR-0015): an absent listener misses them and they carry no payload, so
correctness never rests on them — the consumer's durable cursor drain
(re-derived from the existing tables on startup and every wake) is the reliable
path. `dg_entity_ready` is fired by a DB trigger (first-detection `AFTER
INSERT`); `dg_interaction_leg_ready` is fired blindly from processor code
(P-classification after each classification write, `P-interactions` for
write-time-ready legs) because readiness is a cross-processor join completion no
single table write coincides with. See ADR-0027.
_Avoid_: treating a `_ready` notification as delivery-guaranteed or as carrying
the ready item — it is a "go look" tap; the cursor drain carries the data.

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
`agent_message`, `unknown`); adding a kind is a code change. Unlike the three
structural enums (**Entity** `kind`, **Entity-span role**, **Interaction-span
role**), which are domain-fixed and stored as Postgres `ENUM` types so a bad
value is rejected at write, `content_kind` is `TEXT`: the payload classifier
churns as it matures and `unknown` already covers the open-world case, so the
closed set is enforced in code by the **Payload extraction rule** rather than
by a DB type (see ADR-0014). Each
non-`unknown` kind has its own canonicalization rule under the
**Payload extraction rule**. `unknown` is reserved for payload-shaped
attributes the processor did not recognise: the **Payload** is still stored
(raw bytes, no canonicalization, hash over the raw bytes) so the
**Interaction** still carries a `request_payload_hash` /
`response_payload_hash` and analytics can measure classifier coverage by
filtering `content_kind = 'unknown'`. Stored on
`interaction_payloads.content_kind`.

**Classification**:
The data-governance verdict on the sensitivity of one **Payload** — its
document-level `sensitivity_level`, the regulatory tags it carries, whether it
holds an identity bundle, and the set of **Findings** within its body text.
Produced by **P-classification**, not by `P-interactions`. Keyed to the
**Payload** by `content_hash`: one classification per content-addressed
payload, so a body referenced by many **Interactions** or **Traces** is
classified exactly once (dedup inherited from the payload's content
addressing). Stored one row per payload in `payload_classifications`, with the
**Findings** inline as JSONB. Write-once: a **Payload** is immutable
(content-addressed), so its classification never mutates and there is no
finalization/`seq`-bump analogue — unlike **Interactions** and **Entities**,
which mutate in place. Each row is stamped with a monotonic integer
`model_version` (starts at 1) so classifications from different model/config
generations are comparable; automatic re-classification on version change is
deferred — a model upgrade is handled operationally (truncate
`payload_classifications`, reset the **P-classification** cursor to 0,
re-classify all payloads). See ADR-0024. Exposed inline on the payload read surface: `GET
/api/payloads/{hash}` carries a nullable `classification` field — null while
the **Payload** exists but **P-classification** has not yet run (the
eventual-consistency window), populated once the verdict lands. Every
**Payload** is classified uniformly (no per-kind skipping in this increment),
so a null classification means *exactly* "not yet processed," never "processed
but skipped" — a payload with no sensitive text gets a real `PUBLIC` /
zero-**Findings** verdict, not a null. (A future exclude-filter may skip
selected kinds; that is deferred.)

**Finding**:
One sensitive item the NER model detected in a **Payload**'s **Classifiable
text**: a `(start, end)` region (char offsets into the **Classifiable text**),
its detected entity type (`SSN`, `PN`, `EMAIL`, …), and the sensitivity
attributes derived for it (`sensitivity_level`, `regulatory_tags`,
`identifier_type`). A **Classification** carries zero or more findings; the
document-level verdict is aggregated up from them (plus identity-bundle
detection across the finding set). The stored/served JSON shape (the
`payload_classifications.findings` JSONB, served verbatim on
`/api/payloads/{hash}` and read by the UI's `Finding` wire type) keys the
detected type under **`entity_type`** — one spelling across logic, DB, API, and
UI. (Despite the key name it is an NER tag, not an **Entity**; the name is kept
for parity with the reference tool and the pre-existing UI type.)
_Avoid_: "span" for a finding — a **Span** is an OTEL row `(trace_id,
span_id)`; a finding is a sensitive region of a payload's text. They never
mean the same thing.
_Avoid_: calling a finding's detected type an **Entity** — that word is the
interaction participant (agent/tool/llm/…). A finding's type is an NER tag
(`PN`, `SSN`), a different taxonomy entirely.

**P-classification**:
The processor that reads **Payloads** and derives their **Classification**.
A Layer-2 processor, sibling of `P-interactions`; projects each **Payload**
into its **Classifiable text**, runs the fine-tuned NER model over that text to
detect **Findings**, then aggregates those to a sensitivity verdict. The
projection + aggregation logic is ported into the package
(`data_governance/processors/classification/`: `projection`, `logic`, behind the
`detector` seam) from the reference batch tool `classification/`, which stays as
the offline-eval harness (the port is parity-tested against it) — mirroring how
`P-interactions` was ported from its verified prototype. Consumes
`interaction_payloads`; semantically aware where the receiver is not. Runs the NER
model **in-process** in the drain loop, and
ships as its own container image (`data-governance/classification`) — separate
from the shared receiver/UI/interactions image because its torch + ~500 MB
model-weight dependency closure diverges heavily (the weights are baked into
the image; image tag ↔ `model_version`). See ADR-0022 (own image) and ADR-0023
(in-process model, baked-in weights + config).

**Classifiable text**:
The single natural-language string **P-classification** feeds to the NER
model for one **Payload** — the payload's human-meaningful prose projected
out of its JSONB `content` by the **Text projection rule**. Detected **Finding**
regions are `(start, end)` char offsets into this string, not into the stored
JSONB.

**Text projection rule**:
The processor-side rule by which **P-classification** projects a **Payload**'s
JSONB `content` into its **Classifiable text**, one branch per **Content
kind** (e.g. concatenate message bodies for `llm_chat_prompt`, take the result
string for `tool_call_result`). Like the **Payload extraction rule** it
mirrors, this is a closed set enforced in code that churns as content kinds
mature. `unknown` (and any kind without a branch) falls back to serializing
the whole `content` JSONB to a canonical string — best-effort classification
that also marks the projection-coverage gap.

**Data lineage**:
Where one **Payload**'s content originated and what it passed through. Carried
per **Interaction leg** (not per payload — see the key below) as the **Lineage
metadata** triple, derived by **P-data-lineage** and stored in
`lineage_metadata`. Answers the two governance questions the spec
(`docs/data_lineage_alg.md`) poses: "where did the data originate" (its **Data
source**s) and "what did it pass through" (which **Entities**, with which
**Transformation**s applied). v1 is **intra-trace** only — lineage within one
**Trace**; inter-trace lineage (flow through shared persistent storage, one
trace writing and another reading) is Step II and deferred. See ADR-0028.
_Avoid_: confusing this with **Span lineage** — the two are unrelated. Span
lineage is a graph-structure concept (a **Span**'s ancestors ∪ subtree under a
`seq` horizon, used by `P-interactions` to bound what one span's re-derivation
may rewrite — ADR-0007/0016). Data lineage is about content provenance. Qualify
the word every time: "span lineage" or "data lineage", never bare "lineage".
Served at three grains (ADR-0028 D14): the per-leg triple
(`GET /api/traces/{tid}/data-lineage`), **Lineage reachability** per **Entity**, and
a trace-level sources/destinations roll-up
(`GET /api/traces/{tid}/data-lineage-summary`).

**Lineage reachability** (fanin / fanout):
Which **Entities** one **Data source**'s content reached from a selected entity
(`fanout`, downstream / descendants) or came from (`fanin`, upstream / ancestors),
within one **Trace** — served by
`GET /api/traces/{tid}/entities/{eid}/data-lineage-graph` with a **required**
`direction` of `fanin` or `fanout` **and a required `source`** (a **Data source**
natural key, as listed by the summary read) — ADR-0028 D14; edge rule in D15.
Arriving at `A` at sequence position `s`, a hop `A → B` is followed iff the trace has
an **Interaction leg** whose *per-leg* direction runs `A → B`, that leg has a derived
**Lineage metadata** row, the traced `source` is a **member of that row's
`data_sources`**, and the leg's `seq` is strictly later than `s` (`fanout`) or earlier
(`fanin`). The trace supplies the candidate edges, the metadata supplies whether *this
source's* lineage actually flowed along them, and `seq` supplies which edges are
eligible and in what order. So the walk ends where **that source's** provenance ends,
not where the call graph does. The source is held *constant* for the whole walk (it is
the thing being traced), and the membership test is a **read** of the stored set — no
matching or inference happens at read time (ADR-0028 D7). Reports the traversed legs as
well as the reached entities (the route, so the answer can be drawn), each entity's
fewest `hops` along a *seq-and-source-respecting* path, and a three-valued `state` —
`derived` / `pending` / `no-adjacent`. Multi-source fanin/fanout is **deferred** by the
spec ("Given multiple sources - semantics are not clear"), so the read takes exactly
one; an *unknown* source is a valid empty answer (`no-adjacent`), never a 404, while a
*missing* one is a 400.
_Avoid_: reading the parent **Interaction**'s `caller_entity_id → callee_entity_id`
as the hop direction. A **response** leg runs callee → caller, and an agent's data
mostly *arrives* as the responses to calls it made (ADR-0025), so the parent's fixed
direction would drop most real inbound flow. One consequence defeats intuition: a
leaf tool's `fanout` is **not** empty, because its response delivers data back to its
caller. Also avoid reading an empty `entities` list as "nothing flowed" — that is what
`state` and `pending_frontier` (entities the walk could not continue through *yet*,
because the onward leg has no derived row) exist to disambiguate, the same
three-valued discipline **Lineage coverage** applies to a trace. Note a derived leg that
simply *lacks* the traced source is deliberately **not** on `pending_frontier`: that is a
settled "no", where an undelivered leg is "ask again later", and merging the two would
send a caller back to poll forever. Also avoid assuming `fanin` is just `fanout` with the
edges reversed — the reversal alone is a no-op on a trace's (symmetric) request+response
edge set, and what actually separates upstream from downstream is `seq`. Finally avoid
reading a large `fanout` as thorough tracing: under the trivial matcher every leg inherits
every upstream source, so the *source* rule prunes little and these reads inherit matcher
quality exactly as the triple does. (The `seq` rule prunes regardless of matcher quality,
being a fact about the trace's own ordering.)

**Lineage metadata**:
The triple recorded per **Interaction leg** by **P-data-lineage**: (1)
`data_sources`, the set of **Data source**s the payload's content came from; (2)
`source_transformations`, a map **Data source** → set of **Transformation**s
(order within a set is insignificant); (3) `entities`, the **unordered set** of
**Entities** the data passed through — the spec is explicit that "this is
unordered. In case an order is needed - it will need to be derived from the trace
using an API", so ordering is a deferred trace-derived read and not something this
field supplies (ADR-0028 D10; the field was named `entity_path` until migration
`0013_lineage_entities_rename`). Keyed
`(interaction_id, leg_type)` — the **leg**, not the `payload_hash` (ADR-0028
D5): payloads are content-addressed and deduped, so identical bytes at different
positions carry completely different lineage, and a hash key would collide those
distinct facts. `payload_hash` is kept as a *secondary index* for the deferred
reverse lookup ("where did this content come from / go"). An origin's metadata
is a real *empty* triple (one source, an empty transformation set, an empty
entity set), never NULL — absence of the row is what means "not yet derived".
_Avoid_: reading order out of the persisted/served arrays. `data_sources` and
`entities` are `TEXT[]` (and JSON arrays) only because neither Postgres nor JSON
has a set type; **P-data-lineage** writes them sorted purely so a re-derivation is
byte-identical, which is serialization, not sequence.

**Lineage coverage**:
Whether a **Trace**'s derived **Data lineage** covers the whole trace, recorded
per trace in `lineage_trace_status` as `complete` or `partial` plus the
`stopped_at_seq` a partial one stopped at (ADR-0028 D6/D8). When an **Interaction
leg**'s payload is absent, lineage is derived only up to that leg in leg-`seq`
order — a positional prefix — and the trace is `partial`. The flag exists to
prevent one specific failure: a governance consumer reading a truncated prefix as
the **complete** set of **Data source**s. So it travels with the lineage
everywhere the lineage is served (the `data-lineage` API envelope, a warning at
the top of the **Flow view**). Three values, not two: *absence* of the status row
means **unknown** — the eventual-consistency window before **P-data-lineage** has
reached the trace.
_Avoid_: collapsing **unknown** into `complete` (ADR-0028 D6 "Reading the status" —
they are opposite claims, and defaulting the absent value is the live trap). Also
avoid reading `partial` as an error, or as a statement about *why* the payload is
missing: it is a correct prefix plus a warning, and distinguishing *not captured*
from *redacted* from *genuinely empty* from *in-flight* is deferred (D6), so one
flag currently covers all four. Note `partial` truncates the **lineage**, not the
leg list — every leg is still served, those from the gap on with `lineage: null`.
Finally, avoid expecting only the paths *through* the gap to be affected — the
interim rule stops the whole trace.

**Data source**:
An origin of data in **Data lineage** — recorded as an **Entity**'s **Natural
key** ("the data source is assigned the entity name", spec rule 1). An
**Entity** becomes a data source of a payload by any of three routes: structurally
(it produced the payload with nothing inbound to it — a **Trace** root such as a
user's prompt, ADR-0028 D3(1)); semantically (the matcher found no relationship
between its input and its output, so the output is new data, D3(2)); or by
**declaration** — it is a **Source entity**, so it contributes itself *alongside*
whatever it inherited (D12). The declared route is the only one that fires under the
trivial `simple_match`, since that matcher never lets D3(2) trigger.
_Avoid_: reading a data source as "the entity that stored the data" — that
reverse map (`payload → persisting entity`) is a separate, deferred output. Also
avoid treating the declared route as an alternative to inheritance: a source entity
that matched reports *both* its own contribution and the sources it inherited.

**Source entity**:
An **Entity** declared to contribute content of its own, and therefore added to a
payload's **Data source** set on top of what the payload inherited (ADR-0028 D12,
spec "Entity Taxonomy"). It enters `source_transformations` with an **empty** set —
its own contribution did not undergo the transformation the *inherited* sources did,
so stamping one on would be a false claim. The eventual source of the answer is a
declared per-entity taxonomy table; reading it is **deferred**, so today the answer
is `Entity.kind` defaults — `tool` ✓, `llm` ✗, `agent` ✗ — in the same one named
place as the **Accumulating entity** predicate
(`processors/data_lineage/memory.py`), because the same deferred table supplies
both. The taxonomy's other two columns have no consumer: `target` is unread, and
`location` (internal/external) is a placeholder with no v1 semantics.
_Avoid_: inferring source-hood from a tool's name, description or payload sizes —
considered and rejected (only one of eight tools in the live corpus even carries
`tool.description`). The accepted cost is that a pass-through delegation tool
(`kind='tool'` but carrying no new data) over-reports as a source until the declared
table lands; over-reporting an origin is the safe direction for a governance tool,
where the failure it replaces was *under*-reporting an external data ingress. Also
avoid expecting it to matter in the all-unmatched degrade branch — that branch calls
`init_lineage` at the entity and **ignores** the flag (spec Example 3).

**Transformation**:
What a **Semantic matcher** reports connects two related payloads —
`anonymization`, `summarization`, … A finite but deliberately **open**
enumeration (`matching.Transformation`, a `StrEnum` so adding a member is
additive at the persistence and API boundaries); the full list is still being
finalized with a human. "No transform performed, or none identified" is
represented as *absent* (`None` / an empty set), never as a member — so an
unknown transformation cannot masquerade as a kind of transformation.

**Semantic matcher**:
The black box **Data lineage** is built on: `match(payload_a, payload_b) →
{matched, transformation, …evidence}`, deciding whether two payloads are related
and what **Transformation** connects them. Selected by configuration
(`SEMANTIC_MATCHER`, resolved through `matching.get_matcher`); lineage calls it
and never learns which matcher ran or how it decided. The default
`simple_match` is trivial — always matched, no transformation — which makes
lineage *complete but full of maybes* (every structural edge is treated as real
flow); better matchers prune the maybes without any change to the lineage
algorithm. Matching runs at **ingest**, not at query time (ADR-0028 D7): a read
would otherwise cost a matcher call per payload pair over a trace's whole
history. Matcher versioning and backfill after a matcher change are deferred.

**Accumulating entity**:
An **Entity** that retains its prior inbound payloads within a **Trace**, making
it a partial mixing bowl for **Data lineage**: its output is derived from *all*
its priors, not just its latest input. ADR-0028 D2 assumes
transient/session memory is **always present**, so an accumulating entity's
inbound set grows past one. Since D11 collapsed the algebra to two operations that
size no longer *selects* an operation — every non-root leg runs `merge` — but it
still decides how many priors pool and therefore that op's arity, which is why
lineage needs no separate memory predicate. Working assumption today: an
`agent` accumulates; an `llm` or `tool` does not. `Entity.kind` is the only
signal available, so the predicate is driven from it but lives in exactly one
named place (`processors/data_lineage/memory.py`, beside the **Source entity**
predicate), since declared per-entity config is where it eventually belongs.
_Avoid_: reading "accumulating" off the op name — a memoryless entity's leg also
reads `merge`, over one input. The count is in the derivation's inbound set, not in
the op.
Memory granularity is **open**: the memory node is modelled `(entity_id,
memory_key)` with `memory_key = NULL` meaning unkeyed/blob (the v1 default), so
keying per session/user/thread later is a change of what the derivation computes
rather than a redesign of it — every inbound payload already pools per *node*, so
a keyed policy only has to return a distinct node. The node is a derivation-time
value and is never persisted, so this says nothing either way about schema
churn: `lineage_metadata` records the resulting sources, transformations and
entities, not the memory nodes they were pooled through.

**P-data-lineage**:
The processor that derives **Data lineage**. A Layer-2 processor, sibling of
`P-interactions` and **P-classification**; drains the `interaction_legs` stream
on its own `data_lineage` cursor (woken by the `dg_legs_inserted` NOTIFY, poll as
the backstop) and writes `lineage_metadata` plus the trace's **Lineage coverage**
into `lineage_trace_status`. Because lineage is trace-scoped while the shared
loop's grain is one leg, each arriving leg triggers re-derivation of that leg's
**whole trace** — the `graph_driver` precedent — made safe by a deterministic key
plus an upsert, so re-deriving converges rather than duplicating. Because a
re-derivation can also get *shorter* (a payload goes absent), the derived rows a
re-derivation no longer covers are **deleted** as well, so the persisted lineage
of a trace is exactly the derivation's output. `interaction_legs` carries no
`trace_id`, so the trace is reached by joining through `interactions`. Recovery is
the established one: truncate `lineage_metadata` and `lineage_trace_status`, reset
the cursor to 0, re-drain.

**Flow view**:
The UI surface that renders one **Trace**'s derived **Interaction**/**Entity**
forest — the request/response **Interaction leg**s as an execution-flow list,
each with its **Duration** and aggregated `error`, plus the per-**Interaction**
and per-**Entity** span-evidence drill-in and the Req/Resp **Payload** cells
carrying the inline **Classification** verdict and per-leg **Data lineage**, with
the trace's **Lineage coverage** warning above the tables when it is `partial`.
Backed entirely by **Interaction
retrieval** and `payloads` retrieval; it is the primary consumer that motivated
pulling those reads behind a typed interface. Trace-scoped and eventually
consistent, mirroring the derived data it displays.
_Avoid_: conflating the **Flow view** (the derived interaction forest for one
trace) with the recent-traces listing (**TraceListingEntry** rows across
traces) — different surfaces, different read paths.

**TraceListingEntry**:
One row of the recent-traces UI view — a derived display of a **Trace**,
anchored on its current **Listing root**. Is the element type of the
`GET /api/traces` collection and the body of the `GET /api/traces/{tid}`
singular: `{trace_id, listing_root, counts, in_time_window}`, where
`listing_root` is the anchor **Span**, `counts` is its **Trace counts**,
and `in_time_window` reports whether the anchor is **In-window**. The
collection and singular return the identical shape.
Eventually consistent: the listing root, and therefore the row's display
fields, may change as late spans arrive or **Finalization** advances a
span's `seq`. Identity is `trace_id`; everything else is derived. The UI
dedupes by `trace_id` across paginated responses to collapse anchor flips
into a single row, keeping the highest-`seq` anchor.
_Avoid_: describing this as a `Span` with a sidecar `counts` map — the
row is trace-shaped (identity `trace_id`), with the anchor span nested,
not a bare listing-root span.

**Trace counts**:
The per-**Trace** tally carried on a **TraceListingEntry**: `total` (all
spans in the trace), `in_window` (spans whose `started_at` is **In-window**),
and `error_count`. Computed at query time alongside the **Listing root**;
present on both the `GET /api/traces` collection rows and the
`GET /api/traces/{tid}` singular. Backed by the `TraceCounts` retrieval type.

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
- An **Interaction leg** has at most one **Lineage metadata** row, keyed
  `(interaction_id, leg_type)`. Its `data_sources` and `entities` name
  **Entities** by **Natural key** — both are *sets*, neither carries an order; its
  `source_transformations` maps each **Data source** to a set of
  **Transformation**s. A leg with no payload gets no row.
- A payload's **Data lineage** is derived from the payloads inbound to the
  producing **Entity** — requests inbound to the callee, responses inbound to the
  caller, from **Interaction legs** of lower `seq` in the same **Trace**. How many
  of those an entity retains is decided by whether it is an **Accumulating
  entity**; whether that set is *empty* is what selects `init` / `merge`. A
  **Source entity** adds itself to the result's `data_sources` on top of what it
  inherited.
- The **Retrieval API** is the only sanctioned read path over **Spans**; the UI
  backend composes its REST endpoints from it. The REST layer is
  resource-oriented and namespaced: JSON resources under `/api/`
  (`/api/traces`, `/api/traces/{tid}`, `/api/traces/{tid}/spans[/{sid}[/children]]`,
  the interaction/entity sub-resources, `/api/payloads/{hash}`); HTML pages
  and JS assets under `/ui/`. The single `GET /spans` pass-through was retired
  in favour of these — the library `get_spans` (and its `root_only` /
  `parent_id` parameters) is unchanged; only the HTTP surface was reshaped.

## Example dialogue

> **Dev:** "If a trace's `parent_id IS NULL` span hasn't arrived yet, does
> `GET /api/traces` skip the trace?"
> **Designer:** "No. The **Listing root fallback** picks the earliest **Orphan
> span** as the **Listing root** so the trace still shows up as a
> **TraceListingEntry**. The UI flags it by checking whether the row's
> `listing_root.parent_id` is null (**Real root**) or not (orphan acting as
> listing root)."

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
- **"Span"** in classification — the NER model detects `(start, end)` regions
  that could naturally be called "spans," but **Span** is the OTEL row. Resolved:
  a classification detection is a **Finding**, never a span.
- **"Entity"** in classification — the NER taxonomy calls its tags "entity
  types" (`PN`, `SSN`), but **Entity** is the interaction participant. Resolved:
  a **Finding** has a *detected type* (an NER tag); it is not an **Entity**.
- **"Lineage"** meant two unrelated things. `P-interactions` and ADR-0007/0016
  use it for a **Span**'s ancestors ∪ subtree under a `seq` horizon — a
  graph-structure region bounding what one span's re-derivation may rewrite.
  ADR-0028 uses it for content provenance. Resolved: **Span lineage** vs **Data
  lineage** — distinct terms, never the bare word. They share no code, no table,
  and no key; a grep for "lineage" hits both.
- **"Source"** — a **Data source** is where a payload's content *originated*
  (an **Entity** natural key in **Lineage metadata**). Unrelated to a `spans`
  row's `service_name` or to `Entity.kind = service`. Also distinct from the
  deferred reverse map (`payload → persisting entity`), which is about where
  content was *stored*, not where it came from.
