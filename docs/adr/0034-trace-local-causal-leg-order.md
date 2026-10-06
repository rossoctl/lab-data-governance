---
status: accepted
---

# Derive trace-local causal leg order; keep `seq` as the ingestion cursor

Spans from different services can be ingested out of execution order. Reconciliation
can attach a child to a parent after the child's legs have been stored, while those
legs retain their database `seq`. Sorting a trace by `seq` can therefore place a
downstream request before the request that caused it and derive false lineage.

For every trace read, build one order from the currently known interaction legs.
A parent's request precedes each child's request, and a request precedes its own
response when both are present. Among legs ready under those constraints, prefer
`occurred_at`, then use `seq` and the leg key for deterministic ties. Missing
parents, legs and times leave only the constraints supported by current evidence;
invalid parent cycles are broken deterministically without dropping legs. A
parent response need not wait for a child response because calls can finish
asynchronously. The shared backend rule supplies the trace interactions API's
`leg_order`, lineage derivation, lineage leg reads and lineage graph hop positions.
UI tables and diagrams use that API order and display one-based causal steps.

The graph algorithm also infers tool calls from LLM message attributes. Those
calls have no independent tool execution span: an output call is known at the
LLM response boundary, while an input replay is known at the LLM request
boundary. Its synthetic legs use that boundary as their occurrence time. A
tool resolved to an observed peer keeps the observed timing. Migration 0021
corrects existing inferred-tool rows before replaying their lineage.

`interaction_legs.seq` remains the durable ingestion cursor for the cross-trace
feed, leg readiness and other stream drains. It is not rewritten to repair a trace.
A cursor stream cannot retroactively insert a late parent before a child already
delivered. Consumers that need the corrected trace must re-read it or respond to
an explicit revision. The data-lineage processor re-derives affected traces from
a durable dirty-trace queue, including a migration backfill of lineage previously
computed in ingestion order. A queue generation is removed only if no newer
correction replaced it during derivation. A partial lineage status retains the
cutoff leg's `seq` as its identifier and stores the causal prefix as leg keys;
numeric `seq` comparisons never define that prefix or a graph hop.

Sidecar reconciliation can also remove interactions and legs. Delete triggers
remove lineage rows for vanished legs and queue their old trace so the retained
legs are derived again. If no legs remain, the processor removes the trace's old
coverage claim. During derivation, key-share locks keep the interaction and leg
rows alive until the lineage writes commit; a concurrent deletion then cleans up
those rows afterward. This prevents an orphaned lineage row from being inserted
after its leg was deleted.

Risk processing on the unmerged `origin/risk` branch needs its own reconciliation
change before a corrected parent or endpoint can be treated as current. It remains
separate from this main-based implementation.
