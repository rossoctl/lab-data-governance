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
response when both are present. Among legs ready under those constraints, the
earliest sort time wins. A leg's sort time is its `occurred_at`, except that a
child's leg is pulled no later than its parent's response's sort time, because a
synchronous caller answers only after its callee has. The parent's response is never
moved later to wait for its child, so a sibling call sent after that response still
follows it. Sort times are computed from the root of the interaction tree down, so a
grandchild is bounded by its parent's already-pulled time. Leg times come from
different pods' clocks and from span ends that each SDK closes at its own point, so a
callee can appear to act a few milliseconds after its caller already answered; a
child the parent did not wait for is apart by far more. A child leg timed more than
a clock-skew tolerance of one second after its parent's response is therefore clearly
asynchronous and keeps its own time. A child leg with no time takes the parent
response's time, since an unknown time is not evidence of asynchrony. An untimed
parent response pulls no timed leg, because it already sorts after every timed one.
The pull applies to the child's request as well as its response: the child's
response cannot be ordered before its own request, so a request stamped after the
parent's response, or untimed, would otherwise hold the child's response back behind
it. Legs pulled into a response rank immediately before it, ordered among themselves
by their own times, so at an equal sort time a descendant's response precedes its
ancestor's. Remaining ties use `occurred_at`, then `seq` and the leg key. Only the
order changes: `occurred_at` stays what was observed. Missing parents, legs and times
leave only the constraints supported by current evidence; in particular, a child still
in flight never holds its parent's response back, and a parent with no response pulls
nothing. Invalid parent cycles are broken deterministically without dropping legs:
only the earliest leg on a cycle that no other remaining leg constrains is released,
so a valid child of a cycle member still follows its parent's request. A parent link
on a cycle pulls no time, and a self-parent constrains nothing. The shared backend
rule supplies the trace interactions API's `leg_order`, lineage derivation, lineage
leg reads and lineage graph hop positions. UI tables and diagrams use that API order
and display one-based causal steps.

The graph algorithm also infers tool calls from LLM message attributes. Those
calls have no independent tool execution span: an output call is known at the
LLM response boundary, while an input replay is known at the LLM request
boundary. Its synthetic legs use that boundary as their occurrence time. An
LLM span with no recorded end gives an output call and the LLM's own response
leg its start instead, so the exchange stays whole and in order; an LLM call
that encloses another call keeps its response untimed. A tool resolved to an
observed peer keeps the observed timing. Migration 0026 corrects existing
inferred-tool rows before replaying their lineage.

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
coverage claim. Sidecar reconciliation and lineage derivation of a trace each
take a transaction-scoped advisory lock on that trace before reading it and hold
it until commit, so the two never interleave. A lineage write therefore cannot
land after a concurrent deletion's triggers have run, which would leave an
orphaned lineage row that no later derivation removes. Row locks taken by the
lineage read cannot give this guarantee: the reconciler's delete and the read
lock the same rows in different orders, so either processor can be chosen as a
deadlock victim, and every re-derivation would rewrite a lock into every row of
the trace.

Risk processing on the unmerged `origin/risk` branch needs its own reconciliation
change before a corrected parent or endpoint can be treated as current. It remains
separate from this main-based implementation.
