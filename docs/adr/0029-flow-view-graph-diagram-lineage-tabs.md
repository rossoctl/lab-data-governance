---
status: accepted; partly supersedes ADR-0020
---

# Readmit a topology graph — as a top-level trace view, with Interaction diagram and Lineage beside it

ADR-0020 deleted the trace-detail **Graph** view and its `@xyflow/react` + dagre
dependencies, and closed by saying that if a topology view were ever wanted again it
would be "a fresh, deliberate decision (**and a fresh ADR**), not a flag flip". This
is that ADR.

It readmits a topology graph and adds two neighbours, all three as **top-level views
of a trace**, peers of Span tree and Interaction flow. The trace-detail switcher goes
from two ways to five, each view owning a **path segment**:

| Path | View | Reads |
| --- | --- | --- |
| `/spans` (default) | Span tree | trace spans |
| `/flow` | Interaction flow — tables, `?legs=tree\|flat` | entities + interactions |
| `/diagram` | **Interaction diagram** — sequence diagram | entities + interactions |
| `/graph` | **Execution Flow** — topology graph | entities + interactions |
| `/lineage` | **Lineage** — the graph, highlighted | + trace data-lineage |

`?legs` keeps exactly its two pre-existing values (`tree` | `flat`) — see *Why these
are top-level views* for why the promotion stopped there.

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
  it pulls in) is instead **lazy-loaded**: `FlowTables.tsx` wraps it in two `lazy()`
  calls over one `import()` specifier, so Rollup emits one chunk that is fetched only
  when the Execution Flow or Lineage view is actually opened.

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

## Why these are top-level views

**This reverses a decision taken earlier on this same branch, and the reversal is the
point of this section.** The three new readings first shipped as `?legs` sub-tabs of
the Interaction flow view, on the argument that they are *presentations of the flow
view's own two reads* rather than peer datasets of the span tree. That argument is
still true as a statement about the **data** — and it turned out to be the wrong basis
for **navigation**.

What went wrong in practice: three of the five ways to read a trace were two clicks
deep and invisible until you had already found the Interaction flow tab and noticed a
second tab bar inside it. Dataset identity is a good reason to render two things
through one component; it is not a reason to hide one of them. So `ViewKey` is a
five-way switcher (`spans` | `flow` | `diagram` | `graph` | `lineage`) and the three
new readings sit beside Span tree and Interaction flow as equals.

ADR-0020's headline consequence — "no third top-level view" — is therefore **reversed,
not kept**. That consequence was downstream of ADR-0020's judgement that the graph did
not earn its place at all; once the graph *does* earn it (previous section), a rule
that exists only to keep it out has nothing left to protect.

**What stayed nested, and why the nesting did not simply disappear.** `Tree` and `Flat`
remain `?legs` sub-tabs under Interaction flow, because they are two renderings of
**one table** — the same rows, indented vs flattened. Promoting them too would put a
tab called "Tree" beside one called "Span tree" as if they were peers of comparable
weight, which they are not. This is why `?legs` still takes exactly `tree` | `flat`
and did not become a five-value enum.

**`/traces/{id}/graph` is a real path segment again**, having been one historically
before the graph moved into `?legs`. Old `?legs=` deep links are **redirected, not
dropped**: `?legs=diagram|graph|lineage` on the flow view resolve to the matching new
segment, carrying every other param across (`?iid`, `?eid`, `?src`) minus `legs`
itself — a link to a Lineage view with a chosen source must keep that source or the
redirect answers a different question than the link asked. `?legs=tree|flat` fall
through untouched, being live values still. The migration lives in
`LEGACY_LEGS_TO_VIEW` and is checked *before* the unknown-segment guard;
`parseLegViewKey`'s coercion of junk to `tree` is the last line of defence, not the
migration path.

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

- **`?legs` stays a two-value enum** (`tree` | `flat`, coerced by `parseLegViewKey`;
  unknown and absent both read as `tree`). Note that `lib/flow.ts`'s `LegViewKey` type
  still names all **five** presentations `FlowTables` can render — that type is about
  what the component draws, not about what the param accepts, and the three promoted
  readings are addressed by path segment instead. The Lineage view adds a
  param, `?src` — the natural key of the single data source being traced —
  coerced syntactically by `parseLineageSource`, with the semantic "does this trace
  have that source?" question left to `lineageReachability.resolveSourceChoice`, which
  reports a mismatch as a `'stale'` state rather than silently blanking it. ADR-0028
  D14 requires a `source`, so there is deliberately **no auto-pick** of the first one.
- **ADR-0021's URL contract table is amended by this change**, not merely flagged: it
  gains a path-segment row per promoted view and a `?src=<naturalKey>` row, and it
  records the legacy `?legs=` redirect. That table declares itself the single source of
  truth for `/ui/` state, so leaving it stale while this ADR described the real contract
  would have put the authority and the facts in two different documents. (An earlier
  revision of both documents described the sub-tab design this ADR reversed; that is
  corrected in place rather than left as a second contradictory contract.)
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
