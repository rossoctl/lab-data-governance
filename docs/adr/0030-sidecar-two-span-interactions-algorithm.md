---
status: accepted; implements the Case-Y writer anticipated by ADR-0025
---

# The sidecar two-span derivation is the third interactions algorithm

ADR-0025 split `interactions` into an identity row plus `interaction_legs`
in anticipation of a source that "emits a request span and a response span as
two distinct `(trace_id, span_id)` rows arriving at different times, sharing
an exchange id", and deliberately left the Case-Y **algorithm** unbuilt,
blocked on a captured trace from that source. That source exists — the
AuthBridge sidecar's two-span lineage plugin (wire contract:
`docs/sidecar-wire-contract.md`, v1.6.0) — and this ADR lands its writer as
`INTERACTIONS_ALGORITHM=sidecar`, a production peer of `streaming` (ADR-0007)
and `graph` (ADR-0026), following ADR-0026's selection precedent: one dict
entry in `__main__.py`, one driver module over the shared `_driver` loop, the
shared `interactions` cursor, one algorithm running at a time.

## The algorithm (`processors/interactions/sidecar.py`)

A whole-trace reconcile, re-run per arriving span (the graph driver's
re-derive-per-span shape): pair request/response spans by
`lineage.exchange.id`; every outbound request is an anchor; an inbound request
is an anchor only when its parent walk (phantom-root- and cycle-safe) reaches
no outbound request — otherwise it is the callee-side **echo** that enriches
the outbound's callee identity and dedups a cross-pod call to one edge. All
vocabulary lives in the shared leaf `data_governance/sidecar_facts.py`
(`(direction, protocol[, mcp.method])` → entity/content kinds); the producer
emits facts only.

Exactly the shape ADR-0025 prescribed:

- **Id from the exchange id**: `_interaction_id(trace_id, exchange_id)`. The
  exchange id IS the request span's span id (contract), so ids coincide with
  the streaming formula's — a cutover re-derive lands on the same rows.
- **Observed legs, never fabricated**: the request leg always (`occurred_at` =
  request start, `error` **NULL** — the wire carries no request-side outcome;
  `lineage.outcome` is a completion fact and belongs to the response leg); the
  response leg only when its span exists — absence IS the in-flight signal the
  read-time `duration_seconds: null` renders.
- **Per-leg seq, DB-owned** (the post-#123 model, shared with the streaming
  branch of `state.flush`): the write path omits `seq` at INSERT so the
  column DEFAULT (`nextval`, migration 0009) assigns it once, and omits it
  from `DO UPDATE` so a re-derive preserves the once-assigned value.
  Request-seq < response-seq holds structurally: a response leg is only
  derivable once its request span exists, and the request leg is inserted
  first — same transaction or an earlier drain. (This ADR originally
  specified explicit span-derived seqs with a `max()` clamp; that was
  superseded when #123 landed the DB-owned convention on `main`.)
- **`interaction_spans.leg_type` per span**: the anchor (request) span →
  `request`; the paired response span's connector row → `response`; echo,
  bridge, and other connector spans → NULL — they evidence the interaction's
  territory, not a specific leg (the column is nullable for exactly this).

## Why it has its own write path (and does not share `state.flush`)

The reconcile is whole-trace-authoritative and its wanted set can **shrink**:
an inbound entry is a real interaction until its outbound ancestor arrives,
then it demotes to echo and its interaction row, both its legs, and its anchor
`interaction_spans` row must be **deleted**. `state.flush` deliberately
forbids that — anchors are emit-once and never deleted, and no interaction or
leg row is ever removed — because the streaming algorithm repairs regions
rather than reconciling traces. Adapting flush would have required a synthetic
`ProductionRows`, a sentinel span, a disabled aggregate recompute, and a
bolt-on reconcile delete: more machinery than the ~120-line write path it
replaced, entangling two write models in one function. So the algorithm keeps
its own `_write` — trace-scoped deletes only (stale legs via a subselect on
the trace's interactions, since legs carry no `trace_id`; `entities` is
global, upsert-only, never deleted) — and `state.py`, `procedure.py`, and the
graph modules are untouched, keeping streaming/graph byte-identical by
construction. The `interaction_spans` leg_type parameterization ADR-0025
pre-authorized inside flush turned out unnecessary: the Case-Y source brings
its own writes and stamps leg_type there.

## Read side

`retrieval/interactions.py` re-derives a per-interaction `kinds` object
(`protocol`, `mcp_method`, request/response content kinds) from the anchor
span's stored attributes through the shared vocabulary at read time — bodyless
interactions classify, stale write-once payload kinds cannot mislead, and a
vocabulary change re-labels history without reprocessing. Guarded: an anchor
without lineage facts (streaming/graph interactions) yields `kinds: null`,
never fabricated defaults. `kinds.request_content_kind` is what lets a consumer
tell an infrastructure exchange (MCP lifecycle, tool discovery) from a
substantive one — it is the field a view filters on.

## Deliberately out of scope

- The `graph` algorithm stays an untouched peer for app-emitted spans under
  partial instrumentation; the sidecar source states its facts, so structural
  inference is unnecessary here.
- ADR-0027 leg-readiness consumers (`leg_ready`, `entity_ready`) landed on
  `main` after this ADR was written; the DB-owned leg seq above is exactly
  the ordering they cursor on, so this source feeds them unchanged.
- Upstream `caller_inference.py` is not wired in: its orphan-server ladder
  reads `kagenti.user.id` / `peer.service` / `client.address` / `net.peer.*`,
  none of which the wire contract emits. The sidecar's `client:(unknown)`
  entries stem from ext_proc providing no peer address — a producer-side
  follow-up, not a consumer-side mapping.
