---
status: accepted; partly supersedes ADR-0020
---

# Readmit a topology graph — as a presentation *inside* the flow view, with Interaction diagram and Lineage tabs beside it

ADR-0020 deleted the trace-detail **Graph** view and its `@xyflow/react` + dagre
dependencies, and closed by saying that if a topology view were ever wanted again it
would be "a fresh, deliberate decision (**and a fresh ADR**), not a flag flip". This
is that ADR.

It readmits a topology graph and adds two neighbours, all three as **sub-tabs of the
Interaction flow view** rather than as peer views of the span tree. `?legs` now takes
five values:

| `?legs` | View | Reads |
| --- | --- | --- |
| `tree` (default) | Interaction forest | flow view's own two |
| `flat` | Flat leg list | flow view's own two |
| `diagram` | **Interaction diagram** — sequence diagram | flow view's own two |
| `graph` | **Execution Flow** — topology graph | flow view's own two |
| `lineage` | **Lineage** — the graph, highlighted | + trace data-lineage |

This supersedes ADR-0020's Consequences section. ADR-0020's *reasoning* is addressed
below rather than waved past: the redundancy and dependency-cost arguments were
correct about what they judged, and what changed is the thing being judged.

## Why ADR-0020's rejection does not carry over

ADR-0020 rejected a view with two specific properties. Neither holds here.

- **"Redundant with the Interaction flow view."** The deleted graph drew entities as
  nodes and *interactions* as edges — the same caller→callee relation the flow tables
  already tabulate, restated as a canvas. The Execution Flow graph draws **legs** as
  edges, tagged with `seq`. That is not the tables' relation: ADR-0025 split each
  interaction into request and response legs running in *opposite* directions, so an
  agent's data mostly arrives as the responses to its own calls. The per-leg,
  seq-ordered picture is what makes a data path legible, and it is precisely what a
  caller→callee table cannot show. The `lineage` tab then asks one further question of
  that same node/edge set — "where did this entity's data come from?" — which has no
  tabular equivalent at all.

- **"Does not earn the dependencies it costs."** This is the load-bearing change.
  ADR-0020's complaint was that the heaviest deps in the UI sat in the *initial
  bundle* for a view nobody used. `@patternfly/react-topology` (with the d3 and mobx
  it pulls in) is instead **lazy-loaded**: `FlowTables.tsx:47,62` wrap it in two
  `lazy()` calls over one `import()` specifier, so Rollup emits one chunk that is
  fetched only when `?legs=graph` or `?legs=lineage` is actually selected.

  Measured on a real `vite build`, that chunk is **286kB of JS (89kB gzipped) and
  39kB of CSS (4kB gzipped)**, and the initial chunk contains *zero* modules from
  react-topology, d3, or mobx. A reader who never opens those tabs downloads none of
  it; a reader who does pays one fetch on first open. The dep is also already a
  PatternFly package, so it shares the design system the rest of the UI is built on
  rather than introducing a second one.

  (Two numbers that circulated while this was being written are wrong and are
  corrected here rather than left to propagate: the cost is not "~388kB of JS and
  ~130kB of CSS" — the CSS figure was over 3x the built size — and **dagre is not a
  dependency at all**, transitive or otherwise. It is absent from `package.json` and
  from the chunk's sourcemap. See the layout note under *Considered alternatives*,
  which is the reason: this branch does its own layout.)

  The **Interaction diagram carries no new dependency at all** — it is hand-rolled
  SVG (`InteractionDiagram.tsx:183` records the choice and specifically declines
  react-topology for it).

## Why these are sub-tabs, not peer views

`TraceDetailPage`'s `ViewKey` stays a two-way switcher (`spans` | `flow`), so
ADR-0020's headline consequence — no third top-level view — is *kept*, not reversed.

The reason is dataset identity, not tab-bar economy. The span tree and the flow view
are peers because they read different things. All three new tabs read the flow view's
own two resources (`diagram` and `graph` read *exactly* those; `lineage` adds one
more, the trace's data-lineage, which the flow view already holds). A presentation of
a dataset belongs beside its other presentations. `/traces/{id}/graph` is accordingly
**gone** as a route and now redirects to `/spans` like any other unknown segment.

## Considered alternatives

- **Leave the graph out; extend the tables instead.** Rejected: the thing being shown
  is a *path* through legs, and a table cannot show a path without the reader
  reconstructing it by eye across rows. This is the case ADR-0020 did not have,
  because before ADR-0025 there were no legs to draw and before ADR-0028 there was no
  derived lineage to highlight.
- **Reinstate `@xyflow/react` + dagre, as ADR-0019 had them.** Rejected:
  `@patternfly/react-topology` comes from the design system already in use, and
  layout is not delegated to dagre — `lib/graph.ts` does its own layered
  (column, row) assignment, because the flow view needs columns that mean "hop
  distance from the caller" rather than whatever a generic layout engine picks.
- **One tab that switches mode internally.** Rejected: `?legs` is the URL's record of
  which presentation is on screen (ADR-0021), and a mode toggle inside a tab would be
  state the URL does not carry.
- **Ship the graph without the Lineage tab, deferring the highlight.** Rejected: the
  highlight is the reason the graph earns its place. Without it this is much closer to
  the view ADR-0020 correctly deleted.

## Consequences

- **`?legs` is a five-value enum** (`lib/flow.ts` `LegViewKey`, coerced by
  `parseLegViewKey`; unknown and absent both read as `tree`). The Lineage tab adds a
  second param, `?src` — the natural key of the single data source being traced —
  coerced syntactically by `parseLineageSource`, with the semantic "does this trace
  have that source?" question left to `lineageReachability.resolveSourceChoice`, which
  reports a mismatch as a `'stale'` state rather than silently blanking it. ADR-0028
  D14 requires a `source`, so there is deliberately **no auto-pick** of the first one.
- **ADR-0021's URL contract table is amended by this change**, not merely flagged: it
  gains `?legs=<key>` and `?src=<naturalKey>` rows and its catch-all row now names
  `/traces/{id}/graph` as retired here. That table declares itself the single source of
  truth for `/ui/` state, so leaving it stale while this ADR described the real contract
  would have put the authority and the facts in two different documents.
- **Leg-detail tab state is deliberately not in the URL** — `LegTabs.tsx`'s
  Request/Response and Payload/Classification/Lineage selections are component-local.
  This is a *new* exemption from ADR-0021 and is not covered by that ADR's existing
  pins carve-out, whose justification (transient, multi-set, many span ids) does not
  apply to a single-valued two-character choice. It is recorded here as a known debt
  rather than left as an unexplained comment in the component.
- **A jsdom `getBBox` stub returns.** ADR-0020 removed a `ResizeObserver` polyfill
  that existed solely so the graph could mount; `ui/src/test/setup.ts` now stubs
  `SVGElement.prototype.getBBox`, which jsdom does not implement at all, because
  PatternFly measures node labels and the per-edge `seq` tag during the commit phase
  where a throw unmounts the whole tree. The stub returns zeros, which PF reads as
  "not measured yet" and skips, so no test can mistake them for real geometry.
- **All graph geometry is therefore unasserted.** jsdom lays out no SVG, so column
  assignment, arc routing, and connector lanes are covered only by the pure `lib/`
  unit tests (`graph.test.ts`, `sequenceDiagram.test.ts`,
  `lineageReachability.test.ts`). `ui/e2e/` has **no** spec touching Execution Flow,
  Interaction diagram, Lineage, or the `?legs=` / `?src=` deep links — the only
  lineage reference there is a stubbed route in `classification.spec.ts`. Comments in
  `setup.ts` and `ExecutionFlowGraph.test.tsx` describe those Playwright assertions as
  though they exist; they do not. Since ADR-0021's whole premise is that deep links
  restore what is on screen, and the CSS-specificity fix in `af14b78` is by its own
  admission unverifiable under jsdom, this is the branch's largest test gap and the
  first follow-up.
- **`docs/PROJECT.md`'s "no topology view, no sequence diagrams, no classification
  overlays" is now false on all three counts** and needs updating.
- **`docs/ui-design.md` does not describe any of this.** It stops at the v1 span tree
  and is silent on the flow view, so it is no longer a complete UI standard.
