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
mutations; `seq` advances per mutation; `original_seq` preserves the seq
at creation, mirroring ADR-0004's `arrival_seq` one layer up. Stream
consumers cursor on `seq`; `original_seq` lets them distinguish
first-emission rows from mutations. There is no "complete" flag —
consumers see the current state at query time. Anchor rules set
identity (`caller_entity_id`, `callee_entity_id`) on creation only;
re-fires of the same anchor rule on **Finalization** do not re-assert
identity. Identity is mutated only by a more-informed anchor (e.g.
late-arriving CLIENT parent retargeting the caller, ADR-0007) or by
**Interaction reconciliation** (cross-anchor evidence retargeting the
callee, ADR-0011). The general invariant: more-informed values are
never replaced with less-informed ones.

**Interaction leg** (`direction`) _(deferred — not implemented in v2; the
shipped `interactions` schema is single-row, see ADR-0013)_:
The intended-future model in which a single directed call (**Interaction**)
is recorded as **two rows**, not
one: a `request` leg and a `response` leg, sharing one logical `id` and
keyed `(id, direction)`. Both legs carry the *same* orientation —
`caller_entity_id` and `callee_entity_id` are identical on both, since a
response is the return value of the caller→callee call, not a new
callee→caller call. The legs differ in: payload (`request` carries the
request payload hash, `response` the response payload hash), timing
(`request` knowable at call start, `response` only on **Finalization** /
stream completion), error/status, and lifecycle — each leg has its own
`seq` / `original_seq` / `retracted_at` and finalizes independently. The
two legs **cross-reference each other** by their shared `id`. Identity-
defining fields (caller, callee, anchor, `parent_interaction_id`) live at
the `id` level and are shared; both legs of an interaction always agree on
them. The **Interaction tree** (`parent_interaction_id`) and the unique
span-ownership invariant (ADR-0011) both key on `id`, not on the leg — so
splitting into legs leaves ADR-0008 and ADR-0011's `(trace_id, span_id)`
uniqueness untouched. Drivers: request/response are temporally asymmetric,
have independent lifecycles (streaming responses), draw as two arrows on
an execution-flow diagram, and carry independent governance policy
(classification, retention, redaction) per leg.
_Avoid_: treating `response` as a callee→caller edge — the edge
orientation is identical on both legs; only `direction` distinguishes them.

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
detection across the finding set).
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
detect sensitive spans, then maps those to a sensitivity verdict (see
`classification/`). Consumes `interaction_payloads`; semantically aware where
the receiver is not. Runs the NER model **in-process** in the drain loop, and
ships as its own container image (`data-governance/classification`) — separate
from the shared receiver/UI/interactions image because its torch + ~500 MB
model-weight dependency closure diverges heavily (the weights are baked into
the image; image tag ↔ `model_version`). See ADR-0022 (own image) and ADR-0023
(in-process model, baked-in weights + config).

**Classifiable text**:
The single natural-language string **P-classification** feeds to the NER
model for one **Payload** — the payload's human-meaningful prose projected
out of its JSONB `content` by the **Text projection rule**. Detected entity
spans are char offsets into this string, not into the stored JSONB.

**Text projection rule**:
The processor-side rule by which **P-classification** projects a **Payload**'s
JSONB `content` into its **Classifiable text**, one branch per **Content
kind** (e.g. concatenate message bodies for `llm_chat_prompt`, take the result
string for `tool_call_result`). Like the **Payload extraction rule** it
mirrors, this is a closed set enforced in code that churns as content kinds
mature. `unknown` (and any kind without a branch) falls back to serializing
the whole `content` JSONB to a canonical string — best-effort classification
that also marks the projection-coverage gap.

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
