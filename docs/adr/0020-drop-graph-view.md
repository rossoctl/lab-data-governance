# Drop the entity/interaction Graph view; its React Flow + dagre deps leave with it

The trace-detail view shipped in ADR-0019 as a **three-way switcher**: Span
tree, Interaction flow, and a **Graph** view — a read-only `@xyflow/react` +
dagre canvas drawing entities as nodes and interactions as edges. This ADR
**removes the Graph view**, leaving a two-way switcher (Span tree | Interaction
flow), and deletes the graph-only dependencies (`@xyflow/react`, `dagre`,
`@types/dagre`) along with it.

This supersedes the part of ADR-0019 that lists the graph view as a shipped
deliverable (its "React Flow + dagre for graph views" stack choice and the
"three ported views plus the new React Flow + dagre graph view" cutover). The
rest of ADR-0019 — the single-image, backend-served-SPA topology — stands
unchanged.

## Why

- **Redundant with the Interaction flow view.** The Flow tab (`FlowTables`)
  already presents the same entity/interaction relationships the graph drew,
  in a tabular form that is more usable for the actual task: reading
  interaction summaries, following caller→callee links, and pinning an
  interaction's spans back into the tree. The graph restated that structure as
  a pan/zoom canvas without adding an inspection affordance the tables lack.
- **Unused, and it does not earn the dependencies it costs.** In practice the
  span tree plus flow tables cover the workflows; the graph view was not
  pulling its weight. `@xyflow/react` + dagre are the heaviest deps in the UI,
  and they carried a jsdom tax with them — a `ResizeObserver` polyfill in the
  Vitest setup existed *solely* so the graph could mount under jsdom. Removing
  the view lets that polyfill and the deps go, shrinking the served bundle.

## Considered alternatives

- **Keep the graph view.** Rejected for the reasons above: it duplicates the
  Flow view's information with a less useful affordance while being the single
  largest dependency cost in the UI. Consistency with `ui-v2`'s
  `TopologyGraphView` (which the graph mirrored) is not a goal for this
  single-team internal tool — ADR-0019 already declined `ui-v2`'s deployment
  topology on the same "smallest infra for a single-team tool" grounds.
- **Hide the tab but leave the code and deps in place.** Rejected: dead code
  behind a feature flag keeps the bundle weight and the jsdom polyfill for no
  benefit. If a topology view is ever wanted again it is a fresh, deliberate
  decision (and a fresh ADR), not a flag flip.

## Consequences

- **`TraceDetailPage` is a two-way switcher.** `ViewKey` drops `'graph'`, the
  Graph `<Tab>` and its render branch are gone, and the docstring reads
  "two-way switcher (Span tree | Interaction flow)".
- **Graph source and tests are deleted.**
  `ui/src/components/EntityInteractionGraph.tsx` and the pure-layout helper
  `ui/src/components/entityGraph.ts` are removed, together with their specs
  (`EntityInteractionGraph.test.tsx`, `entityGraph.test.ts`). Unit-test count
  drops accordingly.
- **Graph-only dependencies are removed.** `@xyflow/react`, `dagre`, and
  `@types/dagre` come out of `package.json`; `package-lock.json` is
  regenerated. `useInteractions` / `useEntities` in `api/hooks.ts` stay — they
  are shared with `FlowTables`, which was never a graph-only consumer.
- **The jsdom `ResizeObserver` polyfill is removed.** It existed only to let
  React Flow measure its canvas under jsdom (`ui/src/test/setup.ts`); with the
  graph gone nothing else needs it. The `scrollIntoView` polyfill for the
  SpanTree reveal path stays.
- **Playwright smoke coverage follows the UI.** The deep-link spec asserts the
  two-way switcher (no Graph tab), and the "switching to the Graph tab" test is
  replaced by an "Interaction flow tab activates" test.
