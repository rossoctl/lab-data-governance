# Delegation-forest generalization — nested multi-agent render over `spans`

ADR-0009 gives the execution-forest a **single anchor**: the innermost in-process
`AGENT` with `LLM` descendants, with every `L`/`TOOL` re-parented flat under that
one agent. That is correct for a **single-agent** app and was explicitly scoped
as such — ADR-0009 and `forest_logic.js` flagged *"delegation forests
(A → sub-agent) are a documented future generalization."*

For a **multi-agent delegation** trace (an orchestrator delegating over A2A to
sub-agents — each sub-agent's spans share the orchestrator's `trace_id` via
traceparent propagation) the single-anchor rule **silently mis-renders**:

- `findAnchor` picks the **deepest** `AGENT`, so it anchors on one sub-agent;
- the orchestrator, its planning `L`s, and every *other* sub-agent (with its
  `L`/`TOOL` nodes) are dropped by the `_descendsFrom(anchor)` filter;
- the delegation structure is lost — and it returns `ok:true`.

This was reproduced empirically on a real 4-agent trace (the `store_ops`
order-fulfillment app, `agent-examples-snp/apps/store_ops/`): the deployed forest
anchored on `inventory-agent` and dropped `ops-coordinator`, `pricing-agent`,
and `shipping-agent` entirely. See that app's `FOREST-VERIFICATION.md`.

## The rule (generalizes ADR-0009; span-kinds only, no app names)

- **Real agents.** A *real agent* is an in-process `AGENT` span that **directly
  owns ≥1 in-process `LLM`** — i.e. is that LLM's **nearest** in-process `AGENT`
  ancestor. This excludes the framework Runner-wrapper `AGENT` (`Agent workflow`,
  whose LLMs belong to the deeper inner agent) **generically** — it is exactly
  the ADR-0009 "innermost agent" rule applied per agent, with no name match. A
  single-agent trace has one real agent; an N-agent delegation has N.
- **Ownership.** Each in-process `LLM`/`TOOL` (and each sidecar `agent_to_tool`)
  attaches to its **nearest real-agent ancestor** — the agent that actually made
  the call. This **anchor-scopes the sidecar `agent_to_tool` filter**, fixing the
  ADR-0009 asymmetry where the sidecar tool filter was *not* anchor-scoped (so
  every tool attached to the one anchor regardless of caller).
- **Delegation edge.** A `TOOL` span that is an **ancestor of a real agent** is
  the agent-as-tool delegation wrapper (`openai_agents` wraps each A2A peer as a
  `FunctionTool`; the sub-agent appears across an httpx hop in its subtree). It is
  **collapsed** — not rendered as a tool call — and the sub-agent nests directly
  under its delegating agent (the sub-agent's nearest real-agent ancestor).
- **Top agent + U.** The top agent is the real agent with **no real-agent
  ancestor**; `U` is its root ancestor (the inbound request span), as in ADR-0009.
- **Backward-compatible & additive.** `buildForest` still returns
  `{ user, agent, children, llmCount, toolCount, ok }`. A sub-agent is an
  **additive** child of `role:'agent'` carrying its own nested
  `{ children, llmCount, toolCount }`. `agent`/`children`/`llmCount`/`toolCount`
  describe the **top** agent, so a single-agent trace's output is **byte-identical**
  to ADR-0009 (verified by deep-equality + the unchanged single-agent test suite).
  A flat renderer that only knows `'L'`/`'tool'` degrades gracefully (it sees a
  sub-agent as one extra node); a nesting renderer opts into `children`.

## Consequences

- **Generic.** The reconstruction is expressed only via the six observability
  literals (`source ∈ {in-process, sidecar}`,
  `openinference.span.kind ∈ {AGENT, LLM, TOOL}`,
  `lineage.hop.kind == agent_to_tool`) plus parent_id structure — no agent/tool
  names, stores, or routes. It holds for any multi-agent app and any framework
  that nests sub-agent work under the caller (A2A traceparent, or in-process
  agents-as-tools).
- **Single-agent unchanged.** Existing forest behavior and the ADR-0009 tests are
  preserved verbatim; the data contract is a superset.
- **UI rendering is separate.** This ADR is the **data layer** only. The existing
  `forest.html` renderer (flat `[U][A][children]`) will under-render a multi-agent
  trace (it shows the top agent and treats sub-agent nodes as extra cards). The
  nesting render is folded into the tree-graph renderer on the
  `feat/forest-store-overlay` branch in a later integration pass; `forest.html`
  is intentionally **not** touched here so the two branches stay conflict-free.
- **Status:** Accepted. Implemented in `data_governance/api/ui/forest_logic.js`;
  tested in `tests/api/test_forest_logic_js.py` (multi-agent fixture +
  single-agent regression guard).
