# Execution-forest view — unified per-invocation render over `spans`

DG renders a per-trace tree from `spans.parent_id` at query time (ADR-0001, the
trace-tree view). For a Kagenti agent (`openai_agents` runtime + the lineage
Envoy sidecar) that raw tree is unreadable as an *execution forest*: it nests the
real work under framework wrappers (`Agent workflow`, `mcp_tools`, `turn`), buries
it in httpx/starlette plumbing, carries MCP-handshake noise, and **double-counts
every tool call** — one in-process `TOOL` span *and* one sidecar `TOOL` span for
the same invocation. The demo's target shape (`demo-patent-app/scenario.md` §7) is
a **flat** `U → A → { L, tools }`: each LLM/tool call a direct, sequential child
of the agent invocation.

We add an **execution-forest view**: a second UI view that derives that flat
forest from `spans` — a client-side derivation alongside the trace-tree
(`api/ui/forest_logic.js`, mirroring `trace_tree_logic.js`), fed by the existing
`get_spans(trace_id)` read path. **No new table, no migration** — `spans`
(verbatim, with `parent_id`) already encode the per-invocation forest.

## The rule

- **Derivation, not storage.** The forest is computed at query time from the
  trace's spans (ADR-0001 precedent). Every node maps to a real
  `(trace_id, span_id)`; nothing is synthesized.
- **Anchor A** = the **innermost in-process `AGENT`** span that has in-process
  `LLM` descendants — the agent, not the outer Runner wrapper (`Agent workflow`).
  Chosen as the deepest qualifying `AGENT` so the rule is framework-shape-agnostic
  rather than name-matched.
- **L children** = the in-process `LLM` spans that descend from A, re-parented
  **flat** onto A (the `turn`/`CHAIN` intermediates are dropped). There are
  typically ~3 per turn — the real tool-use loop; it is surfaced, not collapsed
  to one.
- **Tool children** = one node per `agent→tool` invocation, anchored on the
  in-process `TOOL` calls (authoritative, one per call). When the lineage sidecar
  is present, its `agent_to_tool` span is **preferred** for display (carries
  `lineage.*` / `trust.*` + wire bodies) and **de-duplicated** against the
  in-process `TOOL` span for the same call — matched by **trace structure** (the
  sidecar span nests under that `TOOL` span's httpx client span, so the `TOOL`
  span is one of its ancestors), **not** by any tool-name table or string
  munging. Per-invocation: repeated calls of one tool stay distinct nodes. Falls
  back to the in-process `TOOL` span when no sidecar twin exists; a sidecar-only
  call (no in-process `TOOL`) is kept on its own.
- **U→A** = the agent's **root ancestor** (the topmost span above A — the inbound
  request span, whatever it is named). Generic: no route-name match. (The sidecar
  `a2a` span is self-referential and lands in its own trace, so it is not used.)
- **Dropped (noise):** framework `CHAIN` wrappers (`Agent workflow` / `mcp_tools`
  / `turn`), httpx `CLIENT` / starlette `SERVER` plumbing, and MCP-handshake
  `agent_to_service` spans.
- **No datastore nodes.** The `tool→datastore` hop is uncaptured today (e.g. the
  non-HTTP Postgres wire; uninstrumented S3) — the forest stops at the tool
  boundary. The gap is surfaced, never synthesized.

## Why

- **It is a `spans` derivation, like the trace-tree.** ADR-0001 already derives
  tree shape at query time; this is the same posture with a curation pass. The
  non-trivial logic lives in a pure, Node-testable JS module
  (`forest_logic.js`) exactly as `trace_tree_logic.js` does, so it ships in the
  current vanilla-JS UI and survives the React migration (issue #48) as data-layer.
- **Distinct from the aggregate entity-graph (ADR-0007).** `entities`/`edges`
  collapse invocations into one node per `(service_name, semantic_kind, sub_kind)`
  — all `write_file` calls become one node. The forest keeps each **invocation**
  distinct (the precision the §7 view is built on). **Two graphs, two layers**;
  the forest is *not* built from `entities`/`edges`.
- **The unifier is proven.** The algorithm was prototyped standalone against a
  frozen span snapshot (agent-examples-snp `forest-baseline`) and matches §7 for
  both turns; this ADR ports it into DG as the deliverable.

## Considered alternatives

- **Render the raw trace-tree as the forest.** Rejected: that *is* the broken
  baseline (deep nesting, duplicate tool nodes, framework + handshake noise) — it
  cannot express §7.
- **Build the forest from `entities`/`edges` (ADR-0007).** Rejected: wrong grain.
  The aggregate graph collapses the per-invocation distinctness the forest exists
  to show.
- **Materialize a forest table.** Rejected per ADR-0001 — derive at query time;
  `spans` suffice and no marks layer needs stable forest rows (marks attach to the
  aggregate via `edge_annotations`).
- **Prefer the in-process `TOOL` span over the sidecar.** Rejected: the sidecar
  carries `trust.*`/`lineage.*` and the wire bodies; prefer it, fall back to
  in-process only when it is absent.

## Consequences

- A second UI view over the **same** `get_spans` read path — no new endpoint
  shape, no migration, no schema change. New routes `GET /forest/{trace_id}` +
  the `forest_logic.js` asset; `forest.html` reuses the trace-tree's span-detail
  panel for payload-on-click.
- The curation rules (anchor, noise set, sidecar dedup) are **Kagenti-agent
  shaped** (`openai_agents` + lineage sidecar). They generalize via span kinds
  (`AGENT`/`LLM`/`TOOL`) but may need tuning for other frameworks — out of scope
  for v1.
- D/F absence and the ~3-`LLM`-spans loop are documented, expected, and visible —
  not bugs to hide. The cross-session / clean-vs-confidential story the forest
  *cannot* tell is the designed blindness motivating the data graph (the §8 /
  `edge_annotations` layer), not this view's job.
