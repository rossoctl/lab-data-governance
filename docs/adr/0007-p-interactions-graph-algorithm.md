# P-interactions: graph-based algorithm for entity and interaction extraction

The P-interactions processor derives agentic entities and their interactions
from OTel spans using a single base graph that is progressively colored by
agentic-scope semantics, rather than linear span-pattern matching or a stack of
per-scope graphs that are merged at the end. This was chosen because the linear
approach requires hard-coded heuristics for each span pattern and cannot
represent partial instrumentation cleanly, and because the per-scope-then-merge
approach pushes structural decisions into a cross-scope merge step that is hard
to inspect. A single colored base graph makes both the trace structure and the
agentic semantics explicit and inspectable in one place.

## The algorithm

**Step 1 — Base graph.**
Construct one node per span and one White directed edge from each span's parent
to itself, following the OTel traceparent relationships. The result is a single
graph for the entire trace whose connectivity mirrors trace structure exactly.
All nodes and edges start White.

**Step 2.a — Agentic coloring.**
Coloring is additive: White edges are never removed when Gray edges are added,
and Gray edges are never removed when Black edges are added. The base graph's
full White connectivity is preserved throughout.

1. For every span belonging to an *agentic* instrumentation scope (currently
   a2a, openinference, mcp), color the node **Gray**.
2. For every pair of Gray nodes connected by a chain of White edges that does
   not pass through another Gray node, add a directed **Gray edge** between
   them in the same direction as the underlying chain.
3. Each agentic scope has a per-scope classifier that identifies which of its
   spans are *protocol boundaries* — i.e. spans that represent either the
   source (caller side) or the target (callee side) of an agentic protocol call
   (a2a request/response, MCP call, an openinference LLM invocation, …). Color
   each boundary node **Black**. Gray nodes that are not boundaries (internal
   SDK plumbing, lifecycle hooks, framework dispatch) remain Gray.
4. For every Gray edge whose endpoints are both Black, add a **Black edge** in
   the same direction.

**Step 2.b — Combined source-and-target spans.**
Some agentic spans represent both sides of a call in a single span — e.g.
`openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query`, which
records both the outgoing request and the incoming response. For each such
Black node, create an additional Black node referencing the same span. The
**original** node keeps both its parent-side and child-side Gray/White chains
and represents the **source** entity. The **duplicate** node has no neighbours
in the base graph and represents the **target** entity. Add two directed Black
edges between them: source→target (request) and target→source (response).

**Step 2.c — Entity graph.**
Compute connected components over the set of Gray and Black nodes, considering
only White and Gray edges (Black edges are ignored for this purpose). Each
connected component becomes one **entity node**. Attributes from every span in
the component are pooled onto the entity node, and the entity's display label
is derived from those pooled attributes. Each Black edge in the colored base
graph becomes a directed edge in the entity graph between the entity nodes
containing its endpoints.

## Annotations

**Flagging unexpected agentic spans between boundaries.**
Any Gray node that lies on a Gray chain between two Black boundary nodes is
annotated and surfaced in the "Graphs (proto)" UI tab. The intent is to catch
agentic spans that the classifier does not yet recognise as boundaries —
treating the in-between span as a signal that the per-scope span tables or
classifier may be incomplete. The annotation is informational only; it does
not block entity formation.

## Key decisions

**One base graph, no per-scope graphs, no cross-scope merge.**
All scopes contribute spans to a single base graph. Agentic-scope semantics
are overlaid by coloring nodes/edges in place. There is no separate
`ScopeGraph` per scope and no `XScopeGraph` merge step. Cross-scope attribute
reconciliation (e.g. a2a caller and httpx caller representing the same real
entity) is deferred to a later enrichment stage and is out of scope for this
algorithm.

**Edge coloring is additive, not replacement.**
A Gray edge between two Gray nodes is added on top of the underlying White
chain; the White edges remain. A Black edge between two Black nodes is added
on top of the corresponding Gray edge; the Gray edge remains. This keeps trace
structure recoverable at every layer.

**Boundaries are detected only in agentic scopes.**
Each agentic scope (a2a, openinference, mcp, …) has its own classifier that
decides which of its spans are protocol boundaries. Non-agentic scopes (httpx,
starlette, …) are not consulted for boundary detection at this stage; their
spans remain White and contribute no entities. Their attributes will be used
later to enrich agentic entities.

**No inferred-peer fusion within a single scope.**
When both sides of a call are observed (e.g. an a2a client `send_message` span
and the matching server `on_message_send` span), they become two separate
entities in Step 2.c, joined by a Black edge representing the call. There is
no attempt to fuse the two sides into one richer caller/callee pair at this
stage. The two entities remain distinct, and any cross-side attribute
enrichment happens later.

**Entity attributes are pooled from all spans in the component.**
Within a connected component, attributes from every Gray and Black span are
pooled onto the resulting entity node. Internal Gray plumbing nodes contribute
their attributes too — they are not treated as structure-only.

**Combined source-and-target spans assume the target emits no spans of its
own.** Step 2.b's duplicate-node approach is correct when the target side has
no observable spans (e.g. an external LLM call where only the SDK's combined
span is recorded). When the target *does* emit its own spans, those spans
would necessarily appear as parents of the original combined span (because
the combined span carries the result), and the duplicate-stands-alone model
becomes incorrect. This case is deferred.

## Deferred to later stages

The following are intentionally out of scope for the algorithm described here
and will be addressed by a separate enrichment stage:

- **Cross-scope attribute enrichment.** Pulling attributes from non-agentic
  spans (httpx URL/host, starlette route, …) onto the agentic entities they
  describe. Also: recognising that an a2a caller span and an httpx caller span
  on the same chain represent the same real entity.
- **Stub entities for one-sided observation.** When only the caller side is
  instrumented (or only the callee side), the current algorithm produces a
  single entity with no peer and no edge. Filling in a stub for the
  unobserved side is deferred.
- **Broken traceparent / disconnected base graphs.** When a Receive span has
  no traceparent link to its corresponding Send, the base graph is
  disconnected and Step 2.c naturally produces disconnected components in the
  entity graph. No special handling, no annotation at this stage.
- **Combined source-and-target spans whose target emits its own spans.** See
  the corresponding key decision above.
- **Entities from pure non-agentic calls.** A direct httpx call between two
  services with no agentic span on either side produces no entity at this
  stage.

## Considered alternatives

**Linear span-pattern matching** (the original prototype). Three passes over
the full span list — cross-service SERVER anchors, LLM calls, local tool
calls — using hard-coded ancestor walks. Conflates graph construction with
entity inference, cannot represent intermediate states, and requires bespoke
logic per interaction pattern.

**Per-scope graphs with cross-scope merge.** Build one `ScopeGraph` per
instrumentation scope, then merge them via structural isomorphism plus label
matching. Inspectable, but pushes the hardest decisions into the merge step
and forces every scope (including non-agentic ones) to commit to a Send /
Receive / Internal classification up front. The current design moves that
work into a separately-defined enrichment stage and keeps the core algorithm
focused on agentic-scope semantics over a single base graph.

## Consequences

- The prototype writes three sets of scratch tables for inspection: the base
  graph (after Step 1), the colored base graph (after Step 2.a and 2.b,
  including combined-span duplicates and between-boundary flag annotations),
  and the entity graph (after Step 2.c). All three are surfaced in the
  "Graphs (proto)" tab of the trace-tree UI.
- Adding support for a new agentic scope requires only a new per-scope
  classifier (mapping spans to boundary / non-boundary) and a dispatch-table
  entry. Base graph construction, coloring rules, and entity-graph derivation
  are unchanged.
- Non-agentic scopes contribute no entities at this stage. Scopes that emit
  no agentic spans at all will not appear in the entity graph until the
  enrichment stage runs.
- Attribute sources used by each classifier are validated against the
  otel-span-table reference for that scope before the classifier is written.
