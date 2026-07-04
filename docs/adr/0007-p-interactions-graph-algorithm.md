# P-interactions: graph-based algorithm for entity and interaction extraction

The P-interactions processor derives agentic entities and their interactions
from OTel spans using a single base graph that is progressively colored by
scope semantics, rather than linear span-pattern matching or a stack of
per-scope graphs that are merged at the end. This was chosen because the linear
approach requires hard-coded heuristics for each span pattern and cannot
represent partial instrumentation cleanly, and because the per-scope-then-merge
approach pushes structural decisions into a cross-scope merge step that is hard
to inspect. A single colored base graph makes both the trace structure and the
scope semantics explicit and inspectable in one place.

This ADR records the design as it tracks `p_interactions_alg.md` (the
human-owned algorithm spec). The implementation now realises the spec's full
White/Blue/Teal pipeline (transport coloring, Teal server routing, the case-4
inferred agent, and the two-graph merge split); the algorithm vocabulary and
step ordering below follow the spec, and per-section notes record how each step
is implemented and what remains deferred.

> **Spec revision this ADR tracks.** The human spec was restructured into a
> **three top-level step** shape with a **color-based** node vocabulary:
> **Step 1** base (White) graph → **Step 2** enrich the graph with scoped
> semantics (2.a transport → Teal, 2.b agentic → Blue, 2.c derive inferred
> nodes/edges, 2.d merge identical interactions on the execution graph) →
> **Step 3** entity graph (3.a create the entity-graph *nodes* — group
> connected Blue+White subgraphs, then combine same-entity groups; 3.b create
> the entity-graph *edges* — one interaction per Teal transport chain). This ADR
> follows that numbering. Cross-entity calls are the inferred Teal servers routed
> in Step 2.c and dropped when the entity nodes are formed in Step 3.a; there is
> no separate cross-entity edge signal.

## Definitions

The algorithm colors nodes and edges to layer scope semantics onto a single
base graph. Two orthogonal vocabularies apply to a node — its **color**
(which scope it belongs to) and its **provenance** (was it recorded by a real
span, or inferred from one). A third vocabulary — `SpanFacts` — sits between
the raw spans and the algorithm and is described under "Adapter layer" below.

**Colors** — quoting the spec's definitions (lightly case-normalized):
- **White** — *"initial color of all nodes and edges, and those not assigned a
  scope"* (spec def.). Every node and edge starts White; a node stays White if
  no scope claims it.
- **Blue** — *"nodes assigned the agentic scope"* (spec def.). The openinference
  agentic-scope nodes are colored Blue in Step 2.b.
- **Teal** — *"nodes assigned the transport scope (e.g. communication, proxy)"*
  (spec def.). Transport spans (httpx, starlette, asgi) are colored Teal in
  Step 2.a, and inferred transport "server" nodes are Teal. (The spec's Step 2.a
  heading and inference cases call this the "transportation" scope; this ADR uses
  "transport" throughout — same scope.)

**Node kinds** — the spec's remaining node definitions (the spec does not group
these as "roles"; that term appears only in the implementation's `SpanFacts`,
noted below):
- **Event node** — *"Event node representing a local entity"* (spec def.).
- **Source node** / **Target node** — *"Source / Target nodes representing a
  local and remote entity"* (spec def.).

**Edges** — the spec's edge vocabulary in this revision is:
- **White edge** — *"parent child relationship based on trace parent"* (spec
  def.). Step 1 draws one per span, parent→child, mirroring traceparent.
- **Inferred edge** — *"an interaction in the graph we know should exist
  although we don't have a span representing this interaction"* (spec def.).
  The edges Step 2.c draws between an observed node and its inferred peer (and
  through the inferred Teal server node) are inferred edges. An inferred edge
  carries an explicit derivation **order** (see "Inferred interaction ordering"
  under Step 2.c).

**Provenance** (how the node came to exist). The spec defines only the
**Inferred node** (quoted verbatim below); **Observed (real) node** is an ADR
term for its complement (the spec does not name it):
- **Observed (real) node** — a node backed by an emitted span (ADR term; spec
  is silent).
- **Inferred node** — *"a node in the graph we know should exist although we
  don't have a span emitted representing that node"* (spec def.). An agentic
  span can describe an entity other than itself: an LLM `query` span describes
  the LLM it called; a `tool_calls` attribute on an LLM-output span describes a
  tool that was invoked. Step 2.c materialises such peers as inferred nodes. An
  inferred node may later be **merged** (Step 2.d) with another node
  representing the same interaction, and — once the entity graph is built — the
  entities that carry it may be **combined** (Step 3.a) with other groups
  representing the same entity.

> **Vocabulary note (merge / combine / group).** The current spec does **not**
> define standalone `merge` or `fuse` terms (earlier revisions did; they have
> been removed). The spec now uses two distinct operations:
> - **merge** (Step 2.d) — collapse execution-graph nodes/edges representing the
>   *same interaction*. The spec's Step 2.d heading is "merge identical
>   interactions".
> - **combine** (Step 3.a semantic sub-step) — combine the *groups* of
>   execution-graph nodes that represent the *same entity* into one group, where
>   each final group becomes one entity-graph node. The spec speaks of
>   "**groups**" being "**combined**", not of nodes being "fused".
>
> This ADR follows that vocabulary. Where the implementation's function or field
> names still say "fuse" (e.g. `build_entity_graph`'s grouping is called a fuse
> in code comments), those are flagged as code names in the relevant
> as-implemented notes; the algorithm term is "combine groups".

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

## Adapter layer

Raw OTel attribute keys are consulted in **exactly one place**: `adapters.py`.
The rest of the algorithm reads from a typed value object, `SpanFacts`,
produced by an adapter from a span. This isolates the per-framework /
per-version vocabulary drift (rename `llm.model_name` → `llm.model.name`,
add a new kind value, …) from the graph-construction code.

`SpanFacts` carries:

- `kind` — `Kind.LLM`, `Kind.TOOL`, `Kind.AGENT`, or `Kind.OTHER`. Kind
  records *what the span is about* (used to derive natural-key prefix and
  payload shape).
- `is_combined` — true iff one span carries BOTH the source and target side
  of the same call. Triggers Step 2.c duplication.
- `natural_key` — stable per-entity identity. Format is
  `<kind-prefix>:<identifier>` — `tool:<tool-name>`, `llm:<model>`,
  `agent:<agent-name>`. It is the **identifying attribute** the Step 2.d merge
  and the Step 3.a semantic combine group on (carried on the inferred node as
  `peer_match_key`) — both for the inferred↔observed merge and for converging
  repeatedly-called inferred peers. None when the span carries no identifying
  attribute.
- `display_label`, `target_label` — human-facing labels; `target_label` is
  the duplicate's label for combined spans only.
- `request_messages` / `response_messages` / `request_value` /
  `response_value` — payload data for `extractor._derive_interactions`.

> **As-implemented note (role).** `SpanFacts` carries a `role` field
> (`SOURCE` / `TARGET` / `BOTH` / `NONE`). It is not part of the spec's
> algorithm vocabulary; the code uses it only to decide which spans are call
> points needing an inferred peer and, for such a span, whether that peer sits
> on the target or the source side (`synthesize_missing_peers`). Role and kind
> are re-derived from `SpanFacts` on demand for observed nodes
> (`_node_role` / `_node_kind`) and stamped on `node.attributes` at construction
> for inferred nodes.

**Dispatch.** Adapters are registered per `(scope_root, framework)` — e.g.
`(openinference, openai_agents)`, `(openinference, claude_agent_sdk)`. The
framework name is the third dotted segment of the scope name (e.g.
`openinference.instrumentation.openai_agents`). A `*` fallback adapter
covers openinference frameworks not yet profiled (LangChain, LiteLLM,
Haystack, …) using the cross-framework openinference vocabulary; it is
safe-by-default — unrecognised combined-span shapes degrade to one-sided
peers that Step 2.c will stub with an inferred node.

**Versioning.** Adapters that have absorbed schema drift across releases
declare a per-version schema map keyed on `_scope_version(span)`. The
schema records two axes declaratively:

- `kinds[raw_value] → Kind` — maps the raw string the framework emits at
  `openinference.span.kind` to the internal `Kind`. A version that
  introduces a new raw value adds an entry; raw values absent from the
  map decode to `Kind.OTHER`.
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
behavioural. Those decisions live in adapter code, not the schema tables.

**Transport-scope recognition.** The OpenInference MCP adapter
(`openinference.instrumentation.mcp`) exists in the registry but returns
`Kind.OTHER` for every span — the MCP instrumentor only injects / extracts
W3C `traceparent` headers and emits no application spans. The adapter exists
so dispatch recognises the scope, not because MCP entities are detected at
this stage. Standalone a2a and mcp scopes outside openinference remain
deferred, per the "Deferred to later stages" section.

## The algorithm

**Step 1 — Base (White) execution flow graph.**
Construct one node per span and one White directed edge from each span's parent
to itself, following the OTel traceparent relationships. The result is a single
graph for the entire trace whose connectivity mirrors trace structure exactly,
resembling what Phoenix or MLflow shows (with the addition of the inferred
nodes/edges added later). All nodes and edges start White.

**Step 2 — Enrich the execution flow graph with scoped semantics.**
Step 2 colors nodes by scope and extends the graph with inferred nodes/edges,
then merges identical interactions. Coloring is **additive**: coloring a node
Teal or Blue never removes its White edges — the base graph's full White
connectivity is preserved throughout.

**Step 2.a — Transport scope (Teal).**
Requires transport spans such as httpx, starlette, asgi. Traverse the
execution flow graph, identify every node in the **transport** scope, and color
it **Teal**.

> **As-implemented note.** **Implemented** in `builder.color_transport`, which
> colors every transport-scope node Teal. Transport-scope recognition
> (`adapters.is_transport_scope`) covers `opentelemetry.instrumentation.`
> `{httpx,starlette,asgi,aiohttp_*}`; non-communication instrumentations
> (botocore, psycopg) are deliberately excluded. The inferred Teal server nodes
> of Step 2.c are also implemented (see below).

**Step 2.b — Agentic scope (Blue).**
Requires openinference telemetry spans. Traverse the execution flow graph,
identify every node in the **agentic** scope, and color it **Blue**.

> **As-implemented note.** Implemented for the **openinference** scope only, in
> `builder.color_agentic`. It colors every openinference-scope node Blue and
> adds Blue chain edges between consecutive agentic nodes. Cross-entity calls are
> expressed by the Step 2.c server routes, not by any edge promotion here.
> Support for additional agentic scopes — standalone a2a, mcp — is deferred;
> see "Deferred to later stages".

**Step 2.c — Derive inferred nodes and edges (openinference scope).**
Some agentic spans describe or represent an entity *other than the span's own
node*. When a span carries evidence of such a peer, the algorithm materialises
an **inferred node** for it and connects it with **inferred edges**. In this
spec revision the source↔target *call* inferences (cases 1–3 below) route the
call **through an inferred Teal "server" node** — the request and response pass
source→server→target and target→server→source — modelling the transport hop
between the two entities. (Case 4, the inferred *agent* node, is structural and
introduces no Teal server.)

Cases:

1. **Combined source-and-target spans** (LLM call). A single span records both
   sides of a call — e.g. `openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query`,
   which represents a call to an LLM and carries both the outgoing request and
   the incoming response. From it the algorithm infers:
   1. a node representing the **LLM** (target) — agentic-scope **Blue**;
   2. a node representing a **server** — transport-scope **Teal**;
   3. edges: Agent→server, server→LLM, LLM→server, server→Agent.

2. **Tool call spans.** A tool-call span such as
   `openinference.instrumentation.claude_agent_sdk.{tool_name}` represents a
   call to a tool. From it the algorithm infers:
   1. a node representing the **tool** (target) — agentic-scope **Blue**;
   2. a node representing a **server** — transport-scope **Teal**;
   3. edges: agent-tool-call(source)→server, server→tool(target),
      tool→server, server→agent-tool-call.

3. **Peers described in span attributes** (tool from `tool_calls`). An
   LLM-output span may carry a `tool_calls` attribute, e.g.
   `llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments`,
   which describes a tool invoked from the LLM output. The span itself
   represents the LLM call; the attribute additionally evidences a *tool call*
   (the source) and the *tool itself* (the target). The spec's summary line
   says *"We can therefore infer two nodes and three edges"*, but its own
   enumeration then lists **three** nodes and **five** edges (the spec is
   internally inconsistent on this count — see the note below):
   1. a node for the **tool call** (source) — agentic **Blue**;
   2. a node for a **server** — transport **Teal**;
   3. a node for the **tool itself** (target) — agentic **Blue**;
   4. edges: current-span→tool-call, tool-call(source)→server,
      server→tool(target), tool→server, server→tool-call.

   > **Spec inconsistency (unresolved).** Step 2.c case 3 opens with *"infer
   > two nodes and three edges"* but enumerates three nodes and five edges. The
   > "two nodes / three edges" figure appears to predate the addition of the
   > Teal server node (a two-node/three-edge shape — tool-call, tool, plus
   > current→tool-call / tool-call→tool / tool→tool-call — is exactly the
   > pre-Teal version). The ADR reproduces the enumeration (three/five) because
   > it is the internally-detailed one and matches the Teal routing added
   > elsewhere in the same revision, but does **not** silently discard the
   > spec's summary line. This should be reconciled in the spec.

4. **Inferred agent node** (anthropic bare-leaf case). When the framework emits
   only bare leaf LLM spans — no run/agent/wrapper span — an agent node is
   inferred by observing that all LLM spans share a single common parent in the
   transport scope. Example:
   ```
   POST /
    ├─ messages.create
    └─ messages.create
   ```
   The algorithm infers:
   1. a node representing the **agent**;
   2. inferred edges: transport(POST)→agent, and agent→each LLM span;
   3. **disconnect** the original edges but maintain a reference from the newly
      created edges back to the originals.

Additional cases may exist and need implementing (e.g. tools inferred from
input attributes — see the ordering note and the as-implemented note below).

> **As-implemented note.** The inferred-node machinery **routes every call
> through an inferred Teal server node**, per the spec: `_insert_teal_server`
> creates the server (`span_id=""`, `is_inferred=True`) and the four
> request/response edges (source→server, server→target at the call band;
> target→server, server→source at the response band). `duplicate_combined_nodes`,
> `infer_tool_calls_from_attributes`, and `synthesize_missing_peers` all wire
> their pair through a server rather than with a direct edge. The Step 3.a fuse
> drops Teal and reconstructs the interaction from each server (see Step 3.a).
>
> - **Case 1** is `duplicate_combined_nodes`: for a combined span it creates a
>   duplicate node referencing the same span (the LLM target) and routes
>   original→server→duplicate. The adapter signals it via
>   `SpanFacts.is_combined = True` and a `target_label`.
> - **Case 2** (tool/sub-agent dispatch) is recognised in `_ClaudeAgentSDKAdapter`
>   and the missing peer is materialised by `synthesize_missing_peers` (the
>   one-sided stubbing below), routed through a server. Sub-agent and local-tool
>   dispatch take the same path — the span-name suffix / `agent.name` becomes the
>   inferred peer's identity. Repeated dispatches of the same target each produce
>   their own inferred peer; the Step 3.a semantic combine converges them by
>   `natural_key`.
> - **Case 3** (tool-from-`tool_calls`) is `infer_tool_calls_from_attributes`,
>   implemented for the `anthropic` framework. It adds a Blue *fold* edge
>   (current-span→tool-call, so the tool-call folds into the LLM's entity) and
>   routes tool-call→server→tool. It is **gated per-adapter**: only adapters that
>   populate `SpanFacts.tool_calls` trigger it, so frameworks that emit a real
>   tool-execution span (e.g. openai_agents, whose LLM spans also carry output
>   `tool_calls` for the same tool) are not double-counted. Both **output**
>   (`llm.output_messages.*.message.tool_calls.*`) and **input**
>   (`llm.input_messages.*.message.tool_calls.*`, surfaced on
>   `SpanFacts.input_tool_calls`) sides are read; input-side calls are a prior
>   turn's tool use replayed into the request and are ordered *ahead of* the LLM
>   interaction (see ordering below).
> - **Case 4** (inferred agent from bare leaf LLM spans) is **implemented** in
>   `infer_agent_from_bare_leaf_llms`, as the spec writes it: when a transport
>   (Teal) parent's direct Blue children are all LLM spans and no Blue ancestor
>   exists, it creates a new inferred agent node, adds edges transport→agent and
>   agent→each LLM span, and **disconnects** the original transport→LLM edges,
>   recording their ids on the new edges' `Edge.replaces` back-reference. The
>   agent is named from the LLM spans' `service.name` and forces its fused entity
>   `inferred = true` (via the `_inferred_agent` marker) even though it absorbs
>   observed LLM spans — the *agent* is what was inferred. This produces the
>   single-agent bare-leaf entity upstream, at inference time, rather than by any
>   same-service node grouping during the merge.

**One-sided stubbing.** When only one side of a call is observed, the missing
peer is an inferred node: an observed call point with no server-routed peer in
the trace gets an inferred node referencing the same span, carrying the originating
`SpanFacts.natural_key` on a `peer_match_key` field, and the call is routed
through an inferred Teal server. When a natural key is available the inferred
node's display label is the key itself (`tool:<tool-name>`, `llm:<model>`, …);
otherwise it falls back to `(unobserved peer of <source>)`. This is
`synthesize_missing_peers` in `builder.py`.

**Inferred interaction ordering.**
Several inferred interactions are derived from a *single* span and therefore
share that span's timestamp — `started_at` alone cannot order them. The spec
(`p_interactions_alg.md`, "Inferred edges ordering/timing" under Step 2.c)
requires an explicit **execution order**:

1. **Calls before responses.** The outgoing edge (source→target) precedes the
   incoming edge (target→source).
2. **Case-3 edge order.** For a tool inferred from an LLM span: the
   `current LLM span → tool-call` edge precedes the `tool-call → tool` edge;
   and the `tool-call → tool` edge (call) precedes the `tool → tool-call` edge
   (reverse / response). (Rule 1 applied within the case-3 triple. The spec's
   second sub-bullet here is garbled — *"the edge between the tool and the tool
   itself is before the edge between the tool and the tool call (reverse
   edge)"* — this is the sensible reading of it.)
3. **Input-derived tools before the LLM call.** A tool evidenced on the LLM
   span's *input* messages was invoked on a prior turn whose result is being
   fed back in; its interaction is ordered **before** the interaction with the
   LLM.
4. **Output-derived tools after the LLM call.** A tool evidenced on the LLM
   span's *output* messages is what the model asked to invoke as a result of
   this call; its interaction is ordered **after** the interaction with the LLM.

So a single LLM turn expands, in order, to: *(input-derived tool calls) → (the
agent↔LLM call/response) → (output-derived tool calls)*, with each call
immediately preceding its own response per rule 1.

**Representation (design decision).** Ordering is carried as an explicit
integer order field on the derived interaction, assigned at derivation time,
and the CLI/API sort by `(started_at, order)` rather than `started_at` alone.
A derivation-order-only tiebreak was rejected as too fragile — any consumer
that re-sorts would silently lose the contract; an explicit field survives the
SQL round-trip and is inspectable.

> **As-implemented note.** **Implemented.** The explicit order field is a plain
> integer carried on the base-graph `Edge` (`Edge.order`), stamped on the four
> request/response edges of each inferred Teal server at creation time (the call
> band on the request legs, the response band on the response legs). The Step
> 3.a fuse reads the bands back off each server (`_server_endpoints`) and copies
> them onto the reconstructed `EntityEdge.order`; they surface as
> `ProtoInteraction.order` and the `proto_interactions."order"` column. The CLI sorts by `(started_at, order)`
> and the API issues `ORDER BY started_at, "order"`. The band scheme realises
> the rules directly: input-derived tools sit at `-40 + 2k` (call) / `+1`
> (response), the agent↔LLM / combined / one-sided call at `0` / `1`, and
> output-derived tools at `40 + 3k` / `+1` — so negative < 0/1 < positive
> encodes rules 3, 1, 4. Input-side tool inference exists:
> `infer_tool_calls_from_attributes` reads both `SpanFacts.tool_calls` (output,
> positive band) and `SpanFacts.input_tool_calls` (input, negative band), the
> latter from the anthropic adapter's `llm.input_messages.*.message.tool_calls.*`.

**Step 2.d — Merge identical interactions (execution graph).**
This step identifies nodes and/or edges (inferred or observed) in the
**execution graph** representing the *same interaction* and merges them. "Same
interaction" means the **same logical occurrence of processing** — one real call
— **not** the same wall-clock timestamp. (The spec's definition says "the same
processing that took place at the same time"; this is read as *same occurrence*,
because a single call's input is often replayed into later spans at different
timestamps and must still merge — see the database example below and the timing
note. Accordingly "same execution time" is a matching **signal**, not a
requirement.) The spec states this as one heuristic-driven step (heuristics
below); it does not enumerate provenance sub-cases or mandate a node-before-edge
order.

The unit being compared is a **chain (subgraph)**, not an isolated node or edge.
An interaction is a chain `Blue source → transport region → Blue target` (with
response legs back), where:
- the **Blue** ends are the agentic endpoints — the source's call node and the
  target tool / LLM / agent node;
- the **transport region** in between is one or more **Teal (and possibly
  White)** nodes — either a single inferred server (Step 2.c) or a run of
  observed transport spans (Step 2.a), plus any unscoped White plumbing on the
  path.

So Step 2.d compares **chain against chain**: when two chains represent the same
interaction, they are merged *as a unit* — the aligned Blue endpoints merge with
each other and the transport regions merge with each other. It never pairs
mismatched representations (e.g. an isolated edge against a chain); both sides
are the same kind of object because Step 2.c already routed every call into this
chain form. Each side of the comparison may be inferred or observed, so the
merge spans all provenance combinations (inferred↔inferred, inferred↔observed,
observed↔observed).

Two motivating examples from the spec, both chain-vs-chain:
- A trace with an LLM span and a tool-call span may yield, from the first span,
  the chain `tool call → Server → Tool`, and from the second, `Server → Tool`.
  These are the *same* interaction, so they merge as a unit — the tool-call
  nodes, the Server (transport) regions, and the Tool nodes each collapse
  pairwise.
- A tool retrieving from a database followed by multiple LLM interactions: the
  tool input reappears in every following LLM span, producing multiple inferred
  database tool-call chains. Since all represent a *single* call to the
  database, they merge.

Merging is a set of heuristics asserting the same exact processing is observed,
drawing on: **proximity in the trace**, **same tool name**, **same execution
time**, **same input argument and output result**, **whether the node/edge is
inferred or observed in conjunction with the source spans**, and **nodes from
the same scope**.

**Timing note.** When merging edges, account for each edge's timing and keep the
time of the appropriate span. After a tool call, its input may be repeated in
following spans; the timing of that interaction should be **after** the span
that created the tool call.

> **As-implemented note.** Step 2.d is the function `merge_identical_interactions`
> (`builder.py`), run on the execution-flow graph after Step 2.c inference and
> before the Step 3.a fuse (and before the colored-graph snapshot, so the
> snapshot reflects every merge). The spec describes Step 2.d as one
> heuristic-driven step over any same-interaction nodes/edges; the code realises
> it as three operations — two node merges then a Teal-chain (server) merge:
>
> 1. **Node merge — inferred peer into its observed twin.** Fold an inferred
>    peer into an *independently observed* node for the same entity (matching
>    typed key + kind, with the observed twin in a *different* Blue component
>    than the peer's source, so it is the observed callee and not a sibling
>    caller on the same chain). This is the split-graph case — a no-op unless the
>    callee emitted its own spans. Component membership uses `_white_blue_*`,
>    which skips Teal-incident edges, so a caller and its callee land in distinct
>    components.
> 2. **Node merge — observed ↔ observed same interaction.** Merge two *observed*
>    callee nodes that represent the same processing at the same time. The guard
>    is a strict `_same_processing_signature`: identical typed callee identity
>    (`_typed_callee_key`) + kind, same scope, same request payload **and** same
>    response payload, and the same `(started_at, ended_at)` window. Any
>    difference keeps the nodes distinct, so genuinely-separate calls are never
>    over-merged; non-callee nodes (no typed key) never participate.
> 3. **Teal-chain (server) merge — duplicate interactions.** Collapse Teal chains
>    that are the *same* interaction: same source component + same target *typed
>    identity* + same logical call (equal request arguments, else equal
>    `tool_call.id`). Genuinely distinct calls differ in arguments and survive.
>    This collapses a tool-call replay (the same call seen as a span's output and
>    again on a later span's input) from two interactions to one; the duplicate
>    is dropped and the originating order carried onto the survivor's legs. Keying
>    the target on its *typed identity* rather than its component lets a replay
>    merge even though its inferred target peer sits in a separate component
>    (those peers converge later, in the Step 3.a semantic combine).
>
> The remaining spec case — converging multiple *inferred* peers for one entity —
> is handled by the Step 3.a semantic combine (on the entity graph, below), not
> here, per the spec's node/edge split. Guarded by `test_same_processing_merge.py`
> (`test_step2d_merges_two_observed_same_interaction_nodes`, plus over-merge
> guards for distinct arguments and differing scope/name).
>
> **The merged survivor takes the *originating* call's order, not `min(order)`.**
> A tool call is created on the span where it appears as LLM **output** (positive
> band, ordered AFTER that turn's LLM); the same call replayed on a later span's
> **input** carries a negative band. When they merge they are one interaction,
> and per the spec's timing note it takes the time/order of the span that
> *created* the call — so an output (positive) order **wins over** an input-replay
> (negative) one (`_originating_order`). A naive `min(order)` would drag the
> merged call ahead of its own originating LLM. When both merged legs share a
> sign, the earlier band is kept. Guarded by
> `test_merged_tool_call_orders_after_its_originating_llm`.

**Step 3 — Entity graph.**
Create a new graph representing agentic entities and interactions, built from
the colored, merged execution graph. It is used to (1) identify the entities and
(2) identify the interactions. The spec splits construction by **nodes then
edges**: **Step 3.a** creates the entity-graph *nodes*, and **Step 3.b** creates
the entity-graph *edges*.

**Step 3.a — Creating the entity-graph nodes.**
Two sub-steps, structural then semantic:

1. **Structural — group the execution graph.** Consider the Blue and Teal nodes
   in the execution flow graph; form subgraphs by **dropping the Teal nodes**.
   Each subgraph is a set of connected nodes that can only be Blue or White
   (inferred, observed, or both). For each connected Blue+White subgraph, create
   a **group**.
2. **Semantic — combine same-entity groups.** Combine the groups that represent
   the *same entity* into a single group. For example, multiple tool calls (with
   *different arguments*) to a file system yield multiple inferred file-system
   tools — a group each from the structural step — but all represent one
   file-system tool entity, so those groups are **combined**. The heuristics for
   deciding two groups are the same entity: **same tool/service/host/llm name**,
   **same argument/output types**, **nodes from the same scope**.

Each final group is one **entity-graph node**. Each entity node is given a
**key** reflecting its originating subgraph, drawn from one of the subgraph's
node spans — in particular, if a node in the subgraph carries an
agent/service/tool/host name, use it as the key; when the key is not clear, it
can be called `unknown`.

Inferred nodes propagate their inferred marker onto the entity node they form.
(The spec is silent on how a combined entity inherits inferred status; in the
implementation this is carried on the `inferred` boolean field — not label
inspection — and an entity is `inferred = true` iff every absorbed node was
inferred, or a case-4 inferred-agent node was absorbed.)

> **As-implemented note.** The structural grouping is `build_entity_graph` in
> `builder.py` (its in-code comments call the grouping a "fuse" — that is a code
> name; the spec term is "group / combine"). It **drops all Teal nodes** and
> forms connected components over the Blue+White nodes (edges touching a Teal
> node are cut, so the whole Teal chain between two Blue components is the entity
> boundary). Attributes are pooled via `EntityNode.absorb`; White plumbing nodes
> are pulled into a component but seed none. The semantic combine sub-step is a
> separate pass, `combine_identical_entities` — see the "As-implemented note
> (semantic combine)" below (it operates on the just-formed groups/entity nodes
> and so is documented with the node-creation code even though it runs after edge
> reconstruction in
> the current ordering).
>
> Entity naming (`extractor._entity_display_name`) implements the key by
> precedence: (1) the `service.name` of any contributing span
> (`Span.service_name`); (2) the model / tool / agent name parsed from the
> `natural_key` suffix; (3) the literal `unknown`. The spec's other preferred
> identifier — a **hostname** — is deferred: it lives on non-agentic (httpx /
> botocore) spans that are dropped as Teal, so it is unreachable until
> transport-scope enrichment runs. See "Deferred to later stages →
> Hostname-based entity naming".

> **As-implemented note (semantic combine).** The combine sub-step is
> `combine_identical_entities`, a pass over the entity-graph nodes. It groups them
> on the spec's Step 3.a semantic heuristics and combines each group into one
> survivor, re-pointing every `EntityEdge` endpoint to it and preserving all
> edges (distinct calls stay distinct interactions). The combine key is:
> - **same tool/service/host/llm name** — the entity's typed identity
>   (`peer_match_key`, else the natural-key `label`, e.g. `tool:database`);
>   keyless entities (a case-4 inferred agent named from `service.name`, an
>   `unknown` entity) never combine, since there is no name to assert sameness on;
> - **same argument/output types** — the typed-key prefix (`tool:` / `llm:` /
>   `agent:`), the coarse kind, so a tool and an LLM sharing a name stay apart;
> - **nodes from the same scope** — derived from the entity's pooled spans
>   (`spans_by_id`, threaded in from the extractor);
> - **inferred vs observed** — carried in the key (an implementation guard, not
>   a spec heuristic) so an inferred peer never silently absorbs an observed
>   entity.
>
> Both inferred peers and observed entities participate: repeatedly-called
> inferred peers converge (one tool invoked from several sites → a peer per site
> → one entity — the spec's file-system example), and two observed spans of the
> same callee that landed in separate (traceparent-broken) components combine.
> Guarded by `test_same_processing_merge.py`
> (`test_step3b_merges_observed_same_entity_keeping_edges`,
> `test_step3b_scope_separates_same_named_observed_entities`, and guards that
> distinct names, and observed-vs-inferred, do not combine).

**Interaction timing follows the anchor span.**
The spec specifies inferred-edge *ordering* (Step 2.c) and *merge timing*
(Step 2.d timing note) but not how an interaction's absolute timestamps are
chosen; the anchor-span rule below is an implementation resolution of a merge
artifact, not a spec-stated decision. An interaction's `started_at` /
`ended_at` are taken from its **anchor span** (the entity edge's first pooled
span), **not** from an aggregate over every span the interaction touches. This
matters because of the Step 2.d merge, in two ways:
- **Aggregation drags timing.** When a repeatedly-called peer is merged into one
  node, an interaction's pooled spans can include a span from *another* turn. A
  `min(started_at)` over the pooled set would drag every turn's interaction down
  to the earliest turn's time. Hence timing follows the single anchor.
- **The anchor must be the observed endpoint.** For a *response* edge (e.g.
  `LLM → agent`), the edge's *source* is the merged peer — its span is stale.
  Anchoring on the observed (non-inferred) endpoint keeps each turn's response
  on its own span.

(`error`, by contrast, still considers the whole call/response pair — either
side erroring marks the interaction errored.)

> **As-implemented note.** The fuse lists each entity edge's spans
> **observed-endpoint-first**, so `edge_spans[0]` in
> `extractor._derive_interactions` is the observed-side span; the extractor sets
> `started_at` / `ended_at` from that anchor, not from `min`/`max` over
> `edge_spans`. A regression test over a multi-turn anthropic fixture asserts
> each interaction's `started_at` equals its anchor span's, and that per-turn
> agent→LLM calls **and** LLM→agent responses each keep distinct times.

**Step 3.b — Creating the entity-graph edges.**
Purely structural. Given the entity-graph nodes (groups) from Step 3.a:
- edges **internal** to a group are ignored;
- **each path** connecting two entity nodes is represented by a **single
  interaction** between them — in other words, **each Teal transport chain
  between two Blue components becomes one interaction**.

The mapping is **per chain**, not per entity pair: two *distinct* calls between
the same pair of entities run through two distinct transport chains and therefore
become **two** interactions. A chain is collapsed only *within itself* (its
multi-node transport region → one interaction), never across sibling chains.
(Genuinely-duplicate chains — the same call surfaced twice — were already merged
in Step 2.d, so what reaches Step 3.b are the distinct interactions.) The edges
of the entity graph are simply these interactions.

> **As-implemented note.** Edge reconstruction is done inside `build_entity_graph`
> as it drops each Teal chain: **one S→D entity edge (call band) and one D→S
> entity edge (response band) per dropped Teal transport chain.** A Teal
> transport chain is a run of one or more Teal nodes between two Blue components
> — a single inferred server (Step 2.c, `span_id=""`) is the length-1 case; an
> observed httpx→starlette hop (Step 2.a) is the multi-node case. Both drop, and
> both reconstruct: the chain's external source and target and its two order
> bands are read back from its incident edges by `_server_endpoints` (source =
> the endpoint whose inbound edge carries the lower/call band). Because Step 2.d
> already collapsed duplicate chains, distinct calls between the same two
> entities remain distinct interactions — one per surviving chain.
>
> The entity edge's first `span_id` is its **anchor** — the span the extractor
> uses for the interaction's payload *and* timing (see "Interaction timing
> follows the anchor span" above).
> The anchor is the observed (non-inferred) endpoint's span when one exists, else
> the source endpoint's; the Teal nodes themselves never anchor (an inferred
> server carries `span_id=""`; observed transport spans are dropped).

## Observations and assumptions

These are the protocol-level expectations the spec relies on. They motivate the
inferred-peer / split-graph handling and bound where the algorithm is expected
to work.

- **Matched send/receive.** When all events are received, a protocol
  interaction shows up as a send event from one entity and a matching receive
  event from another. The two are expected to be **consecutive** in the trace —
  otherwise the algorithm should be able to identify and flag the *semantics of
  the event in between*.
- **Combined send-and-receive spans exist.** Some frameworks (e.g. Google ADK
  LLM spans, `ClaudeAgentSDK.query`) emit a single span representing both the
  send and the receive. These are handled as combined source-and-target spans
  (Step 2.c case 1).
- **Interleaved sources represent the same entity.** With multiple
  instrumentation sources (a2a and httpx, …) and traceparent on, events
  interleave: `a2a tool call → http send → … → http receive → a2a call
  receive`. The a2a-call and http-send events both represent the *same* entity;
  both receive events represent *another* entity (the caller / callee reading
  follows from send=caller). All events between a receive and a send belong to
  one entity.
- **Missing instrumentation splits the graph.** If a component emits no events,
  only one side is seen and the graph splits. If a component emits only *some*
  sources (e.g. a receiver with no a2a events:
  `a2a tool call → http send → … → http receive |`), traceparent is not
  forwarded, producing two traces and a split graph. Step 2.c's inferred peers
  stub the missing side within a trace; *cross-trace* stitching is deferred.

## Key decisions

**One base graph, no per-scope graphs, no cross-scope merge.**
All scopes contribute spans to a single base graph. Scope semantics are overlaid
by coloring nodes/edges in place (White → Teal / Blue). There is no separate
`ScopeGraph` per scope and no `XScopeGraph` merge step.

**Coloring is additive, not replacement.**
Coloring a node Teal or Blue never removes its White edges. This keeps trace
structure recoverable at every layer.

**Scope coloring is per `(scope, framework)` adapter.**
Each agentic scope has its own per-`(scope, framework)` adapter that produces
`SpanFacts` for every span. The current algorithm colors the openinference
agentic scope Blue; transport (Teal) coloring and standalone a2a / mcp scopes
are deferred. A new framework — even within openinference — is added in
isolation: drop a new adapter class into the registry. Schema drift is absorbed
by per-version schema-map entries; behavioural drift (new combined-span shapes,
new span-name conventions) lives in adapter code.

**Raw OTel attribute keys are isolated to the adapter layer.**
Every attribute lookup happens in `adapters.py`. The graph builder, the
classifier facade, and the extractor read only typed `SpanFacts` fields. A
framework attribute rename is a one-file edit. The classifier module
(`classifiers.py`) is a thin facade translating `SpanFacts` to the older
`AgenticClassification` shape the builder consumes.

**Natural-key prefixes (an implementation construct, not a spec vocabulary).**
The current spec specifies only a *hostname-or-`unknown`* entity key (Step 3.a);
it does **not** define a "natural key" or any prefix format. The following is an
**implementation decision**, not spec-derived algorithm intent. The merge key
produced by an adapter (`SpanFacts.natural_key`) has a fixed format:
`tool:<name>`, `llm:<model>`, `agent:<name>`. The prefix doubles as the entity's
coarse kind in the extractor and as the inferred node's display label when no
friendlier label is available. Adapters strip provider prefixes from model
strings (e.g. `anthropic/claude-3-7` → `claude-3-7`). Hostname / `service.name`
are deliberately *not* used as the *natural-key* — they would over-merge across
distinct entities behind the same proxy.

`service.name` is used only for entity *display naming* (`_entity_display_name`),
never as a merge/combine key.

**Same-interaction merge vs. same-entity combine — two graphs.**
The spec separates two operations across two graphs, and the code follows that
split: Step 2.d (`merge_identical_interactions`) merges same-*interaction*
nodes/edges on the execution-flow graph — folding an inferred peer into its
observed twin, merging observed↔observed same-interaction nodes, and collapsing
duplicate Teal chains — while the **Step 3.a semantic** sub-step
(`combine_identical_entities`) combines same-*entity* groups on the entity graph
(both inferred peers and observed entities) on the spec's Step 3.a heuristics.
See the as-implemented notes under Step 2.d and Step 3.a for the exact keys.

**Inferred peers are created in the core algorithm, not deferred.**
When only one side of a call is observed, Step 2.c materialises an inferred node
for the unobserved peer (recorded with the `is_inferred` boolean on unmerged
survivors) and connects it. This keeps the entity graph shape-consistent — every
observed entity participates in a complete source/target pair — and lets
consumers distinguish observed entities from inferred ones via the marker.

**Inferred identity is a boolean field, not a label convention.**
An inferred node that survives without being merged into an observed node is
identified by a dedicated boolean field — `is_inferred` on the colored-base-graph
node and `inferred` on the entity node — and never by parsing the `label` column.
The label is a human-facing display value and is free to change. The scratch-table
schemas in `cli.py` carry this column explicitly.

**Entity attributes are pooled from all spans in the component.**
Within a connected component, attributes from every contributing span are pooled
onto the resulting entity node. Internal plumbing nodes contribute their
attributes too. (The spec's Step 3.a groups component nodes into one entity but
is silent on attribute handling; pooling is an implementation choice.)

**Attribute sources are validated against the per-scope span reference.**
Every attribute an adapter consults must be validated against the span-table
reference for the framework that emitted it
(`openinference_telemetry_spans.md` for cross-framework openinference;
`openinference_openai_agents_v1.4.1_telemetry_spans.md` for openai_agents 1.4.1;
`openinference_anthropic_v1.0.6_telemetry_spans.md` for the anthropic /
`claude_agent_sdk` framework at 1.0.6; future references for httpx / starlette /
etc.). New attributes are not added on intuition; the reference is regenerated
from the upstream package and the attribute confirmed before it appears in
`_OI_ATTRS` or a per-version schema map. Each adapter records the framework
version(s) it has been verified against in a `documented_version` field.

**Cross-entity calls are the Teal region, not a separate edge signal.**
A cross-entity call is expressed entirely by the Blue/Teal structure the spec
describes: Step 2.c routes an inferred call through a Teal server and Step 2.a
colors observed transport spans Teal, and Step 3.a separates entities by
dropping Teal (edges touching any Teal node are cut, so the Teal transport chain
between two Blue components — a lone server is just the length-1 case — is where
two entities meet). No distinct cross-entity edge type or promotion pass is
needed. In-code names track the spec: `merge_identical_interactions` is Step 2.d,
`build_entity_graph` builds the Step 3 entity graph (nodes + edges), and
`combine_identical_entities` realises the Step 3.a *semantic combine*.

## Deferred to later stages

- **Additional agentic scopes (standalone a2a, standalone mcp).** The current
  algorithm colors the openinference agentic scope only. The OpenInference MCP
  adapter is registered but yields no entities (the MCP instrumentor only
  handles traceparent headers). Standalone a2a and mcp scopes are deferred.
- **Cross-scope attribute enrichment.** Pulling attributes from transport spans
  (httpx URL/host, starlette route, …) onto the agentic entities they describe;
  recognising an agentic caller span and a transport caller span on the same
  chain as the same real entity. (Transport spans are now colored Teal and
  dropped at the fuse; consuming their attributes is the enrichment stage.)
- **Cross-scope multi-agent composition (A2A).** When one agent delegates to
  another over A2A, the two agents' agentic spans are connected only through the
  transport (`httpx → starlette`) bridge between their services. Resolving the
  delegation to a cross-entity call between two observed agent entities (rather
  than an inferred `tool:` peer) depends on the callee's agent-root being
  recognised as a target — deferred.
- **Broken traceparent / disconnected base graphs.** When a Receive span has no
  traceparent link to its Send, the base graph is disconnected and Step 3.a
  naturally produces disconnected entity components. No special handling at this
  stage.
- **Hostname-based entity naming.** Step 3.a names entities from `service.name` /
  the natural-key suffix; the spec's *preferred* identifier, a **hostname**,
  remains deferred — it lives on transport spans that are dropped before fusing.
- **Cross-trace ("system graph") merging and name alignment.** Merging an
  inferred node in one trace with an observed node in another (when traceparent
  is not propagated and one logical interaction is split across two traces), and
  reconciling entity identifiers across runs. Deferred to a separate enrichment
  stage.
- **Combined source-and-target spans whose target emits its own spans.** Step
  2.c's duplicate approach is correct when the target has no observable spans.
  When the target *does* emit spans, they would appear as parents of the combined
  span and the duplicate-stands-alone model becomes incorrect. Deferred.
- **Entities from pure non-agentic calls.** A direct httpx call between two
  services with no agentic span on either side produces no entity at this stage.

## Considered alternatives

**Linear span-pattern matching** (the original prototype). Three passes over the
full span list using hard-coded ancestor walks. Conflates graph construction
with entity inference, cannot represent intermediate states, and requires
bespoke logic per interaction pattern.

**Per-scope graphs with cross-scope merge.** Build one `ScopeGraph` per scope,
then merge them via structural isomorphism plus label matching. Inspectable, but
pushes the hardest decisions into the merge step and forces every scope
(including non-agentic ones) to commit to a Send / Receive / Internal
classification up front. The current design moves that work into a
separately-defined enrichment stage and keeps the core algorithm focused on
scope coloring over a single base graph.

## Consequences

- The prototype writes three sets of scratch tables for inspection: the base
  graph (after Step 1); the colored execution graph (after Step 2 coloring,
  inference, and the Step 2.d merge); and the entity graph (after Step 3
  entity-node grouping and edge creation). The UI surfaces these as the
  execution graph **before** entity formation and the entity graph **after** it,
  in the "Graphs (proto)" tab.
- A trace captured only as a test fixture is not visible in the UI (the CLI
  sources spans from the `spans` table). The throwaway helper
  `data_governance.processors.p_interactions_proto.load_fixture` bridges this by
  inserting a fixture's spans into `spans` (via the receiver's `write_span`, so
  idempotent) and running the normal CLI processor. It mutates whatever
  `DATABASE_URL` points at, so it is disabled unless `PI_LOAD_FIXTURE_CONFIRM=1`
  is set.
- The colored-base-graph node row (`proto_colored_nodes`) carries an
  `is_inferred boolean NOT NULL DEFAULT false` column, and the entity-node row
  (`proto_entity_nodes`) carries an `inferred boolean NOT NULL DEFAULT false`
  column. These are the sole sanctioned signals for "is this an inferred node?"
  — the `label` column is display-only. On the `/proto/graphs/{trace_id}` wire
  the base/colored graphs expose `is_inferred` and the entity graph exposes
  `inferred`, and the UI reads whichever the graph carries.
- The "Graphs (proto)" UI surfaces inferred peers via an `inferred` marker pill
  (`marker-inferred`) and shows a count in the colored-graph and entity-graph
  summary lines. The pill is rendered by reading the boolean field; the label
  string is never inspected.
- Non-agentic scopes contribute no entities at this stage. Scopes that emit no
  agentic spans at all will not appear in the entity graph until the enrichment
  stage runs.
