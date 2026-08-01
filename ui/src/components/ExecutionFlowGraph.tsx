import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import {
  withDragNode,
  withPanZoom,
} from '@patternfly/react-topology/dist/esm/behavior';
// `withSelection` and the event it fires, from the LEAF module rather than
// `behavior`'s own index — same discipline as everything else here. Note the file
// is `useSelection`, NOT `withSelection`: in 5.4.1 the HOC is a thin wrapper
// declared at the bottom of the hook's module, and there is no `withSelection.js`
// to import (a path guessed from the export name resolves to nothing).
import {
  SELECTION_EVENT,
  withSelection,
  type WithSelectionProps,
} from '@patternfly/react-topology/dist/esm/behavior/useSelection';
// `Node.setPosition` takes a geom `Point`, not a plain `{x, y}` — it stores the
// instance and PF's own code calls `.clone()` on it. Deep-imported from
// `geom/Point` (the leaf) rather than `geom` (that subtree's own barrel), same
// discipline as everything above.
import Point from '@patternfly/react-topology/dist/esm/geom/Point';
import { Visualization } from '@patternfly/react-topology/dist/esm/Visualization';
import {
  EdgeStyle,
  EdgeTerminalType,
  ModelKind,
  NodeShape,
  type ComponentFactory,
  type Model,
  type NodeModel,
  // `[x, y]`, which is how a bendpoint is expressed on an `EdgeModel` (PF's
  // `BaseEdge.setModel` turns each tuple into a geom `Point` itself). Same
  // already-inlined `types` leaf as everything above it, so no new Vitest config.
  type PointTuple,
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
import { deriveGraph, type GraphEdgeSpec, type GraphNodeSpec, type GraphSpec } from '../lib/graph';
import { deriveLineageHighlight } from '../lib/lineageGraph';
import { displayNamesByKey, lineageLabel } from '../lib/lineageLabels';
import { kindColorVar } from '../lib/entityKind';
import { LineageCoverageAlert } from './flow/LineageCoverageAlert';
import type {
  DataLineageByLeg,
  Entity,
  Interaction,
  LineageStatus,
} from '../types';

/**
 * Node box size. Fixed because the placement below is arithmetic on it, and
 * because a node whose size is unknown cannot be positioned at all.
 */
const NODE_DIAMETER = 40;

/**
 * How a node/edge participates in a highlight, when one is active.
 *
 * Rides on the element's `data` rather than reaching the renderers through a React
 * context, and that is a constraint rather than a preference: PF's
 * `componentFactory` is a plain function registered on the `Visualization` once at
 * module scope (it must be — see `DraggableKindColouredNode`'s note on why a fresh
 * component identity per render remounts every node), so the renderers are not
 * inside any provider this component could put around them. `element.getData()` is
 * the seam PF itself gives them, and they already read the whole spec row through
 * it.
 *
 * `'none'` is the value when NO highlight is active — the Execution Flow tab's
 * every element. It is a distinct value from `'dimmed'` on purpose: "no question
 * has been asked" must not look like "asked, and this is not part of the answer".
 * Rendering an un-highlighted graph as wholly dimmed would be exactly that lie.
 */
type HighlightRole = 'none' | 'selected' | 'source' | 'carrier' | 'dimmed';

/** What the renderers read off `data`: the derived spec plus its highlight role. */
type NodeData = GraphNodeSpec & { highlight: HighlightRole };
/**
 * An edge's `data`: its derived spec, its highlight role, and whether its parent
 * INTERACTION is the flow view's selected one.
 *
 * `isSelected` is a SECOND, INDEPENDENT axis from `highlight`, not a sixth
 * `HighlightRole`. The two answer different questions and can both be true at once:
 * the highlight answers "is this part of the lineage answer for the entity I asked
 * about", the selection answers "is this the interaction whose detail panel is
 * open". A reader can perfectly well select an entity for lineage AND click an
 * arrow, so folding selection into the role enum would make one of the two
 * unrepresentable — the same reasoning that already keeps `--error` and the
 * highlight role as separate classes on the node and the edge.
 *
 * Per INTERACTION, not per leg, which is why the flag is resolved from
 * `interactionId` rather than from the edge's own id. An interaction's two legs are
 * two edges, the selection is the interaction, and lighting only the clicked leg
 * would tell the reader that legs are separately selectable — which they are not,
 * here or in the Flat table (see `FlatLegsTable`'s note) or in the Interaction
 * diagram (which lights both of its messages for the same reason).
 */
type EdgeData = GraphEdgeSpec & { highlight: HighlightRole; isSelected: boolean };

/**
 * The highlight a caller asks the graph to draw: which nodes are sources of the
 * selected entity's data, which edges carried it, and which node was selected.
 *
 * Optional on {@link EntityGraph} — absent means "draw the plain graph", which is
 * what the Execution Flow tab passes. The graph itself computes NOTHING about
 * lineage: the sets arrive already derived from `lib/lineageGraph`, so this
 * component stays the one renderer of one graph and the lineage question stays in
 * a pure, testable module (jsdom cannot measure an SVG).
 */
export interface GraphHighlight {
  /** The node the reader selected, marked distinctly from its sources. */
  selectedNodeId: string | null;
  /** Node ids that are direct sources of the selected entity's data. */
  nodeIds: readonly string[];
  /** Edge ids (`<interaction>:<leg_type>`) whose legs carried that data. */
  edgeIds: readonly string[];
}

/**
 * The highlight role of one node / edge, or `'none'` when no highlight is active.
 *
 * The DE-EMPHASIS is the load-bearing half: everything outside the answer becomes
 * `'dimmed'`, rather than only the answer becoming brighter. In a graph of any
 * density "slightly brighter" is not findable — the reader has to compare every
 * node against every other to spot it — whereas dimming the rest leaves the answer
 * as the only thing at full strength. The CSS then carries the distinction in
 * opacity and stroke weight (`global.css`), NOT in hue, so it survives a
 * colour-vision deficiency: the node colours are already the entity KIND's
 * (`lib/entityKind`), which means hue is spoken for and cannot also encode this.
 */
function roleOf(id: string, h: GraphHighlight | undefined, isNode: boolean): HighlightRole {
  if (!h) return 'none';
  if (isNode && h.selectedNodeId === id) return 'selected';
  const inAnswer = isNode ? h.nodeIds.includes(id) : h.edgeIds.includes(id);
  // `'source'` for a node in the answer, `'carrier'` for an edge that delivered it
  // — two names because the two are different claims and the stylesheet treats
  // them differently (a lit arrow reads as a route, a lit node as an origin).
  return inAnswer ? (isNode ? 'source' : 'carrier') : 'dimmed';
}

/**
 * The layered grid's pitch, in px: one step per COLUMN across, one per ROW down.
 *
 * The two axes carry two different facts, which is the whole of what makes the
 * picture readable and is why they are separate constants rather than one:
 *   - across = call DEPTH (`lib/graph`'s `column`), so "A calls B" draws A left
 *     of B;
 *   - down = CHRONOLOGY within a depth (`row`), so "A calls B then A calls C"
 *     draws B above C.
 *
 * This replaced a diagonal staircase that stepped BOTH axes per entity. That put
 * every node on one line, which had two consequences the layered grid exists to
 * remove: an edge from slot 0 to slot 3 ran straight over slots 1 and 2, and
 * siblings could not stack — a flat sequence has no way to say "B above C".
 *
 * The column step is the larger of the two because a label is far wider than it
 * is tall, so horizontally adjacent labels collide long before vertically
 * adjacent ones do.
 */
const COLUMN_STEP_X = 220;
const ROW_STEP_Y = 96;

/**
 * Which way the columns grow: `1` = deeper calls further RIGHT, `-1` = further
 * LEFT.
 *
 * THE ONE LINE TO FLIP, kept because the axis it flips is now MEANINGFUL (it was
 * a bare aesthetic choice under the staircase, where both axes advanced
 * together). Left-to-right is what the user confirmed — "if A calls B, A can be
 * to the left of B" — and it also reads with the page and with the two flow
 * tables this tab sits beside. The ROW axis is deliberately NOT flippable: rows
 * are chronological and time reads downwards, so there is no second reading to
 * offer.
 */
const COLUMN_DIRECTION = 1;

/**
 * The grid's own margin from the surface origin, so the first node is not
 * half-clipped at (0,0) before the reader has panned anywhere.
 */
const GRID_ORIGIN = NODE_DIAMETER;

/**
 * Place one entity from its layered (column, row) cell. Fixed arithmetic, so the
 * same cell is the same pixel on every render and every reload.
 *
 * This is the RENDERING half of the layout; the cell itself comes from
 * `lib/graph`'s `column`/`row` and neither the depth walk nor the sibling
 * ordering is repeated here (single source of truth). The node's position is its
 * CENTRE in PF topology, which is why the diameter is what the origin offset is
 * expressed in.
 */
function gridPosition({ column, row }: { column: number; row: number }): { x: number; y: number } {
  return {
    x: GRID_ORIGIN + column * COLUMN_STEP_X * COLUMN_DIRECTION,
    y: GRID_ORIGIN + row * ROW_STEP_Y,
  };
}

/**
 * The bendpoints for one edge, or `[]` for a straight line.
 *
 * THE COLUMN-SKIPPING EDGE is the problem this solves, and it is the one the
 * layered grid cannot solve by placement alone. Columns are discrete, so an edge
 * between ADJACENT columns has nothing between its endpoints and draws straight
 * with no risk. But an edge from column 0 to column 3 crosses columns 1 and 2,
 * and a straight line through them passes over whatever nodes sit there —
 * exactly the complaint that motivated this rewrite. So such an edge is routed:
 * a single bendpoint lifts it OFF the direct line, into the gutter above the rows
 * it would otherwise cross.
 *
 * A SAME-COLUMN edge gets the same treatment for the same reason: two nodes in
 * one column with a third between them would have that third drawn over, and the
 * gutter here is horizontal (to the side of the column) rather than vertical.
 *
 * WHY ONE BENDPOINT AND NOT AN ORTHOGONAL ROUTE. One point is enough to clear the
 * intervening cells, and PF renders a bendpointed edge as a polyline through it,
 * so the arrow visibly detours rather than cutting through — which is the whole
 * readable fact. A full orthogonal router (per-lane channel assignment, corner
 * radii) is a much larger thing, and it would have to be re-derived on every
 * drag: a bendpoint is stored geometry, and a dragged node's edges would keep
 * their stale detour. Keeping the detour to one point that is a pure function of
 * the two ENDPOINT CELLS means it is recomputed with the model and stays honest.
 *
 * THE OFFSET SIGN alternates with the edge's `seq` parity, so two edges over the
 * same span — a request and its response, or two parallel interactions — detour
 * to OPPOSITE sides instead of tracing the same polyline. That is what stops
 * same-pair edges from drawing exactly on top of one another, which the staircase
 * did (and which its own comment admitted). Parity, not an index into the group,
 * because it needs no second pass over the edge list and `seq` is already unique
 * per leg.
 */
function edgeBendpoints(
  edge: GraphEdgeSpec,
  cellById: ReadonlyMap<string, { column: number; row: number }>,
  // `[x, y]` tuples, not `Point`s: `EdgeModel.bendpoints` is `PointTuple[]` and
  // `BaseEdge.setModel` is what constructs the `Point`s from them. Handing it
  // `Point`s instead type-errors, and would be the wrong side of that seam anyway.
): PointTuple[] {
  // A self-call has one endpoint; PF draws it as a degenerate line and the dashed
  // style is what marks it (see the model builder). A bendpoint would not help.
  if (edge.isSelfCall) return [];
  const from = cellById.get(edge.source);
  const to = cellById.get(edge.target);
  if (!from || !to) return [];

  const spans = Math.abs(to.column - from.column);
  // Adjacent columns: nothing in between, so a straight line crosses nothing.
  // This is the common case (a request to the entity you call) and it stays the
  // clean, unbent arrow it should be.
  if (spans === 1) return [];

  const a = gridPosition(from);
  const b = gridPosition(to);
  // Opposite sides for the two legs of a pair, so they do not retrace one line.
  const side = edge.seq % 2 === 0 ? 1 : -1;

  if (spans === 0) {
    // SAME COLUMN. The gutter is horizontal: push the midpoint sideways, clear of
    // the column's own nodes, so the arrow bows out around whatever sits between
    // the two rows instead of running down through them.
    return [[a.x + side * (COLUMN_STEP_X / 2), (a.y + b.y) / 2]];
  }
  // SKIPPING one or more columns. The gutter is vertical: lift the midpoint above
  // (or below) the rows the direct line would cross. Scaled by how far it skips,
  // so a longer detour clears more and two edges spanning different distances do
  // not land on the same bend.
  return [[(a.x + b.x) / 2, (a.y + b.y) / 2 + side * spans * (ROW_STEP_Y / 2)]];
}

/**
 * The graph element's own id in the topology model.
 *
 * A named constant rather than a literal in two places, because the selection
 * handler has to RECOGNISE it: PF's `withSelection` on the graph reports a
 * background click as this id (the graph is itself a selectable element), and
 * "the reader clicked empty canvas" is only distinguishable from "the reader
 * clicked something we do not know about" by comparing against it. See the
 * SELECTION_EVENT subscription.
 */
const GRAPH_ID = 'dg-graph';

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
  const data = element.getData() as NodeData | undefined;
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
        // Two independent facts, so two independent classes rather than one
        // combined state: `--isolated` is a property of the TRACE (no interaction
        // names this entity — see lib/graph's isIsolated) and the highlight role is
        // a property of the reader's current QUESTION. An isolated node can be a
        // dimmed one, and squashing them into a single class would make one of the
        // two unrepresentable.
        className={[
          'dg-graph-node',
          data?.isIsolated ? 'dg-graph-node--isolated' : '',
          // `'none'` (no highlight active) deliberately emits no class at all, so
          // the Execution Flow tab's DOM is byte-identical to what it was before
          // the highlight existed and cannot pick up a dimming rule by accident.
          data && data.highlight !== 'none' ? `dg-graph-node--${data.highlight}` : '',
        ]
          .filter(Boolean)
          .join(' ')}
      />
    </g>
  );
}

/**
 * The one custom edge renderer: one **Interaction leg**, drawn with a directional
 * end terminal (the arrowhead that makes the leg's direction readable), the leg's
 * `seq` as its visible tag, the error colour when THAT LEG failed, and CLICKABLE to
 * select its parent interaction.
 *
 * The visible text is the seq NUMBER, not the interaction's summary: a completed
 * interaction now contributes two edges, so the graph carries roughly twice the
 * labels it used to and prose would collide into unreadable overlap on short
 * arrows. The summary is still one hover away in the `<title>`.
 *
 * Colours come from the repo's own `--dg-*` tokens, no raw hex.
 *
 * THE CLICK IS PF'S OWN `onSelect`, forwarded to `DefaultEdge` and nothing more.
 * `DefaultEdge` binds it as `onClick` on the outer `<g>` that carries the whole
 * edge (`components/edges/DefaultEdge.js`: `<g ref={hoverRef}
 * data-test-id="edge-handler" className={groupClassName} onClick={onSelect}>`), so
 * forwarding the prop is the entire wiring — there is no hand-rolled listener, no
 * `ref`, and no second hit target to keep in sync with the arrow's geometry.
 *
 * REJECTED: a bespoke `onClick` on the wrapper `<g>` here. It would work, but it
 * would have to reinvent the two things `DefaultEdge` already does correctly — the
 * hit area (below) and `pf-m-selected` — and it would put the handler OUTSIDE the
 * element PF hangs its own hover/drag state on, so a click during an edge drag
 * would still fire. Forwarding the prop PF already reads is strictly less code
 * doing strictly more.
 *
 * THE HIT TARGET IS ALREADY WIDE, verified in the library rather than assumed, and
 * this is the reason nothing like `InteractionDiagram`'s hand-rolled full-width hit
 * strip is needed here. `DefaultEdge` renders TWO paths inside that clickable `<g>`:
 * the visible `.pf-topology__edge__link` and, first, a
 * `.pf-topology__edge__background` tracing the same route — which
 * `css/topology-components.css` gives `stroke-width: 10px; stroke: transparent`.
 * A transparent STROKE (unlike a transparent fill) is hit-testable, and the only
 * `pointer-events` rule PF puts on an edge at all is `pointer-events: none` while
 * `.pf-m-dragging`. So the clickable band is ~10px wide along the whole polyline,
 * bendpoints included, not the 1.5px the reader can see. The sequence diagram had
 * to build its own strip because it is hand-rolled SVG with no such layer;
 * duplicating one here would be a second hit target competing with PF's.
 *
 * `pf-m-selectable` IS ours to add, though, and it is the one gap. PF's own
 * `TaskEdge` emits it (`onSelect && 'pf-m-selectable'`) but `DefaultEdge` never
 * does, and it is what flips `--edge--cursor` from `default` to `pointer`. Without
 * it the edge is clickable but does not LOOK clickable, which is a worse defect
 * than it sounds: a reader who never guesses the arrow is a target gets none of
 * this feature.
 */
function DirectedEdge({
  element,
  onSelect,
  ...rest
}: React.ComponentProps<typeof DefaultEdge> &
  // `onSelect` is taken from `withSelection`; its sibling `selected` is dropped at
  // RUNTIME just below (see `pfProps`), because omitting it from the type alone does
  // not stop the HOC injecting it — the type says what this component reads, not what
  // it is handed.
  //
  // WHY THE PROP IS REFUSED, since `withSelection` does inject it and forwarding it to
  // `DefaultEdge` — which would apply `pf-m-selected` from it — is the obvious first
  // move. It is wrong here on two counts, both found by watching what PF actually
  // emits rather than reasoned from the prop's name:
  //
  //   - IT IS PER-LEG, NOT PER-INTERACTION. `selected` is true only for the element
  //     whose id is in PF's `selectedIds`, so a click on the request leg marked THAT
  //     ARROW and left its response sibling unmarked — contradicting the
  //     per-interaction contract every other view follows (`FlatLegsTable`,
  //     `InteractionDiagram`), and telling the reader that legs are separately
  //     selectable, which is the one thing the treatment must not say.
  //   - IT IS CLICK-ONLY. PF's state is populated by its own click handler, so a
  //     selection restored from a `?iid` URL — a deep link, a reload, a back button —
  //     leaves it empty and the arrow unmarked: a silently missing treatment in exactly
  //     the case a reader cannot re-trigger by clicking, since the panel is already
  //     open.
  //
  // So the visible treatment comes wholly from `isSelected` on the element `data`
  // (resolved from the app's own selection, which is the one that survives a reload),
  // and PF's selection state is used for nothing but firing the event.
  Omit<WithSelectionProps, 'selected'>) {
  const data = element.getData() as EdgeData | undefined;
  const colour = data?.isError ? 'var(--dg-color-error)' : 'var(--dg-tree-guide)';
  // `selected` stripped HERE rather than in the destructure above, because the lint
  // config permits no unused binding and a `_`-prefixed one would need a rule change to
  // exempt. Deleting the key off a shallow copy says the same thing with no config
  // change and no disable comment — and it is the runtime drop that actually matters:
  // the HOC injects the prop regardless of what the type declares.
  const pfProps: Record<string, unknown> = { ...rest };
  delete pfProps.selected;
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
      // KEYBOARD OPERABILITY, on the same terms the Interaction diagram's message rows
      // get it: an SVG `<g>` is inert by default, so the role/tabIndex/onKeyDown trio is
      // what makes the arrow reachable and activatable without a mouse. Enter and Space
      // both fire, as a native button would.
      //
      // THIS WRAPPER IS THE RIGHT PLACE, and it is the only place available: PF renders
      // its own `<g>` (the one carrying the click) INSIDE this one and exposes no seam
      // to put attributes on it — `DefaultEdge` passes only `className` through, never
      // arbitrary DOM props, and gives the edge no `tabIndex`, `role` or key handler of
      // its own in 5.4.1. This wrapper is the edge element's sole child, so focusing it
      // focuses the whole arrow and there is no competing focus target inside.
      //
      // WHAT THIS HONESTLY DOES NOT GIVE, stated rather than glossed:
      //   - THE TAB ORDER IS DOCUMENT ORDER, which for edges is `seq` order (the model's
      //     edge array) — not spatial order, and not grouped by interaction. A reader
      //     tabbing through a busy trace walks every leg of every interaction in
      //     chronological order. That is a defensible order (it is the Flat tab's order)
      //     but it is not a chosen one, and with two legs per interaction a large trace
      //     is a long tab sequence with no skip affordance. PF offers no roving-tabindex
      //     or focus-group mechanism to build one on, and hand-rolling one would mean
      //     owning focus for elements PF re-renders and re-layers on hover and drag.
      //   - THERE IS NO KEYBOARD PATH TO PAN OR ZOOM to a focused edge that is off
      //     screen, so a focused arrow may not be visible. `.dg-graph-edge:focus-visible`
      //     draws a ring (global.css) but cannot scroll the surface to it.
      //   - NOTHING ANNOUNCES THE SELECTION CHANGE. The detail panel is not a live
      //     region and focus does not move into it, so a screen-reader user gets no
      //     confirmation beyond the arrow's own label.
      // The tables remain the fully keyboard-equivalent route to every interaction —
      // which is why this is an addition to them rather than the only way in.
      role="button"
      tabIndex={0}
      // Named so the focus ring can be stated on the element that actually RECEIVES
      // focus. `dg-graph-edge` itself lands on PF's inner `<g>` (via `className`), which
      // is not focusable, so a `:focus-visible` rule keyed on it would never match.
      className="dg-graph-edge-focus"
      // The same facts the `<title>` carries, as the accessible NAME: an arrow whose
      // label was only "edge" would be reachable and unidentifiable.
      aria-label={`Interaction leg, seq ${data?.seq ?? '?'}, ${data?.legType ?? ''}: ${
        data?.title ?? ''
      }${data?.isError ? ' (failed)' : ''}`}
      // Reflects the app's selection (per INTERACTION, so both legs read as pressed
      // together) — the state a sighted reader gets from the weight/dash treatment.
      aria-pressed={data?.isSelected ?? false}
      onKeyDown={(e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        // Space would otherwise scroll the surface's container.
        e.preventDefault();
        // Reuses PF's own `onSelect` — the identical path a click takes, including its
        // toggle-to-deselect — rather than a second activation route that could drift
        // from it. `onSelect` only reads `stopPropagation` off the event, which a
        // keyboard event carries too.
        onSelect?.(e as unknown as React.MouseEvent);
      }}
    >
      {/* The interaction's summary, plus which leg of it this arrow is — the
          hover text, now that the visible label is the compact seq number. */}
      <title>{`#${data?.seq ?? '?'} ${data?.legType ?? ''} — ${data?.title ?? ''}`}</title>
      <DefaultEdge
        element={element}
        {...pfProps}
        // THE CLICK TARGET. Forwarded verbatim to the `<g>` PF already binds it on
        // (see this component's note); `withSelection` below is what supplies it.
        onSelect={onSelect}
        // The arrowhead. Without an end terminal the edge is an undirected line
        // and this leg's direction — the whole point of the view — is unreadable.
        endTerminalType={EdgeTerminalType.directional}
        endTerminalSize={12}
        // The leg's `seq`, via PF's own connector tag rather than a hand-placed
        // <text>: it positions itself along the edge and rescales with the zoom,
        // which a bespoke label would have to reimplement.
        tag={data?.label}
        tagClass={[
          'dg-graph-edge-tag',
          data?.isError ? 'dg-graph-edge-tag--error' : '',
          // The tag follows its own arrow into the dim, or it becomes a bright
          // number floating over a faded line — the most eye-catching thing left on
          // screen and the least relevant. Same reasoning that already ties an error
          // tag's colour to its error arrow: one leg, one signal.
          data && data.highlight !== 'none' ? `dg-graph-edge-tag--${data.highlight}` : '',
          // The tag comes back OUT of the dim with its arrow when the interaction is
          // selected: a selected-but-dimmed leg whose seq number stayed faded would
          // be the one arrow the reader is looking at and the one number they cannot
          // read. See `dg-graph-edge-tag--selected` in global.css.
          data?.isSelected ? 'dg-graph-edge-tag--selected' : '',
        ]
          .filter(Boolean)
          .join(' ')}
        className={[
          'dg-graph-edge',
          data?.isError ? 'dg-graph-edge--error' : '',
          // Independent of `--error`, same reasoning as the node's two classes: a
          // failed leg can be the leg that carried the data, and the reader needs to
          // see both.
          data && data.highlight !== 'none' ? `dg-graph-edge--${data.highlight}` : '',
          // A THIRD independent axis, for the same reason again: the selected
          // interaction's leg can also be an error leg, and can also be dimmed by an
          // active lineage highlight. All three have to be able to show at once.
          data?.isSelected ? 'dg-graph-edge--selected' : '',
          // PF's cursor modifier, which `DefaultEdge` does not add for itself (its
          // sibling `TaskEdge` does). Emitted unconditionally rather than gated on
          // `onSelect`, because this renderer is only ever reached through
          // `withSelection` — a gate here would read as "sometimes not selectable"
          // and describe a state that does not exist.
          'pf-m-selectable',
        ]
          .filter(Boolean)
          .join(' ')}
      />
    </g>
  );
}

/**
 * The node renderer, made DRAGGABLE.
 *
 * `withDragNode` with no spec is PF's built-in move behavior: it hands the
 * wrapped component a `dragNodeRef`, and `DefaultNode` — which
 * `KindColouredNode` delegates to — already attaches that ref to its own `<g>`,
 * so the drag surface is the node itself with no hit-target of our own.
 *
 * Computed ONCE at module scope, not inside the factory: the factory is called
 * per element per render, and a fresh HOC identity each time would be a new
 * component type, remounting every node (and dropping the in-flight drag) on any
 * re-render.
 *
 * NOT wrapped in `withSelection` as well, and now deliberately UNLIKE the edges,
 * which are. The reason is the drag gesture, not a lack of anything to drive: a
 * node's whole `<g>` is its own drag surface, so `withSelection`'s `onClick` would
 * sit on the element the pointer is already holding down — and a drag that ends
 * where it began is indistinguishable from a click, so every abandoned or tiny drag
 * would also select. An EDGE has no drag behavior attached at all (verified: the
 * only `pointer-events` rule PF puts on an edge is `pointer-events: none` while
 * `.pf-m-dragging`, which is the *node* drag turning edges inert, and this graph
 * registers no `useReconnect`/`useBendpoint` behavior that would let an edge itself
 * be dragged), so there is no gesture for an edge click to fight. That asymmetry is
 * why edges became click targets and nodes did not.
 *
 * The Entities TABLE remains the way to select an entity, which is what the Lineage
 * tab's "click a row in the Entities table above" notice points at.
 *
 * A drag moves the node, and its edges FOLLOW: we set no start/end points, so
 * `BaseEdge.getStartPoint`/`getEndPoint` fall through to
 * `sourceAnchor.getLocation(...)` / `targetAnchor.getLocation(...)`, which are
 * computed from the live node positions. Those positions are mobx-observable, so
 * the drag's `setPosition` re-renders the edge as well as the node.
 *
 * A BENDPOINT, unlike an endpoint, does NOT follow — it is stored geometry on the
 * edge (`edgeBendpoints` sets it once per model push), so a dragged node's routed
 * edge keeps the detour derived for its GRID cell and only its two ends move. That
 * is deliberate and is the reason routing is computed from the derived cell rather
 * than from the possibly-dragged position: a detour that chased the drag would
 * have to be re-derived on every pointer move, and a bendpoint recomputed against
 * a hand-placed node is dodging cells the reader has already rearranged. The
 * visible consequence is that a dragged node's skipping edge can bow oddly until
 * Reset View, which is a better failure than a per-frame re-route.
 */
const DraggableKindColouredNode = withDragNode()(KindColouredNode);

/**
 * The edge renderer, made SELECTABLE.
 *
 * `withSelection` is PF's own selection behavior, and it is the right mechanism here
 * for three reasons found in its source (`behavior/useSelection.js`) rather than
 * assumed:
 *
 *   - IT IS KIND-AGNOSTIC. It touches only `element.getId()`,
 *     `element.getController()` and `element.raise()` — all on the generic
 *     `GraphElement` interface, with no `isNode` check anywhere — so it works on an
 *     edge in 5.4.1 exactly as it does on a node. (This was the open question worth
 *     verifying: PF's own docs demo it on nodes.)
 *   - IT NEEDS NO PROVIDER OF OURS. It reads `ElementContext`, which PF's
 *     `ElementWrapper` provides per element around whatever the factory returned,
 *     and reaches the controller through `element.getController()` — never through a
 *     React controller context. So registering the factory at module scope, outside
 *     any provider this component could give it, costs nothing here. (The `data`
 *     rider pattern is still what carries OUR facts to the renderer; PF's own
 *     element identity is what `withSelection` needs, and that it has natively.)
 *   - IT STOPS PROPAGATION. `onSelect` calls `e.stopPropagation()`, so an edge click
 *     cannot also reach the graph background's own click handler and immediately
 *     clear what it just selected. That is what makes the background-deselect below
 *     safe to add at all.
 *
 * `raiseOnSelect: false` — the ONE option overridden, and the default (`true`) is
 * wrong for this graph. It calls `element.raise()`, reordering the element within
 * its layer, and this view's z-order is deliberate: `lib/graph` keeps `nodes` in the
 * entities read's order specifically because "reordering the array would silently
 * change z-order in the rendered SVG". Letting a click permanently restack the
 * picture would mean clicking two arrows in a different order left the reader with a
 * different drawing, which is exactly the non-determinism the layout notes
 * elsewhere in this file go to some length to exclude.
 *
 * `controlled` is deliberately NOT set, so PF owns `selectedIds` in its own
 * `getState()`. That is not a second source of truth for the app's selection — the
 * `?iid` selection in `FlowTables` remains the only one anything reads — it is PF's
 * internal note of which element was last clicked, which is what makes `selected`
 * arrive back for the `pf-m-selected` modifier without a round trip through React
 * state. The one thing derived from it is nothing: our own `--selected` treatment
 * comes from `isSelected` on the element `data`, resolved from the app's
 * `selectedInteractionId`, so a selection restored from a `?iid` URL with no click
 * behind it still shows. See {@link EntityGraphProps.selectedInteractionId}.
 *
 * Computed ONCE at module scope for the same reason the drag HOC is: a fresh HOC
 * identity per render is a new component type and would remount every edge.
 */
const SelectableDirectedEdge = withSelection({ raiseOnSelect: false })(DirectedEdge);

/**
 * The graph renderer. Pan/zoom, plus SELECTABLE so a click on empty canvas can
 * deselect.
 *
 * `GraphComponent` binds its `onSelect` to a full-bounds `<rect>` behind the whole
 * drawing (`components/GraphComponent.js`), so wrapping the graph in
 * `withSelection` is what turns "clicked nothing" into an event — and PF's own
 * toggle semantics make that event a DESELECT, since clicking the already-selected
 * graph element resolves to `selectedIds = []`.
 *
 * See the component's `SELECTION_EVENT` subscription for what the app does with it
 * and why background-click clears the panel.
 */
const PanZoomGraph = withPanZoom()(withSelection({ raiseOnSelect: false })(GraphComponent));

/** Map each model kind to its renderer. Stable identity — PF memoises on it. */
const componentFactory: ComponentFactory = (kind: ModelKind, type: string) => {
  if (kind === ModelKind.graph) return PanZoomGraph;
  if (type === 'dg-entity') return DraggableKindColouredNode;
  if (type === 'dg-interaction') return SelectableDirectedEdge;
  return undefined;
};

export interface EntityGraphProps {
  /**
   * The already-derived graph. A `GraphSpec`, not the raw entities/interactions:
   * the Lineage tab has to derive its highlight AGAINST the same node/edge set it
   * renders (a highlight naming an element the graph does not contain would be
   * unreachable), so the owner derives once and hands both halves down. Passing
   * the raw reads instead would have each consumer re-derive and would make that
   * agreement a coincidence rather than a guarantee.
   */
  spec: GraphSpec;
  /**
   * The trace the spec belongs to, used only to know when the reader's dragged
   * positions must be forgotten (a new trace is a new picture). NOT used for any
   * read — this component fetches nothing.
   */
  traceId: string;
  /** An active highlight, or omitted for the plain graph. See {@link GraphHighlight}. */
  highlight?: GraphHighlight;
  /**
   * The flow view's selected INTERACTION id (its `?iid`), or `null`.
   *
   * Drives the selected TREATMENT on both of that interaction's edges. Deliberately
   * a prop rather than something read back out of PF's own `selectedIds`, and the
   * distinction is not academic: a selection restored from a `?iid` URL on load — a
   * deep link, a reload, a back button — has no click behind it, so PF's state is
   * empty and an edge whose highlight came from there would silently fail to show.
   * The app's selection is the one that survives a reload, so the app's selection is
   * what the picture is drawn from.
   *
   * PER INTERACTION, so BOTH legs of it are marked. See {@link EdgeData}.
   */
  selectedInteractionId?: string | null;
  /**
   * Fired with the clicked edge's parent INTERACTION id.
   *
   * An ID, not the `Interaction` object, and that is the seam: this component holds a
   * derived `GraphSpec` and has no interactions array to resolve against, while
   * `FlowTables` — which owns the reads, the evidence fetch, the pin state and the
   * `?iid` URL mirroring — has all of it. So the graph reports only "this edge was
   * clicked" and the owner turns that into the same `selectInteraction(ix)` call a
   * Flat-table row click makes. One selection path, one evidence fetch, one panel;
   * the graph stays ignorant of every one of them.
   *
   * `null` for a click on empty canvas — a deselect. See the SELECTION_EVENT
   * subscription for why the two are one callback rather than two.
   */
  onSelectInteraction?: (interactionId: string | null) => void;
  /**
   * Extra disclosures to render above the surface, alongside the graph's own three
   * notices (dropped interactions / isolated entities / parallel channels).
   *
   * The seam exists because the Lineage tab has notices of its own — unresolvable
   * source keys, a partial roll-up — that are neither facts about the graph nor
   * something this component should know how to word. Rendering them HERE rather
   * than above the whole component keeps them in one strip with the graph's own, so
   * a reader meets every caveat about the picture in one place instead of two.
   */
  notices?: React.ReactNode;
  /** `data-testid` on the wrapper, so each tab is addressable as itself. */
  testId?: string;
}

/**
 * THE ONE graph renderer: a directed graph of a trace's **Entities** (nodes) and
 * **Interaction legs** (edges), each edge labelled with that leg's trace-wide
 * `seq`, optionally with a subset highlighted.
 *
 * One edge per LEG, not per interaction: the request travels caller → callee and
 * the response travels back callee → caller (ADR-0025), so a completed
 * interaction draws two arrows pointing opposite ways at two different seqs. The
 * edge set comes from `flatLegRows` — the Flat table's own row derivation — so the
 * arrows and that table's rows are the same list in the same order by
 * construction.
 *
 * SHARED BY THE `graph` AND `lineage` TABS, not forked for the second one. Both
 * draw the identical picture and differ only in whether a highlight is passed, so a
 * copy would be two implementations of one view — free to drift on the layout,
 * the drag lifecycle, the zoom wiring and all three disclosure notices. It also
 * keeps both tabs on ONE lazy chunk: the ~388kB of PF topology and its ~130kB of
 * CSS are imported by this module alone (see the stylesheet note at the top), so a
 * second component file would either duplicate that chunk or depend on Rollup's
 * hoisting to avoid it. `FlowTables` therefore lazily imports this one module and
 * gets both tab components out of it.
 *
 * Fed a derived `GraphSpec` rather than fetching: the two tab wrappers below own the
 * reads (`useEntities` / `useInteractions` — the same two the flow tables use, no
 * new endpoint), which is what lets the Lineage tab derive its highlight against
 * the very spec being rendered. All node/edge derivation lives in
 * `lib/graph.deriveGraph` and all highlight derivation in
 * `lib/lineageGraph.deriveLineageHighlight`, so both are testable without laying out
 * an SVG (jsdom cannot measure one).
 */
export function EntityGraph({
  spec,
  traceId,
  highlight,
  selectedInteractionId = null,
  onSelectInteraction,
  notices,
  testId = 'execution-flow-graph',
}: EntityGraphProps) {
  // One Visualization instance for the view's lifetime. Created lazily in state
  // (not a ref-with-side-effects) so React owns it; the factory is registered
  // once here, and the model is pushed in by the effect below.
  //
  // NO LAYOUT FACTORY, AND NO `layout` ON THE GRAPH MODEL. Every node carries an
  // explicit `x`/`y` from `gridPosition` over `lib/graph`'s layered (column, row),
  // so there is nothing left for a layout algorithm to decide.
  //
  // DAGRE WAS RECONSIDERED FOR THIS, and rejected on its ORDERING, not on the
  // drag conflict. The drag conflict is genuinely solvable — `BaseLayout` takes
  // `listenForChanges` (LayoutOptions), and its `startListening` only subscribes
  // to `ADD_CHILD_EVENT`/`REMOVE_CHILD_EVENT`/visibility/collapse when that flag
  // is true, so `listenForChanges: false` stops the `requestAnimationFrame`
  // re-layout that used to undo every drag a frame after any model push. Two
  // things Dagre cannot do are what actually decide it:
  //
  //   - IT CANNOT PIN SIBLING ORDER. "A calls B, then A calls C, so B is above C"
  //     is the one thing the reader asked for by name, and Dagre only fixes the
  //     RANK from the edge topology; the order WITHIN a rank comes from
  //     `order/index.js`, which runs barycenter crossing-minimisation sweeps and
  //     keeps whichever permutation crosses least. Seeding the input in encounter
  //     order does not survive that (`init-order.js` is only the starting point of
  //     the sweeps), and dagre's own `disableOptimalOrderHeuristic` escape hatch
  //     is not reachable through PF's `DagreLayoutOptions`, which is
  //     `LayoutOptions & dagre.GraphLabel`. So B-above-C would be a coincidence of
  //     the crossing count, silently different on the next trace.
  //   - IT CANNOT BE TOLD THE CALL DIRECTION. A request and its response are a
  //     2-cycle (ADR-0025), and dagre must break every cycle before ranking; which
  //     edge `acyclic.js` reverses is a function of its DFS, so the RESPONSE leg
  //     can be the one that survives and define the depth. Columns would then
  //     encode the round trip rather than the call, which is the specific thing
  //     `lib/graph.assignColumns` excludes response legs to prevent.
  //
  // Registering it anyway would also cost the two things that ARE nailed down
  // here: `Reset View` would have to go back through `graph.layout()`, whose
  // `runLayout(true)` calls `initializeNodePositions(…, force = true)` and so
  // cannot put SOME nodes back, and the positions would stop being assertable in
  // a unit test (they would be a function of dagre's internals rather than of
  // arithmetic on the derived cell — and jsdom cannot measure an SVG, so there is
  // nowhere else that coverage could live).
  //
  // WHAT DAGRE WOULD HAVE GIVEN US is edge ROUTING, and that half is NOT waved
  // away — it is the reason `edgeBendpoints` exists. A column-skipping or
  // same-column edge gets an explicit bendpoint so it detours around the cells it
  // would otherwise be drawn through; adjacent-column edges have nothing between
  // their endpoints and stay straight. PF's edges still recompute their ENDPOINTS
  // from the live node anchors with no layout involved, which is why they keep up
  // with a dragged node.
  const [controller] = useState<Visualization>(() => {
    const vis = new Visualization();
    vis.registerComponentFactory(componentFactory);
    return vis;
  });

  /**
   * The positions the USER has dragged nodes to, by node id — the only state that
   * must outlive a model push.
   *
   * A ref, not state: nothing renders from it (PF owns the drawn position once a
   * drag has happened), so writing it must not re-render, and re-rendering must
   * not be able to observe a stale copy of it.
   *
   * It is read when the model is rebuilt and it is CLEARED whenever the trace
   * changes — see the two effects below. That split is the whole fix: the model
   * itself is still rebuilt with `merge: false`, so a stale node from a previous
   * trace still cannot survive (which is what that `false` was always for), while
   * the user's own placements are re-applied on top of a genuinely fresh model.
   */
  const draggedPositions = useRef(new Map<string, { x: number; y: number }>());

  // Harvest the current positions before a rebuild throws them away. Runs on
  // every model push, so the positions carried forward are always the last ones
  // the user actually saw — including a drag that happened between two pushes.
  //
  // Only positions that DIFFER from the node's own grid cell are remembered.
  // Recording every node unconditionally would work but would freeze the layout: a
  // later change to the step constants (or to an entity's column/row, when the
  // trace's data is revised by a later poll and its call depth changes) would be
  // overwritten by the harvested copy of the OLD arithmetic, and the picture would
  // silently stop matching the code.
  const harvestDraggedPositions = useCallback(() => {
    if (!controller.hasGraph()) return;
    for (const node of controller.getGraph().getNodes()) {
      const data = node.getData() as NodeData | undefined;
      if (!data) continue;
      const at = node.getPosition();
      const home = gridPosition(data);
      if (at.x === home.x && at.y === home.y) continue;
      draggedPositions.current.set(node.getId(), { x: at.x, y: at.y });
    }
  }, [controller]);

  /**
   * The trace the positions in `draggedPositions` belong to.
   *
   * A NEW TRACE IS A NEW PICTURE, so its positions are forgotten. This is a ref
   * compared inside the model effect rather than a second `useEffect` keyed on
   * `traceId`, because the two effects would have to interleave in an exact order
   * — clear, THEN harvest, THEN rebuild — and React gives no such guarantee
   * across a cleanup/body boundary. A wrong order here is not a visible bug: it
   * silently carries one trace's arrangement onto another trace's nodes wherever
   * two traces happen to share an entity id (which, entities being cross-trace
   * stable by ADR-0013, is the COMMON case, not a rare one). One effect and one
   * comparison cannot get that order wrong.
   */
  const positionsTraceId = useRef(traceId);

  /**
   * The highlight, reduced to a primitive the model effect can depend on.
   *
   * The effect MUST re-run when the highlight changes (the roles are baked into the
   * element `data`, so nothing repaints otherwise) and MUST NOT re-run when it has
   * not (every push harvests and re-applies drag positions, and a push per render
   * would also churn the whole model on every parent re-render — a poll, a pin
   * toggle, an unrelated selection). Depending on the object itself would do the
   * latter for any caller who did not memoise it perfectly, which is a correctness
   * bug hidden behind a caller's discipline.
   *
   * A joined string, not a `useMemo` on the object: the CONTENT is what matters, so
   * comparing content is the honest dependency and it cannot be defeated by a fresh
   * array literal. The sets are small (one trace's nodes and legs) and this runs once
   * per render, so the join is not worth avoiding — and `?? ''` keeps "no highlight"
   * a stable, distinct value rather than colliding with an empty highlight.
   */
  const highlightKey = highlight
    ? `${highlight.selectedNodeId ?? ''}|${highlight.nodeIds.join(',')}|${highlight.edgeIds.join(',')}`
    : '';

  // Push the derived spec into the topology model whenever it changes.
  useEffect(() => {
    if (positionsTraceId.current !== traceId) {
      positionsTraceId.current = traceId;
      draggedPositions.current = new Map();
    } else {
      // Same trace: carry the reader's own placements across the rebuild. Harvest
      // BEFORE `fromModel` replaces the elements, since it reads them off the
      // live graph.
      harvestDraggedPositions();
    }
    // The layered cell of every node, keyed by id — what `edgeBendpoints` needs to
    // know which spans an edge crosses. Built from the same `spec.nodes` the node
    // models come from, so an edge can never be routed against a cell the node is
    // not actually drawn at.
    //
    // Deliberately the DERIVED cell, not the possibly-dragged position: a
    // bendpoint is stored geometry that does not follow a drag (unlike an
    // endpoint, which is recomputed from the live anchor), so routing against a
    // dragged position would bake in a detour that goes stale the moment the node
    // moves again. Routing against the cell keeps the detour a property of the
    // LAYOUT, which is the thing it is dodging.
    const cellById = new Map(spec.nodes.map((n) => [n.id, { column: n.column, row: n.row }]));
    const model: Model = {
      // No `layout` key: see the controller's note. Omitting it leaves
      // `BaseGraph.currentLayout` undefined, so `graph.layout()` is a no-op and
      // nothing re-places a node behind the reader's back.
      graph: { id: GRAPH_ID, type: 'graph' },
      nodes: spec.nodes.map((n): NodeModel => {
        // The layered layout as pixels: the entity's (column, row) cell — call
        // depth across, chronology down — unless the reader has moved it, in which
        // case where they put it wins. This is the ONLY place the two can
        // disagree, so there is no third notion of "where a node is".
        const { x, y } = draggedPositions.current.get(n.id) ?? gridPosition(n);
        return {
          id: n.id,
          type: 'dg-entity',
          label: n.label,
          width: NODE_DIAMETER,
          height: NODE_DIAMETER,
          shape: NodeShape.ellipse,
          x,
          y,
          // The whole spec row rides along as `data` so the renderers read kind /
          // isIsolated / naturalKey / encounterIndex without a second lookup — plus
          // the element's highlight ROLE, resolved here because the renderers are
          // registered on the controller at module scope and are therefore outside
          // any provider this component could give them (see HighlightRole).
          data: { ...n, highlight: roleOf(n.id, highlight, true) } satisfies NodeData,
        };
      }),
      edges: spec.edges.map((e) => ({
        id: e.id,
        type: 'dg-interaction',
        source: e.source,
        target: e.target,
        // A self-call's source and target are the same point, so a straight line
        // would have zero length and be invisible. `dashed` at least marks the
        // node as carrying one; PF has no self-loop routing in 5.4.
        edgeStyle: e.isSelfCall ? EdgeStyle.dashed : EdgeStyle.solid,
        // THE ROUTING. Empty for the adjacent-column case (nothing to dodge), a
        // single detour point for an edge that skips columns or stays within one —
        // the two cases where a straight line would be drawn over an intervening
        // node. See `edgeBendpoints`.
        bendpoints: edgeBendpoints(e, cellById),
        data: {
          ...e,
          highlight: roleOf(e.id, highlight, false),
          // Per INTERACTION, so both legs of the selected one are marked — see
          // EdgeData. A primitive comparison, so this adds nothing the effect's
          // dependency list cannot express (`selectedInteractionId` is itself the
          // digest, unlike the highlight's object).
          isSelected: selectedInteractionId != null && e.interactionId === selectedInteractionId,
        } satisfies EdgeData,
      })),
    };
    // `false` for merge, unchanged: the model is fully re-derived, so a stale node
    // from a previous trace must not survive. The reader's own placements survive
    // it by being re-applied as the node models' `x`/`y` above — which is a
    // deliberately narrower thing to preserve than "merge the old elements", and
    // is keyed by node id so a position for an entity this trace does not have is
    // simply never asked for.
    controller.fromModel(model, false);
    // `highlightKey` (a content digest) rather than `highlight` itself — see its
    // note. `highlight` is read INSIDE the effect and is deliberately not a
    // dependency; the digest changing is exactly when re-reading it matters.
    //
    // `selectedInteractionId` needs NO such digest and is listed directly: it is
    // already a primitive, so depending on it is depending on its content and there
    // is no caller-memoisation hole to plug. It DOES have to be here, though — the
    // selected flag is baked into the element `data`, so nothing repaints without a
    // push — and that is precisely why the drag positions are harvested and
    // re-applied on every push rather than only on a poll: selecting an edge is now
    // one more thing that rebuilds the model, and the reader's dragged nodes have to
    // ride through it exactly as they ride through a highlight change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controller, spec, traceId, harvestDraggedPositions, highlightKey, selectedInteractionId]);

  /**
   * "Put it back": every node returns to its layered (column, row) cell and the
   * reader's drags are forgotten.
   *
   * This REPLACES the `graph.layout()` the Reset View button used to call. With no
   * layout registered that call is now a silent no-op — a button that appears to
   * work and does nothing — so the affordance is re-implemented against the thing
   * that actually decides a position here. Restoring the grid is also a strictly
   * better answer than re-running Dagre would be: it is the same picture every
   * time, whereas a layout re-run could return a different arrangement than the
   * one the reader started from (and Dagre's `runLayout(true)` resets EVERY node
   * regardless, so it could not be the selective "put it back" this is).
   *
   * The forget is not optional. Moving the nodes without clearing the map would
   * leave the next model push (any poll) yanking them all back to where they were
   * dragged, so Reset would undo itself a second later.
   *
   * The EDGES need nothing here: their bendpoints are already the ones derived for
   * the undragged grid (see the model builder's note on why routing is against the
   * cell and not the live position), so restoring the nodes puts the whole picture
   * back without touching an edge.
   */
  const restoreGridPositions = useCallback(() => {
    draggedPositions.current = new Map();
    if (!controller.hasGraph()) return;
    for (const node of controller.getGraph().getNodes()) {
      const data = node.getData() as NodeData | undefined;
      if (!data) continue;
      const { x, y } = gridPosition(data);
      node.setPosition(new Point(x, y));
    }
  }, [controller]);

  /**
   * The click callback, held in a ref so the subscription below does not have to
   * depend on it.
   *
   * An unmemoised `onSelectInteraction` from a caller — which is the normal case, and
   * `FlowTables` passes exactly that — would otherwise change identity on every
   * parent render and tear down and re-add the event listener each time. That is not
   * merely wasteful: PF's `removeEventListener` is matched by function identity, so a
   * churning subscription is precisely the shape that leaks listeners if any single
   * cleanup is ever missed. The same "do not let a caller's memoisation discipline
   * decide our correctness" reasoning behind `highlightKey`.
   */
  const onSelectRef = useRef(onSelectInteraction);
  onSelectRef.current = onSelectInteraction;

  /**
   * Translate PF's selection event into the app's own "an interaction was picked".
   *
   * WHY AN EVENT AND NOT A CALLBACK ON THE RENDERER. The renderers are registered on
   * the controller at module scope and are therefore outside any provider this
   * component could give them — the same constraint that makes {@link HighlightRole}
   * ride on the element `data`. But `data` is a one-way channel: it carries facts
   * DOWN to a renderer, and there is nothing to hang a function on that survives the
   * model being rebuilt (a callback baked into `data` would be re-baked on every
   * push, and would make the model's content digest depend on a function identity).
   * PF's event bus is the seam that goes the other way, and `SELECTION_EVENT` is the
   * event its own selection behavior already fires. So no new mechanism is invented:
   * `withSelection` fires, this listens, the owner acts.
   *
   * THE PAYLOAD IS ELEMENT IDS, so the edge id has to be mapped back to an
   * interaction. An edge id is `<interactionId>:<legType>` (see `GraphEdgeSpec.id`),
   * and rather than split the string — which would re-implement the id's format in a
   * second place and silently break if it ever changed — the id is looked up on the
   * live graph and its `data.interactionId` read. The edge already carries the field
   * for exactly this purpose.
   *
   * ONE CALLBACK FOR BOTH SELECT AND DESELECT, because PF gives one event for both.
   * Splitting it into two props would put the "was this a deselect" decision in the
   * caller, where every caller would re-derive it identically.
   *
   * THE PAYLOAD HAS THREE SHAPES, and telling them apart is the whole of this
   * handler. Verified against the running library, not inferred — the middle one is
   * not what the option names suggest:
   *
   *   1. `['<interaction>:<leg>']` — an edge was clicked. Select that interaction.
   *   2. `['dg-graph']` — the BACKGROUND was clicked. `GraphComponent` renders a
   *      full-bounds `<rect>` bound to its own `onSelect`, and the graph is itself a
   *      selectable element, so PF reports empty canvas as the GRAPH's id rather
   *      than as an empty array. This is the case that must not be dropped: read as
   *      "an element I do not recognise" it would make background-click a silent
   *      no-op, which is what a first cut of this handler did.
   *   3. `[]` — genuinely nothing, which is PF's TOGGLE: clicking the
   *      already-selected edge a second time deselects it (`useSelection`'s
   *      `else { selectedIds = []; }`). Treated as a deselect, which is also what
   *      makes a second click on an open interaction's arrow close its panel.
   *
   * ANY OTHER id is IGNORED rather than treated as a deselect — a node, or an element
   * this graph does not know. Nodes are not wrapped in `withSelection` (see
   * `DraggableKindColouredNode`) so this is unreachable today, but if one ever became
   * selectable, silently reading its selection as "no interaction" would close the
   * reader's open panel for no visible reason. Doing nothing with an id we cannot
   * interpret is the honest response.
   */
  useEffect(() => {
    const onSelectionEvent = (ids: string[]) => {
      const id = ids[0];
      // Shapes 2 and 3 — empty canvas, or the toggle-off of the selected edge.
      if (id == null || id === GRAPH_ID) {
        onSelectRef.current?.(null);
        return;
      }
      // Shape 1. Looked up on the live graph rather than by splitting the id on `:`,
      // so the `<interaction>:<legType>` format stays stated in exactly one place
      // (`lib/graph`'s GraphEdgeSpec.id) — the edge already carries `interactionId`
      // as a field for precisely this mapping.
      const edge = controller.hasGraph() ? controller.getEdgeById(id) : undefined;
      const data = edge?.getData() as EdgeData | undefined;
      if (!data) return;
      onSelectRef.current?.(data.interactionId);
    };
    controller.addEventListener(SELECTION_EVENT, onSelectionEvent);
    return () => {
      controller.removeEventListener(SELECTION_EVENT, onSelectionEvent);
    };
  }, [controller]);

  return (
    <div data-testid={testId}>
      {/* The caller's own disclosures FIRST, above the graph's three: they are
          about the answer the reader asked for, whereas the graph's are standing
          caveats about the drawing. A "some of your sources could not be drawn"
          notice buried under three permanent info boxes is a notice nobody reads. */}
      {notices}
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
          lose the per-leg identity the rest of the UI keys on. The notice still
          matters even though `edgeBendpoints` now fans same-span edges to
          alternating sides by seq parity: fanning separates two, not necessarily
          five, so a busy channel can still read as fewer arrows than it holds —
          and the reader is pointed at the Flat tab where each is its own row.
          (It used to say these drew exactly on top of one another, which the
          bendpoints are what fixed.)

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
              `overflow: hidden` box so it cannot escape it. Bottom-LEFT is still
              the right corner under the layered grid: the grid grows RIGHTWARDS
              with call depth and DOWNWARDS with chronology, so its mass is on the
              right and the deep bottom-left stays clear on all but the tallest
              first column — while the top-right is where the floating detail
              panel sits. (It was chosen for the same reason under the old
              `rankdir: LR` Dagre layout and under the diagonal staircase.)

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
                // Reset View undoes BOTH kinds of "I moved something": the
                // viewport (`reset` — scale and pan) and the nodes
                // (`restoreGridPositions`). `graph.layout()` used to be the second
                // half; with no layout registered it is now a no-op, so putting
                // every node back on its layered cell takes its place. See
                // restoreGridPositions.
                resetViewCallback: () => {
                  controller.getGraph().reset();
                  restoreGridPositions();
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

/**
 * The three not-a-graph outcomes of the two reads, shared by both tabs.
 *
 * Extracted because "the entities read failed" is the same fact and the same
 * required action whichever tab asked, and two tabs wording it differently would be
 * two tabs disagreeing about one dataset. Returns `null` when there IS a graph to
 * draw, so a caller reads as `state ?? <EntityGraph …>`.
 *
 * The three states deliberately mirror `FlowTables`' conventions (the same
 * `Spinner` and `EmptyState` components, the same "may still be draining" tone).
 */
function graphReadState({
  isLoading,
  isError,
  nodeCount,
}: {
  isLoading: boolean;
  isError: boolean;
  nodeCount: number;
}): React.ReactElement | null {
  if (isLoading) return <Spinner aria-label="Loading execution flow graph" />;

  // A failed read is NOT an empty graph: "no entities" and "we could not ask"
  // demand different actions (wait vs retry), the same distinction ADR-0028's
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

  if (nodeCount === 0) {
    return (
      <EmptyState>
        <EmptyStateHeader titleText="No execution flow" headingLevel="h4" />
        <EmptyStateBody>
          {'No entities or interactions for this trace yet, so there is nothing to graph. The interactions processor derives them from the spans table as spans arrive; this trace may still be draining.'}
        </EmptyStateBody>
      </EmptyState>
    );
  }
  return null;
}

/**
 * The **Execution Flow** tab: the graph, with no highlight.
 *
 * A thin wrapper over {@link EntityGraph} that owns the two reads
 * (`useEntities` / `useInteractions` — the same two the flow tables use, so
 * switching tabs costs no fetch) and the `deriveGraph` call. Passing no `highlight`
 * is what makes this the plain view: every element's role is `'none'`, which emits
 * no highlight class at all, so this tab's DOM is exactly what it was before the
 * Lineage tab existed.
 *
 * The two selection props are passed STRAIGHT THROUGH from `FlowTables`, unread and
 * untransformed. This wrapper deliberately does not resolve the clicked id to an
 * `Interaction` even though it holds the interactions array: the resolution belongs
 * where the evidence fetch, the pin key and the `?iid` mirroring already live, and a
 * second `selectInteraction` here would be the duplicated selection path the whole
 * design is avoiding.
 */
export function ExecutionFlowGraph({
  traceId,
  selectedInteractionId = null,
  onSelectInteraction,
}: {
  traceId: string;
  selectedInteractionId?: string | null;
  onSelectInteraction?: (interactionId: string | null) => void;
}) {
  const entitiesQ = useEntities(traceId);
  const interactionsQ = useInteractions(traceId);

  const entities = useMemo(() => entitiesQ.data ?? [], [entitiesQ.data]);
  const interactions = useMemo(() => interactionsQ.data ?? [], [interactionsQ.data]);

  const spec = useMemo(() => deriveGraph(entities, interactions), [entities, interactions]);

  return (
    graphReadState({
      isLoading: entitiesQ.isLoading || interactionsQ.isLoading,
      isError: entitiesQ.isError || interactionsQ.isError,
      nodeCount: spec.nodes.length,
    }) ?? (
      <EntityGraph
        spec={spec}
        traceId={traceId}
        selectedInteractionId={selectedInteractionId}
        onSelectInteraction={onSelectInteraction}
      />
    )
  );
}

/**
 * The **Lineage** tab: the SAME graph, with the selected entity's data sources
 * highlighted.
 *
 * ONE QUESTION ASKED OF THE EXECUTION FLOW PICTURE, not a second picture. It
 * renders {@link EntityGraph} — the identical nodes, edges, layered layout, drag
 * lifecycle and zoom controls — and adds only a highlight plus the caveats that
 * highlight needs. See `lib/lineageGraph`'s header for the exact definition of
 * "all the sources of an entity's data" and why it is inbound-legs-only, direct-only
 * and never transitive.
 *
 * DELIBERATELY IN THIS MODULE, beside the tab it shares its renderer with. PF
 * topology's ~388kB of JS and ~130kB of CSS are imported at the top of THIS file, so
 * a separate component file would put the Lineage tab on a second lazy chunk (or
 * make its absence a Rollup hoisting coincidence). `FlowTables` lazily imports this
 * one module and takes both tab components out of it, so both tabs share one chunk
 * by construction.
 *
 * THE THREE ABSENCE STATES ARE KEPT APART ON SCREEN, which is the whole discipline
 * of ADR-0028 restated for a graph:
 *   - nothing selected      → an instruction, no claim about any entity;
 *   - selected, not derived  → "not yet computed", the eventual-consistency window,
 *                              and NOT an empty highlight (which would read as
 *                              "checked, no sources");
 *   - selected, derived-empty → the real verdict "originates here" (ADR-0028 D3),
 *                              stated in words because an unhighlighted graph is
 *                              indistinguishable from the pending case otherwise.
 * A failed READ is a fourth, separate thing and is owned by the caller
 * (`LineageCoverageAlert`, already on screen above the tabs — see `isLineageError`).
 */
export function LineageGraph({
  traceId,
  entities,
  interactions,
  byLeg,
  status,
  isLineageError,
  selectedEntityId,
  selectedInteractionId = null,
  onSelectInteraction,
}: {
  traceId: string;
  /**
   * The flow view's ALREADY-FETCHED reads, passed in rather than re-queried.
   *
   * Not for the fetch count's sake (TanStack would serve the cache either way) but
   * for the selection: this tab's answer is about the entity the flow view has
   * selected, and the entity ROW that selection came from is the same array. Taking
   * both from one place makes it impossible for the highlight to be computed against
   * a different entity set than the table the reader clicked in.
   */
  entities: readonly Entity[];
  interactions: readonly Interaction[];
  /** Per-leg lineage from `useDataLineage`; `undefined` while in flight. */
  byLeg: DataLineageByLeg | undefined;
  /** The trace's coverage (ADR-0028 D6), so a prefix answer says so. */
  status: LineageStatus;
  /** The trace-scoped lineage read failed — nothing is known, not "no sources". */
  isLineageError: boolean;
  /** The flow view's `?eid` selection. THE one notion of "selected entity". */
  selectedEntityId: string | null;
  /**
   * The flow view's `?iid` selection, and its click callback — offered on THIS tab
   * too, not only on Execution Flow.
   *
   * The two tabs are one graph (see this component's header), so an arrow that is
   * clickable on one and inert on the other would be the fork the shared renderer
   * exists to prevent — and a reader who has learnt that arrows open a panel does not
   * expect that to stop being true because they switched to the reading of the same
   * picture. It also composes usefully: the entity selection drives the lineage
   * highlight while an edge click drills into one of the legs the highlight is
   * pointing at.
   *
   * NOTE the two selections are MUTUALLY EXCLUSIVE downstream, and that is the flow
   * view's own pre-existing rule rather than anything this tab decides: `FlowTables`
   * holds ONE `selection`, so selecting an interaction replaces the selected entity
   * (and clears `?eid` for `?iid`). On this tab that means an edge click also clears
   * the lineage highlight — which is honest, since the highlight was an answer about
   * an entity that is no longer the thing selected. Making the two co-exist would
   * mean a second notion of selection, which is exactly what the requirement forbids.
   */
  selectedInteractionId?: string | null;
  onSelectInteraction?: (interactionId: string | null) => void;
}) {
  const entitiesQ = useEntities(traceId);
  const interactionsQ = useInteractions(traceId);

  // Derived ONCE and handed to both the highlight and the renderer, so the
  // highlight can only ever name elements that are actually on screen.
  const spec = useMemo(() => deriveGraph(entities, interactions), [entities, interactions]);
  const lineage = useMemo(
    () =>
      deriveLineageHighlight({
        entities,
        interactions,
        byLeg,
        status,
        selectedEntityId,
        graph: spec,
      }),
    [entities, interactions, byLeg, status, selectedEntityId, spec],
  );

  // The prop `EntityGraph` bakes into the element data. Omitted entirely when no
  // entity is selected: with nothing asked, nothing may be dimmed (see
  // HighlightRole's note on why `'none'` and `'dimmed'` are different values).
  const highlight: GraphHighlight | undefined =
    lineage.selectedNodeId === null
      ? undefined
      : {
          selectedNodeId: lineage.selectedNodeId,
          nodeIds: lineage.highlightedNodeIds,
          edgeIds: lineage.highlightedEdgeIds,
        };

  // Friendly names for the natural keys the notices quote, from the same helper
  // every other lineage surface uses — so an unresolvable source is named the way
  // the detail panel names a resolvable one.
  const namesByKey = useMemo(() => displayNamesByKey([...entities]), [entities]);

  const readState = graphReadState({
    isLoading: entitiesQ.isLoading || interactionsQ.isLoading,
    isError: entitiesQ.isError || interactionsQ.isError,
    nodeCount: spec.nodes.length,
  });
  if (readState) return readState;

  return (
    <EntityGraph
      spec={spec}
      traceId={traceId}
      highlight={highlight}
      selectedInteractionId={selectedInteractionId}
      onSelectInteraction={onSelectInteraction}
      testId="lineage-graph"
      notices={
        <>
          {/* NOTHING SELECTED. An instruction, not a verdict — the graph below is
              drawn at full strength and claims nothing, because no question has
              been asked of it yet. Points at the Entities table above, which is the
              only place an ENTITY selection can be made: the graph's nodes are drag
              surfaces rather than click targets (see the drag note in
              `DraggableKindColouredNode` for why the gesture and the click cannot
              share one element). The graph's EDGES *are* click targets, but an edge
              selects an INTERACTION, which is not the question this tab answers. */}
          {lineage.selectedNodeId === null && (
            <Alert
              variant="info"
              isInline
              title="Select an entity to see where its data came from"
              style={{ marginBottom: '0.5rem' }}
            >
              {selectedEntityId === null
                ? 'Click a row in the Entities table above. This tab then highlights the entities that are direct sources of that entity’s data, and the interaction legs that carried it; everything else is dimmed.'
                : 'The selected entity is not in this trace’s entity set, so it has no node to highlight. Pick a row in the Entities table above.'}
            </Alert>
          )}

          {/* THE READ FAILED. First, because it invalidates every other statement
              here: with no lineage in hand the highlight below is empty for a reason
              that has nothing to do with the data. Worded as *unknown*, matching
              LineageCoverageAlert — never as "no sources". */}
          {isLineageError && lineage.selectedNodeId !== null && (
            <Alert
              variant="warning"
              isInline
              role="alert"
              title="Data lineage could not be loaded"
              style={{ marginBottom: '0.5rem' }}
            >
              The lineage read failed, so this entity’s data sources are{' '}
              <strong>unknown</strong> — not absent. Nothing is highlighted below
              because nothing was retrieved. Reload to retry.
            </Alert>
          )}

          {/* NOT YET DERIVED. The eventual-consistency window (ADR-0028): legs DO
              deliver to this entity, but none of them has a lineage row yet. Stated
              as its own claim-less state, deliberately NOT as an empty highlight —
              a governance tool must never let "we don't know yet" look like "we
              checked". Same wording as DataLineageView's per-leg note, so one
              phrase means one thing across the app. */}
          {!isLineageError && lineage.state === 'pending' && (
            <Alert
              variant="info"
              isInline
              title="Lineage not yet computed for this entity"
              style={{ marginBottom: '0.5rem' }}
            >
              {`${lineage.inboundLegs} interaction leg${lineage.inboundLegs === 1 ? '' : 's'} deliver${lineage.inboundLegs === 1 ? 's' : ''} data to this entity, but none has lineage derived yet, so its sources are not yet known — this is not "no sources". P-data-lineage derives them as payloads arrive; nothing is highlighted until they do.`}
            </Alert>
          )}

          {/* NOTHING DELIVERS HERE AT ALL. Structurally distinct from the pending
              case above: there is no leg to wait for, so telling the reader to wait
              would be telling them to wait forever. An isolated entity, or one that
              only ever sends, lands here. */}
          {!isLineageError && lineage.state === 'no-inbound' && lineage.selectedNodeId !== null && (
            <Alert
              variant="info"
              isInline
              title="No data arrives at this entity in this trace"
              style={{ marginBottom: '0.5rem' }}
            >
              No interaction leg in this trace delivers data to this entity (no arrow
              points at its node), so there is no lineage to roll up. This is a fact
              about the trace, not a derivation still pending.
            </Alert>
          )}

          {/* DERIVED AND EMPTY — a REAL answer (ADR-0028 D3), and the one state a
              graph cannot show by itself: "no sources" and "sources unknown" both
              look like an unhighlighted picture, so the difference has to be words. */}
          {!isLineageError &&
            lineage.state === 'derived' &&
            lineage.highlightedNodeIds.length === 0 &&
            lineage.unresolved.length === 0 && (
              <Alert
                variant="info"
                isInline
                title="No upstream data sources — this data originates here"
                style={{ marginBottom: '0.5rem' }}
              >
                {`Lineage IS derived for ${lineage.derivedLegs} of the ${lineage.inboundLegs} leg${lineage.inboundLegs === 1 ? '' : 's'} delivering to this entity, and names no upstream source, so nothing upstream is highlighted. That is a derived answer, not a missing one.`}
              </Alert>
            )}

          {/* A PARTIAL ROLL-UP: some deliveries answered, others still in the
              window. Neither "derived" nor "pending" alone tells the truth here, so
              the counts are named — without them a third of the picture could be
              missing with no sign of it. */}
          {!isLineageError &&
            lineage.state === 'derived' &&
            lineage.derivedLegs < lineage.inboundLegs && (
              <Alert
                variant="warning"
                isInline
                role="alert"
                title="This entity’s sources are incomplete"
                style={{ marginBottom: '0.5rem' }}
              >
                {`Only ${lineage.derivedLegs} of the ${lineage.inboundLegs} interaction legs delivering data to this entity have lineage derived, so the highlighted sources are a PARTIAL set. The remaining legs' sources are not yet known.`}
              </Alert>
            )}

          {/* THE TRACE-LEVEL PREFIX (ADR-0028 D6). Reused verbatim rather than
              reworded: the truncation is a fact about the whole trace, so the tab
              must not invent a second phrasing of it. Only rendered for a selection
              — the banner is already on screen above the tabs at all times (see
              FlowTables), and this repeat exists so the caveat sits next to the
              answer it qualifies. `isError` is false here because the failure has
              its own alert above. */}
          {lineage.selectedNodeId !== null && !isLineageError && (
            <LineageCoverageAlert
              status={lineage.status}
              stoppedAtSeq={null}
              isError={false}
              // Both false for the same reason: this repeat only renders once a
              // node is selected, which requires the lineage read to have already
              // settled successfully.
              isLoading={false}
            />
          )}

          {/* UNRESOLVABLE SOURCES: a real origin the graph cannot draw. Disclosed by
              count AND by key, the way lib/graph discloses a dropped interaction —
              "this entity has 5 sources but 3 nodes are lit" is exactly the silent
              under-report a governance reader must never have to discover for
              themselves. Legitimate causes include an origin outside the trace's own
              entity set. */}
          {lineage.unresolved.length > 0 && (
            <Alert
              variant="warning"
              isInline
              role="alert"
              title={`${lineage.unresolved.length} data source${lineage.unresolved.length === 1 ? '' : 's'} not shown as nodes`}
              style={{ marginBottom: '0.5rem' }}
            >
              {`The lineage names ${lineage.unresolved.length === 1 ? 'this source' : 'these sources'} by natural key, but no entity in this trace carries that key, so there is no node to highlight: ${lineage.unresolved
                .map((u) => {
                  const { label } = lineageLabel(u.naturalKey, namesByKey);
                  return `${label} (${u.legCount} leg${u.legCount === 1 ? '' : 's'})`;
                })
                .join('; ')}. ${lineage.unresolved.length === 1 ? 'It is' : 'They are'} still a real source — the highlighted nodes are therefore not the full set.`}
            </Alert>
          )}

          {/* AMBIGUOUS KEYS. Expected empty against a sane server (a natural key is
              an entity's identity, ADR-0013), which is exactly why it is disclosed
              rather than assumed: if it ever fires, a highlighted node is a
              deterministic but ARBITRARY pick among the claimants, and the reader
              has to know that before trusting which node is lit. See
              lineageLabels.entityIdsByKey for the first-wins rule. */}
          {lineage.ambiguousKeys.length > 0 && (
            <Alert
              variant="warning"
              isInline
              role="alert"
              title={`${lineage.ambiguousKeys.length} data source key${lineage.ambiguousKeys.length === 1 ? '' : 's'} matched more than one entity`}
              style={{ marginBottom: '0.5rem' }}
            >
              {`A natural key should identify exactly one entity, but ${lineage.ambiguousKeys
                .map((k) => lineageLabel(k, namesByKey).label)
                .join('; ')} matched several in this trace. The highlighted node is the first match in the entities read — deterministic, but an arbitrary choice among them.`}
            </Alert>
          )}
        </>
      }
    />
  );
}

// A DEFAULT export alongside the named one, purely so `React.lazy(() =>
// import('./ExecutionFlowGraph'))` in FlowTables needs no
// `.then(m => ({ default: m.ExecutionFlowGraph }))` shim. Same component, one
// definition — the named export stays because it is what this component's own
// test renders directly (no Suspense boundary needed there).
//
// `LineageGraph` is a NAMED export only: `FlowTables` reaches it through the same
// lazy module (`lazy(() => import('./ExecutionFlowGraph').then(m => ({ default:
// m.LineageGraph })))`), which is the shim that keeps both tabs on one chunk. Only
// one of the two can be the default, and the Execution Flow tab was here first.
export default ExecutionFlowGraph;
