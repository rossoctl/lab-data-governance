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

**Roles** (what the node represents) — following the spec's definitions
(defs. 1, 2, 5) verbatim where it gives them:
- **Event node** — *"a node representing a local entity"* (spec def. 1),
  recorded by an observed span.
- **Source node** / **Target node** — *"Source / Target nodes representing
  a local and remote entity"* (spec def. 2). The Source node is the caller
  side of an agentic protocol call (the local entity initiating the call);
  the Target node is the callee side (the remote entity receiving the call).
- **Agentic boundary** — *"a boundary is (node) source calling an agent, a
  tool, an LLM or another service, or a target of such a call"* (spec
  def. 5). A boundary is therefore any node playing the Source or Target
  role; Step 3.b colors exactly these nodes Black.

A span's role is assigned by its `(scope, framework)` adapter and surfaced
on `SpanFacts.role` (`SOURCE` / `TARGET` / `BOTH` / `NONE`). `NONE` means
the span is not a call boundary — typically a wrapper / runner /
per-activation span that lacks the specific call evidence (target
identity, request/response payload, or a framework-specific span-name
signal) needed to assert one side of a call. The Step 3.b promotion to
Black is driven by `role`, **not** by `Kind` (see "Boundary promotion is
role-driven, not kind-driven" under Key decisions).

**Provenance** (how the node came to exist) — following the spec's
definitions verbatim:
- **Observed (real) node** — a node backed by an emitted span.
- **Inferred node** — *"a node in the graph we know should exist although we
  don't have a span emitted representing that node"* (spec def. 7). An
  agentic span can describe an entity other than itself: an LLM `query` span
  describes the LLM it called; a `tool_calls` attribute on an LLM-output span
  describes a tool that was invoked. Step 2.a materialises such peers as
  inferred nodes. An inferred node may later be **merged** with another node
  representing the same entity — an observed node or another inferred node —
  in Step 4.
- **Inferred edge** — *"an interaction in the graph we know should exist
  although we don't have a span representing this interaction"* (spec def. 8,
  added by the latest spec). The Black request/response edges Step 2.a draws
  between an observed boundary and its inferred peer are inferred edges; so
  are the three edges of the case-3 `tool_calls` triple. An inferred edge
  carries an explicit derivation **order** (see "Inferred interaction
  ordering" under Step 2.a).
- **Duplicate node** — an additional node referencing the same span as an
  existing boundary node, created by Step 2.a to split a combined
  source-and-target span into two role-distinct nodes. (A duplicate is a
  special case of inferred node: the peer is described by the *same* span
  rather than a separate one.)

**merge** — *"the process of merging nodes and edges representing the same
exact entity and interaction"* (spec def. 9; the latest spec broadened this
from "collapsing inferred nodes with real nodes" to cover **edges** as well as
nodes, and all three provenance combinations). Merging pools the edges and
attributes of the collapsed nodes and is the subject of the consolidated
Step 4 — *"the process starts with merging nodes. Next the process continues
with merging edges."*

**fuse** — *"the process of collapsing multiple nodes together to represent a
single entity"* (spec def., added alongside the def. 9 broadening). Fuse is
distinct from merge: merge identifies *the same* entity/interaction across
nodes/edges (Step 4); fuse is the Step 5.a operation that collapses every
execution-graph node of one connected subgraph into a single **entity** node,
regardless of whether those nodes represent the same fine-grained entity. The
spec's Step 5 text now reads "multiple nodes … are **fused**" where it
previously said "combined".

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

**Edges** — following the spec's definitions (defs. 3, 4, 6):
- **White edge** — *"parent child relationship based on trace parent"*
  (spec def. 3).
- **Gray edge** — *"the (grand-)parent child relationship in agentic
  scoped events"* (spec def. 4): the order of events considering only
  agentic-scoped (Gray) nodes. Because the immediate traceparent parent of
  one Gray node may be a non-agentic (White) node, the Gray edge spans the
  transitive (grand-)parent chain — it connects two Gray nodes whose
  underlying White chain does not pass through another Gray node.
- **Black edge** — *"a source/target across agentic entities/components/
  containers"* (spec def. 6).

A Black boundary node typically *plays* the Source or Target role depending on
which side of the call its span represents. For the combined-span (Step 2.a
case 1) and one-sided-stub cases, the duplicate / inferred node is created to
fill the *opposite* role of an existing boundary node. Attribute-derived
inferred nodes (Step 2.a case 3 — tool nodes inferred from an LLM span's
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
  Drives Step 3.b Black promotion. `Role.NONE` keeps the node Gray.
- `is_combined` — true iff one span carries BOTH the source and target side
  of the same call. Triggers Step 2.a duplication.
- `natural_key` — stable per-boundary identity. Format is
  `<kind-prefix>:<identifier>` — `tool:<tool-name>`, `llm:<model>`,
  `agent:<agent-name>`. It is the **identifying attribute** the Step 4
  same-entity merge groups on (carried on the inferred node as
  `peer_match_key`) — both for the inferred↔observed merge and for converging
  repeatedly-called inferred peers. None when the span carries no identifying
  attribute (those nodes are not combined).
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
boundaries that Step 2.a will stub with an inferred peer.

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

> **Spec step order (latest revision).** The human spec was restructured into
> five top-level steps, and the ADR follows that numbering here:
> **Step 1** base graph → **Step 2** extend the graph per scope (2.a derives
> inferred nodes *and edges* from the openinference scope; 2.b other scopes,
> deferred) → **Step 3** enrich with agentic semantics (3.a color Gray, 3.b
> agentic boundaries / Black) → **Step 4** node-and-edge merge **on the
> execution-flow graph** (all three provenance combinations, nodes first then
> edges) → **Step 5** agentic entity graph (5.a fuse components into entities,
> 5.b name them). This is a
> renumbering of the previous draft (which carried coloring, inference, merge,
> and boundaries all under Step 2 as 2.a–2.d and the entity graph as Step 3);
> the *operations* are unchanged, only their grouping and numbers. As-implemented
> notes map each spec step to the builder functions, whose in-code comments
> still use the older 2.x labels.

**Step 1 — Base graph.**
Construct one node per span and one White directed edge from each span's parent
to itself, following the OTel traceparent relationships. The result is a single
graph for the entire trace whose connectivity mirrors trace structure exactly.
All nodes and edges start White.

**Step 2.a — Inferred nodes and edges (openinference scope).**
The spec's Step 2 *extends* the execution-flow graph per scope; Step 2.a handles
the **openinference** agentic scope (Step 2.b — other scopes — is deferred, see
"Deferred to later stages"). Some agentic spans describe or represent an entity
*other than the span's own node*. When a span carries evidence of such a peer,
the algorithm materialises an **inferred node** for it and connects it with
Black **inferred edges**. Three cases:

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
   inferred peer; the Step 4 same-entity merge (Phase A2) then converges them by
   `natural_key` on the execution-flow graph — see "Same-entity merging — one
   Step-4 pass" under Key decisions.

   > **As-implemented note.** Implemented. The dispatch span is given
   > `role=SOURCE` in `_ClaudeAgentSDKAdapter`; the inferred Target peer is
   > created by `synthesize_missing_peers` (the one-sided stubbing below).
   > This case depends on the Step 3.b edge rule (below): the dispatch span
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
   > implemented for the `anthropic` framework by
   > `builder.infer_tool_calls_from_attributes`: the adapter surfaces
   > output-side tool calls on `SpanFacts.tool_calls`, and the builder
   > materialises the tool-call node (source, folds into the LLM entity via a
   > Gray edge) and the inferred tool node (target) with the request/response
   > Black edges. It is **gated per-adapter**: only adapters that populate
   > `tool_calls` trigger it, so frameworks that emit a real tool-execution
   > span (e.g. openai_agents, whose LLM spans *also* carry output
   > `tool_calls` for the same tool) are not double-counted. Combined-span
   > duplication (case 1), tool/sub-agent dispatch (case 2), and the one-sided
   > stubbing below also materialise inferred nodes.
   >
   > Both the **output** and **input** sides are now read. Input-side tool
   > calls (`llm.input_messages.*.message.tool_calls.*`) are a prior turn's
   > tool use replayed back into the request; the anthropic adapter surfaces
   > them on `SpanFacts.input_tool_calls` and the builder materialises them the
   > same way, ordered *ahead of* the LLM interaction (see "Inferred
   > interaction ordering" below). Per the human spec every input-side tool is
   > inferred — including a replay of a call already seen on a prior span's
   > output — since the replay is a genuine prior interaction fed back into the
   > turn.

**Inferred interaction ordering.**
The spec (`p_interactions_alg.md`, "Inferred edges ordering" under
Step 2.a) requires that inferred edges carry a deterministic order based on
**execution order**, because several inferred interactions are derived from a
*single* span and therefore share that span's timestamp — `started_at` alone
cannot order them. The rules:

1. **Calls before responses.** For any inferred pair, the outgoing edge (the
   call: source→target) precedes the incoming edge (the response:
   target→source).
2. **Case-3 edge order.** For a tool inferred from an LLM span (case 3): the
   `current LLM span → tool-call` edge precedes the `tool-call → tool` edge;
   and the `tool-call → tool` edge (the call) precedes the `tool → tool-call`
   edge (the reverse / response). (This is rule 1 applied within the case-3
   triple.)
3. **Input-derived tools before the LLM call.** A tool evidenced on the LLM
   span's *input* messages (`llm.input_messages.*.message.tool_calls.*`) was
   invoked on a *prior* turn whose result is being fed back in; its inferred
   interaction is ordered **before** the interaction with the LLM.
4. **Output-derived tools after the LLM call.** A tool evidenced on the LLM
   span's *output* messages (`llm.output_messages.*.message.tool_calls.*`) is
   what the model asked to invoke *as a result of* this call; its inferred
   interaction is ordered **after** the interaction with the LLM.

So a single LLM turn expands, in order, to: *(input-derived tool calls) →
(the agent↔LLM call/response) → (output-derived tool calls)*, with each
call immediately preceding its own response per rule 1.

**Representation (design decision).** Ordering is carried as an explicit
integer order field on the derived interaction, assigned at derivation time,
and the CLI/API sort by `(started_at, order)` rather than `started_at` alone.
A derivation-order-only tiebreak was rejected as too fragile — any consumer
that re-sorts (the API already issues `ORDER BY started_at`) would silently
lose the contract; an explicit field survives the SQL round-trip and is
inspectable.

> **As-implemented note.** **Implemented.** (a) The explicit order field is a
> plain integer carried on the base-graph `Edge` (`Edge.order`), stamped at
> Black-edge creation in `duplicate_combined_nodes`, `synthesize_missing_peers`,
> and `infer_tool_calls_from_attributes`, copied onto `EntityEdge.order` in
> `build_entity_graph`, and surfaced as `ProtoInteraction.order` and the
> `proto_interactions."order"` column. The CLI sorts by `(started_at, order)`
> and the API issues `ORDER BY started_at, "order"`. The band scheme realises
> the rules directly: input-derived tools sit at `-40 + 2k` (call) / `+1`
> (response), the agent↔LLM / combined / one-sided call at `0` / `1`, and
> output-derived tools at `40 + 3k` / `+1` — so negative < 0/1 < positive
> encodes rules 3, 1, 4. (b) Input-side tool inference now exists:
> `infer_tool_calls_from_attributes` reads both `SpanFacts.tool_calls`
> (output, positive band) and `SpanFacts.input_tool_calls` (input, negative
> band), the latter populated by the anthropic adapter from
> `llm.input_messages.*.message.tool_calls.*`.

When only one side of a protocol call is observed, the missing peer is also an
inferred node: a boundary node that has no observed peer in the trace gets an
inferred node referencing the same span, carrying the originating boundary's
`SpanFacts.natural_key` on a `peer_match_key` field and bidirectional Black
edges (source→target and target→source). When a natural key is available the
inferred node's display label is the key itself (`tool:<tool-name>`,
`llm:<model>`, …); otherwise it falls back to `(unobserved peer of <source>)`.
The original node retains its Source-or-Target role and `kind`; the inferred
node plays the *opposite* role with the *same* `kind`, so the source→peer pair
satisfies the Step 3.b kind+role-matched edge rule. This one-sided stubbing is
`synthesize_missing_peers` in `builder.py`.

> **Ordering note.** One-sided stubbing depends on knowing a node *is* a
> boundary, which the spec colors in Step 3.b — so this sub-case is logically
> intertwined with boundary detection rather than cleanly preceding it. The
> spec lists it under inferred-node creation (it produces an inferred node);
> the implementation determines boundary-ness from `SpanFacts.role` at
> classification time, so the dependency is satisfied regardless of where the
> Black color is nominally applied. Cases 1 and 3 (combined span,
> `tool_calls`) have no such dependency — they read evidence off the span
> directly. Case 2 (tool/sub-agent dispatch) produces its inferred peer
> *through* this one-sided stubbing, so it shares the same boundary
> dependency.

**Step 3.a — Agentic coloring (Gray).**
The spec's Step 3 enriches the (already extended) execution-flow graph with
agentic semantics. Step 3.a colors the agentic-scope nodes Gray. Coloring is
additive: White edges are never removed when Gray edges are added, and Gray
edges are never removed when Black edges are added (Step 3.b). The base graph's
full White connectivity is preserved throughout.

This stage currently handles the **openinference** agentic scope only. Support
for additional agentic scopes (a2a, mcp) is deferred — see "Deferred to later
stages" below.

1. For every span belonging to the openinference instrumentation scope, color
   the node **Gray**.
2. For every pair of Gray nodes connected by a chain of White edges that does
   not pass through another Gray node, add a directed **Gray edge** between
   them in the same direction as the underlying chain. The White chain is
   followed **regardless of the intermediate nodes' scope** (latest spec, Step
   3.a.3): the bridge between two agentic nodes may run through non-agentic
   (httpx / starlette / a2a) spans — including spans in a *different service* —
   and a Gray edge is still drawn across it as long as no Gray node lies in
   between. This is what lets an agent's call boundary connect, in the Gray
   layer, to the boundary of the *remote* agent it called over A2A: the
   intervening client-send (`httpx POST`) and server-receive (`starlette POST
   /`) spans are White and do not interrupt the chain.

> **As-implemented / ordering note.** In the spec's document order Step 2.a
> (inferred nodes/edges) precedes this coloring, but inference logically reads
> agentic-scope evidence off the spans — so the builder colors Gray first and
> then materialises inferred nodes/edges in the same pass. The builder also
> computes `SpanFacts.role` at classification time and applies the Step 3.b
> Black promotion in the same `color_agentic` pass. The role assignment and the
> set of Gray/Black nodes are unchanged by the renumbering; only the spec's
> grouping of these operations into Step 2 vs. Step 3 moved.
>
> **"Regardless of scope" — partially exercised.** The builder's Gray-edge
> traversal already walks the White chain without consulting intermediate
> nodes' scope, so the spec's 3.a.3 wording matches the code mechanically. But
> in every fixture to date the bridge between two agentic nodes stays *within
> one service* (the openinference spans of a single agent), so the
> cross-service case the new wording targets — a Gray edge spanning the
> `httpx → starlette` A2A bridge between two *different* agents — is not yet
> realised end-to-end: producing the cross-entity **Black** edge from it
> additionally needs the Step 3.b agent-root boundary + "related target"
> binding below, which are spec-level and not yet implemented. See "Cross-scope
> multi-agent composition (A2A)" under Deferred to later stages.

**Step 3.b — Agentic boundaries (Black).**
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

**Agent-root spans as target boundaries (latest spec — not yet implemented).**
The latest spec broadens the boundary node list (Step 3.b clause 1) to include,
alongside tool targets / agent calls / llm calls, the **agent root span**:
the *outermost* per-activation span an agentic framework emits for an agent
run, which represents the agent's **entry / target** side. The spec requires
this be identified **using span kind and attributes only** — i.e. span-locally,
without a tree walk — which is per-framework adapter logic, since the marker
differs by framework (openai_agents: the AGENT-kind span carrying **no**
`graph.node.id` — the inner per-node AGENT span *has* `graph.node.id`;
google_adk: `agent_run [{agent.name}]`; langchain: the `agent` span; validated
against the per-scope span references). Promoting the callee's agent-root span
to a **Target** boundary is what gives a cross-agent delegation an *observed*
target to pair with, instead of the inferred peer Step 2.a stubs today. This is
the callee-side complement to the existing source-side boundaries; the current
code does **not** implement it (AGENT-kind wrappers map to `role=NONE`, so a
delegated-to agent's root stays Gray and the delegation resolves to an inferred
`tool:` peer — see "Cross-scope multi-agent composition (A2A)" under Deferred).

Then color the *edges*. A Gray edge whose endpoints are both Black is promoted
to **Black only when the endpoints form a matched call pair**. The matched-pair
rule has two formulations:

- **As implemented (`_is_matched_call_pair`):** one endpoint is exactly
  `role=SOURCE` (the caller) and the other exactly `role=TARGET` (the callee),
  **and** both carry the same `Kind` (tool→tool, llm→llm, agent→agent).
- **Latest spec:** one endpoint is a **source** and the other **its related
  target in the agentic scope** — the same-`Kind` requirement is **dropped**
  and the same-scope requirement is **not** imposed (the pair is "in the
  agentic scope", not "in the *same* scope"), so a cross-framework
  **`tool call → agent`** delegation (e.g. an openai_agents `delegate_to_*`
  tool-call source paired with a langchain / google_adk agent-root target) is
  now an admitted pair, not only `tool→tool` / `llm→llm` / `agent→agent`.
  The binding is the source and **its related target** (not merely *a*
  target); what makes a target "related" is left open by the spec and is not
  recorded here.

A Black edge means a *cross-entity* call, so the rule keeps two adjacent
Source boundaries on the same Gray chain from being mistaken for a call between
them — e.g. an agent's `ClaudeAgentSDK.query` span and its own
`ClaudeAgentSDK.{tool_name}` dispatch span are *both* Source: the Gray edge
between them stays Gray, and they collapse into the same entity in Step 5.a.
The dispatch's real callee is the inferred Target peer materialised in Step 2.a
(case 2), and *that* source→peer edge is the matched call pair.

`role=BOTH` (a combined source-and-target span) is deliberately **excluded**
from this gray-edge promotion: its target is the duplicate node created in
Step 2.a case 1, wired with Black edges directly — a combined span does not
acquire a target by gray-chain adjacency to an unrelated boundary.

> **As-implemented note.** The implemented rule is `_is_matched_call_pair` in
> `builder.py`, applied in `color_agentic`. To support it, base-graph nodes
> carry the adapter's `role` and `kind` as plain string fields (mirrored from
> the `Role`/`Kind` str-enums to avoid an import cycle). The earlier ADR draft
> promoted *every* Gray edge between two Black endpoints; that blanket rule
> produced spurious cross-entity edges between an agent and its own dispatch
> spans, which is what this rule fixes. The blanket-promotion hazard is large
> in practice: on a multi-agent A2A trace, the overwhelming majority of
> Gray-only edges are *intra-agent* (an agent's internal Gray hops over its own
> non-agentic plumbing) and only a handful cross a service boundary — promoting
> all of them would shatter each agent into one entity per internal Gray hop.
> The matched-pair guard isolates the genuine cross-entity edges from the
> intra-agent ones.
>
> **Spec ahead of code (Step 3.b).** The latest spec adds two things this note's
> rule does not yet do: (1) **agent-root target boundaries** (above), and
> (2) the **same-`Kind`-dropped, "related target"** edge rule admitting
> `tool→agent`. Until both land, a cross-agent A2A delegation still resolves to
> an inferred `tool:` peer rather than a Black edge to the observed callee
> agent. Tracked under "Cross-scope multi-agent composition (A2A)" in Deferred.

**Step 4 — Node and edge merge.**
Per spec def. 9, Step 4 identifies nodes and/or edges that *represent the same
entity or interaction* and merges them. The latest spec **consolidated** what
the previous draft split between an intra-trace node merge (old Step 2.c) and
the entity-graph combine (old Step 3.a): merging is now one named step covering
**all three** provenance combinations — inferred↔inferred, inferred↔observed,
**and** observed↔observed — and explicitly covering **edges** as well as nodes.
The spec prescribes an order: *"the process starts with merging nodes. Next the
process continues with merging edges."*

Merging is a set of heuristics that identify nodes/edges representing the same
entity or interaction. It may draw on:

1. **Similarity of node attributes** — e.g. identical tool names hint that two
   nodes represent the same tool (entity) and should be merged. The edges are
   maintained: the source/target of an incident edge is re-pointed onto the
   survivor (and edges may themselves merge — see below).
2. **Similarity of edge attributes** — two edges with similar arguments and the
   *same source and same target* (e.g. a tool call with the same arguments) can
   represent a single interaction, hinting the two edges should be merged.

Additional hints: **proximity in the trace** (an observed twin of an inferred
node is expected to sit close by — a sibling, an ancestor), **same/similar
time**, and **whether the node or edge is inferred or observed** in conjunction
with the source spans.

The three node-merge combinations:

- **inferred ↔ observed** — fold an inferred peer into the observed node for
  the same entity (the canonical "an inferred stub should not sit beside its
  observed twin" case).
- **inferred ↔ inferred** — converge multiple inferred peers that stub the
  *same* unobserved entity (e.g. one tool invoked from several call sites,
  each materialising its own inferred peer in Step 2.a).
- **observed ↔ observed** — collapse observed nodes/entities that represent the
  same service process but were split across White+Gray components.

Per the spec, Step 4 runs on the **execution-flow graph** — between boundary
coloring (Step 3) and entity formation (Step 5) — so that the fuse in Step 5.a
operates on an already-merged graph.

> **As-implemented note.** Step 4 is the single function `merge_step4`
> (`builder.py`), run on the execution-flow graph after Step 2.a inference and
> before the Step 5.a fuse (and before the colored-graph snapshot, so the
> snapshot reflects every merge). It does **node merge then edge merge**, as the
> spec prescribes.
>
> **Phase A — node merge**, three sub-passes (each collapses same-entity nodes
> into one survivor, pooling attributes/label, rewiring incident edges, dropping
> self-loops; an observed survivor is preferred over an inferred one):
> - **A1 inferred ↔ observed.** Fold an inferred peer into an *independently
>   observed* node for the same entity: matching typed key + kind, and the
>   observed twin in a *different* White+Gray component than the peer's source
>   (so it is the observed callee, not a sibling caller). No-op on single-agent
>   fixtures (every same-key boundary is a sibling caller); fires on a split
>   graph where the callee emitted its own spans.
> - **A2 inferred ↔ inferred.** Converge typed callee peers (TARGET role; key
>   prefix matching kind, via `_typed_callee_key`) that share a key — the
>   repeatedly-called peer, e.g. the per-call `tool:<name>` peers a trace
>   produces when one tool is invoked from several call sites. TARGET-only and
>   prefix-matches-kind are the guards
>   that keep a *caller's* `natural_key` (the agent's `llm:` / `tool:` key) and
>   the Gray-folded tool-call SOURCE nodes from fusing a caller into its callee.
> - **A3 observed ↔ observed.** The same service split across White+Gray
>   components (e.g. raw-anthropic per-turn LLM-source spans joined only by a
>   White non-agentic parent). Keyed on `service.name`, gated to **keyless**
>   observed boundary callers, and only across *different* entity components.
>   Realised as a **fuse-time forced grouping** rather than a node merge: it
>   records `node_id → group` and hands it to `build_entity_graph`, which fuses
>   those components into one entity *without touching nodes or edges* — so every
>   call site keeps its own boundary span (the per-call payload/evidence anchor).
>   Exercised by a single-agent anthropic fixture (two same-service agent
>   components collapse to one entity).
>
> **Phase B — edge merge.** Collapse Black edges that are the *same*
> interaction: same connected-entity (component) pair **and** same logical call
> — equal request arguments, else equal `tool_call.id`. (Arguments are the
> primary key; a replayed call carries identical arguments whether it appears on
> a span's output or a later span's input, even though those spans don't overlap
> in time, so time is *not* required to match.) Genuinely distinct calls differ
> in arguments and survive — multiple LLM calls and repeated tool calls with
> *different* arguments all keep distinct arguments and remain distinct
> interactions. This is what collapses a tool-call replay (the same call seen as
> a span's output and again on a later span's input, carrying the same
> `tool_call.id`) from two interactions to one, while a distinct call with the
> same tool but different arguments stays.
>
> **The merged survivor takes the *originating* call's order, not `min(order)`.**
> A tool call is *created* on the span where it appears as LLM **output**
> (positive band, ordered AFTER that turn's LLM); the same call replayed on a
> later span's **input** carries a negative band (ordered before *that* span's
> LLM). When the two merge they are one interaction, and per the spec's timing
> note it takes the time/order of the span that *created* the call — so an
> output (positive) order **wins over** an input-replay (negative) one
> (`_originating_order` in `builder.py`). A naive `min(order)` would let the
> replay's negative band drag the merged call ahead of its own originating LLM
> (the "database sorts before the first LLM" bug). When both merged edges share
> a sign (a call only ever seen as input replay, never as an output in-trace),
> the earlier band is kept. Guarded by
> `test_merged_tool_call_orders_after_its_originating_llm`.
>
> Because Phase B reduces interaction count *before* the fuse, `build_entity_graph`
> emits **one entity edge per surviving Black edge** (no endpoint-pair dedup),
> so distinct calls between the same two entities remain distinct interactions.

**Step 5 — Agentic entity graph.**
Step 5 derives the entity graph from the colored, merged execution-flow graph
in two sub-steps: create-and-fuse entities (5.a) and name them (5.b). Per spec
Step 5, this is where multiple execution-graph nodes representing the *same
entity* are **fused** into one.

**Step 5.a — Creating the entity graph (fuse).**
Consider the Gray and Black nodes and edges in the (merged) execution-flow
graph; Black edges represent connections *between* entities. First, form
subgraphs by **ignoring the Black edges** — i.e. compute connected components
over the Gray and Black nodes considering only White and Gray edges. A subgraph
may contain inferred nodes, observed nodes, or both. Each connected component
then becomes one **entity node**, **fusing** every execution-graph node in the
component into a single entity. Attributes from every span in the component are
pooled onto the entity node. The Black edges — both observed and inferred — are
**maintained**: each becomes a directed edge in the entity graph between the
entity nodes containing its endpoints. Inferred nodes propagate their inferred
marker onto the entity node they form via a dedicated boolean field
(`inferred`) on the entity node — not via label inspection. An entity is
`inferred = true` iff every absorbed node was an inferred node.

> **As-implemented note.** The fuse is `build_entity_graph` in `builder.py`,
> run *after* the Step 4 merge. It is purely structural — no merging happens
> here (Step 4 already merged on the execution-flow graph):
>
> - Connected components over Gray/Black nodes (White+Gray edges only) → entity
>   nodes; attributes pooled via `EntityNode.absorb`.
> - The Step 4 **A3 forced grouping** (`node_id → group`) is applied as extra
>   adjacency, so the same-service components A3 identified fuse into one entity
>   without any node having been merged — every node keeps its own span.
> - **One entity edge per Black edge — no endpoint-pair dedup.** Step 4 Phase B
>   already collapsed same-interaction edges, so distinct calls between the same
>   two entities (e.g. two separate LLM turns) correctly remain distinct
>   interactions. The entity edge's first `span_id` is its **anchor** — the span
>   the extractor uses for the interaction's payload *and* timing (see below).
>   The anchor is the **observed (non-inferred) endpoint's** span when one
>   exists, else the source endpoint's: an inferred peer that Step 4 merged
>   across turns carries a *stale* span (the merge survivor's, from an earlier
>   turn), so anchoring on the observed endpoint keeps each call site — including
>   the *response* edge, whose source is the merged peer — on its own turn's span.
>
> Inferred nodes propagate their `inferred` marker; an entity is
> `inferred = true` iff every absorbed node was inferred.

**Interaction timing follows the anchor span.**
An interaction's `started_at` / `ended_at` are taken from its **anchor span**
(the entity edge's first pooled span — see the fuse note above), **not** from an
aggregate over every span the interaction touches. This matters specifically
because of the Step 4 merge, in two ways:
- **Aggregation drags timing.** When a repeatedly-called peer is merged into one
  node, an interaction's pooled spans can include a span from *another* turn
  (the merged peer carries an earlier turn's span). A `min(started_at)` over the
  pooled set would drag every turn's interaction down to the earliest turn's
  time, so a later-turn tool call could sort *ahead of* LLM calls that ran after
  it. Hence timing follows the single anchor, not an aggregate.
- **The anchor must be the observed endpoint.** For a *response* edge (e.g.
  `LLM → agent`), the edge's *source* is the merged peer — its span is stale.
  Anchoring on the observed (non-inferred) endpoint instead keeps each turn's
  response on its own span, so the per-turn responses get distinct
  `started_at`s rather than all collapsing onto the first turn's span (which
  would also stack them at one `(started_at, order)` key).

The anchor span is the single source of truth for an interaction's identity, so
its times define both the interaction's time and its position in the
`(started_at, order)` sort. (`error`, by contrast, still considers the whole
call/response pair — either side erroring marks the interaction errored.)

> **As-implemented note.** The fuse (`build_entity_graph`) lists each entity
> edge's spans **observed-endpoint-first**, so `edge_spans[0]` in
> `extractor._derive_interactions` is the observed-side span; the extractor sets
> `started_at` / `ended_at` from that anchor, not from `min`/`max` over
> `edge_spans`. A regression test over a multi-turn anthropic fixture
> asserts each interaction's `started_at` equals its anchor span's, and that the
> per-turn agent→LLM calls **and** LLM→agent responses each keep distinct times
> (the response-direction assertion is what guards the observed-endpoint anchor).

**Step 5.b — Naming nodes.**
Each entity node should be given a key reflecting its originating subgraph,
drawn from one of the subgraph's node spans. In particular, if a node in the
subgraph carries a **hostname**, use it as the key. When no clear key is
available, the entity is named `unknown`.

> **As-implemented note.** **Implemented** (partially, as scoped) in
> `extractor._entity_display_name`. Each entity's `display_name` is derived by
> precedence: (1) the `service.name` of any contributing span (the typed
> `Span.service_name` field — e.g. the agent's or tool's Kubernetes service
> name); (2) the model / tool / agent name parsed from the `natural_key` suffix
> (`llm:claude-…` → `claude-…`); (3) the literal `unknown` when neither is
> available. The spec's *preferred* identifier — a **hostname** — is still
> deferred: it lives on non-agentic (httpx / botocore) spans that are not part
> of the entity-forming subgraph today, so it is unreachable until the
> cross-scope enrichment stage runs. `service.name` is the best identifier
> available now. See "Deferred to later stages → Richer entity naming".

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
  spans (Step 2.a case 1).
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
  forwarded, producing two traces and a split graph. Step 2.a's inferred peers
  stub the missing side within a trace; *cross-trace* stitching is deferred to a
  later enrichment stage (the latest spec removed its former Step 4 system-graph
  section — see "Deferred to later stages").
- **Events between a receive and a send belong to one entity.** All events
  observed between a component's receive and its subsequent send belong to the
  same entity — which is why a connected Gray/White component collapses to a
  single entity in Step 5.a.

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

While *node* promotion ignores `Kind`, *edge* promotion (Step 3.b) does
use it: a Gray edge between two Black nodes becomes Black only for a
`SOURCE`↔`TARGET` pair of the **same** `Kind`. `Kind` here disambiguates
which adjacent boundaries form a genuine call (a tool call paired with a
tool, an LLM call with an LLM) from two same-entity Source spans that
merely sit next to each other on the chain. See Step 3.b.

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
the provider-qualified name. Hostname / `service.name` are deliberately *not*
used as the *natural-key* — they would over-merge across distinct entities
behind the same proxy.

`service.name` *is*, however, used as the combine key in one tightly-gated
place: the Step 4 observed ↔ observed merge (Phase A3), which fires **only** for
**keyless** observed boundary callers — nodes with no typed natural-key on
`peer_match_key` *or* `label` — and only across *different* entity components. A
typed node (`tool:` / `llm:` / `agent:`) always keeps its natural-key identity
and is never merged by service name, so the over-merge hazard above does not
apply: only same-service nodes that have *no other identity than the service*
are combined.

**Same-entity merging — one Step-4 pass on the execution-flow graph.**
The spec consolidates all same-entity convergence — **inferred ↔ observed**,
**inferred ↔ inferred**, and **observed ↔ observed** — plus **edge** merge into
a single Step 4 (def. 9), run **on the execution-flow graph** before the Step 5
fuse. The implementation matches this: `merge_step4` does Phase A (node merge,
three sub-passes A1/A2/A3) then Phase B (edge merge), all on the base graph
(see the Step 4 as-implemented note). The three node sub-passes are not
redundant — inferred↔observed needs an observation to fold into;
inferred↔inferred needs none; observed↔observed unifies two observations of the
same service — and each carries the guard that keeps a *caller* from fusing into
its *callee*:

- **A1 inferred ↔ observed** — typed key + kind, observed twin in a *different*
  entity component (an independent observation, not a sibling caller).
- **A2 inferred ↔ inferred** — typed callee peers (TARGET role, key-prefix
  matching kind) sharing a key; converges a repeatedly-called peer.
- **A3 observed ↔ observed** — same `service.name` across different entity
  components, gated to keyless observed callers. Realised as a fuse-time forced
  grouping (not a node merge) so per-call boundary spans survive.

**Edge merge is implemented** (Phase B): the call-over-count the earlier draft
flagged as a known limitation is fixed. A tool call replayed across spans (same
`tool_call.id` / same arguments between the same entity pair) collapses to one
interaction; genuinely distinct calls (distinct arguments) are preserved. The
fuse then emits one entity edge per surviving Black edge, so interaction counts
reflect Phase B exactly.

> **History.** Earlier ADR drafts (a) asserted "no inferred↔inferred
> de-duplication" — wrong, the spec combines them; (b) split merging across
> Step 2.c and Step 3.a and ran two passes *post-fuse* on the entity graph, a
> divergence from the spec's execution-flow placement; (c) left edge merge
> unimplemented (the call over-count). The current code resolves all three:
> one `merge_step4` on the execution-flow graph, node merge then edge merge.

**Inferred peers are created in the core algorithm, not deferred.**
When only one side of a protocol call is observed, Step 2.a materialises an
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
human-facing display value (e.g. `"(unobserved peer of <source>)"`) and is
free to change for UX reasons; queries and
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
1.4.1; `openinference_anthropic_v1.0.6_telemetry_spans.md` for the anthropic /
`claude_agent_sdk` framework at 1.0.6; future references for
httpx/starlette/etc.). New attributes are not added on
intuition; the reference is regenerated from the upstream package and the
attribute confirmed before it appears in `_OI_ATTRS` or in a per-version
schema map. Each adapter records the framework version(s) it has been
verified against in a `documented_version` field for the next maintainer.

**Combined source-and-target spans assume the target emits no spans of its
own.** Step 2.a's duplicate-node approach is correct when the target side has
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
- **Cross-scope multi-agent composition (A2A).** When one agent delegates to
  another over A2A, the two agents' agentic spans are connected only through
  the non-agentic `httpx → starlette` bridge between their services. The latest
  spec covers this in principle — Step 3.a draws a Gray edge across the bridge
  "regardless of scope"; Step 3.b adds the callee's **agent-root span** as a
  Target boundary and admits a cross-framework `tool call → agent` matched pair
  bound on the source's *related* target — so the delegation should become a
  Black edge between two distinct, observed agent entities rather than an
  inferred `tool:` peer. None of these three pieces is implemented yet: the
  caller-side `delegate_*` span is a Source boundary, but the callee agent-root
  stays Gray (AGENT-kind wrappers map to `role=NONE`), no `tool→agent` edge is
  promoted, and the delegated agent surfaces as an inferred peer. Realising it
  also requires resolving how the **entry** agent's root (delegated-to by no
  in-trace agent) is treated, and what makes a target "related" to a source —
  both open in the spec.
- **Broken traceparent / disconnected base graphs.** When a Receive span has
  no traceparent link to its corresponding Send, the base graph is
  disconnected and Step 5.a naturally produces disconnected components in the
  entity graph. No special handling, no annotation at this stage.
- **Hostname-based entity naming.** Step 5.b now names entities from
  `service.name` / the natural-key suffix (see its as-implemented note), but
  the spec's *preferred* identifier — a **hostname** — remains deferred. It
  lives on non-agentic (httpx / botocore / starlette) spans that are not part
  of the entity-forming subgraph today, so it is unreachable until the
  cross-scope enrichment stage exists.
- ~~**Inferring tool nodes from `tool_calls` attributes.**~~ *Implemented for
  the `anthropic` framework, both sides* — an LLM span's
  `llm.output_messages.*.message.tool_calls.*` (output, ordered after the LLM)
  and `llm.input_messages.*.message.tool_calls.*` (input replay, ordered
  before the LLM) attributes now materialise the inferred tool-call / tool
  nodes and their edges (Step 2.a case 3, gated per-adapter; see the
  as-implemented note under Step 2.a). Other frameworks that emit a real
  tool-execution span do not opt in (their tools are observed, not inferred).
- ~~**Inferred-interaction ordering + input-derived tools.**~~ *Implemented* —
  the spec's "Inferred edges ordering" rules (calls before responses;
  input-derived tools before the LLM interaction; output-derived tools after
  it) are realised by an explicit integer `order` carried from the base-graph
  `Edge` through `EntityEdge` to `ProtoInteraction` and the
  `proto_interactions."order"` column, with the CLI and API sorting by
  `(started_at, order)`. See the "Inferred interaction ordering" as-implemented
  note under Step 2.a.
- ~~**Step 4 node-and-edge merge (all combinations, on the execution-flow
  graph).**~~ *Implemented* as the single `merge_step4` (see "Same-entity
  merging — one Step-4 pass" under Key decisions and the Step 4 as-implemented
  note): Phase A node merge (A1 inferred↔observed, A2 inferred↔inferred, A3
  observed↔observed — A3 as a fuse-time forced grouping) then Phase B edge
  merge, all on the execution-flow graph before the Step 5 fuse. This retires
  two earlier divergences: the post-fuse placement of two merges, and the
  unimplemented edge merge (the call over-count is now fixed).
- **Cross-trace ("system graph") merging and name alignment.** The earlier spec
  carried a top-level "Step 4 — system graph" covering cross-trace work; the
  latest spec **removed** that section (its old Step 4 number is now the
  node/edge merge). The work it described remains deferred to a separate
  enrichment stage:
  - **Inter-trace merging.** Merging an inferred node in one trace with an
    observed node in another — which can happen when traceparent is not
    propagated and a single logical interaction is split across two traces
    (so the graph is split). The cross-trace analogue of the Step 4 intra-trace
    merge.
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
  graph (after Step 1); the colored execution graph (after Step 2 inference,
  Step 3 Gray-coloring and Black boundary promotion, and the execution-flow
  Step 4 node+edge merge — including combined-span duplicates, inferred peers,
  and between-boundary flag annotations); and the entity graph (after the Step 5
  fuse — Step 4 merges already ran on the execution-flow graph, so the fuse is
  purely structural). The UI surfaces these as the execution graph **before**
  the fuse and the entity graph **after** it. All three are in the "Graphs
  (proto)" tab of the trace-tree UI.
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
