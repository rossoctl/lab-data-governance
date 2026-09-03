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
// The pieces of `DefaultEdge` that `CurvedEdge` (below) re-assembles around its own
// `<path>`. Each is deep-imported from its LEAF module for the same reason
// everything else here is — `components/edges/index.js` and `components/layers/index.js`
// are barrels, and the whole point of the discipline above is not to pull one in.
//
// WHY THESE ARE IMPORTED AT ALL, i.e. why `DefaultEdge` is forked rather than
// wrapped: see `CurvedEdge`'s own note. Short version — the path command is
// hardcoded in `DefaultEdge` with no seam, and the seam it DOES offer (`children`)
// renders too late and cannot replace either of the two paths PF draws.
import DefaultConnectorTerminal from '@patternfly/react-topology/dist/esm/components/edges/terminals/DefaultConnectorTerminal';
import DefaultConnectorTag from '@patternfly/react-topology/dist/esm/components/edges/DefaultConnectorTag';
import Layer from '@patternfly/react-topology/dist/esm/components/layers/Layer';
import { TOP_LAYER } from '@patternfly/react-topology/dist/esm/const';
// `useHover` drives PF's own `pf-m-hover` modifier and the TOP_LAYER hoist. Deep
// path is `utils/useHover`, NOT `utils` — that index re-exports it but is itself a
// barrel over the whole utils subtree.
import useHover from '@patternfly/react-topology/dist/esm/utils/useHover';
// `getEdgeStyleClassModifier` maps `EdgeStyle.dashed` → `pf-m-dashed`, which is what
// marks a self-call on this graph (see the model builder's `edgeStyle`), and
// `getEdgeAnimationDuration`/`StatusModifier` are the other two things `DefaultEdge`
// derives for its class list. Re-derived here rather than re-implemented, so a
// self-call's dash keeps coming from PF's own mapping.
import {
  StatusModifier,
  getEdgeAnimationDuration,
  getEdgeStyleClassModifier,
} from '@patternfly/react-topology/dist/esm/utils/style-utils';
// The collapsed-group early return `DefaultEdge` makes, kept verbatim: an edge whose
// two ends have collapsed into one visible group must not be drawn at all.
import { getClosestVisibleParent } from '@patternfly/react-topology/dist/esm/utils/element-utils';
// `getConnectorStartPoint` is the function PF uses to back an arrowhead's tip off the
// endpoint by the terminal's own size. Reused (not reimplemented) so the hit band's
// trimmed ends agree exactly with where PF puts the head it is trimming for.
import { getConnectorStartPoint } from '@patternfly/react-topology/dist/esm/components/edges/terminals/terminalUtils';
// `observer`, which `CurvedEdge` must be wrapped in or a dragged node's edges stop
// following it (see that component's note — this was found by a failing test, not
// assumed).
//
// FROM PF'S OWN RE-EXPORT, not from `mobx-react` directly, and that is the point of
// using this path: `mobx-react` is a transitive dependency of react-topology and is
// NOT in our package.json, so importing it here would be an undeclared dependency that
// happens to resolve — the kind that breaks on an unrelated `npm install`. PF publishes
// `mobx-exports` for exactly this ("re-export for ease of use externally", its own
// comment), so the version we observe with is by construction the one PF's own
// observables were created by. Still a LEAF module, not the barrel: `index.js` does
// `export * from './mobx-exports'`, which is how the barrel would otherwise be the
// only route to it.
import { observer } from '@patternfly/react-topology/dist/esm/mobx-exports';
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
  // The guard and the zoom-detail enum `CurvedEdge` needs to be a faithful stand-in
  // for `DefaultEdge`: `isEdge` for its element assertion, `ScaleDetailsLevel` for the
  // tag gating. (`isNode`, which PF's own collapsed-group guard calls, is deliberately
  // NOT imported — see that guard's note.)
  ScaleDetailsLevel,
  isEdge,
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
// LOAD ORDER: THESE SHEETS LAND *AFTER* global.css, AND EVERY `.dg-graph-*` RULE HAS
// TO BEAT PF ON SPECIFICITY BECAUSE OF IT. Importing them here (rather than in
// main.tsx) is what puts them in the lazy chunk, and that chunk's CSS is injected last
// — so on any tie PF wins, in the built output, every time.
//
// This comment previously said the inversion was "safe because the two `.dg-*` rules
// that touch the graph do not depend on order", and enumerated three. That was true
// when there were three. It is now false — there are ~24 — and its individual claims
// staying correct (`.dg-graph-surface` really does name a class PF has no rule for;
// `--dg-*` tokens really are a disjoint namespace from `--pf-topology__*`) is exactly
// what made it read as reassurance while going stale. It authorised three real bugs,
// all found by review and all fixed in global.css:
//   - five of six edge-tag colour rules were dead — `.dg-graph-edge-tag text` is
//     (0,1,1) and so is PF's `.pf-topology__edge__tag > text`, so every seq tag
//     rendered in PF's white, error tags included;
//   - a selected node lost its 4px ring to PF's (0,3,0)
//     `.pf-topology__node.pf-m-selected .pf-…__background`, collapsing to 2px — the
//     same weight as an unselected node;
//   - a selected self-call kept PF's 4-2 `pf-m-dashed` instead of the 10-4 selected
//     dash, both (0,2,0).
//
// THE RULE, for anyone adding a `.dg-graph-*` rule: name the co-located PF class in the
// selector (`.pf-topology__node.dg-graph-node.dg-graph-node--x`,
// `.pf-topology__edge__tag.dg-graph-edge-tag--x > text`) so the rule wins on
// specificity rather than on an import order it does not control. Check what PF sets on
// the same element first — including `stroke` on tag text, which is why overriding
// `fill` alone left every tag outlined in white. Do NOT try to fix this by reordering
// the imports: moving these back to main.tsx would drag ~130kB of topology CSS onto
// every reader of the trace list, which is the whole reason they live here.
import '@patternfly/react-topology/dist/esm/css/topology-components.css';
import '@patternfly/react-topology/dist/esm/css/topology-view.css';
import '@patternfly/react-topology/dist/esm/css/topology-controlbar.css';

import { useEntities, useInteractions, useLineageReachability, useLineageSummary } from '../api/hooks';
import { deriveGraph, type GraphEdgeSpec, type GraphNodeSpec, type GraphSpec } from '../lib/graph';
import {
  deriveReachabilityHighlight,
  deriveSourceHighlight,
  resolveSourceChoice,
  type DirectionHighlight,
} from '../lib/lineageReachability';
import { displayNamesByKey, lineageLabel } from '../lib/lineageLabels';
import { kindColorVar, nodeNeutralColorVar } from '../lib/entityKind';
import { riskLevelColorVar, moreSevereRiskLevel } from '../lib/riskLevel';
import { LineageCoverageAlert } from './flow/LineageCoverageAlert';
import { LineageSourceNotices, LineageSourcePicker } from './flow/LineageSourcePicker';
import { LineageEntityPicker } from './flow/LineageEntityPicker';
import type { Entity, Interaction, LineageStatus } from '../types';

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

/**
 * The lineage-reachability facts about one node, which are ORTHOGONAL to
 * {@link HighlightRole} and therefore ride beside it rather than inside it.
 *
 * These four can be true in any combination, all at once, on one node:
 *
 * - `isDataSource` — the trace attributed content to this entity (ADR-0028 D14
 *   `list sources`). A fact about the TRACE, so it is set with no selection at all,
 *   which is by itself enough to rule it out of a selection-relative role enum.
 * - `isChosenSource` — this entity is the ONE source whose data the current answer
 *   traces (`fanin(entity, source)` / `fanout(entity, source)`). A STRICT REFINEMENT
 *   of `isDataSource`, never a replacement: the trace's whole source set stays
 *   coloured, and this says which of them is the subject. Two independent booleans
 *   rather than a `'source' | 'chosen-source'` value, because the reader needs both
 *   facts at once — "five origins, and this is the one you are looking at".
 * - `isUpstream` / `isDownstream` — fan-in / fan-out of the selection. Routinely
 *   BOTH: `agent → tool → agent` is the ordinary shape of every tool call, so the
 *   request leg makes the tool downstream and its response makes it upstream
 *   (ADR-0028 D15's cycle note). Against a live trace, fan-in and fan-out of one
 *   leaf tool each returned the same ten entities.
 * - `isFrontier` — on the PENDING FRONTIER: reached, but the walk could not
 *   continue through it because the onward leg has no derived row yet. Distinct
 *   from every other value here and from `'dimmed'`, because "we don't know yet"
 *   must never look like "there is nothing" (D15).
 *
 * `hops` grades the emphasis by distance (nearer = stronger). It is the FEWEST hops
 * from the seed — a distance, not an ordering of the flow (D10) — and is `null`
 * when the node is not in either answer.
 *
 * A single `HighlightRole` cannot express any of this: it would have to pick one
 * winner per node and silently discard the rest, which for a governance reader
 * means being shown one true fact and denied two others. Hence a record of
 * independent booleans, and independent CSS classes composed from them.
 */
interface NodeLineageFacts {
  isDataSource: boolean;
  isChosenSource: boolean;
  isUpstream: boolean;
  isDownstream: boolean;
  isFrontier: boolean;
  hops: number | null;
}

/** The neutral value: no lineage claim about this node. The Execution Flow tab's every node. */
const NO_LINEAGE_FACTS: NodeLineageFacts = {
  isDataSource: false,
  isChosenSource: false,
  isUpstream: false,
  isDownstream: false,
  isFrontier: false,
  hops: null,
};

/**
 * Which reachability walk(s) traversed one edge — again orthogonal, because one leg
 * can be on both routes at once (the two legs of a tool call, or a genuine cycle).
 */
interface EdgeLineageFacts {
  isUpstream: boolean;
  isDownstream: boolean;
}

const NO_EDGE_LINEAGE_FACTS: EdgeLineageFacts = { isUpstream: false, isDownstream: false };

/**
 * What the renderers read off `data`: the derived spec plus its highlight role.
 *
 * `kindColoured` is what makes the SAME renderer paint the two tabs differently, and it
 * is a per-node flag rather than a lookup of "which tab am I" because the renderers are
 * registered on the controller at module scope and cannot see the tab (the same
 * constraint that puts {@link HighlightRole} on `data` — see its note).
 *
 * WHY THE TWO TABS DIVERGE HERE. On **Execution Flow** hue is free: there is no
 * lineage overlay, so entity kind is the only thing colour could mean, and kind
 * colouring is genuinely useful (it is also what the `EntityPill` in the tables beside
 * it shows, so the two agree). On **Lineage** hue is spoken for — the trace's data
 * sources are the one coloured thing, plus two direction hues and error red — so nodes
 * go neutral there and kind moves to the label and the tooltip. One renderer, one flag,
 * both readings honest.
 */
type NodeData = GraphNodeSpec & {
  highlight: HighlightRole;
  lineage: NodeLineageFacts;
  kindColoured: boolean;
  /**
   * This node's ENTITY risk level, resolved when the caller supplied a
   * `riskLevelByInteraction` map (issue #170) — see
   * {@link EntityGraphProps.riskLevelByInteraction}. `undefined` when no map
   * was supplied at all, which is what keeps both existing tabs' node colour
   * unchanged; present (possibly `'unknown'`) whenever a map is passed, even
   * for a node with no incident edges in it.
   *
   * STOPGAP, not a first-class fact: the risk API has no per-entity risk
   * level, only per-INTERACTION (`ForestInteraction.risk`). This is the worse
   * of the node's incident edges' levels (`moreSevereRiskLevel`,
   * `lib/riskLevel.ts`), computed by `EntityGraph` itself rather than by the
   * caller, because the roll-up needs the same edge set the graph already
   * derives. If the risk model ever gains a genuine per-entity level, that
   * should replace this roll-up rather than sit beside it.
   */
  riskLevel?: string;
};
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
type EdgeData = GraphEdgeSpec & {
  highlight: HighlightRole;
  isSelected: boolean;
  lineage: EdgeLineageFacts;
  /**
   * This edge's leg's INTERACTION risk level, looked up from the caller's
   * `riskLevelByInteraction` map (issue #170) by `e.interactionId` — see
   * {@link EntityGraphProps.riskLevelByInteraction}. `undefined` whenever no
   * map was supplied, or the map has no entry for this interaction (not yet
   * computed — eventual consistency, not a "safe" verdict; see
   * `riskForestAdapter.riskLevelByInteraction`'s docstring). Both of an
   * interaction's two legs share the same level, since the verdict is per
   * interaction, not per leg.
   */
  riskLevel?: string;
};

/**
 * The highlight a caller asks the graph to draw: which nodes are sources of the
 * selected entity's data, which edges carried it, and which node was selected.
 *
 * Optional on {@link EntityGraph} — absent means "draw the plain graph", which is
 * what the Execution Flow tab passes. The graph itself computes NOTHING about
 * lineage: the sets arrive already derived from `lib/lineageReachability`, so this
 * component stays the one renderer of one graph and the lineage question stays in
 * a pure, testable module (jsdom cannot measure an SVG). (`lib/lineageGraph`, which
 * this used to name, was deleted when the client-side roll-up was replaced by the
 * served reachability reads — see LineageGraph's "WHAT REPLACED WHAT".)
 */
export interface GraphHighlight {
  /** The node the reader selected, marked distinctly from its sources. */
  selectedNodeId: string | null;
  /** Node ids in the answer — for the reachability tab, the union of both directions. */
  nodeIds: readonly string[];
  /** Edge ids (`<interaction>:<leg_type>`) in the answer — the traversed route. */
  edgeIds: readonly string[];
  /**
   * The **Lineage reachability** overlay (ADR-0028 D14/D15), when the caller has
   * one. Omitted by the Execution Flow tab, which draws the plain graph.
   *
   * A SEPARATE, OPTIONAL field rather than more members of `nodeIds`, because every
   * set here is orthogonal to the others and to `nodeIds` — see
   * {@link NodeLineageFacts}. In particular `dataSourceNodeIds` is populated with
   * NOTHING selected (it is a fact about the trace), so it cannot live inside a
   * selection-relative answer set.
   *
   * All id sets, never natural keys: the translation from lineage's natural keys to
   * node ids already happened in `lib/lineageReachability`, so this component still
   * computes nothing about lineage and the graph stays one renderer of one graph.
   */
  reachability?: GraphReachabilityOverlay;
}

/**
 * The reachability sets a caller asks the Lineage tab to paint.
 *
 * Every field is a set of ids the graph already contains, so a highlight can never
 * name something the reader cannot see. All are derived in
 * `lib/lineageReachability` from the two served `data-lineage-graph` reads plus the
 * `data-lineage-summary` roll-up — this component does no derivation, for the
 * repo's central reason: jsdom cannot measure an SVG, so the logic has to be
 * somewhere a pure test can reach it.
 */
export interface GraphReachabilityOverlay {
  /**
   * The trace's DATA SOURCE node ids (`list sources`, ADR-0028 D14).
   *
   * Always-on: populated whenever the tab is open, selection or not. Note this is
   * the union of the derived `data_sources`, NOT "entities whose kind is declared a
   * source" — the two are different sets and substituting the taxonomy is the
   * mistake D14 explicitly warns about.
   */
  dataSourceNodeIds: readonly string[];
  /**
   * The ONE chosen source's node id — the subject of the current answer — or `null`.
   *
   * A single id rather than a set, and that is the constraint made structural:
   * multi-source semantics are deferred upstream (`docs/data_lineage_alg.md`'s
   * `## deferred issues`), so a UI that could paint two chosen sources at once could
   * paint a union nobody derived. A field that cannot hold two cannot express one.
   *
   * Always a member of {@link dataSourceNodeIds} when non-null (guaranteed by
   * `deriveSourceHighlight`, which resolves it through the same key bridge), so the
   * refinement can never contradict the always-on set.
   */
  chosenSourceNodeId: string | null;
  /** Fan-in: node ids the selection's data came FROM. */
  upstreamNodeIds: readonly string[];
  /** Fan-out: node ids it went TO. */
  downstreamNodeIds: readonly string[];
  /** Legs the fan-in walk traversed — the upstream route. */
  upstreamEdgeIds: readonly string[];
  /** Legs the fan-out walk traversed — the downstream route. */
  downstreamEdgeIds: readonly string[];
  /**
   * Node ids on either walk's PENDING FRONTIER: reached, but not traversable yet.
   * Marked as provisional rather than dimmed — "not yet" is not "nothing".
   */
  frontierNodeIds: readonly string[];
  /** `node id → fewest hops from the seed`, for the graded treatment. */
  hopsByNodeId: ReadonlyMap<string, number>;
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
  // NOTHING SELECTED IS NOT A QUESTION, so nothing may be dimmed.
  //
  // This guard exists because the Lineage tab now passes a highlight even with no
  // selection — it has to, since the trace's data sources are an always-on fact and
  // they arrive through this same object. Without the guard, opening the tab would
  // dim every node that is not a data source, which asserts "these are not part of
  // the answer" when no answer was requested. That is exactly the lie the
  // `'none'`-vs-`'dimmed'` distinction in {@link HighlightRole} was written to
  // prevent; it just became reachable from a second direction.
  //
  // Note this deliberately keys on `selectedNodeId`, not on whether the sets are
  // empty: a selected entity whose answer is legitimately empty (`no-adjacent`)
  // SHOULD dim the rest, because a question was asked and the answer is "nothing".
  if (h.selectedNodeId === null) return 'none';
  if (isNode && h.selectedNodeId === id) return 'selected';
  const inAnswer = isNode ? h.nodeIds.includes(id) : h.edgeIds.includes(id);
  // `'source'` for a node in the answer, `'carrier'` for an edge that delivered it
  // — two names because the two are different claims and the stylesheet treats
  // them differently (a lit arrow reads as a route, a lit node as an origin).
  return inAnswer ? (isNode ? 'source' : 'carrier') : 'dimmed';
}

/**
 * The reachability facts about one node, read off the overlay.
 *
 * Independent booleans rather than a resolved single value — see
 * {@link NodeLineageFacts} for why one node can be a data source AND upstream AND
 * downstream at the same time, and why picking a winner would deny the reader two
 * true facts.
 *
 * `isDataSource` is resolved with no reference to the selection, which is the whole
 * point of the always-on half.
 */
function lineageFactsOf(id: string, h: GraphHighlight | undefined): NodeLineageFacts {
  const r = h?.reachability;
  if (!r) return NO_LINEAGE_FACTS;
  return {
    isDataSource: r.dataSourceNodeIds.includes(id),
    // A REFINEMENT, so it is set independently of `isDataSource` and both land
    // together on the chosen node. Not `=== id && isDataSource`: the invariant that a
    // chosen source is always in the source set belongs to `deriveSourceHighlight`
    // (which resolves both through one bridge), and re-asserting it here would be a
    // second place for it to be true — the duplication the house rules forbid.
    isChosenSource: r.chosenSourceNodeId === id,
    isUpstream: r.upstreamNodeIds.includes(id),
    isDownstream: r.downstreamNodeIds.includes(id),
    // A node that IS in an answer is not on the frontier: a derived route to it
    // exists, whatever else about it is undelivered. The server already subtracts
    // the reached set from the frontier; this mirrors that so a server that ever
    // stopped doing so could not make a node claim both at once on screen.
    isFrontier:
      r.frontierNodeIds.includes(id) &&
      !r.upstreamNodeIds.includes(id) &&
      !r.downstreamNodeIds.includes(id),
    hops: r.hopsByNodeId.get(id) ?? null,
  };
}

/** Which walk(s) traversed one edge. Both, for the two legs of one tool call. */
function edgeLineageFactsOf(id: string, h: GraphHighlight | undefined): EdgeLineageFacts {
  const r = h?.reachability;
  if (!r) return NO_EDGE_LINEAGE_FACTS;
  return {
    isUpstream: r.upstreamEdgeIds.includes(id),
    isDownstream: r.downstreamEdgeIds.includes(id),
  };
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
 * How far, in px, an ADJACENT-COLUMN edge bows off the straight line between its
 * two cells — applied with the `seq` parity sign, so a request and its response
 * arc to opposite sides of the same span.
 *
 * Sized for LEGIBILITY, not clearance: there is nothing between two adjacent
 * cells to route around (that is what distinguishes this from the skipping and
 * same-column cases), so the only job is to separate the pair. It has to clear
 * the node discs at both ends — `NODE_DIAMETER / 2` is the radius the arrow
 * leaves from — while staying well inside the row gutter, or the bow would
 * wander into the row above/below and read as pointing at the wrong entity.
 * At a 96px row pitch with 40px discs there is ~28px of clear gutter each side,
 * so 18px separates the two legs plainly without crowding either neighbour.
 *
 * The two legs therefore sit 36px apart at the midpoint — comfortably more than
 * the `seq` tags need to stop colliding, which is the specific defect this fixes.
 */
const ADJACENT_BOW_Y = 18;

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
 * intervening cells, and the edge is DRAWN THROUGH it, so the arrow visibly detours
 * rather than cutting through — which is the whole readable fact. A full orthogonal
 * router (per-lane channel assignment, corner radii) is a much larger thing, and it
 * would have to be re-derived on every drag: a bendpoint is stored geometry, and a
 * dragged node's edges would keep their stale detour. Keeping the detour to one point
 * that is a pure function of the two ENDPOINT CELLS means it is recomputed with the
 * model and stays honest.
 *
 * NOTE ON WHAT "THROUGH IT" MEANS NOW. It used to mean a POLYLINE — that is what PF's
 * `DefaultEdge` draws, an `L` to the bendpoint and an `L` onwards. The graph no longer
 * uses that renderer: `CurvedEdge` draws a smooth quadratic arc whose APEX is this
 * bendpoint (see `controlThrough` for the compensation that makes the apex land on it
 * exactly rather than at half the offset). Nothing in THIS function changed — the
 * bendpoint is still the single notion of curvature and is still sized for clearance
 * the same way — but the shape it produces is an arc, not a corner, and the clearance
 * it buys is identical.
 *
 * THE OFFSET SIGN alternates with the edge's `seq` parity, so two edges over the
 * same span — a request and its response, or two parallel interactions — detour
 * to OPPOSITE sides instead of tracing the same line. That is what stops
 * same-pair edges from drawing exactly on top of one another, which the staircase
 * did (and which its own comment admitted). Parity, not an index into the group,
 * because it needs no second pass over the edge list and `seq` is already unique
 * per leg.
 *
 * AN ADJACENT-COLUMN PAIR IS BOWED, NOT LEFT STRAIGHT — and this is the case that
 * matters most, because it is the shape of an ordinary call. An earlier version of
 * this function returned no bendpoint for `spans === 1` on the reasoning that a
 * straight line there crosses no intervening cell. True, but it missed the other
 * reason an edge needs routing: A→B and B→A over one column step share BOTH
 * anchor points, so the request and its response were drawn exactly on top of each
 * other — one line with an arrowhead at each end and the two `seq` tags colliding.
 * The claim that parity "stops same-pair edges from drawing on top of one another"
 * was therefore only true for the spans that fell past that early return, i.e. the
 * rarer ones.
 *
 * So `spans === 1` now gets a SMALL parity-signed bow (`ADJACENT_BOW_Y`) rather
 * than the half-row lift a skipping edge takes: the pair has to separate enough to
 * read as two arrows, while each still reads as the direct connection it is. There
 * is nothing between the two cells to clear, so the offset is sized for legibility
 * (clear of the node discs, well inside the row gutter), not for clearance.
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

  const a = gridPosition(from);
  const b = gridPosition(to);
  // Opposite sides for the two legs of a pair, so they do not retrace one line.
  const side = edge.seq % 2 === 0 ? 1 : -1;

  if (spans === 1) {
    // ADJACENT COLUMNS — the ordinary call, and the pair that used to overlap
    // exactly (see the note above). Nothing sits between the two cells, so this
    // bow exists purely to separate the request from its response: a small
    // parity-signed lift at the midpoint, so the two legs arc apart and each
    // keeps its own `seq` tag legible instead of both landing on one line.
    return [[(a.x + b.x) / 2, (a.y + b.y) / 2 + side * ADJACENT_BOW_Y]];
  }

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

/** A plain 2-D point. Structurally compatible with PF's geom `Point` for reading. */
interface XY {
  readonly x: number;
  readonly y: number;
}

/**
 * How far along the curve, as a fraction of the last segment, the point used to aim
 * the END ARROWHEAD is taken from.
 *
 * THE ARROWHEAD IS AIMED BY A POINT, not by an angle, because that is the only lever
 * PF gives: `ConnectorArrow` computes its own `rotate()` from
 * `getConnectorRotationAngle(startPoint, endPoint)` — the angle of the chord between
 * the two points it is handed — and exposes no rotation prop
 * (`components/edges/terminals/ConnectorArrow.js`). So to point the head along the
 * curve we hand `DefaultConnectorTerminal` an explicit `startPoint` that lies ON the
 * curve just short of the end, making its "chord" a secant that approximates the
 * tangent. (`DefaultConnectorTerminal` accepts `startPoint`/`endPoint` overrides and
 * falls back to the bendpoint only when they are absent — verified in its source.)
 *
 * WHY A SECANT AND NOT THE EXACT TANGENT. The exact tangent direction of a quadratic
 * at t=1 is `end - control`, and handing the terminal `control` as its start point
 * would give precisely the right angle. It is deliberately not done, because PF does
 * not only take the ANGLE from that point: `getConnectorStartPoint` also uses the
 * distance between the two points to back the arrow's tip off the endpoint by
 * `size`, and a far-away control point is fine there while a NEAR one silently is
 * not (when the separation is under `size` the ratio goes negative and the head
 * flips to the far side of the node). Sampling the curve at a fixed t gives a point
 * whose direction is within a degree of the tangent for the curvatures this graph
 * produces AND whose distance from the end is a stable fraction of the segment, so
 * both of the things PF derives from it stay sane.
 *
 * 0.9 rather than something smaller: closer to 1 is a better tangent approximation
 * but a shorter secant, and PF's `size` back-off needs the secant to be comfortably
 * longer than the terminal (12px here). At the ~200px segments this grid produces,
 * 0.1 of a segment is ~20px — longer than the head, short enough that the secant and
 * the tangent are visually the same line.
 */
const TANGENT_SAMPLE_T = 0.9;

/**
 * Sample a quadratic Bézier at `t`.
 *
 * Its own function rather than inlined, because it is called for the start terminal,
 * the end terminal and the hit band's two trimmed ends, and an off-by-one in the
 * Bernstein coefficients would be a subtly wrong arrowhead angle in three places.
 */
function quadraticAt(from: XY, control: XY, to: XY, t: number): XY {
  const u = 1 - t;
  return {
    x: u * u * from.x + 2 * u * t * control.x + t * t * to.x,
    y: u * u * from.y + 2 * u * t * control.y + t * t * to.y,
  };
}

/**
 * The CONTROL point that makes a quadratic Bézier actually PASS THROUGH `via`.
 *
 * THIS IS THE COMPENSATION, and it is the whole reason this function exists rather
 * than `Q<bendpoint>` being written straight into the path. A quadratic does not go
 * through its control point: at t=0.5 it sits at `(from + 2*control + to) / 4`, i.e.
 * exactly HALFWAY between the chord's midpoint and the control point. So naively
 * using the bendpoint as the control would draw a curve with only HALF the
 * bendpoint's offset — and that offset is not decorative. `edgeBendpoints` sizes it
 * to clear the cells a straight line would cross (a column-skipping edge lifts by
 * `spans * ROW_STEP_Y / 2` precisely to get above the rows in between), so halving
 * it would put the curve back over the nodes the routing exists to avoid — silently
 * re-introducing the overlap `edgeBendpoints` was written to fix.
 *
 * Inverting the t=0.5 identity gives `control = 2*via - (from + to)/2`: place the
 * control at twice the bendpoint's offset from the chord midpoint and the curve's
 * apex lands ON the bendpoint. The drawn arc therefore has the SAME clearance the
 * polyline had, with the corner rounded off instead of the detour reduced.
 *
 * REJECTED: a cubic through the bendpoint. Two control points would let the curve
 * hug the bend more tightly (flatter approach at both ends, sharper apex), which is
 * arguably a closer match to the polyline's silhouette — but it needs a second
 * invented parameter (how far along the chord each control sits) that no existing
 * geometry supplies, and the task's constraint is to reuse the bendpoint as the ONE
 * notion of curvature. The quadratic needs no such parameter: the bendpoint fully
 * determines it. A single smooth arc through the same apex is also the more
 * legible shape at this scale, where each edge spans one or two grid steps.
 */
function controlThrough(from: XY, via: XY, to: XY): XY {
  return {
    x: 2 * via.x - (from.x + to.x) / 2,
    y: 2 * via.y - (from.y + to.y) / 2,
  };
}

/**
 * The pivot points a chain of quadratic arcs is built around, for an edge with N
 * bendpoints.
 *
 * N BENDPOINTS IS HANDLED, NOT ASSERTED AWAY. `edgeBendpoints` returns 0 or 1 today,
 * and it would have been legitimate to assert that and fail loudly — but the assert
 * would have to live in the RENDERER, which also draws the Lineage tab and would
 * then throw on a model some future router pushed rather than degrade. A general
 * chain is barely more code than the assertion would be and cannot fail that way.
 *
 * The chain: for bendpoints b1..bN, draw an arc through each `bi`, joining
 * consecutive arcs at the MIDPOINT of `bi`→`bi+1`. Joining at midpoints is what makes
 * the chain smooth (it is the standard quadratic-spline construction): the tangent
 * entering the joint and the tangent leaving it are both parallel to `bi`→`bi+1`, so
 * there is no visible corner. Joining at the bendpoints themselves would put a kink
 * at every one and be no better than the polyline.
 *
 * Returns the sequence of `[start, via, end]` triples, one per arc. For N=1 this is
 * the single `[start, b1, end]` the common case wants, with no special-casing.
 */
function arcSegments(from: XY, bendpoints: readonly XY[], to: XY): Array<[XY, XY, XY]> {
  const mid = (p: XY, q: XY): XY => ({ x: (p.x + q.x) / 2, y: (p.y + q.y) / 2 });
  return bendpoints.map((via, i) => [
    // First arc starts at the true start; later arcs start where the previous ended.
    i === 0 ? from : mid(bendpoints[i - 1]!, via),
    via,
    // Last arc ends at the true end; earlier arcs end at the joint with the next.
    i === bendpoints.length - 1 ? to : mid(via, bendpoints[i + 1]!),
  ]);
}

/**
 * The SVG path data for one edge, as a smooth curve, plus the points the two
 * terminals must be aimed with.
 *
 * ONE BUILDER FOR BOTH PATHS PF DRAWS. `DefaultEdge` builds the visible link and the
 * transparent ~10px hit band separately but identically, and the band is what makes
 * a 1.5px arrow clickable — so if the band kept tracing the straight chord while the
 * link curved, every edge would be clickable somewhere it is not drawn and not
 * clickable where it is. They differ only in their ENDPOINTS (the band stops short of
 * the terminals so the transparent stroke does not overhang the arrowhead), which is
 * why this takes the two ends as arguments and is called twice.
 *
 * ZERO-LENGTH AND UNROUTED EDGES fall through to a straight `L`, deliberately: with
 * no bendpoint there is no control geometry to curve with, and inventing one here
 * would be the second notion of curvature this design exists to avoid. In practice
 * the only such edges are SELF-CALLS (see the model builder) — every routed edge has
 * a bendpoint after 94769aa, so every edge the reader sees as a connection curves.
 */
function curvePath(from: XY, bendpoints: readonly XY[], to: XY): string {
  if (bendpoints.length === 0) return `M${from.x} ${from.y} L${to.x} ${to.y}`;
  const segments = arcSegments(from, bendpoints, to);
  return (
    `M${from.x} ${from.y} ` +
    segments
      .map(([segFrom, via, segTo]) => {
        const c = controlThrough(segFrom, via, segTo);
        return `Q${c.x} ${c.y} ${segTo.x} ${segTo.y}`;
      })
      .join(' ')
  );
}

/**
 * A point ON the curve, `t` of the way along its FIRST or LAST arc — what the start
 * and end terminals are aimed with (see {@link TANGENT_SAMPLE_T}).
 *
 * `fromEnd` picks which arc, because the two terminals need opposite ends of a
 * multi-arc chain and the end one is the one that matters (it carries the arrowhead).
 */
function curveTangentPoint(
  from: XY,
  bendpoints: readonly XY[],
  to: XY,
  fromEnd: boolean,
): XY {
  if (bendpoints.length === 0) return fromEnd ? from : to;
  const segments = arcSegments(from, bendpoints, to);
  const [segFrom, via, segTo] = fromEnd ? segments[segments.length - 1]! : segments[0]!;
  const c = controlThrough(segFrom, via, segTo);
  // For the END terminal, sample near t=1 (approaching `segTo`); for the START one,
  // sample near t=0 — in both cases a point just INSIDE the curve from the terminal
  // it aims, so the secant to that terminal points the way the curve is going.
  return quadraticAt(segFrom, c, segTo, fromEnd ? TANGENT_SAMPLE_T : 1 - TANGENT_SAMPLE_T);
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
 * The one custom node renderer: PF's `DefaultNode` with a NEUTRAL stroke/label colour
 * pushed in through the two CSS variables PF's own node styles read.
 *
 * NEUTRAL FOR EVERY KIND, which reverses what this renderer used to do (it painted
 * each node its entity kind's hue via `kindColorVar`, the same map `EntityPill` uses).
 * The reversal is recorded rather than quietly applied, because the old arrangement
 * was deliberate and its reasoning is still visible elsewhere in this file:
 *
 * HUE WAS OVERSUBSCRIBED. On this one graph, colour was carrying entity kind, error,
 * fan-in and fan-out — four meanings — which is why `global.css` had to give the
 * trace's DATA SOURCES a ring instead of a colour, and why its own header block says
 * "hue is already carrying entity kind, error and two directions" as the reason a
 * source hue would be "both ambiguous and invisible". Freeing kind's hue resolves
 * that: the data sources become the one COLOURED thing on the graph, which is the
 * fact the Lineage tab exists to show.
 *
 * KIND IS NOT LOST, it moves off hue onto channels that were already carrying it:
 * the node's visible label, its `<title>` and accessible name (see {@link nodeTitle}),
 * and the kind-coloured `EntityPill` in the flow tables on the same screen. See
 * `lib/entityKind.nodeNeutralColorVar` for the divergence this creates with those
 * pills, which is intentional and stated there.
 */
/**
 * A node's hover/AT text: kind, natural key, and every lineage claim IN WORDS.
 *
 * The words are the point, not a nicety. The lineage treatments are rings, haloes
 * and dashes on a 40px circle, and a reader who cannot distinguish the hues (or who
 * is using a screen reader, or who has simply not read the legend) gets the same
 * facts here as text. That is what stops the meaning being encoded in hue alone —
 * the requirement the stylesheet's stacking note also addresses visually.
 *
 * `hops` is included as a DISTANCE ("2 hops away"), never as a position in a
 * sequence: two entities at the same depth were reached by different routes and the
 * lineage algebra has no truthful interleaving to offer (ADR-0028 D10).
 *
 * Phrasing is deliberately the same vocabulary the tab's alerts use, so the tooltip
 * and the notice above the graph cannot describe one state in two ways.
 */
function nodeTitle(data: NodeData | undefined): string {
  const head = `${data?.kind ?? 'entity'} — ${data?.naturalKey ?? ''}`;
  const l = data?.lineage;
  if (!l) return head;
  const claims: string[] = [];
  // "data source" first: it is the standing fact about the trace, true regardless of
  // what is selected, so it reads oddly after the selection-relative claims.
  //
  // THE CHOSEN SOURCE IS NAMED IN WORDS, which is the accessibility half of the
  // distinction: the visual refinement is a second ring plus a heavier weight (see
  // global.css), and hue is already spoken for three times over on this graph. A
  // reader who sees no colour difference at all still gets "the source being traced"
  // in the tooltip and the screen-reader name. Stated as ONE claim rather than two
  // adjacent clauses, because "a data source, and also the traced one" reads as two
  // coincidences rather than as a refinement.
  if (l.isChosenSource) claims.push('the data source being traced (this trace’s source too)');
  else if (l.isDataSource) claims.push('data source for this trace');
  if (l.isUpstream && l.isDownstream) {
    // Called out as one claim rather than two, because "both" is the governance-
    // relevant shape (data left and came back) and two separate clauses read as two
    // unrelated coincidences.
    claims.push('both upstream and downstream of the selected entity');
  } else if (l.isUpstream) {
    claims.push('upstream of the selected entity (its data came from here)');
  } else if (l.isDownstream) {
    claims.push('downstream of the selected entity (its data went here)');
  }
  if (l.isFrontier) {
    claims.push('on the pending frontier — lineage through it is not derived yet');
  }
  if (l.hops !== null) claims.push(`${l.hops} hop${l.hops === 1 ? '' : 's'} away`);
  return claims.length > 0 ? `${head} — ${claims.join('; ')}` : head;
}

/**
 * Which reachability walk(s) traversed this leg, in words, for the edge's accessible
 * name and hover text.
 *
 * Same reasoning as {@link nodeTitle}: the routes are distinguished visually by hue
 * AND dash, and a reader who gets neither still needs to know that this arrow is
 * part of the answer and which half of it. Returns `''` when no walk claims the leg,
 * so the Execution Flow tab's labels are unchanged.
 */
function edgeLineageSuffix(data: EdgeData | undefined): string {
  const l = data?.lineage;
  if (!l) return '';
  if (l.isUpstream && l.isDownstream) return ' (on both the upstream and downstream route)';
  if (l.isUpstream) return ' (on the upstream route)';
  if (l.isDownstream) return ' (on the downstream route)';
  return '';
}

function KindColouredNode({ element, ...rest }: React.ComponentProps<typeof DefaultNode>) {
  const data = element.getData() as NodeData | undefined;
  // RISK TAKES PRECEDENCE (issue #170), same reasoning as the edge colour below:
  // a node's `riskLevel` is present only when a caller passed
  // `riskLevelByInteraction` at all (see `EntityGraphProps.riskLevelByInteraction`),
  // which today is only the trace-detail view — both existing tabs leave it
  // `undefined` and fall straight through to the unchanged branch beneath.
  //
  // PER TAB OTHERWISE (see `NodeData.kindColoured`): Execution Flow paints entity
  // kind, because nothing else on that tab wants hue; Lineage paints every node
  // neutral so the trace's data sources can be the one coloured thing. Defaults to
  // NEUTRAL when `data` is missing — the conservative direction, since a stray kind
  // hue on the Lineage tab would compete with the source colouring.
  const colour =
    data?.riskLevel !== undefined
      ? riskLevelColorVar(data.riskLevel)
      : data?.kindColoured
        ? kindColorVar(data.kind)
        : nodeNeutralColorVar();
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
      <title>{nodeTitle(data)}</title>
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
          // The reachability facts, as INDEPENDENT classes that compose. A node that
          // is a data source, upstream AND downstream carries all three, and the
          // stylesheet stacks their treatments (see global.css's stacking note) —
          // which is only possible because these are not values of one enum.
          data?.lineage.isDataSource ? 'dg-graph-node--datasource' : '',
          // Both classes on the chosen source, never one instead of the other — see
          // NodeLineageFacts. The stylesheet keys the refinement on the PAIR, so the
          // trace's source ring is still what says "this is an origin".
          data?.lineage.isChosenSource ? 'dg-graph-node--chosen-source' : '',
          data?.lineage.isUpstream ? 'dg-graph-node--upstream' : '',
          data?.lineage.isDownstream ? 'dg-graph-node--downstream' : '',
          data?.lineage.isFrontier ? 'dg-graph-node--frontier' : '',
        ]
          .filter(Boolean)
          .join(' ')}
      />
    </g>
  );
}

/**
 * PF's `DefaultEdge`, re-assembled around a CURVED path instead of a polyline.
 *
 * A drop-in for `DefaultEdge` at the props this file uses, so `DirectedEdge` below
 * reads the same as it did — the substitution is one identifier.
 *
 * ITS PROPS ARE TYPED AS `React.ComponentProps<typeof DefaultEdge>`, i.e. `DefaultEdge`
 * is still imported even though nothing renders it. That is deliberate — deriving the
 * prop shape from the component this stands in for is what makes "drop-in" a checked
 * claim rather than a comment, so a 5.x bump that changes PF's edge props is a type
 * error here instead of a silently divergent fork. It costs nothing at runtime:
 * verified in the built chunk, Rollup tree-shakes the component's implementation away
 * (its distinctive `bgStartPoint`/`backgroundPath` locals are absent from
 * `dist/assets/ExecutionFlowGraph-*.js`), because a `typeof` in a type position is
 * erased and leaves no value reference behind.
 *
 * WHY THE LIBRARY COMPONENT IS FORKED AT ALL, since forking is the expensive option
 * and the whole of this comment block is the justification. Read from
 * `components/edges/DefaultEdge.js` in 5.4.1, not inferred:
 *
 *   - IT HARDCODES THE PATH COMMAND. The link is built as
 *     `` `M${start} ${bendpoints.map(b => `L${b} `)}L${end}` `` — literal `L`
 *     segments, with no prop, hook, render-prop or context that influences it. Its
 *     `backgroundPath` (the hit band) is built the same way, separately.
 *   - IT IS THE ONLY EDGE RENDERER IN THE PACKAGE. There is no curved, bezier or
 *     spline variant anywhere in 5.4.1 (`components/edges/` holds `DefaultEdge`,
 *     `TaskEdge`, the connector terminals and the tag — nothing else draws a link).
 *     So there is no supported component to switch to.
 *
 * REJECTED: rendering our own `<path>` through `DefaultEdge`'s `children` slot and
 * hiding PF's link with CSS. This was the first thing tried, because it would have
 * kept every feature below for free. It does not work, for two reasons found in the
 * source and the stylesheet rather than guessed:
 *
 *   - THE HIT BAND CANNOT BE REPLACED, ONLY ADDED TO. `children` is rendered LAST
 *     inside PF's `<g>`, after both paths. PF's `.pf-topology__edge__background` — the
 *     `stroke-width: 10px; stroke: transparent` band that is the entire reason a 1.5px
 *     arrow is clickable — would go on tracing the straight chord. Every edge would
 *     then be clickable along a line it is not drawn on and NOT clickable where the
 *     reader can see it, which breaks edge selection on precisely the bowed edges that
 *     94769aa introduced. Hiding that band and adding our own means re-deriving its
 *     `.pf-m-selected` / `.pf-m-hover` stroke rules ourselves, i.e. most of this
 *     component anyway.
 *   - THE STYLING IS KEYED ON PF'S TWO CLASS NAMES, ours and PF's alike. Our
 *     `--carrier` (3px), `--selected` (4px + dash) and PF's own selected/hover rules
 *     are all `… .pf-topology__edge__link`, and `pf-m-dashed` (which is how a self-call
 *     is marked) is `.pf-topology__edge__link.pf-m-dashed`. A child path with a
 *     different class silently loses all of it; a child path with the SAME class
 *     cannot be distinguished from PF's by any CSS rule that would hide one and not
 *     the other. Either way the fix is worse than the fork.
 *
 * So: our own two paths, carrying PF's own two class names, with everything else
 * `DefaultEdge` does reproduced deliberately. What is reproduced, and why each
 * matters here:
 *
 *   1. THE ARROWHEAD (`DefaultConnectorTerminal`), aimed along the CURVE. See
 *      {@link TANGENT_SAMPLE_T} — this is the one thing a naive fork gets visibly
 *      wrong, because PF's default aim is the chord from the last bendpoint and on a
 *      bowed edge that chord points measurably off the curve's actual heading.
 *   2. THE `seq` TAG (`DefaultConnectorTag`), with PF's `ScaleDetailsLevel` gating
 *      and hover rescale, unchanged. The tag places itself at the CHORD midpoint,
 *      which is now off the curve by half the bendpoint offset — see the note at its
 *      call site for why that is left alone.
 *   3. THE ~10px TRANSPARENT HIT BAND, now following the curve (see `curvePath`).
 *   4. THE STATE CLASSES AND BEHAVIOURS: `pf-m-selected`, `pf-m-hover` via PF's own
 *      `useHover`, `pf-m-dragging`, `StatusModifier`, the `TOP_LAYER` hoist on
 *      hover/drag, `onClick`/`onSelect` (which is what edge selection is wired
 *      through), `onContextMenu`, and the `edgeStyle` modifier that marks self-calls.
 *   5. THE COLLAPSED-GROUP EARLY RETURN.
 *
 * NOT reproduced, deliberately: `onShowRemoveConnector`/`onHideRemoveConnector` and
 * the `sourceDragRef`/`targetDragRef` reconnect handles. This graph registers no
 * `useReconnect` or remove-connector behavior (verified — the only behaviors it
 * composes are `withPanZoom`, `withSelection` and `withDragNode`), so those props are
 * never passed and wiring them would be dead code that reads as if a feature existed.
 *
 * AN `observer`, exactly as `DefaultEdgeInner` is, and this is NOT ceremony — it was
 * verified the hard way. The first draft of this fork omitted it on the reasoning that
 * PF's `ElementWrapper` is already an `observer` and renders us inside it, so the
 * subscription was someone else's job. That is wrong, and the existing "keeps both
 * legs attached to a node that has moved" test caught it: a dragged node's edge stopped
 * following. `ElementWrapper`'s `observer` only dereferences `element` itself; the
 * observable actually read on a drag is the SOURCE NODE'S POSITION, and it is read
 * here — `element.getStartPoint()` falls through to `sourceAnchor.getLocation(…)`,
 * which reads the live node position. mobx tracks a dereference in the component that
 * performs it, so the subscription has to be on THIS component. Without it the edge
 * re-renders only when something else happens to re-render it, i.e. the arrows detach
 * from the node the reader is dragging.
 */
const CurvedEdge = observer(function CurvedEdge({
  element,
  dragging,
  edgeStyle,
  animationDuration,
  endTerminalType = EdgeTerminalType.directional,
  endTerminalClass,
  endTerminalStatus,
  endTerminalSize = 14,
  startTerminalType = EdgeTerminalType.none,
  startTerminalClass,
  startTerminalStatus,
  startTerminalSize = 14,
  tag,
  tagClass,
  tagStatus,
  children,
  className,
  selected,
  onSelect,
  onContextMenu,
}: React.ComponentProps<typeof DefaultEdge>) {
  // PF's own hover hook, so `pf-m-hover` and the TOP_LAYER hoist behave exactly as
  // they do on a `DefaultEdge` — including its 200ms in/out delays, which exist to
  // stop the layer hoist flickering as the pointer crosses an edge.
  const [hover, hoverRef] = useHover<SVGGElement>();

  if (!isEdge(element)) throw new Error('CurvedEdge must be used only on Edge elements');

  const startPoint = element.getStartPoint();
  const endPoint = element.getEndPoint();
  const bendpoints = element.getBendpoints();

  // COLLAPSED GROUPS, kept verbatim from `DefaultEdge`: when both ends have collapsed
  // into the SAME visible parent there is nothing to draw between, and drawing it
  // anyway would put a stray loop on the group. Never fires on this graph (it builds
  // no groups) but dropping it would be a silent behavioural change in a fork.
  // `getClosestVisibleParent` returns `Node | null`, so the null is what is tested
  // here. PF's own copy of this guard spells it `isNode(sourceParent) && …`, which
  // under `strict` does not typecheck against that nullable return — and the `isNode`
  // call is redundant regardless, since the declared return type is already `Node`.
  const sourceParent = getClosestVisibleParent(element.getSource());
  const targetParent = getClosestVisibleParent(element.getTarget());
  if (sourceParent && sourceParent.isCollapsed() && sourceParent === targetParent) {
    return null;
  }

  const detailsLevel = element.getGraph().getDetailsLevel();

  // THE VISIBLE LINK, end to end.
  const linkPath = curvePath(startPoint, bendpoints, endPoint);

  // THE HIT BAND. Same curve, but each end pulled back by its terminal's size when a
  // terminal is drawn there — `DefaultEdge` does this so the 10px transparent stroke
  // does not overhang the arrowhead, and the back-off is measured along the TANGENT
  // (via the same sampled point that aims the head) rather than along the chord, so
  // on a bowed edge the band stops where the curve actually arrives instead of off to
  // one side of it.
  //
  // Note the bendpoints are NOT trimmed — only the ends move — so the band is the
  // same arc as the link through its whole middle. That is the property that makes a
  // click anywhere along the drawn curve land.
  const tangentToEnd = pointOf(curveTangentPoint(startPoint, bendpoints, endPoint, true));
  const tangentToStart = pointOf(curveTangentPoint(startPoint, bendpoints, endPoint, false));
  const bandStart =
    !startTerminalType || startTerminalType === EdgeTerminalType.none
      ? startPoint
      : pointFromPair(getConnectorStartPoint(tangentToStart, startPoint, startTerminalSize));
  const bandEnd =
    !endTerminalType || endTerminalType === EdgeTerminalType.none
      ? endPoint
      : pointFromPair(getConnectorStartPoint(tangentToEnd, endPoint, endTerminalSize));
  const backgroundPath = curvePath(bandStart, bendpoints, bandEnd);

  // PF's own class composition, reproduced. `styles.topologyEdge` etc. are spelled as
  // literals rather than imported from `css/topology-components`: that module is CJS
  // with a `require('./topology-components.css')` side effect, the stylesheet is
  // already imported at the top of this file, and the three names are the same
  // literals our own global.css and the tests key on — so a literal here is the
  // single source of truth those already share, not a fourth copy of it.
  const groupClassName = [
    'pf-topology__edge',
    className,
    dragging ? 'pf-m-dragging' : '',
    hover && !dragging ? 'pf-m-hover' : '',
    selected && !dragging ? 'pf-m-selected' : '',
    endTerminalStatus ? StatusModifier[endTerminalStatus] : '',
  ]
    .filter(Boolean)
    .join(' ');
  const linkClassName = [
    'pf-topology__edge__link',
    getEdgeStyleClassModifier(edgeStyle ?? element.getEdgeStyle()),
  ]
    .filter(Boolean)
    .join(' ');

  // The tag's own gating and hover rescale, unchanged from `DefaultEdge`.
  const showTag = tag && (detailsLevel === ScaleDetailsLevel.high || hover);
  const scale = element.getGraph().getScale();
  const tagScale = hover && detailsLevel !== ScaleDetailsLevel.high ? Math.max(1, 1 / scale) : 1;
  const tagPositionScale =
    hover && detailsLevel !== ScaleDetailsLevel.high ? Math.min(1, scale) : 1;

  return (
    <Layer id={dragging || hover ? TOP_LAYER : undefined}>
      {/* `data-test-id="edge-handler"` is PF's own attribute on this group and the
          existing tests target it to click an edge — kept, spelling included (it is
          `data-test-id`, not the `data-testid` RTL looks for). */}
      <g
        ref={hoverRef}
        data-test-id="edge-handler"
        className={groupClassName}
        onClick={onSelect}
        onContextMenu={onContextMenu}
      >
        <path className="pf-topology__edge__background" d={backgroundPath} />
        <path
          className={linkClassName}
          d={linkPath}
          style={{
            animationDuration: `${
              animationDuration ?? getEdgeAnimationDuration(element.getEdgeAnimationSpeed())
            }s`,
          }}
        />
        {showTag && (
          <g transform={`scale(${hover ? tagScale : 1})`}>
            {/* THE TAG STILL SITS ON THE CHORD MIDPOINT, not on the curve, because
                `DefaultConnectorTag` computes its own translate from the two points it
                is given (`start + (end - start) * 0.5`) and takes no position
                override. Handing it the curve's apex instead — which IS available, it
                is the bendpoint — was considered and rejected: the apex is where the
                two legs of a pair are FURTHEST apart, but the tag is a small filled
                rect and putting it exactly on the drawn line hides that line under it
                at the one place the reader uses to tell the two arcs apart. The chord
                midpoint leaves the label just inside its own arc, off the stroke, and
                the parity-signed bow keeps the request's and response's labels on
                opposite sides — which is the collision this graph actually had to
                fix. */}
            <DefaultConnectorTag
              className={tagClass}
              startPoint={element.getStartPoint().scale(tagPositionScale)}
              endPoint={element.getEndPoint().scale(tagPositionScale)}
              tag={tag}
              status={tagStatus}
            />
          </g>
        )}
        {/* THE TERMINALS, each aimed with an explicit `startPoint` sampled from the
            curve (see TANGENT_SAMPLE_T). Without the override PF re-derives the aim
            from `edge.getBendpoints()` itself — the chord from the last bendpoint to
            the end — which on a bowed edge is visibly off the curve's heading at the
            node, i.e. an arrowhead pointing somewhere the line does not go. This is
            the single most noticeable way a curved fork goes wrong, so the override is
            not an optimisation. */}
        <DefaultConnectorTerminal
          className={startTerminalClass}
          isTarget={false}
          edge={element}
          size={startTerminalSize}
          terminalType={startTerminalType}
          status={startTerminalStatus}
          highlight={dragging || hover}
          startPoint={tangentToStart}
          endPoint={startPoint}
        />
        <DefaultConnectorTerminal
          className={endTerminalClass}
          isTarget
          edge={element}
          size={endTerminalSize}
          terminalType={endTerminalType}
          status={endTerminalStatus}
          highlight={dragging || hover}
          startPoint={tangentToEnd}
          endPoint={endPoint}
        />
        {children}
      </g>
    </Layer>
  );
});

/**
 * A geom `Point` from a plain `{x, y}`.
 *
 * Needed because PF's terminal props and `getConnectorStartPoint` are typed on the
 * geom class, not on a structural `{x, y}` — the curve helpers above work in plain
 * objects (they are arithmetic, and a `Point` per intermediate sample would be
 * allocation for nothing), so the conversion happens once at the boundary.
 */
function pointOf(p: XY): Point {
  return new Point(p.x, p.y);
}

/** The same, from the `[x, y]` tuple `getConnectorStartPoint` returns. */
function pointFromPair([x, y]: [number, number]): Point {
  return new Point(x, y);
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
 * THE CLICK IS PF'S OWN `onSelect`, forwarded to the edge renderer and nothing more.
 * That renderer binds it as `onClick` on the outer `<g>` that carries the whole edge
 * (`<g ref={hoverRef} data-test-id="edge-handler" className={groupClassName}
 * onClick={onSelect}>`), so forwarding the prop is the entire wiring — there is no
 * hand-rolled listener, no `ref`, and no second hit target to keep in sync with the
 * arrow's geometry. The renderer is now {@link CurvedEdge} rather than PF's
 * `DefaultEdge`, and that group is reproduced there verbatim (attribute spelling
 * included) precisely so this wiring did not have to change.
 *
 * REJECTED: a bespoke `onClick` on the wrapper `<g>` here. It would work, but it
 * would have to reinvent the two things the edge renderer already does correctly —
 * the hit area (below) and `pf-m-selected` — and it would put the handler OUTSIDE the
 * element PF hangs its own hover/drag state on, so a click during an edge drag
 * would still fire. Forwarding the prop PF already reads is strictly less code
 * doing strictly more.
 *
 * THE HIT TARGET IS ALREADY WIDE, verified in the library rather than assumed, and
 * this is the reason nothing like `InteractionDiagram`'s hand-rolled full-width hit
 * strip is needed here. The edge renders TWO paths inside that clickable `<g>`: the
 * visible `.pf-topology__edge__link` and, first, a `.pf-topology__edge__background`
 * tracing the same route — which `css/topology-components.css` gives
 * `stroke-width: 10px; stroke: transparent`. A transparent STROKE (unlike a
 * transparent fill) is hit-testable, and the only `pointer-events` rule PF puts on an
 * edge at all is `pointer-events: none` while `.pf-m-dragging`. So the clickable band
 * is ~10px wide along the whole ARC — bendpoint apex included, since `CurvedEdge`
 * builds both paths from the same curve builder specifically so the band cannot drift
 * onto the chord while the line bows away from it. Not the 1.5px the reader can see.
 * The sequence diagram had to build its own strip because it is hand-rolled SVG with
 * no such layer; duplicating one here would be a second hit target competing with the
 * one already there.
 *
 * `pf-m-selectable` IS ours to add, though, and it is the one gap. PF's own
 * `TaskEdge` emits it (`onSelect && 'pf-m-selectable'`) but `DefaultEdge` never
 * does — and `CurvedEdge`, being a faithful fork of it, does not either. It is what
 * flips `--edge--cursor` from `default` to `pointer`. Without it the edge is clickable
 * but does not LOOK clickable, which is a worse defect than it sounds: a reader who
 * never guesses the arrow is a target gets none of this feature.
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
  // ERROR KEEPS PRECEDENCE (issue #170): a leg's `isError` is a fact about what
  // happened on the wire, not a risk grade, so it wins even over a critical risk
  // level. Risk colour is next, present only when the caller passed
  // `riskLevelByInteraction` AND this edge's interaction has an entry in it —
  // both existing tabs never pass the map, so `data?.riskLevel` is always
  // `undefined` for them and this falls straight through unchanged.
  const colour = data?.isError
    ? 'var(--dg-color-error)'
    : data?.riskLevel !== undefined
      ? riskLevelColorVar(data.riskLevel)
      : 'var(--dg-tree-guide)';
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
      //     screen, so a focused arrow may not be visible. `.dg-graph-edge-focus:focus-visible`
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
      }${data?.isError ? ' (failed)' : ''}${edgeLineageSuffix(data)}`}
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
      {/* `CurvedEdge`, not PF's `DefaultEdge` — a drop-in at these props that draws a
          smooth arc through the routed bendpoint instead of a polyline corner. Every
          feature relied on below (the click target, the arrowhead, the tag, the wide
          hit band, the state classes) is reproduced there; see its note for what forced
          the fork and what was rejected first. */}
      <CurvedEdge
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
          // The tag takes its arrow's direction hue, so the seq number stays visually
          // attached to the route it labels rather than floating in the label grey.
          data?.lineage.isUpstream ? 'dg-graph-edge-tag--upstream' : '',
          data?.lineage.isDownstream ? 'dg-graph-edge-tag--downstream' : '',
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
          // The traversed-route classes — THE ROUTE, which is what the endpoint
          // returns `legs` for. Independent of every axis above and of each other: one
          // leg can be on both walks (the two legs of a tool call), can be the selected
          // interaction's, and can have failed, all at once.
          data?.lineage.isUpstream ? 'dg-graph-edge--upstream' : '',
          data?.lineage.isDownstream ? 'dg-graph-edge--downstream' : '',
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
 * NOW WRAPPED IN `withSelection` TOO, which REVERSES an earlier decision here. The
 * reversal is recorded rather than quietly applied, because the old reasoning was
 * specific and is worth knowing was checked rather than forgotten.
 *
 * **What the old note claimed, and why it was wrong.** It said a node's whole `<g>`
 * is its own drag surface, so `withSelection`'s `onClick` would sit on the element
 * the pointer is already holding down — and "a drag that ends where it began is
 * indistinguishable from a click, so every abandoned or tiny drag would also
 * select". The first half is true; the conclusion is not, because **d3-drag
 * distinguishes them for us**. Read in the installed sources rather than assumed:
 *
 * - `d3-drag/src/drag.js` tracks `mousemoving`, set once the pointer travels
 *   further than `clickDistance` (`dx*dx + dy*dy > clickDistance2`), and on mouseup
 *   calls `yesdrag(event.view, mousemoving)`.
 * - `d3-drag/src/nodrag.js`'s `yesdrag(view, noclick)` installs a **capturing**
 *   `click.drag` handler that swallows the click — but ONLY when `noclick` is true,
 *   i.e. only when the pointer actually moved — and removes it again on the next
 *   tick (`setTimeout(..., 0)`).
 *
 * So a real drag's trailing click is suppressed by d3 itself, and a press-release
 * with no movement is delivered as an ordinary click. The "tiny drag" case resolves
 * the same way: any movement at all exceeds the default `clickDistance` of 0, so it
 * counts as a drag and is suppressed. We are not fighting the gesture; we are using
 * the discrimination d3 already performs.
 *
 * **PF ships exactly this combination.** `DefaultNode` binds `onClick: onSelect` on
 * the same `<g>` that receives its `refs` — the ref list that includes
 * `dragNodeRef` (`components/nodes/DefaultNode.js`). A draggable, selectable node is
 * the library's own default arrangement, not a combination being invented here.
 *
 * **Why it matters enough to change.** The Lineage tab asks the reader to pick an
 * entity and then shows its fan-in and fan-out. Requiring them to leave the graph,
 * find the row in the Entities table and come back is a worse instrument for
 * exploring a graph than clicking the node they are already looking at. The Entities
 * table remains a fully equivalent control — the same `selectEntity` path, so there
 * is still exactly ONE notion of "selected entity" — and remains the accessible
 * route, since a table row is reachable by keyboard in a way an SVG circle is not.
 *
 * `raiseOnSelect: false` matches the edges: raising reorders the SVG, and a node
 * jumping in z-order on click reads as the graph twitching.
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
 * visible consequence is that a dragged node's edge can bow oddly until Reset View,
 * which is a better failure than a per-frame re-route.
 *
 * STILL TRUE AFTER THE CURVE, but the shape of the rough edge changed enough to be
 * worth restating rather than left to be inferred. `CurvedEdge` anchors the arc's APEX
 * on the bendpoint (see `controlThrough`), so a stale bendpoint now skews the whole
 * curve rather than misplacing one corner of a polyline — the arc leans toward where
 * the node used to be. It is the same staleness with the same cause and the same fix
 * (Reset View), and it is if anything MORE legible as a drag artefact than a stray
 * corner was: a smooth arc that leans is obviously a routing leftover, whereas a
 * displaced polyline vertex read as a kink that might have been deliberate. No new
 * defect, and nothing here needs to change — the two ends still track the drag
 * exactly, which is what keeps the arrow attached to the node.
 */
const DraggableKindColouredNode = withDragNode()(
  withSelection({ raiseOnSelect: false })(KindColouredNode),
);

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
   * Fired with the clicked NODE's entity id.
   *
   * An id for the same reason `onSelectInteraction` takes one: the graph holds a
   * derived spec and no entities array, while `FlowTables` owns the reads, the
   * evidence fetch and the `?eid` mirroring. So the graph reports "this node was
   * clicked" and the owner routes it into the SAME `selectEntity` an Entities-table
   * row click makes — which is what keeps one notion of "selected entity" rather
   * than the graph growing a second one.
   *
   * Omitted by the Execution Flow tab. Node clicks still fire PF's selection event
   * there (nodes are uniformly selectable), but with no handler the event is simply
   * not acted on, so that tab's behaviour is unchanged.
   *
   * There is deliberately no `null` arm: a background click already deselects
   * through `onSelectInteraction(null)`, and giving both callbacks a deselect would
   * mean two ways to say one thing.
   */
  onSelectEntity?: (entityId: string) => void;
  /**
   * Extra disclosures to render BELOW the surface, alongside the graph's own three
   * notices (dropped interactions / isolated entities / parallel channels).
   *
   * The seam exists because the Lineage tab has notices of its own — unresolvable
   * source keys, a partial roll-up — that are neither facts about the graph nor
   * something this component should know how to word. Rendering them HERE rather
   * than above the whole component keeps them in one block with the graph's own, so
   * a reader meets every caveat about the picture in one place instead of two.
   *
   * THEY HAVE NOW MOVED TWICE, and the history is worth keeping because each move
   * fixed a real defect rather than being a restyle:
   *   1. Originally STACKED ABOVE the graph. This tab can legitimately have ten
   *      notices at once (a source roll-up, a coverage caveat, two prompts, two
   *      per-direction verdicts, three graph disclosures), and stacked inline they
   *      pushed the drawing area entirely below the fold — the reader scrolled past
   *      every caveat to reach the thing they qualify.
   *   2. Then into a fixed-width SIDE RAIL. That kept both on screen, but it spent
   *      22rem of horizontal room permanently — on a graph whose columns grow
   *      rightwards with call depth, which is the axis it could least afford.
   *   3. Now BELOW the surface, by request. The graph gets the full width, and the
   *      informational text reads as the footnotes it is: consulted after looking at
   *      the picture, in the same place the legend already sits.
   * The CONTROLS did not follow them down — see {@link controls} for why a control a
   * reader has to act on cannot live below the thing it drives.
   */
  notices?: React.ReactNode;
  /**
   * Interactive CONTROLS to render ABOVE the surface.
   *
   * A SEPARATE SLOT FROM {@link notices}, and the distinction is the whole point of
   * this prop existing: notices are text a reader READS (and which therefore belongs
   * below the picture, out of the way), whereas controls are things a reader has to
   * ACT ON before the picture can answer anything. The Lineage view's two pickers —
   * the entity and the data source — are the two halves of `fanin(entity, source)`,
   * and a graph that says "select an entity to trace this source's data" while its
   * only entity control sits below the fold would be an instruction the reader cannot
   * follow without hunting for it.
   *
   * So the informational text went below the graph and the pickers deliberately did
   * not. Empty on the Execution Flow tab, which asks the reader for nothing.
   */
  controls?: React.ReactNode;
  /**
   * The key to the graph's treatments, rendered BELOW the surface.
   *
   * A separate slot from {@link notices} because it is a different kind of thing and
   * now lives in a different place: notices are transient claims about THIS trace
   * (and go in the side rail), whereas the legend is a standing key to what the
   * drawing's colours and rings MEAN. Below the graph specifically — a reader
   * consults a key after looking at the picture and finding a treatment they cannot
   * read, so it belongs where the eye lands on the way back out, not above the thing
   * it explains.
   *
   * Still in DOCUMENT ORDER after the surface, so a screen reader meets the graph and
   * then its key; it is deliberately not a floating overlay, which would cover the
   * drawing it exists to explain.
   */
  legend?: React.ReactNode;
  /** `data-testid` on the wrapper, so each tab is addressable as itself. */
  testId?: string;
  /**
   * Interaction id -> risk level (issue #170's Alert Execution view), from
   * `lib/riskForestAdapter.ts`'s `riskLevelByInteraction`. Omitted entirely
   * by both existing tabs (Execution Flow, Lineage) — a colour a third view
   * needs, not something either of them renders — which is what keeps their
   * node/edge colours byte-for-byte unchanged: every risk-colour branch below
   * is reached only when this is present.
   *
   * An interaction absent from the map (its `risk` was `null` — not yet
   * computed) gets no edge/node treatment at all, same as an absent map;
   * only an explicit entry paints anything, so "not yet computed" is never
   * drawn as a colour.
   */
  riskLevelByInteraction?: ReadonlyMap<string, string>;
  /**
   * Suppress the built-in "N entity pair(s) with multiple interactions"
   * notice (issue #170's Alert Execution view). That notice's whole premise
   * is the request/response leg pair a completed interaction normally draws
   * in the same channel — see the notice's own comment below, "a single
   * completed interaction always puts two arrows... so counting edges would
   * fire this notice on virtually every trace". The risk trace view draws
   * request legs only (`riskForestAdapter.toFlowInteractions`), so a
   * "multiple interactions" notice there is answering a question about
   * response arrows that page never draws — noise, not a caveat. Both
   * existing tabs omit this prop and keep the notice unchanged.
   */
  hideParallelGroupsNotice?: boolean;
  /**
   * Suppress each edge's visible `seq`-number tag (issue #170's Alert
   * Execution view). That number is meaningful on the Execution Flow/Lineage
   * tabs, where it is the trace-wide leg order a reader cross-references
   * against the Flat table's own `seq` column — a table this view has no
   * equivalent of. Here the arrows are the whole picture (one request leg
   * per interaction, no table alongside them), so a bare number floating on
   * every arrow is clutter with nothing to cross-reference, not information.
   * The interaction's full summary is still available via the edge's
   * `<title>` hover text either way. Both existing tabs omit this prop and
   * keep their tags unchanged.
   */
  hideEdgeLabels?: boolean;
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
 * `lib/lineageReachability` (`deriveSourceHighlight` / `deriveReachabilityHighlight` /
 * `resolveSourceChoice`), so all of it is testable without laying out an SVG (jsdom
 * cannot measure one). Note `resolveSourceChoice` is in that list because it decides
 * whether a read FIRES at all, which is likewise not something a render test of an
 * SVG-bearing component can honestly assert.
 */
export function EntityGraph({
  spec,
  traceId,
  highlight,
  selectedInteractionId = null,
  onSelectInteraction,
  onSelectEntity,
  notices,
  controls,
  legend,
  testId = 'execution-flow-graph',
  riskLevelByInteraction,
  hideParallelGroupsNotice = false,
  hideEdgeLabels = false,
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
    ? [
        highlight.selectedNodeId ?? '',
        highlight.nodeIds.join(','),
        highlight.edgeIds.join(','),
        // The reachability sets are part of the CONTENT, so they belong in the digest:
        // they are baked into the element `data` exactly as the roles are, and a
        // digest that ignored them would leave the source colouring painted from a
        // stale model. That is not hypothetical — `dataSourceNodeIds` arrives from a
        // separate query that resolves on its own schedule, typically AFTER the first
        // model push, so it is the normal case rather than an edge one.
        highlight.reachability?.dataSourceNodeIds.join(',') ?? '',
        // The chosen source is part of the CONTENT for exactly the reason the note
        // above gives for the source set: switching source repaints one node's
        // refinement and, if the answer is cached, may change nothing else at all — so
        // a digest without it would leave the previous source marked as the traced one.
        highlight.reachability?.chosenSourceNodeId ?? '',
        highlight.reachability?.upstreamNodeIds.join(',') ?? '',
        highlight.reachability?.downstreamNodeIds.join(',') ?? '',
        highlight.reachability?.upstreamEdgeIds.join(',') ?? '',
        highlight.reachability?.downstreamEdgeIds.join(',') ?? '',
        highlight.reachability?.frontierNodeIds.join(',') ?? '',
      ].join('|')
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
    // A node's risk colour (see `NodeData.riskLevel`'s STOPGAP note) is the
    // worst level among the edges it touches, as either source or target —
    // built once per model push rather than per node, since it is one pass
    // over `spec.edges` regardless of node count. `undefined` for a node with
    // no risk-mapped incident edge, which leaves it uncoloured, not "safe".
    const riskLevelByNode = new Map<string, string>();
    if (riskLevelByInteraction !== undefined) {
      for (const e of spec.edges) {
        const level = riskLevelByInteraction.get(e.interactionId);
        if (level === undefined) continue;
        for (const nodeId of [e.source, e.target]) {
          const existing = riskLevelByNode.get(nodeId);
          riskLevelByNode.set(nodeId, existing === undefined ? level : moreSevereRiskLevel(existing, level));
        }
      }
    }
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
          data: {
            ...n,
            highlight: roleOf(n.id, highlight, true),
            lineage: lineageFactsOf(n.id, highlight),
            // KIND COLOURS ON EXECUTION FLOW, NEUTRAL ON LINEAGE. Keyed on the
            // presence of a `highlight` because that IS the difference between the two
            // tabs (the Execution Flow wrapper passes none — see `ExecutionFlowGraph`),
            // rather than on a new "which tab" prop that would be a second way to say
            // the same thing and could contradict it.
            kindColoured: highlight === undefined,
            riskLevel: riskLevelByNode.get(n.id),
          } satisfies NodeData,
        };
      }),
      edges: spec.edges.map((e) => ({
        id: e.id,
        type: 'dg-interaction',
        source: e.source,
        target: e.target,
        // A self-call's source and target are the same point, so its path has zero
        // length and is invisible. `dashed` at least marks the node as carrying one;
        // PF has no self-loop routing in 5.4, and `CurvedEdge` deliberately did not
        // add one — see its `curvePath`, and the `edgeBendpoints` early return, for
        // why a self-loop arc would need a second notion of curvature beside the
        // bendpoint. Unchanged by the curve work; still the weakest thing on this
        // graph.
        edgeStyle: e.isSelfCall ? EdgeStyle.dashed : EdgeStyle.solid,
        // THE ROUTING, and the apex the drawn arc bends through. One point for every
        // edge that connects two distinct cells — a small parity-signed bow for the
        // adjacent-column pair (so a request and its response separate rather than
        // retrace one line), a larger detour for an edge that skips columns or stays
        // within one. Empty ONLY for a self-call, which has nowhere to bend to. See
        // `edgeBendpoints`, and `controlThrough` for how the arc is made to pass
        // through this point exactly rather than at half its offset.
        bendpoints: edgeBendpoints(e, cellById),
        data: {
          ...e,
          // Blanked rather than left on the spec when the caller opts out (see
          // `EntityGraphProps.hideEdgeLabels`) — `DirectedEdge` reads only
          // `data?.label` for its visible tag, so this is the single place
          // that needs to know about the flag.
          label: hideEdgeLabels ? '' : e.label,
          highlight: roleOf(e.id, highlight, false),
          // Per INTERACTION, so both legs of the selected one are marked — see
          // EdgeData. A primitive comparison, so this adds nothing the effect's
          // dependency list cannot express (`selectedInteractionId` is itself the
          // digest, unlike the highlight's object).
          isSelected: selectedInteractionId != null && e.interactionId === selectedInteractionId,
          lineage: edgeLineageFactsOf(e.id, highlight),
          riskLevel: riskLevelByInteraction?.get(e.interactionId),
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
    //
    // `riskLevelByInteraction` is listed directly too, same reasoning as
    // `selectedInteractionId`: the risk colour is baked into element `data`, and
    // both existing tabs pass `undefined` forever, so this dependency is a no-op
    // for them. `hideEdgeLabels` joins them for the identical reason: the blanked
    // label is baked into element `data` too, and both existing tabs pass `false`
    // forever.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    controller,
    spec,
    traceId,
    harvestDraggedPositions,
    highlightKey,
    selectedInteractionId,
    riskLevelByInteraction,
    hideEdgeLabels,
  ]);

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

  /** Same latest-value ref treatment for the node-click callback, same reasoning. */
  const onSelectEntityRef = useRef(onSelectEntity);
  onSelectEntityRef.current = onSelectEntity;

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
   *   4. `['<entityId>']` — a NODE was clicked. Select that entity. This shape is
   *      new: nodes became selectable once d3-drag was confirmed to suppress the
   *      trailing click of a real drag and pass a stationary one through (the
   *      evidence is in `DraggableKindColouredNode`'s note). It routes to
   *      `onSelectEntity`, i.e. to the SAME `selectEntity` the Entities table calls,
   *      so clicking a node and clicking its row are one path and there is still one
   *      notion of "selected entity".
   *
   * ANY OTHER id is IGNORED rather than treated as a deselect — an element this graph
   * does not know. Silently reading an uninterpretable id as "no interaction" would
   * close the reader's open panel for no visible reason. Doing nothing with an id we
   * cannot interpret is the honest response.
   *
   * ORDER OF THE TWO LOOKUPS is not arbitrary: an edge id (`<uuid>:request`) and a
   * node id (a bare uuid) cannot collide, so either order is correct — edges are
   * tried first only because they are the older, more frequent case.
   */
  useEffect(() => {
    const onSelectionEvent = (ids: string[]) => {
      const id = ids[0];
      // Shapes 2 and 3 — empty canvas, or the toggle-off of the selected element.
      if (id == null || id === GRAPH_ID) {
        onSelectRef.current?.(null);
        return;
      }
      if (!controller.hasGraph()) return;
      // Shape 1. Looked up on the live graph rather than by splitting the id on `:`,
      // so the `<interaction>:<legType>` format stays stated in exactly one place
      // (`lib/graph`'s GraphEdgeSpec.id) — the edge already carries `interactionId`
      // as a field for precisely this mapping.
      const edge = controller.getEdgeById(id);
      const data = edge?.getData() as EdgeData | undefined;
      if (data) {
        onSelectRef.current?.(data.interactionId);
        return;
      }
      // Shape 4. A node's id IS its entity id (`GraphNodeSpec.id` is `Entity.id`), so
      // unlike the edge there is nothing to map — but it is still resolved through the
      // graph rather than passed through blind, so an id belonging to no drawn node
      // cannot reach the caller as a selection.
      const node = controller.getNodeById(id);
      if (!node) return;
      onSelectEntityRef.current?.(node.getId());
    };
    controller.addEventListener(SELECTION_EVENT, onSelectionEvent);
    return () => {
      controller.removeEventListener(SELECTION_EVENT, onSelectionEvent);
    };
  }, [controller]);

  return (
    <div data-testid={testId} className="dg-graph-layout">
      {/* THE CONTROLS, above the surface — the only thing that stayed above it. They
          are what the reader ACTS on (the Lineage view's entity and source pickers),
          and an instruction to "select an entity" whose control sat below the fold
          would be unfollowable. See `EntityGraphProps.controls`. */}
      {controls}
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
      {/* THE LEGEND, immediately below the surface — a key belongs under the picture it
          decodes, and above the prose so it stays adjacent to the drawing it explains.
          See `EntityGraphProps.legend`. */}
      {legend}

      {/* EVERY INFORMATIONAL AND WARNING BALLOON, BELOW THE GRAPH. Was stacked above it
          (which pushed the drawing off screen), then in a fixed-width side rail (which
          spent 22rem of the axis the graph grows along) — now here, by request. See
          `EntityGraphProps.notices` for the full history and why the CONTROLS did not
          follow the text down.

          `role="complementary"` + a label so an AT reader can jump to the caveats, or
          skip past them, rather than walking every alert to reach the next thing.

          DOCUMENT ORDER NOW MATCHES VISUAL ORDER, which it deliberately did not in the
          side-rail arrangement (the rail was first in the DOM and second on screen, so
          that a screen reader met the caveats before the drawing). That inversion was
          only defensible while the notices were visually adjacent to the graph. Below it,
          DOM-first would mean announcing ten alerts before the reader reaches the picture
          they qualify — so the two orders agree again, and the landmark above is what
          makes the block reachable without being unavoidable. */}
      <div className="dg-graph-notices" role="complementary" aria-label="Graph notices">
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

            Counted in INTERACTIONS, not edges: under the leg model a single
            completed interaction always puts two arrows (its request and its
            response) in the same channel, so counting edges would fire this notice
            on virtually every trace — noise that is always on carries no
            information. See lib/graph's parallelGroups. */}
        {spec.parallelGroups.length > 0 && !hideParallelGroupsNotice && (
          <Alert
            variant="info"
            isInline
            title={`${spec.parallelGroups.length} entity pair${spec.parallelGroups.length === 1 ? '' : 's'} with multiple interactions`}
            style={{ marginBottom: '0.5rem' }}
          >
            {'Each leg of each interaction is drawn as its own arrow rather than being merged into one, so the arrow count matches the leg count on the Flat tab.'}
          </Alert>
        )}
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
 * The **Lineage** tab: the SAME graph, with the trace's data sources coloured and —
 * for ONE chosen source and one selected entity — that source's fan-in AND fan-out
 * highlighted (ADR-0028 D14/D15).
 *
 * ONE QUESTION ASKED OF THE EXECUTION FLOW PICTURE, not a second picture. It
 * renders {@link EntityGraph} — the identical nodes, edges, layered layout, drag
 * lifecycle and zoom controls — and adds only a highlight plus the caveats that
 * highlight needs.
 *
 * THE QUESTION HAS TWO REQUIRED HALVES, which is the shape of the read rather than a
 * UI choice: `docs/data_lineage_alg.md`'s `## API` specifies
 * `fanin(entity, source)` / `fanout(entity, source)`, and an edge is traversed only
 * when the chosen source appears in that leg's lineage `data_sources`. So the tab
 * traces "THIS source's data through THIS entity", and neither half alone is askable —
 * omitting `source` is a 400, not a broader answer. Hence a source PICKER
 * ({@link LineageSourcePicker}) beside the existing entity selection, and a reachability
 * read gated on both.
 *
 * EXACTLY ONE SOURCE AT A TIME. Multi-source semantics are deferred upstream
 * (`docs/data_lineage_alg.md`'s `## deferred issues`: "Do we expect the exact set of
 * sources? Any of them?"), so offering "all sources" would mean this component choosing
 * between a union and an intersection and presenting its choice as a served answer.
 * The picker is therefore single-select by design — see its header before "improving" it.
 *
 * TWO DISTINCT VISUAL JOBS, and they are independent:
 *
 * 1. **Source colouring, always on.** Driven by `data-lineage-summary`'s
 *    `list sources` — the union of the trace's derived `data_sources` (NOT a
 *    taxonomy read of "entities declared sources", which is a different set that
 *    diverges exactly where a delegation-shaped tool over-reports, D14). Shown
 *    from first paint with nothing selected, because it is a standing fact about
 *    the trace and a fact a reader has to click to discover is not being reported.
 *    It is emphatically NOT conditional on which source is chosen; the chosen one is
 *    marked as a REFINEMENT on top (a double halo — see `global.css`), so a reader sees
 *    both "the trace has these origins" and "this graph is about that one". The same
 *    read is also the picker's only supplier of options.
 * 2. **Fan-in and fan-out on selection AND a chosen source.** Both directions at once,
 *    from the two `data-lineage-graph` reads, with the traversed LEGS lit as well as the
 *    nodes — the route is what the endpoint returns `legs` for. Every verdict names the
 *    source it is about, because "nothing upstream of this entity" is a far stronger
 *    claim than a source-scoped walk supports.
 *
 * BOTH DIRECTIONS AT ONCE, NOT A TOGGLE, and the alternative was considered
 * seriously. A toggle halves the API calls and the visual load; it was rejected
 * because fan-in and fan-out are two halves of ONE question ("what touched this
 * entity's data"), so a reader forced to flip between them has to hold half the
 * graph in their head to assemble an answer the tool could simply have shown. It
 * would also HIDE the most governance-relevant shape in the picture — an entity
 * that is both upstream and downstream, i.e. data that went out and came back —
 * because that fact only exists in the overlap of the two answers. They are kept
 * visually DISTINCT instead (see `global.css`'s reachability block: separate hues,
 * plus dash-vs-solid so the distinction is not carried by colour alone), which is
 * what stops "show both" from becoming one undifferentiated blob.
 *
 * THE HIGHLIGHT IS NOT DERIVED HERE. Both jobs are mapped to node/edge id sets by
 * `lib/lineageReachability`, for the repo's central reason: jsdom cannot measure an
 * SVG, so anything mapping API responses to highlighted ids has to live where a
 * pure test can reach it.
 *
 * WHAT REPLACED WHAT. This tab previously rolled its own answer up client-side from
 * the per-leg `byLeg` map (`lib/lineageGraph`, now deleted): direct sources only,
 * one hop, explicitly refusing to walk transitively because "a transitive claim the
 * backend never derived would be the UI inventing lineage". That reasoning was
 * right, and ADR-0028 D15 records that it is precisely what the server removed —
 * the backend now derives the multi-hop claim, so it is citable. The tab therefore
 * renders a SERVED answer instead of composing one, and the old module was deleted
 * rather than kept beside this: two competing notions of "the lineage highlight" is
 * the duplication that lets two readings of one trace disagree.
 *
 * THE ABSENCE STATES ARE KEPT APART ON SCREEN, PER DIRECTION, which is ADR-0028's
 * discipline restated for a graph. Four states, never collapsed:
 *   - `derived`     → an answer, including when it is empty (provenance ends here);
 *   - `pending`     → the eventual-consistency window, with `pending_frontier`
 *                     naming the entities the walk could not continue through YET.
 *                     Emphatically not an empty answer;
 *   - `no-adjacent` → the ONLY state where empty is a complete answer;
 *   - a failed READ → a fourth, separate thing: nothing is known, so retry. It is
 *                     tracked per direction, so a failed fan-out cannot erase a
 *                     good fan-in.
 * `truncated` is reported separately from the frontier because they are different
 * claims (D15): the frontier says "ask again later", truncation says "derived, but
 * this answer declined to return it all".
 *
 * THE FOUR ABOVE ARE STATES OF AN ANSWER, and they sit BELOW three states in which
 * there is no answer to be in a state about — kept apart from them for the same reason
 * the four are kept apart from each other:
 *   - **no source chosen** → an INSTRUCTION ("choose a data source to trace"), rendered
 *     by the picker. No request is fired at all, so none of the four applies;
 *   - **a stale `?src`**   → a WARNING naming what was asked for, so a bookmark that
 *     does not restore says why instead of degrading to a bare prompt;
 *   - **zero sources**     → nothing derived to trace. Stated once, by the source
 *     roll-up alert, and distinct from both "still loading" and "the read failed".
 * `resolveSourceChoice` decides which of the three applies; none of them is an answer,
 * and none may be worded as one.
 */
export function LineageGraph({
  traceId,
  entities,
  interactions,
  status,
  isLineageError,
  selectedEntityId,
  lineageSource = null,
  onLineageSourceChange,
  selectedInteractionId = null,
  onSelectInteraction,
  onSelectEntity,
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
  /**
   * The trace's coverage (ADR-0028 D6), from the flow view's `useDataLineage`.
   *
   * Still taken from the caller even though this tab no longer reads `byLeg`: the
   * coverage banner above the tabs and this tab's repeat of it must quote ONE value,
   * and the reachability responses carry their own `status` that could in principle
   * be read at a different instant. Passing the caller's keeps the two agreeing.
   */
  status: LineageStatus;
  /** The trace-scoped lineage read failed — nothing is known, not "no sources". */
  isLineageError: boolean;
  /** The flow view's `?eid` selection. THE one notion of "selected entity". */
  selectedEntityId: string | null;
  /**
   * The reader's chosen data source (`?src`), as an **Entity natural key**, or `null`.
   *
   * A CONTROLLED PROP, like `selectedEntityId` and `legView`, because the page owns
   * every URL param in this view (`TraceDetailPage`) — this component never reaches
   * for `useSearchParams`, so a reload/bookmark restores the choice through the same
   * one path the other params use rather than through a second mechanism.
   *
   * A natural KEY, not an entity id, because that is what lineage stores and what the
   * endpoint's `source` parameter takes (`lineageLabels`' header). It is reconciled
   * against the trace's own roll-up by `resolveSourceChoice` before anything is
   * asked, so a value this trace has no lineage for becomes a *notice* rather than a
   * request the server would refuse.
   *
   * EXACTLY ONE, never a list: multi-source semantics are deferred upstream
   * (`docs/data_lineage_alg.md`'s `## deferred issues`), so a union would be the UI
   * inventing an answer. See `LineageSourcePicker`'s header.
   */
  lineageSource?: string | null;
  /** Fired with the newly chosen source so the parent can mirror `?src`. */
  onLineageSourceChange?: (source: string) => void;
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
  /**
   * A NODE was clicked — routed to the same `selectEntity` the Entities table calls.
   *
   * Nodes became click targets once d3-drag was confirmed to suppress a real drag's
   * trailing click while passing a stationary one through; the library evidence is in
   * `DraggableKindColouredNode`'s note. The table remains an equivalent (and the
   * keyboard-accessible) control, and both go through this one callback's owner, so
   * there is still exactly one selection path.
   */
  onSelectEntity?: (entityId: string) => void;
}) {
  const entitiesQ = useEntities(traceId);
  const interactionsQ = useInteractions(traceId);

  // THE TWO NEW READS. The summary is ungated — the trace's sources are a standing
  // fact, so they paint with nothing selected, AND it is the only supplier of the
  // choosable source list, so the picker cannot be drawn before it lands.
  const summaryQ = useLineageSummary(traceId);

  /**
   * The reader's `?src`, reconciled against the trace's actual roll-up.
   *
   * Derived BEFORE the reachability hooks because it decides whether they fire at all:
   * `source` is required by the read (`fanin(entity, source)`), so an unchosen or
   * stale choice means there is no askable question and `useLineageGraph`'s `enabled`
   * must be false. Firing anyway would produce an unavoidable 400 on first paint,
   * which the tab would then have to render as the "failed read" state — telling the
   * reader to retry something that cannot succeed until they choose.
   */
  const sourceChoice = useMemo(
    () => resolveSourceChoice({ requested: lineageSource, summary: summaryQ.data }),
    [lineageSource, summaryQ.data],
  );

  // Gated on BOTH halves of the question: a seed entity and a chosen source. Either
  // missing → no request. See `useLineageGraph`.
  const reach = useLineageReachability(traceId, selectedEntityId, sourceChoice.source);

  // Derived ONCE and handed to both the highlight and the renderer, so the
  // highlight can only ever name elements that are actually on screen.
  const spec = useMemo(() => deriveGraph(entities, interactions), [entities, interactions]);

  const sources = useMemo(
    () =>
      deriveSourceHighlight({
        entities,
        summary: summaryQ.data,
        graph: spec,
        // The chosen source is resolved to a node HERE, through the same key bridge
        // every other source goes through — so the "this is the traced one" mark can
        // only ever land on a node that is also one of the trace's marked sources.
        chosenSource: sourceChoice.source,
      }),
    [entities, summaryQ.data, spec, sourceChoice.source],
  );

  const reachability = useMemo(
    () =>
      deriveReachabilityHighlight({
        selectedEntityId,
        source: sourceChoice.source,
        fanin: {
          data: reach.fanin.data,
          isError: reach.fanin.isError,
          isLoading: reach.fanin.isLoading,
        },
        fanout: {
          data: reach.fanout.data,
          isError: reach.fanout.isError,
          isLoading: reach.fanout.isLoading,
        },
        graph: spec,
      }),
    [
      selectedEntityId,
      sourceChoice.source,
      reach.fanin.data,
      reach.fanin.isError,
      reach.fanin.isLoading,
      reach.fanout.data,
      reach.fanout.isError,
      reach.fanout.isLoading,
      spec,
    ],
  );

  /**
   * The prop `EntityGraph` bakes into the element data.
   *
   * Passed even with NOTHING selected — unlike the previous version, which omitted
   * it — because the source colouring is selection-independent and travels on this
   * same object. `selectedNodeId: null` is what tells `roleOf` not to dim anything
   * (see its guard): "no question asked" must not be painted as "not part of the
   * answer", and that distinction now has to survive a highlight being present.
   */
  const highlight: GraphHighlight = useMemo(
    () => ({
      selectedNodeId: reachability.selectedNodeId,
      nodeIds: reachability.litNodeIds,
      edgeIds: reachability.litEdgeIds,
      reachability: {
        dataSourceNodeIds: sources.sourceNodeIds,
        chosenSourceNodeId: sources.chosenSourceNodeId,
        upstreamNodeIds: reachability.fanin.nodeIds,
        downstreamNodeIds: reachability.fanout.nodeIds,
        upstreamEdgeIds: reachability.fanin.edgeIds,
        downstreamEdgeIds: reachability.fanout.edgeIds,
        // Both walks' frontiers, unioned: a node the reader cannot yet see through is
        // provisional regardless of which direction discovered that.
        frontierNodeIds: [
          ...new Set([
            ...reachability.fanin.pendingFrontierNodeIds,
            ...reachability.fanout.pendingFrontierNodeIds,
          ]),
        ].sort(),
        // Fan-in's distance wins a tie only because one number can be shown; the
        // hover text names WHICH direction(s) claim the node, so the graded ring is
        // never the only thing saying what the distance means.
        hopsByNodeId: new Map([
          ...reachability.fanout.hopsByNodeId,
          ...reachability.fanin.hopsByNodeId,
        ]),
      },
    }),
    [reachability, sources.sourceNodeIds, sources.chosenSourceNodeId],
  );

  // Friendly names for the natural keys the notices quote, from the same helper
  // every other lineage surface uses — so an unresolvable source is named the way
  // the detail panel names a resolvable one.
  const namesByKey = useMemo(() => displayNamesByKey([...entities]), [entities]);

  /**
   * The chosen source's friendly label, for the verdicts that name their subject.
   *
   * Read off `reachability.source` rather than off `sourceChoice.source`, and the
   * difference matters: `reachability` is the object the highlight was built from, so
   * the label and the lit nodes are guaranteed to be about the SAME source even
   * mid-transition. Taking it from the choice would let a re-select move the label a
   * render before the highlight follows — a correct label over the previous source's
   * graph, which is the one failure mode worse than no label at all.
   *
   * `''` when there is no source, which is unreachable where it is used (the notices
   * are gated on `selectedNodeId !== null`, which implies a source) but is the honest
   * value rather than a non-null assertion.
   */
  const sourceLabel =
    reachability.source === null ? '' : lineageLabel(reachability.source, namesByKey).label;

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
      onSelectEntity={onSelectEntity}
      testId="lineage-graph"
      /* THE LEGEND, in its own slot so it renders BELOW the graph rather than above it
         with the notices. A colour with no key is a puzzle, so every treatment the
         graph can paint is named here — and it is rendered unconditionally, because
         the source colouring is on from first paint and would otherwise be a red ring
         with no explanation anywhere on screen. Marked as a `group` with a label
         rather than a bare div so it is announced as the key it is. */
      legend={
        <div className="dg-lineage-legend" role="group" aria-label="Lineage graph legend">
            <span className="dg-lineage-legend-item">
              <span
                className="dg-lineage-swatch dg-lineage-swatch--datasource"
                aria-hidden="true"
              />
              Data source for this trace
            </span>
            {/* THE REFINEMENT, named right after the set it refines so the pair reads
                as "origins, and the one being traced" rather than as two unrelated
                treatments. Explained unconditionally alongside the others: the whole
                point of a legend is that a treatment is never on screen unexplained,
                and gating this entry on a choice having been made would mean the one
                time it appears is the one time it has no key. */}
            <span className="dg-lineage-legend-item">
              <span
                className="dg-lineage-swatch dg-lineage-swatch--chosen-source"
                aria-hidden="true"
              />
              The source being traced (double ring)
            </span>
            <span className="dg-lineage-legend-item">
              <span className="dg-lineage-swatch dg-lineage-swatch--upstream" aria-hidden="true" />
              Upstream of selection (data came from)
            </span>
            <span className="dg-lineage-legend-item">
              <span
                className="dg-lineage-swatch dg-lineage-swatch--downstream"
                aria-hidden="true"
              />
              Downstream of selection (data went to)
            </span>
            <span className="dg-lineage-legend-item">
              <span className="dg-lineage-swatch dg-lineage-swatch--frontier" aria-hidden="true" />
              Not derived yet (pending frontier)
            </span>
        </div>
      }
      /* THE TWO CONTROLS, ABOVE THE GRAPH — the two halves of `fanin(entity, source)`.
         Everything a reader READS moved below the picture; these are what they ACT on,
         so they stayed. An instruction reading "select an entity to trace this source's
         data" is unfollowable if its control sits under ten alerts.

         ENTITY ABOVE SOURCE, matching the walk's own signature and the way the question
         reads aloud: "what happened to THIS entity's data" is the question, "traced from
         which origin" is the qualifier. See LineageEntityPicker's header.

         The entity picker is why this slot exists at all. Scoping the Entities table to
         Tree|Flat left the graph node as the only entity control on this view, and an SVG
         circle is not tabbable — so a keyboard or screen-reader reader could not ask this
         view's question. Both pickers write through the SAME selection paths the node
         click and the table row used (`onSelectEntity` → `?eid`, `onChange` → `?src`), so
         adding a control did not add a second notion of what is selected. */
      controls={
        <>
          <LineageEntityPicker
            entities={entities}
            selectedEntityId={selectedEntityId}
            // Routed to the same `selectEntity` a node click calls. Absent handler → the
            // control would visibly do nothing, so it is not offered (same honesty rule
            // as the source picker's `onChange` below).
            onChange={onSelectEntity ?? (() => {})}
          />
          <LineageSourcePicker
            chosen={sourceChoice}
            namesByKey={namesByKey}
            // Not defaulted to a no-op: an absent handler means the parent does not own
            // a `?src` mirror, in which case offering a control that visibly changes
            // nothing would be worse than not offering one. `LineageSourcePicker`
            // requires the callback, so this component supplies one that is honest
            // about doing nothing only when the parent genuinely passed none.
            onChange={onLineageSourceChange ?? (() => {})}
            // Just the dropdown up here. Its two alerts are informational text, so they
            // render with the rest of the prose below the graph — see
            // `LineageSourceNotices` in the notices block.
            showNotices={false}
          />
        </>
      }
      notices={
        <>
          {/* THE SOURCE PICKER'S OWN TWO STATES ("choose a source" / "that source is not
              one of this trace's"), rendered here rather than under the dropdown because
              they are prose and all prose is below the picture now. Same component owns
              the wording, so the control and its explanation cannot drift. */}
          <LineageSourceNotices chosen={sourceChoice} />

          {/* THE SOURCE ROLL-UP, stated in words beside the colouring. Rendered with
              no selection, because that is when the colouring is the only thing on
              screen and a reader needs to know what it is claiming — specifically
              that it is the union of DERIVED sources, not a list of entities someone
              declared to be sources. */}
          {summaryQ.isError ? (
            <Alert
              variant="warning"
              isInline
              role="alert"
              title="The trace’s data sources could not be loaded"
              style={{ marginBottom: '0.5rem' }}
            >
              The sources read failed, so which entities are data sources is{' '}
              <strong>unknown</strong> — not none. No node is marked as a source
              below because nothing was retrieved. Reload to retry.
            </Alert>
          ) : summaryQ.isLoading ? (
            <Alert
              variant="info"
              isInline
              title="Loading the trace’s data sources"
              style={{ marginBottom: '0.5rem' }}
            >
              Which entities are data sources is not yet known.
            </Alert>
          ) : sources.totalSources === 0 ? (
            /* ZERO SOURCES, and the ONE statement of it. Two consequences follow from
               this single fact — no node is marked, and there is nothing to trace — and
               they are deliberately worded together here rather than split across this
               alert and the picker's own: two alerts stating one fact in two wordings is
               precisely the drift that lets a reader think they are two different
               problems. `LineageSourcePicker` therefore renders NOTHING in this state
               (it takes the `'no-sources'` branch, which is a null render) and this
               alert speaks for both. */
            <Alert
              variant="info"
              isInline
              title="No data sources attributed in this trace yet"
              style={{ marginBottom: '0.5rem' }}
            >
              No derived lineage in this trace names an origin, so no node is marked
              as a data source and there is <strong>nothing to trace</strong> — the
              reachability walk follows one named source, and this trace has none.
              Under a partial or not-yet-derived trace this is <strong>not</strong> the
              same as "this trace has no sources" — check the coverage note above. It
              is also neither a failed read nor a pending one: the roll-up came back
              and named nothing.
            </Alert>
          ) : (
            <Alert
              variant="info"
              isInline
              title={`${sources.sourceNodeIds.length} of ${sources.totalSources} data source${sources.totalSources === 1 ? '' : 's'} marked on the graph`}
              style={{ marginBottom: '0.5rem' }}
            >
              These are the origins the derived lineage attributed this trace’s
              content to (the union of every derived leg’s data sources) — not a list
              of entities declared to be sources, which is a different set.
            </Alert>
          )}

          {/* SOURCES THE GRAPH CANNOT DRAW: a real origin with no node. Disclosed by
              count AND by key, the way lib/graph discloses a dropped interaction —
              "8 sources but 6 marked" is exactly the silent under-report a governance
              reader must never have to discover for themselves. A legitimate cause is
              an origin outside the trace's own entity set. */}
          {sources.unresolved.length > 0 && (
            <Alert
              variant="warning"
              isInline
              role="alert"
              title={`${sources.unresolved.length} data source${sources.unresolved.length === 1 ? '' : 's'} not shown as nodes`}
              style={{ marginBottom: '0.5rem' }}
            >
              {`The lineage names ${sources.unresolved.length === 1 ? 'this source' : 'these sources'} by natural key, but no node in this trace carries that key: ${sources.unresolved
                .map((u) => lineageLabel(u.ref, namesByKey).label)
                .join('; ')}. ${sources.unresolved.length === 1 ? 'It is' : 'They are'} still a real source — the marked nodes are therefore not the full set.`}
            </Alert>
          )}

          {/* AMBIGUOUS KEYS. Expected empty against a sane server (a natural key is
              an entity's identity, ADR-0013), which is exactly why it is disclosed
              rather than assumed: if it ever fires, a marked node is a deterministic
              but ARBITRARY pick among the claimants, and the reader has to know that
              before trusting which node is lit. See lineageLabels.entityIdsByKey for
              the first-wins rule. */}
          {sources.ambiguousKeys.length > 0 && (
            <Alert
              variant="warning"
              isInline
              role="alert"
              title={`${sources.ambiguousKeys.length} data source key${sources.ambiguousKeys.length === 1 ? '' : 's'} matched more than one entity`}
              style={{ marginBottom: '0.5rem' }}
            >
              {`A natural key should identify exactly one entity, but ${sources.ambiguousKeys
                .map((k) => lineageLabel(k, namesByKey).label)
                .join('; ')} matched several in this trace. The marked node is the first match in the entities read — deterministic, but an arbitrary choice among them.`}
            </Alert>
          )}

          {/* NOTHING SELECTED. An instruction, not a verdict — the graph below is
              drawn at full strength for everything except the source marks, and
              claims nothing about any one entity because no such question has been
              asked. Names BOTH controls: nodes are click targets now (see
              `DraggableKindColouredNode`), and the table remains the keyboard route.

              Gated on a source being CHOSEN as well, so the reader is asked for one
              missing half at a time: with no source there is nothing an entity
              selection could answer yet, and two simultaneous prompts read as a broken
              tab rather than as a two-step question. The picker's own `'unchosen'`
              alert is what is on screen instead. */}
          {sourceChoice.state === 'chosen' && reachability.selectedNodeId === null && (
            <Alert
              variant="info"
              isInline
              title="Select an entity to trace this source’s data in and out"
              style={{ marginBottom: '0.5rem' }}
            >
              {selectedEntityId === null
                ? 'Click a node on the graph, or a row in the Entities table above. This tab then highlights the entities the chosen source’s data reached that entity FROM (upstream) and went TO (downstream), and the interaction legs that carried it; everything else is dimmed.'
                : 'The selected entity is not in this trace’s entity set, so it has no node to highlight. Pick a node on the graph, or a row in the Entities table above.'}
            </Alert>
          )}

          {/* THE TRACE-LEVEL PREFIX (ADR-0028 D6). Reused verbatim rather than
              reworded: the truncation is a fact about the whole trace, so the tab
              must not invent a second phrasing of it. Only rendered for a selection —
              the banner is already on screen above the tabs at all times (see
              FlowTables), and this repeat exists so the caveat sits next to the answer
              it qualifies. */}
          {reachability.selectedNodeId !== null && !isLineageError && (
            <LineageCoverageAlert
              status={status}
              stoppedAtSeq={null}
              isError={false}
              isLoading={false}
            />
          )}

          {/* PER-DIRECTION STATE. One component, rendered twice, so fan-in and
              fan-out cannot end up worded differently for the same state — and so
              each keeps its own four-way outcome rather than being merged into a
              single verdict that would have to hide one of the two.

              `sourceLabel` is passed so every verdict names its SUBJECT. Since the
              walk is `fanin(entity, source)`, "nothing upstream" is only true OF ONE
              SOURCE — an unqualified "nothing upstream of this entity" would be a
              much stronger (and false) claim, and it is the claim a reader would
              naturally take away. `reachability.selectedNodeId !== null` already
              implies a chosen source (see `deriveReachabilityHighlight`), so this is
              never rendered without one. */}
          {reachability.selectedNodeId !== null && (
            <>
              <DirectionNotice highlight={reachability.fanin} sourceLabel={sourceLabel} />
              <DirectionNotice highlight={reachability.fanout} sourceLabel={sourceLabel} />
            </>
          )}
        </>
      }
    />
  );
}

/** How one direction is named on screen. One place, so the two never drift apart. */
const DIRECTION_WORDS = {
  fanin: {
    noun: 'Upstream',
    /** The claim in plain words, for the states that need a sentence. */
    came: 'came from',
    /** What an empty-but-derived answer means for THIS direction. */
    originates:
      'Lineage IS derived here and reaches no further upstream, so this entity’s data originates at it within this trace. That is a derived answer, not a missing one.',
    noAdjacent:
      'No interaction leg in this trace delivers data to this entity along a lineage-bearing path, so there is nothing upstream to show. This is a fact about the trace, not a derivation still pending.',
  },
  fanout: {
    noun: 'Downstream',
    came: 'went to',
    originates:
      'Lineage IS derived here and reaches no further downstream, so this entity’s data goes nowhere else within this trace. That is a derived answer, not a missing one.',
    noAdjacent:
      'No interaction leg in this trace carries this entity’s data onward along a lineage-bearing path, so there is nothing downstream to show. This is a fact about the trace, not a derivation still pending.',
  },
} as const;

/**
 * One direction's outcome, in words — the four states kept visibly apart.
 *
 * A COMPONENT RENDERED TWICE rather than two blocks of JSX, because the two
 * directions must not be able to word the same state differently: "not yet derived"
 * meaning one thing for fan-in and another for fan-out is precisely the drift a
 * governance UI cannot afford. The only per-direction text lives in
 * {@link DIRECTION_WORDS}.
 *
 * Note the ORDER of the arms. A failed read comes first because it invalidates
 * every other statement — with nothing retrieved, an empty highlight says nothing
 * about the data. `pending` then precedes the derived arms so "we have not got here
 * yet" can never be reached through a branch that would have called it an answer.
 *
 * EVERY VERDICT NAMES ITS SOURCE, because the walk is `fanin(entity, source)` and so
 * each of the four states is a claim about ONE source's data, not about the entity in
 * general. "Nothing upstream of this entity" is a far stronger statement than the read
 * supports and is exactly what an unqualified sentence would be taken to mean — the
 * entity may well have plenty upstream, for a different source. The qualification is
 * therefore appended to the state's own sentence rather than replacing it, so the
 * distinction between the four states is untouched and only their scope is corrected.
 */
function DirectionNotice({
  highlight,
  sourceLabel,
}: {
  highlight: DirectionHighlight;
  /**
   * The chosen source's friendly label. `''` only where a source is impossible, in
   * which case the qualifying clause is omitted rather than rendering "for source ''".
   */
  sourceLabel: string;
}) {
  const words = DIRECTION_WORDS[highlight.direction];
  const style = { marginBottom: '0.5rem' };
  // One clause, built once, so the six arms below cannot word the scope six ways.
  const forSource = sourceLabel ? ` for the data source ${sourceLabel}` : '';

  // STATE 4 of 4: the read itself failed. Not one of the server's three, because a
  // request that never returned said nothing at all. Worded as *unknown*, matching
  // LineageCoverageAlert — never as "none".
  if (highlight.isError) {
    return (
      <Alert
        variant="warning"
        isInline
        role="alert"
        title={`${words.noun} lineage could not be loaded`}
        style={style}
      >
        The {highlight.direction} read failed, so where this entity’s data{' '}
        {words.came}
        {forSource} is <strong>unknown</strong> — not absent. Nothing is highlighted
        for this direction because nothing was retrieved. Reload to retry.
      </Alert>
    );
  }

  if (highlight.isLoading) {
    return (
      <Alert variant="info" isInline title={`Loading ${highlight.direction} lineage`} style={style}>
        Where this entity’s data {words.came}
        {forSource} is not yet known.
      </Alert>
    );
  }

  // STATE 2 of 4: `pending`. The eventual-consistency window — adjacency EXISTS but
  // carries no derived lineage row yet. Stated as its own claim-less state, and
  // deliberately NOT as an empty answer: a governance tool must never let "we don't
  // know yet" look like "we checked and there is nothing".
  if (highlight.state === 'pending') {
    const n = highlight.pendingFrontierNodeIds.length + highlight.unresolvedFrontier.length;
    return (
      <Alert
        variant="info"
        isInline
        title={`${words.noun} lineage not yet computed for this entity`}
        style={style}
      >
        {`This entity has adjacent interaction legs in this trace, but none has lineage derived yet, so where its data ${words.came}${forSource} is not yet known — this is not "nothing flowed". P-data-lineage derives them as payloads arrive.`}
        {n > 0 &&
          ` ${n} entit${n === 1 ? 'y is' : 'ies are'} on the pending frontier: the walk reached ${n === 1 ? 'it' : 'them'} but cannot continue through ${n === 1 ? 'it' : 'them'} yet.`}
      </Alert>
    );
  }

  // STATE 3 of 4: `no-adjacent` — the ONE state where an empty answer is COMPLETE.
  // There is nothing to wait for, so telling the reader to wait would be telling
  // them to wait forever.
  if (highlight.state === 'no-adjacent') {
    return (
      <Alert
        variant="info"
        isInline
        title={`Nothing ${words.noun.toLowerCase()} of this entity in this trace`}
        style={style}
      >
        {/* The state's own sentence UNCHANGED, plus the scope. Two sentences rather
            than a reworded one so this arm still reads as the same distinct state it
            was — and because `noAdjacent` is shared with the other direction and must
            not grow per-source grammar. */}
        {words.noAdjacent}
        {sourceLabel
          ? ` This is scoped to the data source ${sourceLabel}: a different source may well reach this entity, and this answer says nothing about that.`
          : ''}
      </Alert>
    );
  }

  // STATE 1 of 4: `derived`. An answer — including when it is EMPTY, which is the one
  // state a graph cannot show by itself (an unhighlighted picture looks identical to
  // the pending case), so the difference has to be words.
  const count = highlight.nodeIds.length;
  return (
    <>
      {count === 0 ? (
        <Alert
          variant="info"
          isInline
          title={`No ${words.noun.toLowerCase()} entities — derived, not missing`}
          style={style}
        >
          {words.originates}
          {/* Same two-sentence treatment as `no-adjacent` above, and needed MORE here:
              "this entity's data originates at it" is the strongest claim on this tab,
              and it is only true of the traced source. */}
          {sourceLabel
            ? ` Scoped to the data source ${sourceLabel} — a different source may reach further, and this answer does not speak for it.`
            : ''}
        </Alert>
      ) : (
        <Alert
          variant="info"
          isInline
          title={`${count} ${words.noun.toLowerCase()} entit${count === 1 ? 'y' : 'ies'}, over ${highlight.edgeIds.length} interaction leg${highlight.edgeIds.length === 1 ? '' : 's'}`}
          style={style}
        >
          {`Where this entity’s data ${words.came}${forSource}, as derived lineage — the highlighted legs are the route the walk followed. A hop exists only where the trace has a leg, that leg's lineage was derived, AND that lineage names this source, so this ends where THIS source's provenance ends rather than where the call graph does. It inherits matcher quality: under a trivial matcher nothing prunes a hop, so a large answer is not evidence of thorough tracing.`}
        </Alert>
      )}

      {/* PENDING FRONTIER ON A DERIVED ANSWER. Both can be true at once: the walk
          followed real hops AND ran into legs it cannot pass yet, so the answer is
          expected to GROW. Reported separately from the answer above rather than
          folded into its count, which would overstate what is known. */}
      {highlight.pendingFrontierNodeIds.length + highlight.unresolvedFrontier.length > 0 && (
        <Alert
          variant="info"
          isInline
          title={`${words.noun} answer may grow — ${highlight.pendingFrontierNodeIds.length + highlight.unresolvedFrontier.length} on the pending frontier`}
          style={style}
        >
          The walk reached these entities but cannot continue through them yet,
          because the onward leg has no derived lineage row. This is{' '}
          <strong>not yet known</strong>, not a dead end — ask again once
          P-data-lineage has caught up.
        </Alert>
      )}

      {/* TRUNCATION IS A DIFFERENT CLAIM FROM THE FRONTIER (ADR-0028 D15) and must
          not be merged with it: waiting will never deliver what a walk bound
          declined to return, only a wider bound will. Kept as its own notice so the
          reader is not told to poll for something that will not arrive. */}
      {highlight.truncated && (
        <Alert
          variant="warning"
          isInline
          role="alert"
          title={`${words.noun} answer is truncated`}
          style={style}
        >
          The walk hit a size bound, so what is highlighted is a <strong>prefix</strong>{' '}
          of the real reachable set. Unlike the pending frontier, waiting will not
          complete this — it is derived data the answer declined to return in full.
        </Alert>
      )}

      {/* FRONTIER ENTITIES WITH NO NODE. Same silent-under-report reasoning as the
          unresolvable sources above: the walk named an entity this graph cannot draw,
          so the marked frontier is not the whole frontier. */}
      {highlight.unresolvedFrontier.length > 0 && (
        <Alert
          variant="info"
          isInline
          title={`${highlight.unresolvedFrontier.length} ${words.noun.toLowerCase()} frontier entit${highlight.unresolvedFrontier.length === 1 ? 'y is' : 'ies are'} not shown as nodes`}
          style={style}
        >
          The walk named{' '}
          {highlight.unresolvedFrontier.length === 1 ? 'an entity' : 'entities'} this
          trace’s graph has no node for, so{' '}
          {highlight.unresolvedFrontier.length === 1 ? 'it is' : 'they are'} counted
          but not marked.
        </Alert>
      )}
    </>
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
