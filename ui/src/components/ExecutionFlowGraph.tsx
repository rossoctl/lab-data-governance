import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  EmptyState,
  EmptyStateBody,
  EmptyStateHeader,
  Spinner,
} from '@patternfly/react-core';
// DEEP imports, not the `@patternfly/react-topology` barrel — it keeps the
// barrel's pipelines / side-bar / context-menu subtrees out of the production
// bundle, and out of Vitest's module graph.
//
// The barrel is ALSO the specific thing that used to break the test run, and the
// control bar below is why that is worth spelling out. Every
// `@patternfly/react-icons` icon module (`search-plus-icon`, `expand-icon`, …)
// imports its factory with an EXTENSIONLESS relative specifier
// (`from '../createIcon'`), which Node's ESM loader rejects — so the icons are
// only loadable under test because `vite.config.ts` inlines
// `@patternfly/react-topology` into Vite's transform pipeline, which resolves
// extensionless specifiers the way a bundler does. That inlining covers these
// deep paths, so importing `TopologyControlBar` deeply is safe and needed no new
// Vitest config. Importing it through the BARREL is not the same thing: the
// barrel drags in sibling subtrees whose own dependencies are not covered, which
// is what killed the file before.
import {
  DefaultEdge,
  DefaultNode,
  GraphComponent,
  VisualizationProvider,
  VisualizationSurface,
} from '@patternfly/react-topology/dist/esm/components';
import {
  TopologyControlBar,
  createTopologyControlButtons,
  defaultControlButtonsOptions,
} from '@patternfly/react-topology/dist/esm/components/TopologyControlBar';
import { withPanZoom } from '@patternfly/react-topology/dist/esm/behavior';
import { DagreLayout } from '@patternfly/react-topology/dist/esm/layouts';
import { Visualization } from '@patternfly/react-topology/dist/esm/Visualization';
import {
  EdgeStyle,
  EdgeTerminalType,
  ModelKind,
  NodeShape,
  type ComponentFactory,
  type Graph,
  type Layout,
  type Model,
} from '@patternfly/react-topology/dist/esm/types';

// PF topology's own stylesheets (NOT in react-core's base.css — the graph is
// unstyled without them). Imported HERE rather than in main.tsx so they are part
// of this module's dependency graph and therefore ride the lazy chunk: a reader
// who never opens the Execution Flow tab downloads neither the JS nor the CSS.
//
// Moving them out of main.tsx inverts the load ORDER relative to global.css —
// these now land after it, not before — and that is safe because the two `.dg-*`
// rules that touch the graph do not depend on order:
//   - `.dg-graph-surface` names a class PF has no rule for at all, so there is
//     nothing to lose a tie against.
//   - `.dg-graph-node--isolated .pf-topology__node__background` is specificity
//     0,2,0 against PF's 0,1,0 `.pf-topology__node__background`, so it wins on
//     specificity regardless of which sheet came last.
//   - The `--dg-*` design tokens on `:root` are a disjoint namespace from PF's
//     `--pf-topology__*`, so nothing overwrites anything.
// main.tsx's original "imported BEFORE global.css so ours win on equal
// specificity" note was therefore guarding a tie that never actually existed.
import '@patternfly/react-topology/dist/esm/css/topology-components.css';
import '@patternfly/react-topology/dist/esm/css/topology-view.css';
import '@patternfly/react-topology/dist/esm/css/topology-controlbar.css';

import { useEntities, useInteractions } from '../api/hooks';
import { deriveGraph, type GraphEdgeSpec, type GraphNodeSpec } from '../lib/graph';
import { kindColorVar } from '../lib/entityKind';

/** Node box size. Fixed because Dagre needs a size before it can place anything. */
const NODE_DIAMETER = 40;

/**
 * Zoom factor per zoom-in click (and its reciprocal per zoom-out, so the two
 * buttons are exact inverses and n in / n out returns to the starting scale).
 * 4/3 is a perceptible step without overshooting a legible range in two clicks.
 */
const ZOOM_STEP = 4 / 3;

/** Padding, in px, left around the graph by Fit-to-screen. */
const FIT_PADDING = 24;

/**
 * The one custom node renderer: PF's `DefaultNode` with the entity's kind colour
 * pushed in through the two CSS variables PF's own node styles read.
 *
 * The colour comes from `lib/entityKind.kindColorVar`, which is the SAME map the
 * `EntityPill` in the flow tables uses — the graph does not own a second palette,
 * so a node and its table row can never disagree about what colour an `agent` is.
 * `kindColorVar` returns a `var(--pf-v5-c-label--m-<colour>__content--Color)`
 * reference rather than a literal, so the dark theme's overrides apply here too.
 */
function KindColouredNode({ element, ...rest }: React.ComponentProps<typeof DefaultNode>) {
  const data = element.getData() as GraphNodeSpec | undefined;
  const colour = kindColorVar(data?.kind ?? '');
  return (
    <g
      // The exact variables PF's own topology-components.css reads for a node's
      // outline and its label text (`.pf-topology__node__background` →
      // `--pf-topology__node__background--Stroke`, `.pf-topology__node__label__text`
      // → `--pf-topology__node__label__text--Fill`). Note the prefix is
      // `--pf-topology__…`, NOT `--pf-v5-topology-…`: setting the latter is a
      // silent no-op and leaves every node the default grey. Overriding them on
      // this wrapper tints the whole node by kind without a custom shape.
      style={
        {
          '--pf-topology__node__background--Stroke': colour,
          '--pf-topology__node__background--StrokeWidth': '2px',
          '--pf-topology__node__label__text--Fill': colour,
        } as React.CSSProperties
      }
    >
      <title>{`${data?.kind ?? 'entity'} — ${data?.naturalKey ?? ''}`}</title>
      <DefaultNode
        element={element}
        {...rest}
        truncateLength={24}
        // An isolated entity (no interaction names it) is shown with a dashed
        // outline so it reads as "present but unconnected" rather than looking
        // like a node whose edges failed to draw. See lib/graph's isIsolated.
        className={data?.isIsolated ? 'dg-graph-node dg-graph-node--isolated' : 'dg-graph-node'}
      />
    </g>
  );
}

/**
 * The one custom edge renderer: one **Interaction leg**, drawn with a directional
 * end terminal (the arrowhead that makes the leg's direction readable), the leg's
 * `seq` as its visible tag, and the error colour when THAT LEG failed.
 *
 * The visible text is the seq NUMBER, not the interaction's summary: a completed
 * interaction now contributes two edges, so the graph carries roughly twice the
 * labels it used to and prose would collide into unreadable overlap on short
 * arrows. The summary is still one hover away in the `<title>`.
 *
 * Colours come from the repo's own `--dg-*` tokens, no raw hex.
 */
function DirectedEdge({ element, ...rest }: React.ComponentProps<typeof DefaultEdge>) {
  const data = element.getData() as GraphEdgeSpec | undefined;
  const colour = data?.isError ? 'var(--dg-color-error)' : 'var(--dg-tree-guide)';
  return (
    <g
      // `--pf-topology__edge--Stroke` is the right lever, and the only one that
      // works from out here. The line (`.pf-topology__edge__link`) and the
      // ARROWHEAD (`.pf-topology-connector-arrow`) both paint from
      // `--edge--stroke`/`--edge--fill` — but `.pf-topology__edge`, a DESCENDANT
      // of this wrapper, re-declares both (`--edge--stroke:
      // var(--pf-topology__edge--Stroke); --edge--fill: var(--edge--stroke)`), so
      // a value set here for `--edge--stroke` is shadowed and silently ignored.
      // Setting the upstream var that its declaration reads instead lets the
      // cascade carry the colour through to both the line and the head.
      style={
        { '--pf-topology__edge--Stroke': colour } as React.CSSProperties
      }
    >
      {/* The interaction's summary, plus which leg of it this arrow is — the
          hover text, now that the visible label is the compact seq number. */}
      <title>{`#${data?.seq ?? '?'} ${data?.legType ?? ''} — ${data?.title ?? ''}`}</title>
      <DefaultEdge
        element={element}
        {...rest}
        // The arrowhead. Without an end terminal the edge is an undirected line
        // and this leg's direction — the whole point of the view — is unreadable.
        endTerminalType={EdgeTerminalType.directional}
        endTerminalSize={12}
        // The leg's `seq`, via PF's own connector tag rather than a hand-placed
        // <text>: it positions itself along the edge and rescales with the zoom,
        // which a bespoke label would have to reimplement.
        tag={data?.label}
        tagClass={data?.isError ? 'dg-graph-edge-tag dg-graph-edge-tag--error' : 'dg-graph-edge-tag'}
        className={
          data?.isError ? 'dg-graph-edge dg-graph-edge--error' : 'dg-graph-edge'
        }
      />
    </g>
  );
}

/** Map each model kind to its renderer. Stable identity — PF memoises on it. */
const componentFactory: ComponentFactory = (kind: ModelKind, type: string) => {
  if (kind === ModelKind.graph) return withPanZoom()(GraphComponent);
  if (type === 'dg-entity') return KindColouredNode;
  if (type === 'dg-interaction') return DirectedEdge;
  return undefined;
};

/**
 * The Execution Flow view: a directed graph of a trace's **Entities** (nodes) and
 * **Interaction legs** (edges), each edge labelled with that leg's trace-wide
 * `seq`.
 *
 * One edge per LEG, not per interaction: the request travels caller → callee and
 * the response travels back callee → caller (ADR-0025), so a completed
 * interaction draws two arrows pointing opposite ways at two different seqs. The
 * edge set comes from `flatLegRows` — the Flat table's own row derivation — so the
 * arrows and that table's rows are the same list in the same order by
 * construction.
 *
 * A third presentation of the same two reads the Interaction flow tables use
 * (`useEntities` / `useInteractions`) — no new endpoint. The tables answer "what
 * happened, in order"; this answers "who talked to whom". All node/edge
 * derivation, including every edge case, lives in `lib/graph.deriveGraph` so it
 * is testable without laying out an SVG (jsdom cannot measure one).
 *
 * Loading / error / empty states deliberately mirror `FlowTables`' conventions
 * (the same `Spinner` and `EmptyState` components, the same "may still be
 * draining" tone), because these are two tabs over one dataset and they must not
 * disagree about what "nothing here" looks like.
 */
export function ExecutionFlowGraph({ traceId }: { traceId: string }) {
  const entitiesQ = useEntities(traceId);
  const interactionsQ = useInteractions(traceId);

  const entities = useMemo(() => entitiesQ.data ?? [], [entitiesQ.data]);
  const interactions = useMemo(() => interactionsQ.data ?? [], [interactionsQ.data]);

  const spec = useMemo(() => deriveGraph(entities, interactions), [entities, interactions]);

  // One Visualization instance for the view's lifetime. Created lazily in state
  // (not a ref-with-side-effects) so React owns it; the layout + factory are
  // registered once here, and the model is pushed in by the effect below.
  const [controller] = useState<Visualization>(() => {
    const vis = new Visualization();
    vis.registerLayoutFactory((_type: string, graph: Graph): Layout =>
      // Dagre: a layered layout for DIRECTED graphs, which is what this is.
      // `rankdir: LR` puts callers left of callees so the arrows read like the
      // call actually flowed.
      new DagreLayout(graph, { rankdir: 'LR', nodesep: 24, ranksep: 56 }),
    );
    vis.registerComponentFactory(componentFactory);
    return vis;
  });

  // Push the derived spec into the topology model whenever it changes.
  useEffect(() => {
    const model: Model = {
      graph: { id: 'dg-graph', type: 'graph', layout: 'Dagre' },
      nodes: spec.nodes.map((n) => ({
        id: n.id,
        type: 'dg-entity',
        label: n.label,
        width: NODE_DIAMETER,
        height: NODE_DIAMETER,
        shape: NodeShape.ellipse,
        // The whole spec row rides along as `data` so the renderers read kind /
        // isIsolated / naturalKey without a second lookup.
        data: n,
      })),
      edges: spec.edges.map((e) => ({
        id: e.id,
        type: 'dg-interaction',
        source: e.source,
        target: e.target,
        // A self-call's source and target are the same point, so a straight line
        // would have zero length and be invisible. `dashed` at least marks the
        // node as carrying one; PF has no self-loop routing in 5.4.
        edgeStyle: e.isSelfCall ? EdgeStyle.dashed : EdgeStyle.solid,
        data: e,
      })),
    };
    // `false` for merge: the model is fully re-derived, so a stale node from a
    // previous trace must not survive. `true` to run the layout after.
    controller.fromModel(model, false);
  }, [controller, spec]);

  const isLoading = entitiesQ.isLoading || interactionsQ.isLoading;
  const isError = entitiesQ.isError || interactionsQ.isError;

  if (isLoading) return <Spinner aria-label="Loading execution flow graph" />;

  // A failed read is NOT an empty graph: "no entities" and "we could not ask"
  // demand different actions (wait vs retry), the same distinction ADR-0027's
  // LineageState draws. Reporting a fetch failure as an empty trace would be a
  // silent under-report.
  if (isError) {
    return (
      <Alert variant="warning" title="Could not load the execution flow" isInline>
        The entities/interactions read failed, so the graph cannot be drawn. This
        is a failed request, not an empty trace — retry rather than wait.
      </Alert>
    );
  }

  if (spec.nodes.length === 0) {
    return (
      <EmptyState>
        <EmptyStateHeader titleText="No execution flow" headingLevel="h4" />
        <EmptyStateBody>
          {'No entities or interactions for this trace yet, so there is nothing to graph. The interactions processor derives them from the spans table as spans arrive; this trace may still be draining.'}
        </EmptyStateBody>
      </EmptyState>
    );
  }

  return (
    <div data-testid="execution-flow-graph">
      {/* EDGE CASE, disclosed in the UI rather than only in a comment: an
          interaction whose caller or callee could not be resolved to an entity
          has no second endpoint, so neither of its legs can be an arrow. Counted
          once per INTERACTION (the unresolved participant is one defect on the
          identity row, shared by both legs — see lib/graph's DroppedInteraction),
          with the lost leg count spelled out separately so the arrow arithmetic
          still adds up for a reader comparing this to the Flat tab. */}
      {spec.dropped.length > 0 && (
        <Alert
          variant="info"
          isInline
          title={`${spec.dropped.length} interaction${spec.dropped.length === 1 ? '' : 's'} not shown as edges`}
          style={{ marginBottom: '0.5rem' }}
        >
          {`These interactions have an unresolved participant (no caller and/or callee entity), so they have no second endpoint to draw an arrow to: ${spec.dropped
            .map(
              (d) =>
                `${d.label} (missing ${d.missing}, ${d.legCount} leg${d.legCount === 1 ? '' : 's'})`,
            )
            .join('; ')}. They are still listed in full on the Interaction flow tab.`}
        </Alert>
      )}
      {/* EDGE CASE: entities that no interaction names. They ARE drawn (an
          entity is a governance fact on its own) with a dashed outline, and
          counted here so a reader knows the unconnected nodes are real data
          rather than edges that failed to render. */}
      {spec.nodes.some((n) => n.isIsolated) && (
        <Alert
          variant="info"
          isInline
          title={`${spec.nodes.filter((n) => n.isIsolated).length} isolated entit${
            spec.nodes.filter((n) => n.isIsolated).length === 1 ? 'y' : 'ies'
          }`}
          style={{ marginBottom: '0.5rem' }}
        >
          {'Drawn with a dashed outline: these entities were derived from the trace but no interaction names them as caller or callee, so they have no edges.'}
        </Alert>
      )}
      {/* EDGE CASE: two entities can interact more than once, and each
          interaction is its own governance fact — so every leg of each gets its
          own arrow rather than being collapsed into one with a count, which would
          lose the per-leg identity the rest of the UI keys on. Dagre fans
          same-pair edges apart on its own.

          Counted in INTERACTIONS, not edges: under the leg model a single
          completed interaction always puts two arrows (its request and its
          response) in the same channel, so counting edges would fire this notice
          on virtually every trace — noise that is always on carries no
          information. See lib/graph's parallelGroups. */}
      {spec.parallelGroups.length > 0 && (
        <Alert
          variant="info"
          isInline
          title={`${spec.parallelGroups.length} entity pair${spec.parallelGroups.length === 1 ? '' : 's'} with multiple interactions`}
          style={{ marginBottom: '0.5rem' }}
        >
          {'Each leg of each interaction is drawn as its own arrow rather than being merged into one, so the arrow count matches the leg count on the Flat tab.'}
        </Alert>
      )}
      <div className="dg-graph-surface">
        <VisualizationProvider controller={controller}>
          <VisualizationSurface />
          {/* Zoom controls. PF's own `TopologyControlBar` rather than four
              hand-rolled buttons, so they carry the design system's chrome, its
              tooltips and its accessible names (each button renders its label in
              a `pf-v5-screen-reader` span, so `getByRole('button', {name})`
              finds it), and so they drive the visualization's OWN zoom API
              (`Graph.scaleBy` / `fit` / `reset`) instead of a second, divergent
              notion of scale.

              Positioned absolutely at the bottom-left of the surface by
              `.dg-graph-controls` (global.css) — inside the surface's rounded,
              `overflow: hidden` box so it cannot escape it, and clear of the
              Dagre `rankdir: LR` layout, which grows left-to-right along the
              vertical centre and so leaves the bottom corner free of nodes.

              `legend: false`: the control bar offers a legend button by default,
              but this view has no legend panel to open, and a button that does
              nothing is worse than an absent one. */}
          <div className="dg-graph-controls">
            <TopologyControlBar
              controlButtons={createTopologyControlButtons({
                ...defaultControlButtonsOptions,
                zoomInCallback: () => controller.getGraph().scaleBy(ZOOM_STEP),
                zoomOutCallback: () => controller.getGraph().scaleBy(1 / ZOOM_STEP),
                // `fit` frames the whole graph with a small padding; `reset`
                // returns to 1:1 at the origin. They are genuinely different
                // answers to "I am lost", so both are kept.
                fitToScreenCallback: () => controller.getGraph().fit(FIT_PADDING),
                resetViewCallback: () => {
                  controller.getGraph().reset();
                  controller.getGraph().layout();
                },
                legend: false,
              })}
            />
          </div>
        </VisualizationProvider>
      </div>
    </div>
  );
}

// A DEFAULT export alongside the named one, purely so `React.lazy(() =>
// import('./ExecutionFlowGraph'))` in FlowTables needs no
// `.then(m => ({ default: m.ExecutionFlowGraph }))` shim. Same component, one
// definition — the named export stays because it is what this component's own
// test renders directly (no Suspense boundary needed there).
export default ExecutionFlowGraph;
