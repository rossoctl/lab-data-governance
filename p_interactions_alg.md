## Overview

This document is a walkthrough of the P-interactions algorithm: how we go from
raw OTel spans to a graph of agentic entities and their interactions. It is
the readable companion to **ADR-0007** (`docs/adr/0007-p-interactions-graph-algorithm.md`),
which is the canonical reference. Where this document and the ADR diverge,
the ADR wins.

### The algorithm from 10,000 feet

The goal is to identify *agentic* entities (agents, LLM calls, tools, …) and
the interactions between them. Some entities map to Kubernetes-level objects
(a service, a pod); others live entirely inside a single container (e.g. an
in-process tool).

- **Input:** OTel spans from multiple instrumentation scopes — agentic scopes
  (openinference, a2a, …) and non-agentic scopes (httpx, starlette, …).
- **Output:** an *entity graph* whose nodes are agentic entities and whose
  edges are observed interactions between them.

The current algorithm covers the **openinference** agentic scope only. a2a
and mcp classifiers, and use of non-agentic spans for enrichment, are
deferred — see "Deferred" at the end.

### Observations that drive the design

When agentic protocols are well-instrumented, every call shows up as a
**send** event from one entity and a matching **receive** event from another.
The two should be *consecutive* in the trace; if anything agentic appears
between them, that is a signal — either we have an entity we did not classify,
or a span pattern we have not yet learnt to recognise.

A few cases break the simple send/receive picture and the algorithm has to
handle them:

- **Combined source-and-target spans.** Some SDKs record both the outgoing
  request and the incoming response in a single span (e.g.
  `openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query`,
  Google ADK LLM spans). One span, two roles.
- **Interleaved scopes.** With traceparent on, an a2a tool call and the
  underlying httpx send are part of the same chain:
  `a2a tool call → http send → … → http receive → a2a call receive`. The
  caller-side a2a span and the caller-side http span describe the *same*
  real entity; same on the receive side.
- **Partial instrumentation.** A peer that emits no agentic spans at all
  leaves us with only one side of the call. If traceparent is also lost
  (e.g. the receiver does not propagate it), the trace splits and the base
  graph is disconnected at that point. Both halves are still useful.
- **Mixed-scope entities.** All spans between a receive event and the next
  send event on the same chain belong to the same entity, regardless of
  which scope emitted them.

### Terminology

The algorithm uses two orthogonal vocabularies for nodes — **role** describes
what the node represents in an interaction; **structural descriptors**
describe how the node was produced.

**Roles**

- **Event node** — represents a local entity, recorded by an observed span.
- **Source node** — the caller side of an agentic protocol call.
- **Target node** — the callee side of an agentic protocol call.

**Structural descriptors**

- **Boundary node** — a Black node whose span was classified as a protocol
  boundary by its scope's classifier.
- **Duplicate node** — an additional Black node added by Step 2.b to split
  a combined source-and-target span into two role-distinct nodes.
- **Synthetic node** — a Black node added by Step 2.c to represent an
  unobserved peer; carries `synthetic: true`.

**Edges**

- **White edge** — parent/child relationship via OTel traceparent.
- **Gray edge** — order between two agentic-scope events whose underlying
  White chain does not pass through another Gray node.
- **Black edge** — a source/target relationship between agentic
  entities/components/containers.

A Black boundary node *plays* the Source or Target role depending on which
side of the call its span represents. A duplicate or synthetic node always
plays the *opposite* role of the boundary node it was created from.

Examples of caller-side and callee-side boundary spans (a2a, deferred but
illustrative):

- Source / caller side: `a2a.client.transports.jsonrpc.JsonRpcTransport.send_message`
- Target / receiver side: `a2a.server.request_handlers.default_request_handler_v2.DefaultRequestHandlerV2.on_message_send`

---

## Step 1 — Base graph

Construct one node per span and one White directed edge from each span's
parent to itself, following traceparent. The result is a single graph for
the entire trace whose connectivity mirrors trace structure exactly. All
nodes and edges start White.

## Step 2 — Agentic coloring

Coloring is **additive**: White edges are never removed when Gray edges are
added; Gray edges are never removed when Black edges are added. The full
White connectivity is preserved at every layer.

This step currently handles the **openinference** scope only. a2a, mcp, and
non-agentic scopes leave their spans White.

### Step 2.a — Color the base graph

Reference: `openinference_telemetry_spans.md` for the attribute set.

1. For every span belonging to the openinference instrumentation scope,
   color the node **Gray**.
2. For every pair of Gray nodes connected by a chain of White edges that
   does not pass through another Gray node, add a directed **Gray edge**
   between them in the same direction as the underlying chain.
3. The openinference classifier identifies which spans are *protocol
   boundaries* — caller or callee side of an agentic call (LLM invocation,
   agent-to-agent call, tool call, …). Color each boundary node **Black**.
   Gray nodes that are not boundaries (SDK plumbing, lifecycle hooks,
   framework dispatch) remain Gray.
4. For every Gray edge whose endpoints are both Black, add a **Black edge**
   in the same direction.

### Step 2.b — Combined source-and-target spans

Some boundary spans represent both sides of a call. Example:
`openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query`
records both the outgoing request and the incoming response in one span.

For each such Black node, create an additional Black **duplicate node**
referencing the same span. The original keeps its parent-side and child-side
Gray/White chains and plays the **Source** role. The duplicate has no
neighbours in the base graph and plays the **Target** role. Add two directed
Black edges: source→target (request) and target→source (response).

This is correct when the target side emits no spans of its own (the typical
case — an external LLM endpoint). When the target *does* emit its own spans,
the duplicate-stands-alone model breaks; that case is deferred.

### Step 2.c — Stub one-sided observations

A Black boundary node with no Black edges means the peer side was not
observed (missing instrumentation, a bug, or genuinely uninstrumented code
on the other side).

For every such Black node, create an additional Black **synthetic node**
referencing the same span and carrying `synthetic: true`. The original
retains its Source-or-Target role; the synthetic node plays the *opposite*
role. Add two directed Black edges between them (source→target,
target→source).

When both sides of a call are observed in the same trace, Step 2.a.4 has
already added a Black edge between them, so this step does not fire. A
Black node with no Black edges therefore reliably indicates an unobserved
peer.

### Step 2 — Annotation: between-boundary flag

Any Gray node that lies on a Gray chain between two Black boundary nodes is
annotated and surfaced in the "Graphs (proto)" UI tab. The intent is to
catch agentic spans that the classifier does not yet recognise as
boundaries — the in-between span is a signal that the per-scope span tables
or classifier may be incomplete. The annotation is informational; it does
not block entity formation.

## Step 3 — Agentic entity graph

The colored base graph still has one node per span. Step 3 collapses it
into an **entity graph** whose nodes are agentic entities and whose edges
are interactions.

### Step 3.a — Form entities

Compute connected components over the set of Gray and Black nodes,
considering only White and Gray edges (Black edges are ignored for this
purpose). Each connected component becomes one **entity node**. Attributes
from every span in the component are pooled onto the entity node. Each
Black edge in the colored base graph becomes a directed edge in the entity
graph between the entity nodes containing its endpoints. Synthetic nodes
propagate their `synthetic: true` marker onto the entity node they form.

### Step 3.b — Merge identical synthetic peers

Step 2.c materialises a synthetic Black node for every observed boundary
whose peer was not observed; in Step 3.a each becomes its own entity. When
the same real peer is the unobserved target of multiple calls (e.g. the
same tool invoked from two different agents in the same trace), Step 3.a
produces multiple synthetic entities that should collapse into one.

For every pair of synthetic entity nodes whose **source-span attributes
match** (e.g. the same `tool.name`, the same `llm.model_name`, the same
identifying attribute used by the originating boundary's classifier), merge
them into a single synthetic entity. The merged entity keeps the
`synthetic: true` marker and inherits all Black edges from the merged
peers, so every observed source that was pointing at any of the duplicates
now points at the single merged entity.

Only synthetic entities are merged. Observed entities (those formed from a
real boundary span on the peer side) are never fused at this stage.

### Step 3.c — Name nodes

Each entity node is assigned the ID `unknown` at this stage. Richer naming
— deriving an ID from the entity's pooled attributes (hostname from
non-agentic httpx/starlette enrichment, `service.name`, framework-specific
attributes such as `llm.model_name` / `tool.name` / `agent.name`) — is
deferred until the cross-scope enrichment stage exists.

---

## Decisions

- **One base graph, no per-scope graphs, no cross-scope merge.** All
  scopes contribute spans to the same base graph; agentic semantics are
  overlaid by coloring nodes/edges in place.
- **Edge coloring is additive.** White stays under Gray; Gray stays under
  Black. Trace structure remains recoverable at every layer.
- **Boundaries are detected only in agentic scopes.** Non-agentic scopes
  (httpx, starlette, …) are not consulted for boundary detection at this
  stage.
- **Each scope is developed and implemented separately.** A new agentic
  scope is added in isolation: a per-scope classifier plus a dispatch-table
  entry. Base graph, coloring rules, and entity-graph derivation are
  unchanged across scopes.
- **Observed peers are never fused; synthetic peers may be merged.** Two
  observed sides of a call become two distinct entities joined by a Black
  edge. Synthetic peers are merged in Step 3.b when their source-span
  attributes match.
- **Synthetic peers are created in the core algorithm, not deferred.**
  Step 2.c always materialises them so the entity graph stays
  shape-consistent across observed-both-sides and observed-one-side cases.
- **Entity attributes are pooled from all spans in the component.** Gray
  plumbing nodes contribute their attributes too — they are not treated as
  structure-only.
- **Attribute sources are validated against the per-scope span reference.**
  Every attribute used by the algorithm or its classifiers is confirmed
  against the span-table reference for the scope that emitted it
  (`openinference_telemetry_spans.md`, `asgi_telemetry_spans.md`, …)
  before being introduced.

## Deferred

- **Additional agentic scopes (a2a, mcp).** Classifiers and integration
  with Step 2.a are deferred.
- **Cross-scope attribute enrichment.** Pulling attributes from non-agentic
  spans (httpx URL/host, starlette route, …) onto agentic entities;
  recognising that an agentic caller and an httpx caller on the same chain
  represent the same real entity.
- **Richer entity naming.** Step 3.c assigns `unknown`; later stages
  derive an ID from the entity's pooled attributes.
- **Combined source-and-target spans whose target emits its own spans.**
  Step 2.b's duplicate-stands-alone model only handles targets that emit
  no spans of their own.
- **Broken traceparent / disconnected base graphs.** Step 3.a naturally
  produces disconnected components in this case; no special handling at
  this stage.
- **Entities from pure non-agentic calls.** A direct httpx call between
  two services with no agentic span on either side produces no entity at
  this stage.
