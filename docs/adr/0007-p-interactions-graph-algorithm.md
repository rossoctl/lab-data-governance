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
**structural** origin of the node in the graph. A third vocabulary —
`SpanFacts` — sits between the raw spans and the algorithm and is described
under "Adapter layer" below.

**Roles** (what the node represents):
- **Event node** — a node representing a local entity, recorded by an
  observed span.
- **Source node** — a node representing the caller side of an agentic
  protocol call (the local entity initiating the call).
- **Target node** — a node representing the callee side of an agentic
  protocol call (the remote entity receiving the call).

A span's role is assigned by its `(scope, framework)` adapter and surfaced
on `SpanFacts.role` (`SOURCE` / `TARGET` / `BOTH` / `NONE`). `NONE` means
the span is not a call boundary — typically a wrapper / runner /
per-activation span that lacks the specific call evidence (target
identity, request/response payload, or a framework-specific span-name
signal) needed to assert one side of a call. The Step 2.a.3 promotion to
Black is driven by `role`, **not** by `Kind` (see "Boundary promotion is
role-driven, not kind-driven" under Key decisions).

**Structural descriptors** (how the node was produced):
- **Boundary node** — a Black node whose underlying span was classified as a
  protocol boundary (source or target) by its scope's classifier.
- **Duplicate node** — an additional Black node referencing the same span as
  an existing Black node, created by Step 2.b to split a combined
  source-and-target span into two role-distinct nodes.
- **Synthetic node** — a Black node created by Step 2.c to represent an
  unobserved peer when only one side of a protocol call was instrumented.
  Synthetic nodes are identified by a dedicated boolean field
  (`is_synthetic` on colored-base-graph nodes, `synthetic` on entity nodes).
  Identification is never done by inspecting the node's label or any other
  display string — the field is the single source of truth.

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

## Adapter layer

Raw OTel attribute keys are consulted in **exactly one place**: `adapters.py`.
The rest of the algorithm reads from a typed value object, `SpanFacts`,
produced by an adapter from a span. This isolates the per-framework /
per-version vocabulary drift (rename `llm.model_name` → `llm.model.name`,
add a new kind value, …) from the graph-construction code.

`SpanFacts` carries:

- `kind` — `Kind.LLM`, `Kind.TOOL`, `Kind.AGENT`, or `Kind.OTHER`. Kind
  records *what the span is about* (used to derive natural-key prefix and
  payload shape) — it does **not** by itself decide boundary-ness. A
  wrapper AGENT span carries `Kind.AGENT` and `role=NONE`.
- `role` — `Role.SOURCE`, `Role.TARGET`, `Role.BOTH`, or `Role.NONE`.
  Assigned by the adapter from the span's call evidence: target identity,
  request/response payload, or a framework-specific span-name signal.
  Drives Step 2.a.3 Black promotion. `Role.NONE` keeps the node Gray.
- `is_combined` — true iff one span carries BOTH the source and target side
  of the same call. Triggers Step 2.b duplication.
- `natural_key` — stable per-boundary identity used as the Step 3.b
  synthetic-peer merge key. Format is `<kind-prefix>:<identifier>` —
  `tool:get_weather`, `llm:gpt-4o`, `agent:travel_advisor`. None when the
  span carries no identifying attribute (those synthetics stay distinct in
  3.b).
- `display_label`, `target_label` — human-facing labels; `target_label` is
  the duplicate's label for combined spans only.
- `request_messages` / `response_messages` / `request_value` /
  `response_value` — payload data for `extractor._derive_interactions`.

**Dispatch.** Adapters are registered per `(scope_root, framework)` — e.g.
`(openinference, openai_agents)`, `(openinference, claude_agent_sdk)`. The
framework name is the third dotted segment of the scope name (e.g.
`openinference.instrumentation.openai_agents`). A `*` fallback adapter
covers openinference frameworks not yet profiled (LangChain, LiteLLM,
Haystack, …) using the cross-framework openinference vocabulary; it is
safe-by-default — unrecognised combined-span shapes degrade to one-sided
boundaries that Step 2.c will stub.

**Versioning.** Adapters that have absorbed schema drift across releases
declare a per-version schema map keyed on `_scope_version(span)`. The
schema records two axes declaratively:

- `kinds[raw_value] → Kind` — maps the raw string the framework emits at
  `openinference.span.kind` to the internal `Kind`. A version that
  introduces a new raw value adds an entry; raw values absent from the
  map decode to `Kind.OTHER` (better Gray than mis-Black).
- `fields[(Kind, raw_attr_key)] → logical_field` — maps a (decoded kind,
  physical attribute key the framework emits) pair to the adapter's
  internal logical field name (`"model"`, `"name"`, `"input_value"`,
  `"output_value"`). A version-rename is a single new entry; aliases
  during a transition are multiple entries with the same RHS.

The `openai_agents` adapter currently carries entries for `1.4.1` and
`1.5.1` (the only known divergence is `GuardrailSpanData`: kind=`CHAIN`
at 1.4.1, kind=`GUARDRAIL` at 1.5.1; both decode to `Kind.OTHER`, so the
algorithm sees them identically — but the divergence is captured
declaratively, not in a code branch). A `"*"` entry in every schema map
ensures unknown future versions degrade gracefully to the broadest known
vocabulary.

The honest division of labour is **schema drift in tables, behaviour drift
in code**. Span-name parsing (the `"handoff to {target}"` prefix in
openai_agents, the `ClaudeAgentSDK.{tool_name}` sub-agent prefix, the
`ClaudeAgentSDK.query` combined-span recognition) is genuinely behavioural
— recognising the prefix is coupled to a consequence (switch the
natural-key kind, mark the span combined). Those decisions live in adapter
code, not the schema tables.

**Boundary detection vs. agentic-scope recognition.** The OpenInference MCP
adapter (`openinference.instrumentation.mcp`) exists in the registry but
returns `Kind.OTHER` for every span — the MCP instrumentor only injects /
extracts W3C `traceparent` headers and emits no application spans. The
adapter exists so dispatch recognises the scope (future enrichment can
find these spans), not because MCP boundaries are detected at this stage.
Standalone a2a and mcp scopes outside openinference remain deferred, per
the "Deferred to later stages" section.

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
3. The openinference classifier delegates to the `(scope, framework)`
   adapter to produce a `SpanFacts` for each span. A span is a *protocol
   boundary* iff `SpanFacts.role` is not `NONE` — the adapter has
   determined that the span carries explicit call evidence and represents
   the source (caller side), the target (callee side), or both sides of
   an agentic protocol call. Color each boundary node **Black**. Gray
   nodes whose `SpanFacts.role` is `NONE` remain Gray. This includes:
   internal SDK plumbing, lifecycle hooks, framework dispatch, guardrail
   checks, custom CHAIN spans (kind=`OTHER`); **and** wrapper spans whose
   kind is `AGENT`/`TOOL`/`LLM` but which lack the specific call evidence
   needed to assert one side of a call (e.g. a top-level agent-run
   wrapper that does not itself carry target identity or payload —
   typically a more specific child span is the real boundary). The
   adapter is the single arbiter of role; the builder reads only the
   field.
4. For every Gray edge whose endpoints are both Black, add a **Black edge** in
   the same direction.

**Step 2.b — Combined source-and-target spans.**
Some agentic spans represent both sides of a call in a single span — e.g.
`ClaudeAgentSDK.query` (and `ClaudeAgentSDK.ClaudeSDKClient.receive_response`)
in the `claude_agent_sdk` framework, which record both the outgoing request
to the remote LLM and the incoming response. The adapter signals this by
returning `SpanFacts.is_combined = True` together with a `target_label` for
the duplicated node (typically `llm:<model>`). For each such Black node,
create an additional Black **duplicate node** referencing the same span. The
**original** node keeps both its parent-side and child-side Gray/White
chains and plays the **Source** role. The **duplicate** node has no
neighbours in the base graph and plays the **Target** role; its label is
`SpanFacts.target_label`. Add two directed Black edges between them:
source→target (request) and target→source (response).

**Step 2.c — Stubbing one-sided observations.**
A Black boundary node with no Black edges indicates that the peer side of the
call was not observed (missing instrumentation, a bug, or genuinely uninstrumented
code on the other side). For every such Black node, create an additional Black
**synthetic node** referencing the same span and carrying `is_synthetic = true`
as a dedicated boolean field on the node (not encoded in the label or any other
display string). The synthetic node also stores the originating boundary's
`SpanFacts.natural_key` on a `peer_match_key` field — Step 3.b uses this to
merge synthetic peers stubbing the same real callee from multiple sources.
When a natural key is available the synthetic node's display label is the
key itself (`tool:get_weather`, `llm:gpt-4o`, …); otherwise it falls back
to `(unobserved peer of <source>)`. The original node retains its
Source-or-Target role as classified; the synthetic node plays the
*opposite* role. Add two directed Black edges between them: source→target
and target→source.

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
nodes containing its endpoints. Synthetic nodes propagate their `is_synthetic`
marker onto the entity node they form via a dedicated boolean field
(`synthetic`) on the entity node — again, not via label inspection. An entity
is `synthetic = true` iff every absorbed Black node was synthetic.

**Step 3.b — Merging identical synthetic peers.**
Step 2.c materialises a synthetic Black node for every observed boundary whose
peer was not observed; in Step 3.a each such synthetic node becomes its own
entity node. When the same real peer is the unobserved target of multiple
calls (e.g. the same tool invoked from two different agents in the same
trace), Step 3.a produces multiple synthetic entities that should collapse
into one.

For every pair of synthetic entity nodes whose **`peer_match_key` matches**,
merge them into a single synthetic entity. The match key is the
`SpanFacts.natural_key` of the originating boundary span — `tool:<name>`,
`llm:<model>`, or `agent:<name>` — propagated from the synthetic Black
node onto the entity in Step 3.a. Synthetic entities with no key (the
originating span had no identifying attribute) are not merged; they remain
distinct. The merged entity keeps the `synthetic: true` marker.

**All edges and interactions are preserved across the merge.** Every Black
edge incident on any of the merged peers is rewritten onto the single
surviving entity — none is dropped, deduplicated by endpoint pair, or
collapsed. Each pre-merge edge represents a distinct observed call site,
and each must survive as a distinct edge (and therefore a distinct
interaction in the extractor) so that the count and provenance of calls to
the unobserved peer is preserved. Concretely: if two observed sources both
called the same unobserved tool, the pre-merge graph has two synthetic
entities with one edge each; the post-merge graph has one synthetic entity
with two edges, not one.

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
Each agentic scope has its own per-`(scope, framework)` adapter that
produces `SpanFacts` for every span; a `SpanFacts.role` other than
`NONE` marks a boundary. The current algorithm covers the openinference
scope only; standalone a2a and mcp adapters are deferred. Non-agentic
scopes (httpx, starlette, …) are not consulted for boundary detection at
this stage; their spans remain White and contribute no entities. Their
attributes will be used later to enrich agentic entities.

**Boundary promotion is role-driven, not kind-driven.**
A Gray node is promoted to Black iff the adapter assigned a non-`NONE`
role. A span's `Kind` (LLM/TOOL/AGENT/OTHER) records *what the span is
about* — it drives natural-key prefix selection (`llm:` / `tool:` /
`agent:`) and payload-shape selection (chat messages vs. opaque
input/output values). It does **not** by itself decide boundary-ness.
This separation matters because the agentic SDKs emit wrapper spans
(top-level agent runs, per-activation `AgentSpanData`, runner spans)
that carry `kind=AGENT` but represent no specific call: they do not
carry a target identity, do not carry request/response payloads, and
typically have a more specific child span that *is* the real boundary.
Promoting wrappers to Black on kind alone would produce duplicate
boundaries on the same Gray chain and inflate the entity graph with
non-call edges. The adapter therefore assigns `role=NONE` to wrappers
and reserves `SOURCE`/`TARGET`/`BOTH` for spans that carry explicit
call evidence — target identity, request/response payload, or a
framework-specific span-name signal (e.g. `"handoff to {target}"`,
`ClaudeAgentSDK.query`).

For LLM-kind spans the rule is more permissive: `role=SOURCE` is
assigned even when both `llm.input_messages` and `input.value` are
absent. Empty payloads on an LLM-kind span are an instrumentation gap,
not absence of a call — the kind itself is sufficient call evidence.
This asymmetry with AGENT/TOOL is deliberate: AGENT-kind has too many
wrapper-shape false positives to treat kind alone as evidence; LLM-kind
does not.

**Each (scope, framework) pair is developed and implemented separately.**
A new framework — even within an existing scope like openinference — is
added in isolation: drop a new adapter class into the registry. The base
graph, coloring rules, and entity-graph derivation are shared and
unchanged across scopes and frameworks. A new framework version with
schema drift is absorbed by adding entries to that adapter's per-version
schema map; behavioural drift (new combined-span shapes, new span-name
conventions) lives in adapter code.

**Raw OTel attribute keys are isolated to the adapter layer.**
Every attribute lookup happens in `adapters.py`. The graph builder, the
classifier facade, and the extractor read only typed `SpanFacts` fields
— `kind`, `natural_key`, `is_combined`, `target_label`,
`request_messages`, `response_messages`, `request_value`,
`response_value`. A framework attribute rename is a one-file edit; before
this layer existed the same rename required edits in classifiers,
builder, and extractor. The classifier module (`classifiers.py`) is now
a thin facade that translates `SpanFacts` to the older
`AgenticClassification` shape the builder consumes.

**Natural-key prefixes are part of the public algorithm vocabulary.**
The synthetic-peer merge key produced by an adapter has a fixed format:
`tool:<name>`, `llm:<model>`, `agent:<name>`. The prefix doubles as the
entity's coarse kind in the extractor (`_kind_from_label`) and as the
synthetic node's display label when no friendlier label is available.
Adapters strip provider prefixes from model strings (e.g.
`anthropic/claude-3-7` → `claude-3-7`) so the key is the model alone, not
the provider-qualified name. Hostname / `service.name` fallbacks are
deliberately *not* used as keys — they would over-merge across distinct
entities behind the same proxy.

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
synthetic Black node for the unobserved peer (marked with the dedicated
`is_synthetic` boolean field — see "Synthetic identity is a boolean field,
not a label convention" below) and connects it with bidirectional Black
edges. This keeps the entity graph shape-consistent — every observed boundary
participates in a complete source/target pair — and lets downstream consumers
distinguish observed entities from inferred ones via the marker. The
alternative (leave the lone boundary edgeless and stub later) was rejected
because it would leave the entity graph topologically inconsistent across
observed-both-sides vs observed-one-side cases.

**Synthetic identity is a boolean field, not a label convention.**
Synthetic nodes are identified by a dedicated boolean field on the node row
— `is_synthetic` on the colored-base-graph node and `synthetic` on the
entity node — and never by parsing the `label` column or any other display
string. The label is a human-facing display value (e.g. `"(unobserved peer
of dl-demo-travel-advisor)"`) and is free to change for UX reasons; queries
and downstream processors must filter on the boolean field. The scratch-
table schemas in `cli.py` carry this column explicitly so external SQL
inspection has a typed signal rather than a string-pattern heuristic.

**Entity attributes are pooled from all spans in the component.**
Within a connected component, attributes from every Gray and Black span are
pooled onto the resulting entity node. Internal Gray plumbing nodes contribute
their attributes too — they are not treated as structure-only.

**Attribute sources are validated against the per-scope span reference.**
Every attribute an adapter consults must be validated against the
span-table reference for the framework that emitted it
(`openinference_telemetry_spans.md` for cross-framework openinference at
the current main snapshot;
`openinference_openai_agents_v1.4.1_telemetry_spans.md` for openai_agents
1.4.1 — the version that produced the canonical live trace; future
references for httpx/starlette/etc.). New attributes are not added on
intuition; the reference is regenerated from the upstream package and the
attribute confirmed before it appears in `_OI_ATTRS` or in a per-version
schema map. Each adapter records the framework version(s) it has been
verified against in a `documented_version` field for the next maintainer.

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

- **Additional agentic scopes (standalone a2a, standalone mcp).** The current
  algorithm handles the openinference scope only. The OpenInference MCP
  adapter (`openinference.instrumentation.mcp`) is registered but emits no
  boundaries — the MCP instrumentor only injects/extracts traceparent
  headers. Standalone a2a and mcp scopes (outside openinference) and their
  integration into the coloring step are deferred.
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
- **`ClaudeAgentSDK.{tool_name}` sub-agent dispatch.** These spans
  structurally look like a call boundary — span name carries the target
  agent name and `kind=AGENT` is set — but they do not carry payload or
  enough call evidence to be classified as a real boundary under the
  role-driven rule. The adapter assigns `role=NONE` for now, leaving
  these nodes Gray. Treat as a known gap pending either richer
  upstream instrumentation or a span-name-only boundary rule that we
  are not yet ready to commit to.

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
- The colored-base-graph node row (`proto_colored_nodes`) carries an
  `is_synthetic boolean NOT NULL DEFAULT false` column, and the entity-node
  row (`proto_entity_nodes`) carries a `synthetic boolean NOT NULL DEFAULT
  false` column. These are the sole sanctioned signals for "is this an
  unobserved-peer stub?" — the `label` column is display-only and must not
  be parsed for this purpose. The mismatch in column name (`is_synthetic`
  on the node table vs. `synthetic` on the entity table) follows the
  existing `is_*` / `contains_*` convention on each table; the
  `/proto/graphs/{trace_id}` API normalises both to `is_synthetic` on the
  wire so the UI sees one boolean shape across base / colored / entity
  graphs.
- The "Graphs (proto)" UI surfaces synthetic peers via a `synth` marker
  pill alongside the existing `boundary`, `target`, and `flagged` pills,
  and shows a synthetic count in the colored-graph and entity-graph
  summary lines. The pill is rendered by reading the boolean field; the
  label string is never inspected.
- Non-agentic scopes contribute no entities at this stage. Scopes that emit
  no agentic spans at all will not appear in the entity graph until the
  enrichment stage runs.
