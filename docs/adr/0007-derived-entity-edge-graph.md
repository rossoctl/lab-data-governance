# Derived entity/edge graph, materialized by a second processor

The project's mission is data lineage and classification — "how and what
information moves through agents, tools, and systems" (PROJECT.md
Introduction). v1 stores only raw **Spans**; the parent/child structure is a
per-trace tree derived at query time, never named or classified (ADR-0001).
Delivering lineage needs a layer above spans: **entities** (the things
information moves between) and **edges** (the boundaries it crosses), which can
then be marked, propagated, and overlaid.

We chose to introduce a derived **entity/edge graph** — two new tables,
`entities` and `edges` — **materialized by a second Layer-2 processor** (the
**Graph-builder**) that reads `spans` and writes the graph. This is the
read+write processor that PROJECT.md §6 and ADR-0005 already anticipated, not a
new architectural exception: the receiver stays trivial, and all derivation
lives in the new processor. The marks layer that decorates edges
(`edge_annotations`, classification/policy/taint) is a separate concern pinned
by ADR-0008; this ADR fixes only the graph it sits on.

## The rule

- **Entity grain = `(service_name, semantic_kind, sub_kind)`.** One `entities`
  row per distinct tuple. `semantic_kind` is derived by a fixed ladder
  (`openinference.span.kind` → any `llm.*` key present → any `gen_ai.*` key →
  OTLP span kind → `UNKNOWN`). `sub_kind` is the refinement discriminator —
  the model name for `LLM`, the tool name for `TOOL`, else `NULL` — so distinct
  models and distinct tools do not collapse into one node. The `entity_id` is a
  deterministic opaque key over the tuple, so re-derivation is idempotent.

- **Edge = one directed row per cross-entity parent→child boundary**
  (caller→callee). An edge exists when a span's entity differs from its
  parent's entity; same-entity (internal) parent/child pairs produce no edge. A
  **Real root** (`parent_id IS NULL`) produces no edge. The edge is keyed by
  the **child** span `(trace_id, span_id)` — the same key as `spans` — so each
  child has at most one edge.

- **Orphan policy: `from_entity` is nullable.** A child whose `parent_id` is
  set but whose parent **Span** is absent at derivation time (dropped by the
  §3.1 **Blocklist**, lost, or not yet ingested) yields an edge with
  `from_entity = NULL` and `edge_kind = UNKNOWN_<toKind>`. The boundary is
  preserved, not dropped — the same choice ADR-0001 makes for orphan **Listing
  roots**. This is **eventually consistent**: when the parent later arrives, the
  next builder pass flips `from_entity` NULL→real. An aggregated view renders
  NULL `from_entity` as a single `(external / uncaptured)` source node.

- **Join back, don't copy (ADR-0006).** `edges` carries no denormalized
  `started_at`/`ended_at` and no payload. Timing and content are read by
  joining back to `spans` on `(trace_id, span_id)`. The payload that crossed an
  edge lives on the **child** span (for an OpenInference LLM boundary the child
  carries *both* `llm.input_messages` and `llm.output_messages`), so deferring
  response-direction concerns costs nothing in reachability.

- **Idempotent re-derivation.** Both tables upsert on their deterministic keys
  (`entities` on `entity_id`, `edges` on `(trace_id, span_id)`). `edges` carries
  a stable `edge_seq` allocated from a sequence **at INSERT and preserved on
  update** (mirroring `spans.arrival_seq`), so the read path can keyset on the
  same axis it sorts on and re-running the builder is a no-op.

## Why

- **Reconciliation with ADR-0001 (materialize vs derive-at-query-time).**
  ADR-0001 declined to materialize even the `traces` summary, computing listing
  roots at query time and reserving a materialized view as a *strictly additive*
  remediation "if profiling shows [it] dominates." By this design an edge is 1:1
  with its child span, so `edges` is essentially a materialized self-join of
  `spans` with an entity-diff predicate — the kind of thing ADR-0001 would
  default to deriving. We still **store** it, for reasons ADR-0001's own logic
  admits: (1) the `semantic_kind`/`sub_kind` classification is non-trivial CASE
  logic over `attributes`, and re-evaluating it inside every view query and on
  both endpoints of every self-join is exactly the cost materialization buys
  out; (2) the marks layer (ADR-0008) needs stable edge rows to attach
  propagated annotations to; (3) ADR-0001's objection was to burdening the
  *trivial receiver* with fix-pass logic — here a **separate** processor does
  the work, so the receiver stays trivial. That distinction is the whole reason
  this is allowed.

- **The graph-builder is the planned second writer, not an exception.**
  PROJECT.md §6 and ADR-0005 explicitly sanction processors that read and write
  through the Layer-1 **db module**, and route bulk callers (analytics,
  backfills) to Layer 1 directly rather than through the 500-capped
  `get_spans`. The builder's full-scan backfill is exactly such a caller.

- **Eventual consistency is already pervasive, so the graph inherits it.**
  Listing roots flip as late spans arrive (ADR-0001); `seq` advances on
  **Finalization** (ADR-0004). A NULL `from_entity` that resolves on a later
  pass is the same model, not a new one.

## Considered alternatives

- **Derive the graph at query time (no tables), per the ADR-0001 precedent.**
  Rejected for v1 *as the primary path* — the classification ladder is too
  expensive to re-run on every view query and self-join endpoint, and the marks
  layer needs durable edge identity. **Retained as the de-materialization
  fallback** (see Consequences): if profiling later shows `edges` isn't worth
  storing, it can be demoted to a `VIEW` additively.

- **Service-only entity grain.** Rejected: it hides agent→LLM as a self-loop
  (an LLM span carries `openinference.span.kind=LLM` but the agent's
  `service_name`) and collapses all tools into one node and all models into one
  node — gutting the lineage view's value. `sub_kind` prevents the collapse.

- **Denormalize `started_at`/`ended_at`/payload onto `edges`.** Rejected per
  ADR-0006: `spans.ended_at` and `seq` are mutable on **Finalization**, so a
  denormalized copy goes stale. Joining back to the authoritative `spans` row is
  the standing posture.

- **Drop orphan-parent boundaries.** Rejected: it silently loses lineage signal
  (something outside the captured graph called the callee). The nullable
  `from_entity` preserves the boundary and self-heals, consistent with
  ADR-0001's orphan listing-root fallback.

## Consequences

- **A second class of writer joins `P-otel-receiver`.** The receiver and the
  `spans` schema are untouched; a new processor tier reads `spans` and writes
  the derived tables. The first **table→table (derived) flow** the system has
  had.

- **De-materialization is a reserved, additive path.** Because
  `edge_annotations` keys on the child `(trace_id, span_id)` — a stable `spans`
  PK — the marks layer does **not** depend on whether `edges` is a table or a
  view. If profiling says the table isn't worth it, `edges` can become a `VIEW`
  with no change to marks. This mirrors ADR-0001's reserved materialized-view
  remediation, run in the opposite direction.

- **Schema and downstream slices land separately.** This ADR fixes the contract
  only. The DDL ships as hand-written Alembic migration #0004 (no ORM,
  ADR-0002); the builder, retrieval methods, API, and UI follow as their own
  increments. The marks model — `derived`/`derived_from` lineage and propagation
  ordered by the edge DAG (not wall-clock) — is **deferred to ADR-0008**.

- **Finer grain stays open.** `sub_kind` handles model/tool today; keying
  further (per-deployment, per-namespace) is a later refinement, kept safe by
  every edge's `(trace_id, span_id)` provenance pointer back to `spans`.
