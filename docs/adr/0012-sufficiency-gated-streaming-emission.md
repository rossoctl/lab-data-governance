# Sufficiency-gated streaming emission: emit-once-when-decidable, no flush, no retraction

`P-interactions` derives **Entities** and **Interactions** from **Spans**
streamed one at a time by ascending `seq`. ADR-0011 proposed deriving
them *eagerly* — emit on the first anchor span, then *retract* and
*reconcile* once cross-anchor evidence arrives (emit-then-correct). This
ADR supersedes that emission discipline: an entity or interaction is
materialised **only at the arrival of the span that makes the
already-arrived span set sufficient to decide it finally** — emit-once,
when-decidable, with no eager-then-retract and no end-of-trace flush.

## The rule

On each span `S` arriving (the processor may read only spans with
`seq <= S.seq` — the past and present, never the future):

1. **Entity attempt.** `S`, together with already-arrived ancestors and
   descendants, may complete a *sufficient set* to decide an entity's
   identity **finally** (no future span can change it). If so, upsert
   the entity now; otherwise emit nothing.
2. **Interaction attempt.** `S` may complete a sufficient set for a
   directed edge between two entities — either because `S` is itself the
   completing span, or because `S` is the missing endpoint of an
   *earlier-arrived* span (look-back). If so, materialise the
   **Interaction** now.
3. **Attachment.** `S` attaches itself to its innermost current owning
   **Interaction** (the nearest arrived interaction whose anchor encloses
   `S`). On **every** span arrival the processor (a) retries every
   not-yet-emitted endpoint against the full arrived set, and (b) re-derives
   `interaction_spans` ownership and `parent_interaction_id` for the arrived
   set. Both are pure functions of (arrived anchors, ancestry), so
   recomputing them per arrival makes the final graph order-independent.

Nothing is retained between arrivals beyond the durable tables
(`entities`, `interactions`, `entity_spans`, `interaction_spans`,
`interaction_payloads`) and the `spans` table itself. There is no parking
buffer, no pending list, and no `result()` / trace-complete step.

### Implementation notes (refinements found during the prototype build)

The prototype (since removed; its classification lives on verbatim in
`processors/interactions/procedure.py`) validated this model against the demo
trace with the `--scramble` (reversed-arrival) acceptance gate producing
byte-identical output. Three refinements to the bullet above
proved load-bearing and are reflected in the code:

- **Per-arrival retry + repair, not per-emit subtree re-derive.** The
  original "re-derive the *emitting* interaction's subtree" is insufficient:
  the root `POST /` SERVER finalizes near max-seq, so under `--scramble` it
  arrives *first*, long before the spans in its territory — its one-shot
  subtree re-derive covers nothing. The working model retries all pending
  endpoints and repairs the whole arrived tree on *every* arrival
  (`_retry_pending_endpoints` + `_repair_after_arrival`). An endpoint's
  identity can be completed by any later span (most importantly its service's
  OI AGENT span, also near max-seq), so retrying per-arrival — rather than
  special-casing each completing-span kind — is what converges.
- **external-http is anchored on the CLIENT span, not the OI TOOL span.**
  The OI TOOL span is already the agent→tool edge's primary anchor, and
  emit-once keys on the primary; anchoring external-http there too would
  collapse the two edges. The CLIENT POST is external-http's unique anchor.
  A `/mcp`-path egress is MCP transport (absorbed); only a non-`/mcp` egress
  to an unowned host (e.g. `psp-mock:9091/charge`) is external-http.
- **Deployed-vs-in-process tool classification is gated on a transport
  signal, not committed eagerly.** A deployed-MCP tool's OI TOOL span reaches
  a `POST /mcp` SERVER transitively (⟹ deployed); a delegate FunctionTool
  reaches an A2A sub-agent SERVER on a different service (⟹ in-process);
  while neither has arrived the emit *defers* (the per-arrival retry picks it
  up later). Both decisive answers are positive SERVER arrivals, so the
  classification converges regardless of order — eager commitment on the bare
  OI TOOL span otherwise froze a premature (and duplicated) in-process entity
  under `--scramble`. The ADR-0011 `mcp_tools`-advert classification path was
  dropped (it was the order-sensitive source).

### Sufficiency by anchor rule

Every rule emits on a **positive** completing span; none waits on a
*negative* ("X never arrives") conclusion, because a negative has no
triggering event and would force a trace-end flush:

- **`agent → llm` (openinference-llm).** Callee `llm:<host>/<model>` is
  fully determined by the OI LLM span's own attributes (model +
  `base_url` host, post-`8d6c876`). Sufficient set =
  `{OI LLM span} ∪ {enclosing caller-bearing ancestor}`; emit on whichever
  arrives second.
- **`agent → tool` / `agent → llm` callers, cross-service callees.**
  Sufficient set = `{parent endpoint span, child endpoint span}`; emit on
  the second to arrive (look-back when the child arrived first).
- **Service-side identity.** Resolved only on **positive** evidence: an
  OI `AGENT`/`LLM`/`CHAIN`/`TOOL` span ⟹ `agent:(project,canonical)`; a
  `/mcp` `SERVER` span ⟹ `tool:(project,canonical)`. A bare `SERVER`
  span (e.g. `POST /` with no `openinference.span.kind`) is **not
  sufficient** to mint any entity and anchors nothing until such a span
  arrives.
- **`client/user → agent` (orphan-server).** Caller (`user:`/`client:`)
  is determined by the root `SERVER` span's own attributes; callee
  `agent:` completes on the service's OI `AGENT` span. Emit on the AGENT
  span (look-back to the root). A root whose service never emits an AGENT
  span anchors nothing.
- **`tool → service:<host>` (external-http, e.g. `charge_card → psp-mock`).**
  Re-anchored: the edge is **not** anchored on the CLIENT POST. Its
  sufficient set = `{owning OI TOOL span} ∪ {descendant CLIENT POST whose
  host is owned by no other entity}`. Because a leaf tool-call HTTP egress
  can have no future `SERVER` child, sufficiency is genuinely final at the
  OI TOOL span's arrival — no eager emit, nothing to retract.

## Why

- **The negative conclusion is the whole problem.** "This host is
  external because no instrumented `SERVER` child will ever appear" and
  "this service is plain HTTP because it emits no framework span" are both
  conclusions with *no triggering span*. ADR-0011 reached them by emitting
  eagerly and retracting on contrary evidence; the only alternative that
  avoids retraction *and* avoids a flush is to **re-anchor each rule on a
  positive completing span**, so sufficiency is always reached by an
  arrival, never by trace-end.
- **No retraction means no reconciliation layer.** Emit-once-when-decidable
  never emits a wrong row, so there is nothing to destructively retract,
  no provisional entities to GC, and no tree-repair-on-retract. The
  ADR-0011 reconciliation machinery (pairing search, retarget, orphan GC,
  tombstones) is unnecessary under this discipline.
- **No flush means truly streamable.** Order-independence is the
  acceptance gate (`--scramble` reverses arrival order and must produce
  the same graph). Because look-back resolves late parents on the
  *parent's* arrival and every rule emits on a positive span, the model
  needs no end-of-trace pass — satisfying "nothing runs when the trace is
  complete" and "nothing stays in memory between arrivals" (the durable
  tables and the `spans` rows are the only state).
- **Attachment is re-derived, not stolen.** On each arrival the affected
  subtree's `interaction_spans` are recomputed against the current
  interaction set (innermost owner wins), rather than incrementally
  reassigned. This makes final ownership a pure function of
  `(anchors, span ancestry)` and therefore arrival-order-independent,
  preserving ADR-0008's innermost-territory rule and ADR-0011's
  `UNIQUE (trace_id, span_id)` invariant.

## Considered alternatives

- **Eager-emit + destructive retract (ADR-0011).** Emit on the first
  anchor, correct later. Rejected as the emission discipline: it
  reintroduces the retraction/reconciliation layer this ADR removes, and
  the gate trace never requires it — every edge is decidable on a positive
  completing span.
- **Trace-end flush (`result()`).** The prototype's prior shape: defer the
  external-http / orphan-server / tree-recompute decisions to a pass after
  the last span. Rejected: violates "nothing runs when the trace is
  complete" and re-introduces the wall-clock "trace is quiet" detector
  ADR-0007 already rejected.
- **Idempotent full re-derive per arrival.** Re-derive the entire arrived
  trace on every span. Rejected as the default for being O(n²)/trace,
  though the bounded-subtree re-derive on interaction emit is a scoped form
  of the same idea.
- **Eager-classify with an up-only kind-precedence ladder
  (`service < agent < tool`).** Mint `service:<host>` on a bare SERVER
  span, refine upward when a framework span arrives. Rejected: reintroduces
  entity retarget + orphan-retract for a case the gate trace does not
  exercise (no plain-HTTP inbound service exists in it; see
  `demo/entities.md`).

## Consequences

- **The bare-SERVER → `service:<host>` rung is dropped.** A pure-HTTP
  inbound service is not classifiable under this model without a flush. The
  gate trace contains none (all `service:` entities are CLIENT-side
  egress). A real fixture with a pure-HTTP inbound service is a new
  requirement to be handled when one exists.
- **ADR-0011 is partially superseded.** Its reconciliation/retraction
  emission model is replaced by sufficiency-gated emission. ADR-0011's
  schema scaffolding (`retracted_at`, `original_seq`,
  `UNIQUE (trace_id, span_id)`) survives; the `retracted_at` path is simply
  never exercised.
- **`llm:(unknown)/<model>` is eliminated on the live trace.** The host is
  present on every OI LLM span (`llm.invocation_parameters.base_url`), and the
  model is resolved either from the standard `llm.model_name` /
  `gen_ai.request.model` attributes or — for the GoogleADK runtime
  (booking-agent), which emits neither — from `output.value.model_version` in
  the response body (`caller_inference._llm_model` falls back to it). Without
  that fallback the four GoogleADK LLM spans minted a stray
  `llm:(unknown)/<model>` entity duplicating the real
  `llm:<host>/claude-haiku-4-5-20251001`. The unresolved placeholder is
  therefore never minted; if a future trace has a genuinely uninstrumented
  egress it would still surface as a *final* unresolved LLM, not a provisional
  one awaiting reconciliation.
- **Two emission models now coexist in the docs.** Production direction
  follows this ADR; ADR-0011's reconciliation terms in CONTEXT.md
  (**Interaction reconciliation**, **Destructive retract**, **Provisional
  entity**) are legacy under it. See the **Sufficiency-gated emission**
  term.
