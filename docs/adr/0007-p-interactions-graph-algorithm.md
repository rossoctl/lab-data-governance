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

## Definitions

The algorithm uses two orthogonal vocabularies for nodes — one describes the
**role** the node plays in an interaction, the other describes the
**structural** origin of the node in the graph.

**Roles** (what the node represents):
- **Event node** — a node representing a local entity, recorded by an
  observed span.
- **Source node** — a node representing the caller side of an agentic
  protocol call (the local entity initiating the call).
- **Target node** — a node representing the callee side of an agentic
  protocol call (the remote entity receiving the call).

**Structural descriptors** (how the node was produced):
- **Boundary node** — a Black node whose underlying span was classified as a
  protocol boundary (source or target) by its scope's classifier.
- **Duplicate node** — an additional Black node referencing the same span as
  an existing Black node, created by Step 2.b to split a combined
  source-and-target span into two role-distinct nodes.
- **Synthetic node** — a Black node created by Step 2.c to represent an
  unobserved peer when only one side of a protocol call was instrumented.
  Synthetic nodes carry a `synthetic: true` marker.

**Edges:**
- **White edge** — parent/child relationship via OTel traceparent.
- **Gray edge** — order of events considering only agentic-scoped events; a
  Gray edge connects two Gray nodes whose underlying White chain does not
  pass through another Gray node.
- **Black edge** — a source/target relationship across agentic
  entities/components/containers.

A Black boundary node typically *plays* the Source or Target role depending on
which side of the call its span represents. A duplicate or synthetic node is
always created to fill in the *opposite* role of an existing boundary node.

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

This stage currently handles the **openinference** agentic scope only. Support
for additional agentic scopes (a2a, mcp) is deferred — see "Deferred to later
stages" below.

1. For every span belonging to the openinference instrumentation scope, color
   the node **Gray**.
2. For every pair of Gray nodes connected by a chain of White edges that does
   not pass through another Gray node, add a directed **Gray edge** between
   them in the same direction as the underlying chain.
3. The openinference classifier identifies which of its spans are *protocol
   boundaries* — i.e. spans that represent either the source (caller side) or
   the target (callee side) of an agentic protocol call (an LLM invocation,
   an agent-to-agent call, …). Color each boundary node **Black**. Gray nodes
   that are not boundaries (internal SDK plumbing, lifecycle hooks, framework
   dispatch) remain Gray.
4. For every Gray edge whose endpoints are both Black, add a **Black edge** in
   the same direction.

**Step 2.b — Combined source-and-target spans.**
Some agentic spans represent both sides of a call in a single span — e.g.
`openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query`, which
records both the outgoing request and the incoming response. For each such
Black node, create an additional Black **duplicate node** referencing the same
span. The **original** node keeps both its parent-side and child-side
Gray/White chains and plays the **Source** role. The **duplicate** node has no
neighbours in the base graph and plays the **Target** role. Add two directed
Black edges between them: source→target (request) and target→source (response).

**Step 2.c — Stubbing one-sided observations.**
A Black boundary node with no Black edges indicates that the peer side of the
call was not observed (missing instrumentation, a bug, or genuinely uninstrumented
code on the other side). For every such Black node, create an additional Black
**synthetic node** referencing the same span and carrying `synthetic: true`.
The original node retains its Source-or-Target role as classified; the synthetic
node plays the *opposite* role. Add two directed Black edges between them:
source→target and target→source.

When both sides of a call are observed in the same trace, Step 2.a.4 will have
already added a Black edge between them, so this step does not fire. A Black
node with no Black edges therefore reliably indicates an unobserved peer.

**Step 3 — Agentic entity graph.**
Step 3 derives the entity graph from the colored base graph in three sub-steps:
form entities (3.a), merge identical synthetic peers (3.b), and name them (3.c).

**Step 3.a — Creating the graph.**
Compute connected components over the set of Gray and Black nodes, considering
only White and Gray edges (Black edges are ignored for this purpose). Each
connected component becomes one **entity node**. Attributes from every span in
the component are pooled onto the entity node. Each Black edge in the colored
base graph becomes a directed edge in the entity graph between the entity
nodes containing its endpoints. Synthetic nodes propagate their
`synthetic: true` marker onto the entity node they form.

**Step 3.b — Merging identical synthetic peers.**
Step 2.c materialises a synthetic Black node for every observed boundary whose
peer was not observed; in Step 3.a each such synthetic node becomes its own
entity node. When the same real peer is the unobserved target of multiple
calls (e.g. the same tool invoked from two different agents in the same
trace), Step 3.a produces multiple synthetic entities that should collapse
into one.

For every pair of synthetic entity nodes whose **source-span attributes
match** (e.g. the same `tool.name`, the same `llm.model_name`, the same
identifying attribute used by the originating boundary's classifier), merge
them into a single synthetic entity. The merged entity keeps the
`synthetic: true` marker and inherits all Black edges from the merged peers,
so every observed source that was pointing at any of the duplicates now
points at the single merged entity.

Only synthetic entities are merged. Observed entities (those formed from a
real boundary span on the peer side) are never fused at this stage — the
"No inferred-peer fusion" decision below records the rationale.

**Step 3.c — Naming nodes.**
Each entity node is assigned the ID `unknown` at this stage. Richer naming
— deriving an ID from the entity's pooled attributes (hostname from non-
agentic enrichment, service name, model/tool name, etc.) — is deferred; see
"Deferred to later stages" below.

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
Each agentic scope has its own classifier that decides which of its spans are
protocol boundaries. The current algorithm covers the openinference scope
only; a2a and mcp classifiers are deferred. Non-agentic scopes (httpx,
starlette, …) are not consulted for boundary detection at this stage; their
spans remain White and contribute no entities. Their attributes will be used
later to enrich agentic entities.

**Each scope is developed and implemented separately.**
A new agentic scope is added in isolation: a per-scope classifier mapping spans
to boundary / non-boundary, plus a dispatch-table entry. The base graph,
coloring rules, and entity-graph derivation are shared and unchanged across
scopes.

**Observed peers are never fused; synthetic peers may be merged.**
When both sides of a call are observed, they become two separate entities in
Step 3.a, joined by a Black edge representing the call. The two observed
entities remain distinct — there is no attempt to fuse them into one richer
caller/callee pair at this stage, and any cross-side attribute enrichment
happens later. Synthetic peers (produced by Step 2.c when only one side was
observed) are different: Step 3.b merges synthetic entities whose
source-span attributes match, so a single unobserved real peer called from
multiple sources collapses into one entity rather than appearing as N
look-alike duplicates.

**Synthetic peers are created in the core algorithm, not deferred.**
When only one side of a protocol call is observed, Step 2.c materialises a
synthetic Black node for the unobserved peer (marked `synthetic: true`) and
connects it with bidirectional Black edges. This keeps the entity graph
shape-consistent — every observed boundary participates in a complete
source/target pair — and lets downstream consumers distinguish observed
entities from inferred ones via the marker. The alternative (leave the lone
boundary edgeless and stub later) was rejected because it would leave the
entity graph topologically inconsistent across observed-both-sides vs
observed-one-side cases.

**Entity attributes are pooled from all spans in the component.**
Within a connected component, attributes from every Gray and Black span are
pooled onto the resulting entity node. Internal Gray plumbing nodes contribute
their attributes too — they are not treated as structure-only.

**Attribute sources are validated against the per-scope span reference.**
Every attribute the algorithm or its classifiers consult must be validated
against the span-table reference for the scope that emitted it (e.g.
`openinference_telemetry_spans.md` for openinference,
`asgi_telemetry_spans.md` for httpx/starlette). New attributes are not added
on intuition; the reference is regenerated from the upstream package and the
attribute confirmed before it is used.

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

- **Additional agentic scopes (a2a, mcp).** The current algorithm handles
  openinference only. a2a and mcp classifiers, and integration of their spans
  into the coloring step, are deferred.
- **Cross-scope attribute enrichment.** Pulling attributes from non-agentic
  spans (httpx URL/host, starlette route, …) onto the agentic entities they
  describe. Also: recognising that an agentic caller span and an httpx caller
  span on the same chain represent the same real entity.
- **Broken traceparent / disconnected base graphs.** When a Receive span has
  no traceparent link to its corresponding Send, the base graph is
  disconnected and Step 3.a naturally produces disconnected components in the
  entity graph. No special handling, no annotation at this stage.
- **Richer entity naming.** Step 3.c assigns `unknown` to every entity. A
  later stage will derive an ID from the entity's pooled attributes —
  hostname (from non-agentic httpx/starlette enrichment),
  `service.name` (OTel resource), or framework-specific attributes
  (`llm.model_name`, `tool.name`, `agent.name`, …). This is deferred until
  the cross-scope enrichment stage exists, since the most useful identifier
  (hostname) lives on non-agentic spans that today are not part of the
  entity-forming subgraph.
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
  graph (after Step 1), the colored base graph (after Steps 2.a, 2.b, and 2.c,
  including combined-span duplicates, synthetic peers, and between-boundary
  flag annotations), and the entity graph (after Step 3, including synthetic-
  peer merging). All three are surfaced in the "Graphs (proto)" tab of the
  trace-tree UI.
- Non-agentic scopes contribute no entities at this stage. Scopes that emit
  no agentic spans at all will not appear in the entity graph until the
  enrichment stage runs.
