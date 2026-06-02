# LLM and HTTP transport are one interaction; LLM identity may resolve via streaming reconciliation

When an OpenInference-instrumented agent calls an LLM over HTTP, two
anchor rules fire on overlapping spans:

- The **openinference-llm** rule fires on the agent-side LLM span
  (e.g. `LiteLLM.acompletion`), producing an `agent → llm` interaction.
  Its payload is the structured `llm.input_messages` /
  `llm.output_messages` on that span.
- The **external-http** rule (one of the cross-service / external
  rules per ADR-0009) fires on a CLIENT POST descendant of the LLM
  span — the actual HTTP request — producing an `agent → service`
  interaction whose callee is `service:<host>`. Its payload is the
  HTTP body.

In the test trace nine OI LLM spans appear: five with a descendant
CLIENT POST, three with a sibling CLIENT POST on the same service,
and one with no paired CLIENT POST (uninstrumented egress). The eight
that pair describe the **same logical call** — the agent making one
LLM request — at two levels of abstraction; the ninth's OI LLM
interaction stays attached to a placeholder LLM entity, surfacing the
uninstrumented egress as a signal to consumers.

This ADR defines the model for collapsing them. The model has two
parts:

1. **Streaming emission with deferred LLM-host resolution.** The OI
   LLM rule fires immediately on every OI LLM span, even when the
   LLM's host is not yet known, by emitting against a placeholder
   entity `llm:(unknown)/<model>`. ADR-0011 supersedes ADR-0007's
   "left orphaned" stance for provisional entities of all kinds.
2. **Interaction reconciliation** — a new layer above anchor rules
   that collapses redundant interactions and refines provisional
   entities once cross-anchor evidence (the paired CLIENT POST)
   becomes visible.

This is the same shape ADR-0010 used for in-process / deployed-tool
collisions, but ADR-0010 keeps both interactions and lets the
**Interaction tree** distinguish them. ADR-0011 *retracts* one of
them — the LLM and the HTTP transport are the same logical call,
not two levels of abstraction in the agent's call stack.

## The rule

### 1. Eager emission of OI LLM interactions

Every `openinference-llm` anchor fires immediately on the OI LLM span,
regardless of whether a CLIENT POST descendant has arrived yet. The
callee entity is:

- `llm:<host>/<model>` if the OI LLM span itself carries the host
  (rare in practice — the host is on the CLIENT POST, not the LLM
  span);
- `llm:(unknown)/<model>` otherwise.

The unresolved entity is **not scoped** by service or by span. One
`llm:(unknown)/<model>` entity per model, **cross-trace stable**.
Multiple unresolved interactions across multiple traces all attach
to the same entity until each individually retargets.

### 2. Interaction reconciliation: LLM/HTTP-transport pairing

After every span arrival, the reconciler attempts to pair each
unresolved OI LLM interaction with its HTTP-transport CLIENT POST.
The pairing search is **structural-first**, scoped within the trace:

- **CLIENT-side trigger** (CLIENT POST arrives): walk the span's
  ancestors. If exactly one ancestor span anchors an unresolved OI
  LLM interaction, pair them.
- **LLM-side trigger** (OI LLM arrives): scan the trace's
  already-arrived spans for descendant CLIENT POSTs. If exactly one
  descendant CLIENT POST anchors an `external-http` interaction,
  pair them.
- **Sibling-case fallback**: if no descendant pairing matches, look
  for a CLIENT POST sibling on the same canonical-service that is
  uniquely contained in the OI LLM's time-window. On multi-match
  (parallel LLM calls), fail closed — both stay unresolved.

When pairing succeeds:

1. **Retarget the OI LLM's callee.** Detach the interaction from
   `llm:(unknown)/<model>`, attach to a freshly-created or
   already-existing `llm:<host>/<model>`, where `<host>` comes from
   the CLIENT POST's `server.address` (preferred) or `peer.service`.
2. **Destructively retract the `external-http` interaction.** The
   OI LLM survives (the structured payload is the canonical
   representation; the raw HTTP body is redundant).
3. **Transfer the CLIENT POST span ownership.** The CLIENT POST,
   previously attached to the now-retracted `external-http`
   interaction as its anchor, is re-attached to the surviving OI
   LLM interaction as `connector` (or `info` if it carries
   payload/error per `_has_payload_or_error`).
4. **Repair the interaction tree.** If the retracted
   `external-http` had children in the **Interaction tree** (per
   ADR-0008's tree walk), those children's `parent_interaction_id`
   is recomputed by the same walk, skipping retracted anchors. A
   child may become a new top-level interaction if no surviving
   ancestor remains.
5. **GC orphaned entities.** Two entities may now be orphaned:
   the `service:<host>` previously referenced by the retracted
   `external-http` interaction, and `llm:(unknown)/<model>` if
   this was its last attached interaction. The `llm:(unknown)`
   entity is shared cross-trace, so the GC check is across all
   traces; the `service:<host>` check is over all interactions
   referencing it (also cross-trace). Each entity, if orphaned,
   is destructively retracted by the universal-destructive-retract
   rule.

### 3. Schema invariants

ADR-0011 introduces three schema additions:

- **`UNIQUE (trace_id, span_id)` on `interaction_spans`.** Each
  span in a trace belongs to exactly one interaction. This
  materialises ADR-0008's innermost-territory rule as a hard
  constraint; ownership conflicts surface as commit-time errors
  rather than silent `min(started_at)` distortions. Reconciliation
  rules that retract an interaction must transfer or clear its
  `interaction_spans` rows in the same transaction.
- **`retracted_at TIMESTAMP NULL` on `interactions` and `entities`.**
  The wire format for **destructive retract**. `seq` advances on
  the retraction event. Default reads filter `retracted_at IS NULL`;
  stream consumers cursoring on `seq` see the retraction as a
  `seq`-ordered mutation by reading the column directly.
- **`original_seq` on `interactions` and `entities`.** Preserved
  on creation, never updated. Mirrors ADR-0004's `arrival_seq` one
  layer up. Lets stream consumers distinguish first-emission rows
  from mutations without per-row state.

### 4. Identity invariant for re-fires

Anchor rules set identity (`caller_entity_id`, `callee_entity_id`)
on creation only. Re-fires of the same anchor rule on **Finalization**
do not re-assert identity. Identity is mutated only by:

- a more-informed anchor (e.g. a late-arriving CLIENT parent
  retargeting the caller, ADR-0007), or
- **Interaction reconciliation** (cross-anchor evidence retargeting
  the callee, this ADR).

The general invariant: more-informed values are never replaced with
less-informed ones. No `identity_locked` flag is needed; the rule is
that anchor rules only ever *initialise* identity.

### 5. Pre-emission ownership check

Before emitting any interaction, anchor rules check
`interaction_spans` for the proposed primary anchor's
`(trace_id, span_id)`. If a non-retracted interaction already owns
it, skip emission. This prevents a re-fire of the `external-http`
rule on a CLIENT POST that has already been transferred onto a
surviving OI LLM interaction by reconciliation.

### 6. Durability

Reconciliation queries the durable `interactions` and `spans`
tables (scoped to the current trace) for pairing candidates, not
in-memory processor state. This makes reconciliation correct across
cursor replay and processor restart: a span replayed after restart
finds its pair via DB lookup, just as a freshly-arrived span does.

## Why

- **Pairing is structural, not temporal.** Walking the span tree
  matches the actual relation between an OI LLM and its HTTP
  transport. Time-window heuristics are a proxy that gets wrong
  in the parallel-call case; structural lookup gets the descendant
  case right unambiguously and forces a fail-closed answer on the
  ambiguous sibling case.
- **Eager emission preserves the ADR-0007 streaming model.**
  Gating the OI LLM interaction's visibility on its CLIENT POST's
  arrival re-introduces the wall-clock-shaped delay anti-pattern
  that ADR-0007 explicitly rejected for the `complete` flag.
  Long-running LLM calls would be invisible until the HTTP request
  finishes; payload data on the OI LLM span would be unreachable.
- **`(unknown)/<model>` carries signal.** A persistent
  `llm:(unknown)/<model>` entity (one whose interactions never
  resolved a host) flags **uninstrumented egress** to consumers.
  The UI can surface it as an instrumentation suggestion: the
  agent is talking to an LLM but its HTTP layer isn't traced. This
  is information, not noise — discarding the unresolved entity
  hides a real coverage gap.
- **Per-interaction retarget is more general than per-entity
  scoping.** Multiple unresolved interactions may share one
  `llm:(unknown)/<model>` entity and resolve to *different* hosts
  (a multi-gateway deployment). Retargeting per-interaction
  handles this without scoping the unresolved entity by service or
  span. The unresolved entity is GC'd only when its last attached
  interaction retargets away.
- **Anchor rules stay independent (ADR-0009).** Reconciliation
  operates *after* anchor rules, on emitted interactions. The
  anchor rules do not check whether their would-be callee will be
  resolved later; they fire on the span in front of them. ADR-0009
  is preserved.
- **The schema invariant catches a class of attachment bugs.**
  Without `UNIQUE (trace_id, span_id)`, the caller-side connector
  walk can attach an agent-loop wrapper span to a cross-service
  interaction *and* to the LLM/tool interactions that share the
  wrapper as ancestor. The cross-service interaction's
  `started_at = min(attached_span.started_at)` is then pulled back
  to the wrapper's start, mis-ordering the interaction in the UI.
  The constraint surfaces this as a commit-time error rather than
  silent display-order corruption.
- **Tombstone wire format preserves the cursor model.** Hard
  delete forces consumers to diff snapshots to detect retraction,
  breaking ADR-0007's `seq`-cursor pattern. A separate retraction
  event log is the same effect with extra plumbing. A `retracted_at`
  column on the row itself is the simplest shape that works
  uniformly for cursor consumers (read the column) and default-view
  consumers (filter it out).

## Considered alternatives

- **Suppress the `external-http` interaction at fire time when its
  CLIENT POST sits under an OI LLM ancestor.** Rejected: this is the
  same shape of error as ADR-0010's round-1 dedup. The anchor rule
  would have to know about an OI LLM ancestor, re-coupling
  per-span anchor decisions to cross-anchor state. Violates
  ADR-0009.
- **Defer the `external-http` rule for one tick when the CLIENT
  POST has an OI LLM ancestor** (the round-1/round-2 R2b option).
  Rejected on stronger grounds than originally captured: gating
  emission behind a SERVER close re-introduces the wall-clock
  anti-pattern from ADR-0007. Long-running LLM calls would be
  invisible; payload on the OI LLM span unreachable until the HTTP
  layer finishes.
- **Per-service `(unknown)` scoping** —
  `llm:(unknown)@<service>/<model>`. Rejected: over-engineering.
  Per-interaction retarget already handles multi-gateway
  deployments without scoping. Service-scoping would also break
  cross-trace stability of the unresolved entity (the same
  uninstrumented LLM access from different services would
  produce distinct entities).
- **Per-span `(unknown)` scoping** —
  `llm:(unknown)/<model>/<span_id>`. Rejected: breaks cross-trace
  stability entirely; produces an over-counted "distinct LLMs"
  signal that misrepresents the entity graph.
- **Append-only retraction with separate retraction event log.**
  Conceptually equivalent to the tombstone column for stream
  consumers but with two tables and two cursors. Rejected as
  unnecessary complexity for the same observable behaviour.
- **Hard delete on retract.** Rejected: breaks the cursor-based
  consumer model. Default-view consumers want the row gone (they
  get that with the tombstone via filter); cursor consumers want
  the retraction visible as a `seq`-ordered event (they get that
  by reading the column).
- **`identity_locked` flag (per-row or per-field) on
  `interactions`.** Rejected: overengineering. The general
  invariant "anchor rules initialise identity, only more-informed
  sources mutate it" is enough. Re-fires of the same anchor rule
  on Finalization compute the same value they computed first time
  (or a less-informed value), and the invariant skips them.

## Consequences

- **Inter-arrival visibility window for the duplicate
  `external-http` interaction.** Within one transaction the
  duplicate exists between emit and reconciliation-retract; across
  transactions, after the OI LLM and CLIENT POST have both arrived
  and reconciled, no duplicate persists. Consistent with ADR-0007
  eventual consistency; bounded by the transaction boundary.
- **Persistent unresolved entities flag uninstrumented egress.**
  An `llm:(unknown)/<model>` whose interactions never paired is a
  real signal for the consumer, not garbage. The UI may surface
  these explicitly.
- **Parallel-LLM-call sibling pairing fails closed.** Two
  overlapping OI LLM spans on the same canonical-service with
  matching CLIENT POSTs leave both unresolved (sibling-case
  multi-match). This is the conservative answer; the alternative
  (greedy heuristic pairing) is silently wrong.
- **Entity IDs may 404 between consumer reads** (universal
  destructive retract supersedes ADR-0007's no-GC stance for
  provisional entities). Default-view consumers see this as the row
  vanishing; cursor consumers see the retraction event. No live
  consumer today depends on entity ID stability across mutation
  boundaries; this ADR makes the policy explicit.
- **New schema columns.** `interactions.retracted_at`,
  `interactions.original_seq`, `entities.retracted_at`,
  `entities.original_seq`, plus the `UNIQUE (trace_id, span_id)`
  constraint on `interaction_spans`. Migration ordering: add
  columns first, backfill `original_seq` from current `seq`,
  enforce uniqueness last (after deduplicating any existing
  double-attachments).
- **Reconciliation must be DB-backed.** The prototype's
  in-memory state cache is acceptable for single-trace
  experimentation but does not survive cursor replay or processor
  restart. Production reconciliation queries
  `interactions`/`spans` per pairing attempt.
- **Tree-repair on retract is non-trivial.** Children of a
  retracted interaction need `parent_interaction_id` recomputed
  via the ADR-0008 walk. A child may become a new top-level
  interaction. The walk is bounded by trace size and runs in the
  same transaction as the retract.

## Explicitly does NOT do

- **Cross-trace reconciliation.** Pairing is scoped to a single
  trace. An OI LLM in trace A and a CLIENT POST in trace B do not
  pair, even if they correspond to the same logical wall-clock
  call.
- **Merge events on the wire.** Consumers do not see "interaction
  X was merged into interaction Y." They see X retracted (`X.retracted_at`
  set, `X.seq` bumped) and Y mutated (`Y.callee_entity_id` retargeted,
  `Y.seq` bumped). Reconstructing the merge from these two events
  is the consumer's job if they care.
- **Wall-clock deadlines for unresolved LLMs.** A persistent
  `llm:(unknown)/<model>` is not garbage-collected by a sweep
  job. It persists until its last attached interaction retargets
  away. (If that never happens, it persists forever — the signal
  it carries is itself the desired outcome.)
- **Cross-anchor entity merging.** `llm:(unknown)/<model>` and
  `service:<host>` are not equated, nor is the resolved
  `llm:<host>/<model>` merged with the `external-http`-side
  `service:<host>`. The OI LLM interaction retargets onto a
  freshly-named `llm:<host>/<model>` entity; the `external-http`
  interaction is retracted independently; the two `<host>`-bearing
  entities (`llm:<host>/<model>` and `service:<host>`) coexist as
  distinct rows. Future ADRs may introduce a tool-style merge
  (compare ADR-0010), but this ADR does not.
