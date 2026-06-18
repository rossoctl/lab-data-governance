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

This ADR records the design as it tracks `p_interactions_alg.md` (the
human-owned algorithm spec). Where the current implementation realises only a
subset of the spec, the text says so explicitly: the algorithm vocabulary and
step ordering below follow the spec, and per-section notes flag what the code
implements today versus what is deferred.

## Definitions

The algorithm uses two orthogonal vocabularies for nodes — one describes the
**role** the node plays in an interaction, the other describes the
**provenance** of the node (was it recorded by a real span, or inferred from
one). A third vocabulary — `SpanFacts` — sits between the raw spans and the
algorithm and is described under "Adapter layer" below.

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
signal) needed to assert one side of a call. The Step 2.d promotion to
Black is driven by `role`, **not** by `Kind` (see "Boundary promotion is
role-driven, not kind-driven" under Key decisions).

**Provenance** (how the node came to exist) — following the spec's
definitions verbatim:
- **Observed (real) node** — a node backed by an emitted span.
- **Inferred node** — *"a node in the graph we know should exist although we
  don't have a span emitted representing that node"* (spec def. 7). An
  agentic span can describe an entity other than itself: an LLM `query` span
  describes the LLM it called; a `tool_calls` attribute on an LLM-output span
  describes a tool that was invoked. Step 2.b materialises such peers as
  inferred nodes. An inferred node may later be **merged** with the real node
  representing the same entity (Step 2.c).
- **Duplicate node** — an additional node referencing the same span as an
  existing boundary node, created by Step 2.b to split a combined
  source-and-target span into two role-distinct nodes. (A duplicate is a
  special case of inferred node: the peer is described by the *same* span
  rather than a separate one.)

**merge** — *"the process of collapsing inferred nodes with real nodes — this
process can be based on heuristics"* (spec def. 8). Merging pools the edges
and attributes of the collapsed nodes (see Step 2.c).

In the implementation, an inferred node that survives to the entity graph
without being merged into a real node is recorded with a dedicated boolean
field — `is_inferred` on colored-base-graph nodes, `inferred` on entity nodes.
Identification is **never** done by inspecting the node's label or any other
display string — the field is the single source of truth.

> **Naming note.** These columns were originally `is_synthetic` / `synthetic`.
> The algorithm vocabulary is "inferred", so they have been renamed to
> `is_inferred` / `inferred` across the stack (processor, scratch-table
> schema, API wire shape, and the UI marker). This ADR uses the current
> `inferred` names throughout.

**Edges:**
- **White edge** — parent/child relationship via OTel traceparent.
- **Gray edge** — order of events considering only agentic-scoped events; a
  Gray edge connects two Gray nodes whose underlying White chain does not
  pass through another Gray node.
- **Black edge** — a source/target relationship across agentic
  entities/components/containers.

A Black boundary node typically *plays* the Source or Target role depending on
which side of the call its span represents. For the combined-span (Step 2.b
case 1) and one-sided-stub cases, the duplicate / inferred node is created to
fill the *opposite* role of an existing boundary node. Attribute-derived
inferred nodes (Step 2.b case 2 — tool nodes inferred from an LLM span's
`tool_calls`) are different: they introduce their *own* source/target pair
(the tool-call node and the tool node) rather than completing the role of an
existing boundary.

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
  Drives Step 2.d Black promotion. `Role.NONE` keeps the node Gray.
- `is_combined` — true iff one span carries BOTH the source and target side
  of the same call. Triggers Step 2.b duplication.
- `natural_key` — stable per-boundary identity. Format is
  `<kind-prefix>:<identifier>` — `tool:get_weather`, `llm:gpt-4o`,
  `agent:travel_advisor`. It is the **identifying attribute** the Step 3.a
  phase-2 combine groups on (carried on the inferred node as `peer_match_key`),
  and the natural candidate key for the spec's Step 2.c inferred↔observed
  merge. None when the span carries no identifying attribute (those nodes are
  not combined).
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
boundaries that Step 2.b will stub with an inferred peer.

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
openai_agents, the `ClaudeAgentSDK.{tool_name}` tool/sub-agent dispatch
prefix, the `ClaudeAgentSDK.query` combined-span recognition) is genuinely
behavioural — recognising the prefix is coupled to a consequence (switch the
natural-key kind, mark the span combined, or assign `role=SOURCE` to a
dispatch span so it becomes a boundary with an inferred target peer). Those
decisions live in adapter code, not the schema tables.

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

**Step 2.a — Agentic coloring (Gray).**
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

Boundary promotion (Gray → Black) is **not** done here. The spec orders
boundary detection *after* inferred-node creation and intra-trace merging,
as Step 2.d — so a node only becomes Black once the algorithm has had a
chance to materialise inferred peers and fold inferred nodes into the
observed nodes they describe.

> **As-implemented note.** The current builder computes `SpanFacts.role` at
> classification time and promotes boundaries early (it effectively folds the
> Step 2.d promotion into 2.a). The spec ordering below is the intended
> model; the role assignment itself is unchanged — only *when* the Black
> color is applied moves.

**Step 2.b — Inferred nodes.**
Some agentic spans describe or represent an entity *other than the span's own
node*. When a span carries evidence of such a peer, the algorithm materialises
an **inferred node** for it and connects it with Black edges. Three cases:

1. **Combined source-and-target spans.** A single span records both sides of a
   call — e.g. `ClaudeAgentSDK.query` (and
   `ClaudeAgentSDK.ClaudeSDKClient.receive_response`) in the
   `claude_agent_sdk` framework, which record both the outgoing request to
   the remote LLM and the incoming response. The adapter signals this by
   returning `SpanFacts.is_combined = True` together with a `target_label`
   for the inferred node (typically `llm:<model>`). For each such span,
   create an additional **duplicate node** referencing the same span. The
   **original** node keeps both its parent-side and child-side Gray/White
   chains and plays the **Source** role (`role=BOTH`). The **duplicate**
   node has no neighbours in the base graph and plays the **Target** role
   (`role=TARGET`, same `kind` as the original); its label is
   `SpanFacts.target_label`. Add two directed Black edges between them:
   source→target (request) and target→source (response).

2. **Tool / sub-agent dispatch spans.** A `ClaudeAgentSDK.{tool_name}` (or
   `ClaudeAgentSDK.Subagent`) span records the agent *dispatching* a tool or
   sub-agent; the span name carries the dispatched target's name and
   `agent.name` is set, but the dispatched target emits **no span of its
   own**. The adapter classifies the dispatch span as a **Source** boundary
   (`role=SOURCE`) with `natural_key=agent:<name>`. Because the callee is
   unobserved, the missing peer is materialised as an **inferred Target
   node** of the same `kind` by the one-sided stubbing below, with
   bidirectional Black edges (source→target, target→source). Sub-agent and
   local-tool dispatch take the **same** path — the span-name suffix /
   `agent.name` becomes the inferred peer's identity in both cases. Repeated
   dispatches of the same target from one agent each produce their own
   inferred peer; Step 3.a phase 2 then converges them to a single entity by
   `natural_key`.

   > **As-implemented note.** Implemented. The dispatch span is given
   > `role=SOURCE` in `_ClaudeAgentSDKAdapter`; the inferred Target peer is
   > created by `synthesize_missing_peers` (the one-sided stubbing below).
   > This case depends on the Step 2.d edge rule (below): the dispatch span
   > and its parent agent span are *both* Source boundaries, so the Gray
   > edge between them is **not** promoted to Black — they remain one
   > entity, and only the dispatch→inferred-peer edge is a cross-entity
   > call.

3. **Peers described in span attributes.** An LLM-output span may carry a
   `tool_calls` attribute, e.g.
   `llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments`,
   which describes a tool that was invoked from the LLM output. The span
   itself represents the LLM call; the attribute additionally evidences a
   *tool call* (the source) and the *tool itself* (the target). From it the
   algorithm infers two nodes and three edges: (1) an edge from the current
   span to the tool-call node, (2) an edge from the tool-call node to the
   tool node (the target), and (3) the reverse edge from the tool node back
   to the tool-call node.

   > **As-implemented note.** Case 3 (tool-from-`tool_calls` inference) is
   > **not yet implemented**. It is recorded here as intended design;
   > currently only combined-span duplication (case 1), tool/sub-agent
   > dispatch (case 2), and the one-sided stubbing described below
   > materialise inferred nodes.

When only one side of a protocol call is observed, the missing peer is also an
inferred node: a boundary node that has no observed peer in the trace gets an
inferred node referencing the same span, carrying the originating boundary's
`SpanFacts.natural_key` on a `peer_match_key` field and bidirectional Black
edges (source→target and target→source). When a natural key is available the
inferred node's display label is the key itself (`tool:get_weather`,
`llm:gpt-4o`, …); otherwise it falls back to `(unobserved peer of <source>)`.
The original node retains its Source-or-Target role and `kind`; the inferred
node plays the *opposite* role with the *same* `kind`, so the source→peer pair
satisfies the Step 2.d kind+role-matched edge rule. This one-sided stubbing is
`synthesize_missing_peers` in `builder.py`.

> **Ordering note.** One-sided stubbing depends on knowing a node *is* a
> boundary, which the spec colors in Step 2.d — so this sub-case is logically
> intertwined with boundary detection rather than cleanly preceding it. The
> spec lists it under inferred-node creation (it produces an inferred node);
> the implementation determines boundary-ness from `SpanFacts.role` at
> classification time, so the dependency is satisfied regardless of where the
> Black color is nominally applied. Cases 1 and 3 (combined span,
> `tool_calls`) have no such dependency — they read evidence off the span
> directly. Case 2 (tool/sub-agent dispatch) produces its inferred peer
> *through* this one-sided stubbing, so it shares the same boundary
> dependency.

**Step 2.c — Intra-trace merging.**
Per spec def. 8, this step merges an **inferred** node with the **real
(observed)** node representing the same entity, where both reside in the same
trace. Merging a pair collapses them into a single node, pooling their
**edges** and **attributes**.

Merging is a set of heuristics that identify nodes representing the same
entity. It may draw on:

1. **Proximity in the trace** — an observed node representing the same entity
   as an inferred node is expected to sit close by: a sibling, an ancestor,
   etc.
2. **Similarity of attributes** — e.g. identical tool names, identical
   values.

> **As-implemented note.** This step (inferred↔observed heuristic merging by
> proximity or attribute similarity, in the execution-flow graph) is **not yet
> implemented**; inferred nodes currently pass through to Step 3.a unmerged.
> Implementing it would fold an inferred node into its *observed* twin when one
> exists. It is complementary to — not a substitute for — the Step 3.a phase-2
> combine, which converges repeatedly-called peers by identifying attribute
> even when no observed twin exists.

**Step 2.d — Agentic boundaries (Black).**
Identify the Gray nodes that *represent an agentic boundary* and color them
**Black**. A node is a boundary iff its `SpanFacts.role` is not `NONE` — the
adapter has determined that the span carries explicit call evidence and
represents the source (caller side), the target (callee side), or both sides
of an agentic protocol call. Gray nodes whose role is `NONE` remain Gray:
internal SDK plumbing, lifecycle hooks, framework dispatch, guardrail checks,
custom CHAIN spans (kind=`OTHER`); **and** wrapper spans whose kind is
`AGENT`/`TOOL`/`LLM` but which lack the specific call evidence needed to
assert one side of a call (e.g. a top-level agent-run wrapper that does not
itself carry target identity or payload — typically a more specific child span
is the real boundary). The adapter is the single arbiter of role; the builder
reads only the field. *Node* promotion is driven by `role`, **not** by `Kind`
(see "Boundary promotion is role-driven, not kind-driven" under Key decisions).

Then color the *edges*. A Gray edge whose endpoints are both Black is promoted
to **Black only when the endpoints form a matched call pair**: one endpoint is
exactly `role=SOURCE` (the caller) and the other exactly `role=TARGET` (the
callee), **and** both carry the same `Kind` (tool→tool, llm→llm, agent→agent).
A Black edge means a *cross-entity* call, so this rule keeps two adjacent
Source boundaries on the same Gray chain from being mistaken for a call between
them — e.g. an agent's `ClaudeAgentSDK.query` span and its own
`ClaudeAgentSDK.{tool_name}` dispatch span are *both* Source: the Gray edge
between them stays Gray, and they collapse into the same entity in Step 3.a.
The dispatch's real callee is the inferred Target peer materialised in Step 2.b
(case 2), and *that* source→peer edge is the matched call pair.

`role=BOTH` (a combined source-and-target span) is deliberately **excluded**
from this gray-edge promotion: its target is the duplicate node created in
Step 2.b case 1, wired with Black edges directly — a combined span does not
acquire a target by gray-chain adjacency to an unrelated boundary.

> **As-implemented note.** This rule is `_is_matched_call_pair` in
> `builder.py`, applied in `color_agentic`. To support it, base-graph nodes
> carry the adapter's `role` and `kind` as plain string fields (mirrored from
> the `Role`/`Kind` str-enums to avoid an import cycle). The earlier ADR draft
> promoted *every* Gray edge between two Black endpoints; that blanket rule
> produced spurious cross-entity edges between an agent and its own dispatch
> spans, which is what this rule fixes.

**Step 3 — Agentic entity graph.**
Step 3 derives the entity graph from the colored execution-flow graph in two
sub-steps: form-and-combine entities (3.a) and name them (3.b). Per spec
Step 3, this is where multiple execution-graph nodes representing the *same
entity* are combined into one.

**Step 3.a — Creating the entity graph.**
This sub-step has two phases.

*Phase 1 — component → entity.* Compute connected components over the set of
Gray and Black nodes, considering only White and Gray edges (Black edges are
ignored for this purpose). Each connected component becomes one **entity
node** — combining every execution-graph node in the component (inferred,
observed, or both) into a single entity. Attributes from every span in the
component are pooled onto the entity node. Each Black edge in the colored
graph becomes a directed edge in the entity graph between the entity nodes
containing its endpoints. Inferred nodes propagate their inferred marker onto
the entity node they form via a dedicated boolean field (`inferred`) on the
entity node — not via label inspection. An entity is `inferred = true` iff
every absorbed node was an inferred node.

*Phase 2 — combine same-entity nodes by identifying attribute.* After phase 1,
the entity graph can still hold several entity nodes that represent the *same
real entity*. This happens whenever the same peer is the target of several
calls: Step 2.b materialises one inferred node per call site (e.g. one tool
invoked from two agents → two inferred `tool:get_weather` nodes), and phase 1
turns each into its own entity. Combine entity nodes that share an
**identifying attribute** — the boundary's `natural_key` (`tool:<name>`,
`llm:<model>`, `agent:<name>`) — into a single entity. **All edges are
maintained**: every Black-derived edge incident on any combined node is
rewritten onto the survivor, none dropped or deduplicated, so a tool called
from two sources yields one entity with two edges (preserving the count and
provenance of calls). Entity nodes with no identifying attribute are not
combined; they remain distinct.

This phase-2 combine is what makes a repeatedly-called peer converge to one
entity **even when that peer is never observed** in the trace — the case
Step 2.c (which needs an observed node to merge into) cannot resolve. The two
are complementary: Step 2.c folds an inferred node into an *observed* twin
when one exists; Step 3.a phase 2 combines entity nodes that share an
identifying attribute regardless of whether any was observed.

> **As-implemented note.** Phase 1 is `build_entity_graph`; phase 2 is the
> `merge_inferred_peers` pass (`builder.py`), which combines entity nodes by
> `peer_match_key` (= the `natural_key`) and preserves all incident edges.
> Both are implemented and exercised by the canonical-trace test (5 entities /
> 18 interactions). Today phase 2 only combines *inferred* entities; the spec
> phrases the combine generally ("based on an identifying attribute"), so
> extending it to observed entities sharing a key is a possible later
> broadening, not a current behavior.

**Step 3.b — Naming nodes.**
Each entity node should be given a key reflecting its originating subgraph,
drawn from one of the subgraph's node spans. In particular, if a node in the
subgraph carries a **hostname**, use it as the key. When no clear key is
available, the entity is named `unknown`.

> **As-implemented note.** The current implementation assigns `unknown` to
> every entity. Richer naming — deriving an ID from the entity's pooled
> attributes (hostname from non-agentic enrichment, service name, model/tool
> name, etc.) — is deferred; the most useful identifier (hostname) lives on
> non-agentic spans that today are not part of the entity-forming subgraph.
> See "Deferred to later stages".

## Annotations

**Flagging unexpected agentic spans between boundaries.**
Any Gray node that lies on a Gray chain between two Black boundary nodes is
annotated and surfaced in the "Graphs (proto)" UI tab. The intent is to catch
agentic spans that the classifier does not yet recognise as boundaries —
treating the in-between span as a signal that the per-scope span tables or
classifier may be incomplete. The annotation is informational only; it does
not block entity formation.

## Observations and assumptions

These are the protocol-level expectations the spec relies on. They motivate
the consecutive-send/receive flagging (Annotations) and the inferred-peer /
split-graph handling, and they bound where the algorithm is expected to work.

- **Matched send/receive.** When all events are received, a protocol
  interaction shows up as a send event from one entity and a matching receive
  event from another. The two are expected to be **consecutive** in the trace
  — if an unexpected agentic span sits between them, the algorithm should be
  able to identify and flag it (this is what the between-boundary annotation
  catches).
- **Combined send-and-receive spans exist.** Some frameworks (e.g. Google
  ADK LLM spans, `ClaudeAgentSDK.query`) emit a single span representing both
  the send and the receive. These are handled as combined source-and-target
  spans (Step 2.b case 1).
- **Interleaved sources represent the same entity.** With multiple
  instrumentation sources (a2a and httpx, …) and traceparent on, events
  interleave: `a2a tool call → http send → … → http receive → a2a call
  receive`. The a2a-call and http-send events both represent the *same*
  caller entity; both receive events represent the *same* callee entity. This
  is the basis for the deferred cross-scope reconciliation.
- **Missing instrumentation splits the graph.** If a component emits no
  events, only one side of the interaction is seen and the graph splits. If a
  component emits only *some* sources (e.g. a receiver with no a2a events:
  `a2a tool call → http send → … → http receive |`), traceparent is not
  forwarded, producing two traces and a split graph. Step 2.b's inferred peers
  stub the missing side within a trace; cross-trace stitching is deferred to
  Step 4.
- **Events between a receive and a send belong to one entity.** All events
  observed between a component's receive and its subsequent send belong to the
  same entity — which is why a connected Gray/White component collapses to a
  single entity in Step 3.a.

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

**Boundary-node promotion is role-driven, not kind-driven; edge promotion
is kind+role-matched.**
A Gray *node* is promoted to Black iff the adapter assigned a non-`NONE`
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
`ClaudeAgentSDK.query`, `ClaudeAgentSDK.{tool_name}`).

For LLM-kind spans the rule is more permissive: `role=SOURCE` is
assigned even when both `llm.input_messages` and `input.value` are
absent. Empty payloads on an LLM-kind span are an instrumentation gap,
not absence of a call — the kind itself is sufficient call evidence.
This asymmetry with AGENT/TOOL is deliberate: AGENT-kind has too many
wrapper-shape false positives to treat kind alone as evidence; LLM-kind
does not. The `ClaudeAgentSDK.{tool_name}` dispatch span is a deliberate
AGENT-kind exception: the framework-specific span-name signal is itself
the call evidence (it names the dispatched target), so the adapter
assigns `role=SOURCE` there.

While *node* promotion ignores `Kind`, *edge* promotion (Step 2.d) does
use it: a Gray edge between two Black nodes becomes Black only for a
`SOURCE`↔`TARGET` pair of the **same** `Kind`. `Kind` here disambiguates
which adjacent boundaries form a genuine call (a tool call paired with a
tool, an LLM call with an LLM) from two same-entity Source spans that
merely sit next to each other on the chain. See Step 2.d.

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
The inferred-peer merge key produced by an adapter has a fixed format:
`tool:<name>`, `llm:<model>`, `agent:<name>`. The prefix doubles as the
entity's coarse kind in the extractor (`_kind_from_label`) and as the
inferred node's display label when no friendlier label is available.
Adapters strip provider prefixes from model strings (e.g.
`anthropic/claude-3-7` → `claude-3-7`) so the key is the model alone, not
the provider-qualified name. Hostname / `service.name` fallbacks are
deliberately *not* used as keys — they would over-merge across distinct
entities behind the same proxy.

**Two complementary places where nodes representing the same entity combine.**
Convergence of same-entity nodes is not one operation but two, at different
stages and on different graphs:

- **Step 2.c — inferred↔observed merge, execution-flow graph (spec def. 8).**
  Collapse an **inferred** node into the **observed** node representing the
  same entity, in the same trace, using proximity in the trace and attribute
  similarity. The intent is that an inferred stub does not sit beside its
  observed twin. **Not yet implemented**; inferred nodes pass through to
  Step 3.a unmerged.
- **Step 3.a phase 2 — combine by identifying attribute, entity graph (spec
  Step 3).** After components are turned into entities, combine entity nodes
  that share an identifying attribute (the boundary `natural_key`). This is
  what converges a peer that is *called repeatedly but never observed* — the
  case Step 2.c cannot handle because there is no observed node to merge into.
  **Implemented** (`merge_inferred_peers`), and required by the
  canonical-trace test.

These are complementary, not redundant: 2.c removes an inferred node in favour
of a real one (needs an observation); 3.a phase 2 fuses entities that share a
key (needs no observation). The earlier ADR draft asserted "no inferred↔
inferred de-duplication" and that unobserved repeated peers stay distinct —
that was wrong; the spec's Step 3.a explicitly combines them by identifying
attribute, and the code already does so.

**Inferred peers are created in the core algorithm, not deferred.**
When only one side of a protocol call is observed, Step 2.b materialises an
inferred node for the unobserved peer (recorded with the dedicated
`is_inferred` boolean field on unmerged survivors — see "Inferred identity
is a boolean field, not a label convention" below) and connects it with
bidirectional Black edges. This keeps the entity graph shape-consistent —
every observed boundary participates in a complete source/target pair — and
lets downstream consumers distinguish observed entities from inferred ones
via the marker. The alternative (leave the lone boundary edgeless and stub
later) was rejected because it would leave the entity graph topologically
inconsistent across observed-both-sides vs observed-one-side cases.

**Inferred identity is a boolean field, not a label convention.**
An inferred node that survives without being merged into an observed node is
identified by a dedicated boolean field on the node row — `is_inferred` on
the colored-base-graph node and `inferred` on the entity node — and never by
parsing the `label` column or any other display string. The label is a
human-facing display value (e.g. `"(unobserved peer of
dl-demo-travel-advisor)"`) and is free to change for UX reasons; queries and
downstream processors must filter on the boolean field. The scratch-table
schemas in `cli.py` carry this column explicitly so external SQL inspection
has a typed signal rather than a string-pattern heuristic. (These columns are
named `is_inferred` on the node row and `inferred` on the entity row — see the
naming note in Definitions.)

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
1.4.1 — the version that produced the canonical live trace;
`openinference_anthropic_v1.0.6_telemetry_spans.md` for the anthropic /
`claude_agent_sdk` framework at 1.0.6; future references for
httpx/starlette/etc.). New attributes are not added on
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
- **Richer entity naming.** Step 3.b assigns `unknown` to every entity. A
  later stage will derive an ID from the entity's pooled attributes —
  hostname (from non-agentic httpx/starlette enrichment),
  `service.name` (OTel resource), or framework-specific attributes
  (`llm.model_name`, `tool.name`, `agent.name`, …). This is deferred until
  the cross-scope enrichment stage exists, since the most useful identifier
  (hostname) lives on non-agentic spans that today are not part of the
  entity-forming subgraph.
- **Inferring tool nodes from `tool_calls` attributes.** An LLM-output span's
  `llm.output_messages.*.message.tool_calls.*` attributes evidence a tool
  call and the tool itself (Step 2.b case 3). Materialising those inferred
  tool-call / tool nodes and their edges from span attributes is intended
  design but not yet implemented.
- **Step 2.c inferred↔observed merging.** The spec's Step 2.c — collapsing an
  inferred peer into the observed node representing the same entity, by
  proximity in the trace and attribute similarity — is specified but **not yet
  implemented** (inferred nodes pass through to Step 3.a). This is distinct
  from the Step 3.a phase-2 combine (combine by identifying attribute), which
  *is* implemented and converges repeatedly-called peers even when never
  observed.
- **Step 4 (spec) — system graph.** The spec's Step 4 (deferred) builds a
  cross-trace "system graph" and groups:
  - **Inter-trace merging.** Merging an inferred node in one trace with an
    observed node in another — which can happen when traceparent is not
    propagated and a single logical interaction is split across two traces
    (so the graph is split). The cross-trace analogue of Step 2.c.
  - **Align names across executions.** Reconciling entity identifiers across
    different traces / runs of the same system.
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
  graph (after Step 1), the colored base graph (after the Step 2 coloring
  passes — Gray coloring, inferred/duplicate nodes, boundary promotion —
  including combined-span duplicates, inferred peers, and between-boundary
  flag annotations), and the entity graph (after Step 3). All three are
  surfaced in the "Graphs (proto)" tab of the trace-tree UI.
- A trace captured only as a test fixture (the extractor's tests run it as a
  pure function over `fixtures/*.json`, never touching Postgres) is not
  visible in the UI, because the CLI sources its spans *from* the `spans`
  table. The throwaway helper
  `data_governance.processors.p_interactions_proto.load_fixture` bridges this:
  it inserts a fixture's spans into `spans` (via the receiver's own
  `write_span`, so insertion is idempotent) and then runs the normal CLI
  processor over that trace, populating `proto_*` *and* satisfying the UI's
  `proto_interaction_spans → spans` evidence join. It mutates whatever
  `DATABASE_URL` points at, so it is disabled by default and refuses to run
  unless `PI_LOAD_FIXTURE_CONFIRM=1` is set (see its module docstring).
- The colored-base-graph node row (`proto_colored_nodes`) carries an
  `is_inferred boolean NOT NULL DEFAULT false` column, and the entity-node
  row (`proto_entity_nodes`) carries an `inferred boolean NOT NULL DEFAULT
  false` column. These are the sole sanctioned signals for "is this an
  inferred (unobserved-peer) node?" — the `label` column is display-only and
  must not be parsed for this purpose. The difference in column name
  (`is_inferred` on the node table vs. `inferred` on the entity table)
  follows the existing `is_*` / `contains_*` convention on each table; on the
  `/proto/graphs/{trace_id}` wire the base/colored graphs expose `is_inferred`
  and the entity graph exposes `inferred`, and the UI reads whichever the
  graph carries.
- The "Graphs (proto)" UI surfaces inferred peers via an `inferred` marker
  pill (`marker-inferred`) alongside the existing `boundary`, `target`, and
  `flagged` pills, and shows a count in the colored-graph and entity-graph
  summary lines. The pill is rendered by reading the boolean field; the
  label string is never inspected.
- Non-agentic scopes contribute no entities at this stage. Scopes that emit
  no agentic spans at all will not appear in the entity graph until the
  enrichment stage runs.
