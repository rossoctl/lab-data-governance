import { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  Button,
  Title,
  Spinner,
  EmptyState,
  EmptyStateBody,
  EmptyStateHeader,
  Tabs,
  Tab,
  TabTitleText,
  ToggleGroup,
  ToggleGroupItem,
} from '@patternfly/react-core';

import { useDataLineage } from '../api/hooks';
import { useEntitiesAndInteractions } from '../api/live';
import { fetchJson } from '../api/client';
import { useRuleCatalogIndex, useTraceAlerts, useTraceRiskDetail } from '../risk-api/hooks';
import { classificationByInteraction, riskLevelByInteraction, violationsOf } from '../lib/riskForestAdapter';
import { parseViolationIndex, stepViolation } from '../lib/riskViolation';
import { InteractionPanel } from '../risk-components/InteractionPanel';
import { PolicyDecisionPanel } from '../risk-components/PolicyDecisionPanel';
import { ViolationStepper } from '../risk-components/ViolationStepper';
import { TraceAlertsSection } from '../risk-components/TraceAlertsSection';
import type { ForestInteraction, RuleListItem } from '../risk-api/types';
import {
  computeInteractionDepths,
  flatConnectorRoles,
  flatLegRows,
  httpSummary,
  isInfrastructure,
  legOfType,
  parseLegViewKey,
  type LegViewKey,
} from '../lib/flow';
import type { PinStore } from '../lib/pins';
import { InteractionDiagram } from './InteractionDiagram';
import { EntitiesTable } from './flow/EntitiesTable';
import { FlatLegsTable } from './flow/FlatLegsTable';
import { FlowDetailPanel } from './flow/FlowDetailPanel';
import { HopHoverContext } from './flow/hopHover';
import { LabelTrackContext, type LabelTrack } from './flow/labelTrack';
import { TrackBanner } from './flow/TrackBanner';
import { TrackPicker, type PickerTarget } from './flow/TrackPicker';
import {
  defaultTrackValues,
  deriveLabelTrackHighlight,
  toGraphHighlight,
  type TrackedValue,
} from '../lib/labelTrack';
import type { LegLabels } from '../types';
import { InteractionsTable } from './flow/InteractionsTable';
import { LineageCoverageAlert } from './flow/LineageCoverageAlert';
import type { Selection } from './flow/selection';
import type { Entity, Interaction, SpanEvidence } from '../types';

/**
 * The Execution Flow graph, behind a dynamic import.
 *
 * `@patternfly/react-topology` (plus the d3 and mobx it drags in) builds to ~286kB
 * of JS and ~39kB of CSS — 89kB / 4kB gzipped — serving these TWO tabs, so a static
 * import made every reader of the trace list and the span tree pay for a view most
 * never open. (Figures measured from `vite build`; an earlier version of this
 * comment said ~388kB / ~130kB and named dagre, which is not a dependency here at
 * all — this branch does its own layout. See ADR-0029.)
 * `React.lazy` puts it in its own async chunk that is fetched the first time the
 * Execution Flow or Lineage view is active (the `/graph` or `/lineage` path segment
 * now, `?legs=graph`/`?legs=lineage` before those views were promoted) — see the
 * Suspense boundary at the render site, and ExecutionFlowGraph.tsx's note on why its
 * stylesheets moved in there too.
 *
 * `ExecutionFlowGraph` has a default export purely so this needs no
 * `.then(m => ({ default: m.X }))` unwrap.
 */
const ExecutionFlowGraph = lazy(() => import('./ExecutionFlowGraph'));

/**
 * The Lineage graph, from the SAME lazy module — which is the point.
 *
 * Two `lazy()` calls over one `import()` specifier: Vite/Rollup emits one chunk per
 * module, so both tabs share the single PF-topology chunk rather than each carrying
 * a copy of it (or relying on the bundler to hoist a shared dependency out of two
 * sibling chunks, which is a guarantee nothing here would notice the loss of). It is
 * also why `LineageGraph` lives in `ExecutionFlowGraph.tsx` beside the component it
 * reuses instead of in a file of its own.
 *
 * The `.then` unwrap is needed because only one export can be the default, and the
 * Execution Flow tab has been it since before this tab existed.
 */
const LineageGraph = lazy(() =>
  import('./ExecutionFlowGraph').then((m) => ({ default: m.LineageGraph })),
);

/** Which flow row is selected, mirrored to/from the URL (?iid | ?eid). */
export interface FlowSelection {
  iid?: string;
  eid?: string;
  /**
   * With the risk layer on: the open policy event's 1-based position, written as
   * `?violation=` in the same URL write as the selection (deleted at 1, the
   * default — `lib/riskViolation.ts`). Absent, the param is left as it is. One
   * write, because two consecutive `setSearchParams` calls would each read the
   * same stale params. See `FlowTablesProps.risk` for the contract.
   */
  violation?: number;
}

/** The forest with the layer off: one array, so the memos below keep their identity. */
const NO_FOREST: readonly ForestInteraction[] = [];

/**
 * A forest stand-in for an interaction the risk read has no record of yet (or
 * for a trace with no risk record at all): `InteractionPanel` then says "not
 * classified" and "not yet computed", the same words the risk page uses for an
 * interaction whose `risk` is null, rather than the layer saying nothing.
 */
function forestStandIn(ix: Interaction, traceId: string): ForestInteraction {
  return {
    interaction_id: ix.id,
    trace_id: traceId,
    parent_interaction_id: ix.parent_interaction_id,
    caller_entity_id: ix.caller_entity_id,
    callee_entity_id: ix.callee_entity_id,
    summary: ix.summary,
    legs: ix.legs.map((l) => ({
      leg_type: l.leg_type,
      occurred_at: l.occurred_at,
      payload_hash: l.payload_hash,
      error: l.error,
    })),
    risk: null,
    span_count: ix.span_count,
    anchor_count: ix.anchor_count,
  };
}

/** Re-exported for the callers that already reach for it through this module (the
 *  tab set it drives lives here); defined in ../lib/flow next to its coercion. */
export type { LegViewKey };

export interface FlowTablesProps {
  traceId: string;
  pins: PinStore;
  onPinsChange: () => void;
  /** Navigate to a span in the tree view (row's span-link). */
  onNavigateToSpan?: (spanId: string) => void;
  /** Reveal a set of spans in the tree view (fired on Add-to-highlights). */
  onRevealSpans?: (spanIds: string[]) => void;
  /**
   * Selection to restore from the URL on load. Once the tables have data, the
   * matching row auto-selects (deep-link / reload restore). A stale id that
   * matches no loaded row is ignored (no selection, no crash).
   */
  initialSelection?: FlowSelection;
  /**
   * Fired when the selected row changes so the parent can mirror it into the
   * URL. `null` on deselect.
   */
  onSelectionChange?: (sel: FlowSelection | null) => void;
  /**
   * Which of the five presentations to render.
   *
   * THE VALUE NOW COMES FROM TWO DIFFERENT PLACES IN THE URL, and this component is
   * deliberately ignorant of which: `tree`/`flat` come from `?legs` (the sub-tab bar
   * below), while `diagram`/`graph`/`lineage` come from the PATH SEGMENT, because those
   * three were promoted to top-level views beside Span tree. `TraceDetailPage` resolves
   * the two into one value (its `effectiveLegView`) and hands it here, so this component
   * renders what it is told and there is still exactly one notion of "the presentation".
   *
   * The parent owns every URL param in this view (same as `initialSelection`), so this
   * is a controlled prop rather than internal state: this component never reaches for
   * `useSearchParams` itself, and reload / bookmark / back restore the view. Defaults to
   * `tree` when omitted.
   */
  legView?: LegViewKey;
  /**
   * Fired when the Tree|Flat sub-tab changes so the parent can mirror `?legs`.
   *
   * Can only ever emit `tree` or `flat` in practice — the bar offers no others and
   * `parseLegViewKey` accepts no others — even though the signature admits all five
   * `LegViewKey`s. Kept at the wider type so the prop matches `legView`'s; the three
   * promoted views are navigated to by path, not announced through this callback.
   */
  onLegViewChange?: (key: LegViewKey) => void;
  /**
   * Whether to render the Tree|Flat sub-tab bar.
   *
   * `true` only on the `flow` view, where those two ARE the choice. The three promoted
   * views (Interaction diagram / Execution Flow / Lineage) are selected by the
   * top-level tabs in `TraceDetailPage`, so a bar inside one of them would be a second
   * control for a decision already made — and one that could disagree with the path.
   *
   * Defaults to `true` so an existing caller that renders the tables keeps its bar.
   */
  showLegTabs?: boolean;
  /**
   * Whether infrastructure interactions (MCP lifecycle / tool discovery) are shown.
   *
   * Hidden is the DEFAULT, and the non-default state is what the URL encodes
   * (`?showInfra=1`, ADR-0021: defaults are omitted, not written) — same
   * convention as `?hideOrphans` on the trace list. A controlled prop for the
   * same reason `legView` and `lineageSource` are: `TraceDetailPage` owns every
   * URL param in this view, so this component never reaches for
   * `useSearchParams` and reload / bookmark / back restore the choice.
   */
  showInfra?: boolean;
  /** Fired when the infrastructure affordance is toggled, so the parent can mirror `?showInfra`. */
  onShowInfraChange?: (show: boolean) => void;
  /**
   * Which single **data source** the Lineage tab is tracing (`?src`), as an **Entity
   * natural key**, or `null` for "none chosen yet".
   *
   * A CONTROLLED PROP for exactly the same reason `legView` and `initialSelection`
   * are: the parent owns every URL param in this view (`TraceDetailPage`), so this
   * component never reaches for `useSearchParams` and a reload / bookmark / back
   * button restores the choice through the one path the other params already use.
   * Adding a second mechanism here is what would let the tab's state and the URL
   * disagree.
   *
   * Passed straight through to `LineageGraph` — this component neither validates it
   * (only the trace's own roll-up can, and `LineageGraph` holds that read) nor uses
   * it for anything else. It is `?src`'s courier, no more.
   */
  lineageSource?: string | null;
  /** Fired when the traced source changes so the parent can mirror `?src`. */
  onLineageSourceChange?: (source: string) => void;
  /**
   * THE RISK LAYER: risk is a layer on the trace page, on every
   * view at once, not a page of its own. On, this component makes the trace's
   * risk read (`useTraceRiskDetail`, the forest `/risk/traces/{id}` serves) and
   * the rule catalog read, and:
   *
   * - colours every view's edges by risk level (`riskLevelByInteraction`), the
   *   execution flow also showing the classification tags
   *   (`classificationByInteraction`), as the risk trace page does;
   * - adds the selected interaction's risk to the detail panel (the same
   *   `InteractionPanel` the risk page renders) and, when it triggered rules, the
   *   stepper and the decision card (`ViolationStepper`, `PolicyDecisionPanel`);
   * - makes the right-click's tracking default the decision-derived one (the
   *   carried-in values whose labels the fired rules name — `defaultTrackValues`).
   *
   * Off (the default), no risk read is made and nothing here changes. A controlled
   * prop, like every other URL-backed choice here: the parent mirrors it as
   * `?risk=1` (absent = off).
   *
   * THE `?violation=` / SELECTION CONTRACT, the risk page's reproduced with `?iid=`
   * as the selection. The open policy event is a 1-based position in
   * `violationsOf(forest)` (`violation`, the raw `?violation=`; 1 when absent). A
   * selected interaction that IS a policy event wins over the param (a cold `?iid=`
   * naming an event moves the stepper to it). With no `?iid=`/`?eid=`, the open
   * event's interaction is the DEFAULT selection: it opens in the detail panel
   * without being written to the URL, so a `?violation=N` naming an event that has
   * not arrived yet shows event 1 for now and lands on N when N arrives. A click on
   * a policy event's edge writes `?violation=` and drops `?iid=` (its interaction is
   * the default); a click on any other edge writes `?iid=` and pins the open event
   * into `?violation=`; Previous/Next write `?violation=` and drop `?iid=`. A live
   * re-read can shift positions (an earlier interaction gains its first rule): the
   * event shown is held by id and `?violation=` is rewritten to its new position
   * (`onViolationChange`, replace, before paint). Closing the panel dismisses the
   * default for that event; stepping to another reopens it.
   */
  risk?: boolean;
  /** The raw `?violation=` — the open policy event's 1-based position, default 1 when absent. The parent owns the param. */
  violation?: string | null;
  /** Fired when the open policy event's position must be rewritten in place (a live re-read moved it); the parent writes `?violation=` with `replace`. */
  onViolationChange?: (next: number) => void;
  /**
   * The two graph views' edges: one per LEG (the default: request and response
   * arrows, which label lineage needs — a value born on a response leg is lit
   * there) or one per INTERACTION (its request leg, as the risk trace page draws).
   * A controlled prop mirrored as `?edges=interaction` by the
   * parent (absent = per leg). With edges per interaction a lit response leg
   * lights its interaction's one arrow, so the overlay still shows the route.
   */
  edgesPerInteraction?: boolean;
  /** Fired when the edge mode is switched, so the parent can mirror `?edges`. */
  onEdgesPerInteractionChange?: (perInteraction: boolean) => void;
}

/**
 * The interaction-flow view: a presentation of the trace's Interactions — and, on the
 * Tree|Flat presentations only, an Entities table above it. Both are derived from spans
 * by the in-cluster processor (ADR-0013). Ports the vanilla execution_flow_logic.js —
 * depth indentation via the parent walk, pin dots mirroring the tree's highlight store,
 * and lazy span-evidence fetch + a detail panel on row click.
 *
 * The Entities table used to render on all five presentations; it is now scoped to the
 * two table ones, because on the three PICTURE presentations the entities are the thing
 * being drawn and the table restated them. See the gate at its render site for what that
 * costs on the Lineage view.
 *
 * The Interactions section has five peer presentations (`LegViewKey`): `tree`, `flat`,
 * `diagram` (the Interaction diagram — a sequence diagram of the flat leg list), `graph`
 * (Execution Flow) and `lineage` (that same graph, highlighting how ONE chosen **data
 * source**'s data reached the selected entity and where it went). All five read the same
 * two queries, so switching between them costs no fetch.
 *
 * ONLY `tree` AND `flat` ARE CHOSEN HERE, by the sub-tab bar below. The other three are
 * top-level views selected by path segment in `TraceDetailPage`, which renders this same
 * component with `legView` set from the path and `showLegTabs={false}`. So this one
 * component still owns all five renderings — and therefore one selection, one evidence
 * fetch, one detail panel — while the navigation to three of them lives a level up.
 *
 * `lineage` DOES cost reads, and the earlier claim here that it "costs no fetch
 * either" is no longer true: it owns the trace's `data-lineage-summary` (which is both
 * the source colouring and the choosable source list) plus the two directional
 * `data-lineage-graph` reads, gated on having BOTH an entity selection and a chosen
 * source. Only its trace-level `status` still comes from this component's
 * `useDataLineage`, so the coverage banner and the tab quote one value.
 *
 * This module owns only the composition and the selection/URL state; the tables,
 * the floating detail panel and the coverage banner live in ./flow, the sequence
 * diagram in ./InteractionDiagram, and BOTH graph tabs in ../ExecutionFlowGraph
 * (lazy, one shared chunk — see the two imports above).
 */
export function FlowTables({
  traceId,
  pins,
  onPinsChange,
  onNavigateToSpan,
  onRevealSpans,
  initialSelection,
  onSelectionChange,
  legView = 'tree',
  onLegViewChange,
  showLegTabs = true,
  showInfra = false,
  onShowInfraChange,
  lineageSource = null,
  onLineageSourceChange,
  risk = false,
  violation = null,
  onViolationChange,
  edgesPerInteraction = false,
  onEdgesPerInteractionChange,
}: FlowTablesProps) {
  // One reading of the two, so a live re-read never shows (or hands the Lineage
  // tab) a half-updated trace — see `useEntitiesAndInteractions`.
  const { entities, interactions, entitiesQ, interactionsQ } = useEntitiesAndInteractions(traceId);
  const [selection, setSelection] = useState<Selection | null>(null);
  // THE RISK LAYER'S READS, made only while the layer is on (a disabled query
  // makes no request, and `risk` gates the data too, so a layer switched off
  // does not keep painting from the cache). The forest is this component's whole
  // view of risk: edge colours, the policy events, the selected interaction's
  // record. The alerts are read here too, for the section above the views (the
  // span tree, which does not render this component, has the page's own).
  const riskDetail = useTraceRiskDetail(risk ? traceId : undefined);
  const alerts = useTraceAlerts(risk ? traceId : undefined);
  const catalog = useRuleCatalogIndex(risk);
  const forest = useMemo(
    () => (risk ? (riskDetail.data?.interactions ?? NO_FOREST) : NO_FOREST),
    [risk, riskDetail.data],
  );
  const riskColours = useMemo(() => (risk ? riskLevelByInteraction(forest) : undefined), [risk, forest]);
  const classifications = useMemo(() => (risk ? classificationByInteraction(forest) : undefined), [risk, forest]);
  const violations = useMemo(() => violationsOf(forest), [forest]);
  // Interaction id -> 1-based position, the numbering `?violation=` uses.
  const violationIndexByInteractionId = useMemo(() => {
    const map = new Map<string, number>();
    violations.forEach((v, i) => map.set(v.interaction_id, i + 1));
    return map;
  }, [violations]);
  // The interaction a lineage hop in the detail panel is pointing at; the
  // sequence diagram lights its legs (see `flow/hopHover.ts`).
  const [hoveredInteractionId, setHoveredInteractionId] = useState<string | null>(null);
  // The values tracked on the graph (label lineage, `lib/labelTrack`): ticked in
  // the detail panel's Label lineage tab or by a right-click on a leg. One set
  // for all four views, so the picture does not change with the presentation.
  const [tracked, setTracked] = useState<ReadonlyMap<string, TrackedValue>>(() => new Map());
  // The track picker (the check menu), open for one interaction at a point.
  const [picker, setPicker] = useState<PickerTarget | null>(null);
  const labelTrack = useMemo<LabelTrack>(
    () => ({
      available: true,
      tracked,
      set: (key, value) =>
        setTracked((prev) => {
          const next = new Map(prev);
          if (value == null) next.delete(key);
          else next.set(key, value);
          return next;
        }),
      replace: (values) => setTracked(new Map(values.map((v) => [v.key, v]))),
    }),
    [tracked],
  );
  const trackHighlight = useMemo(
    () => (tracked.size === 0 ? null : deriveLabelTrackHighlight([...tracked.values()], interactions, traceId)),
    [tracked, interactions, traceId],
  );
  // The graphs' version of the overlay: with edges per interaction, a lit
  // response leg lights the interaction's one arrow (its request leg's id).
  const graphHighlight = useMemo(() => {
    if (!trackHighlight) return undefined;
    const h = toGraphHighlight(trackHighlight);
    if (!edgesPerInteraction) return h;
    return { ...h, edgeIds: [...new Set(h.edgeIds.map((id) => id.replace(/:response$/, ':request')))] };
  }, [trackHighlight, edgesPerInteraction]);
  // Tracking is per trace: a new trace starts with nothing tracked.
  useEffect(() => {
    setTracked(new Map());
    setPicker(null);
  }, [traceId]);
  const closePicker = useCallback(() => setPicker(null), []);
  /**
   * The right-click on a leg: select its interaction (the detail panel opens),
   * track the default values of THAT leg (added to what is tracked), and open
   * the picker there so the reader can untick or add. The default is every
   * carried-in value of the leg — or, with the risk layer on and the interaction
   * having fired rules, the values whose labels those rules name
   * (`defaultTrackValues`, the risk page's default). Reads the same labels
   * resource the Label lineage tab reads.
   */
  function trackDefaultOf(interactionId: string, legType: 'request' | 'response', at: { x: number; y: number }) {
    selectInteractionById(interactionId);
    setPicker({ interactionId, legType, at });
    const rules: RuleListItem[] = risk
      ? (forest.find((i) => i.interaction_id === interactionId)?.risk?.triggered_rule_ids ?? [])
          .map((id) => catalog.data?.get(id))
          .filter((r): r is RuleListItem => r != null)
      : [];
    void fetchJson<{ legs?: LegLabels[] }>(`/traces/${traceId}/interactions/${interactionId}/labels`)
      .then((r) => {
        const values = defaultTrackValues(interactionId, r.legs ?? [], legType, rules);
        if (values.length === 0) return;
        setTracked((prev) => {
          const next = new Map(prev);
          for (const v of values) next.set(v.key, v);
          return next;
        });
      })
      .catch(() => {});
  }
  // Persisted **Data lineage** for the whole trace (ADR-0028), read ONCE per
  // trace and keyed per leg — not per payload expansion. TanStack caches on
  // `traceId`, so the panel switching between rows/legs never re-fetches.
  //
  // NOT gated on a selection (as #119 had it): since #120 this read also carries
  // the trace's complete/partial coverage, and that warning belongs on the first
  // paint. A truncation a reader must select a row to discover cannot stop them
  // reading the visible rows as the whole picture — which is the entire point of
  // the flag (ADR-0028 D6).
  const lineageQ = useDataLineage(traceId);
  // Monotonic click token: each row click bumps it, and a click's async
  // evidence fetch only commits its setState if it is still the latest click.
  // Guards the out-of-order race where a slow fetch resolves after a later
  // click and would otherwise overwrite the selection/highlight.
  const clickSeq = useRef(0);
  // THE RISK LAYER'S DEFAULT SELECTION, what it needs to know about itself (see
  // `FlowTablesProps.risk` and the effects below): the interaction it selected
  // silently (`defaultSelectedId`, null once the reader picks anything), whether
  // the reader dismissed the panel (`dismissed`: closed it or clicked the graph's
  // background while the layer is on; reset by any toggle of the layer, cleared
  // by a step or a pick, never by the silent path), a position the
  // hold has just asked the parent to write (`pendingViolation`), and the seq of
  // a reader's click still fetching its evidence (`userClickInFlight`), which the
  // default must not race.
  const defaultSelectedId = useRef<string | null>(null);
  const dismissed = useRef(false);
  const pendingViolation = useRef<number | null>(null);
  const userClickInFlight = useRef(0);

  // The selected id per table kind. Only the latest-selected row (entity or
  // interaction) is highlighted; `selection` already tracks that single row
  // across both kinds, so the other table's id is null.
  const selectedEntityId = selection?.kind === 'entity' ? selection.id : null;
  const selectedInteractionId = selection?.kind === 'interaction' ? selection.id : null;

  // The open policy event (see `FlowTablesProps.risk`): the selected interaction's
  // position when it is one, else the param's. `fellBack` marks a `?violation=N`
  // whose event has not arrived (shown as 1 for now) — not the reader's pick, so
  // it is not held across a re-read. As on the risk page, only a selection that
  // IS a policy event exempts the param, and the default selection (the event
  // the param itself seeded, `defaultSelectedId`) is not a pick: a plain `?iid=`
  // or an `?eid=` leaves an out-of-range param a fallback, so it lands on N.
  const selectedViolationIndex =
    selectedInteractionId != null ? violationIndexByInteractionId.get(selectedInteractionId) : undefined;
  const paramViolationIndex = parseViolationIndex(violation, violations.length);
  const violationIndex = selectedViolationIndex ?? paramViolationIndex;
  const openEvent = violationIndex != null ? violations[violationIndex - 1] : null;
  const urlHasSelection = Boolean(initialSelection?.iid || initialSelection?.eid);
  const pickedEvent = selectedViolationIndex !== undefined && selectedInteractionId !== defaultSelectedId.current;
  const fellBack = !pickedEvent && violation != null && String(paramViolationIndex) !== violation;

  const entById = useMemo(() => {
    const m = new Map<string, Entity>();
    entities.forEach((e) => m.set(e.id, e));
    return m;
  }, [entities]);
  // INFRASTRUCTURE FILTER. MCP plumbing — lifecycle handshakes and tool
  // discovery, per the server's sanctioned `kinds` — is hidden by default: on a
  // real trace it is most of the rows and none of the signal (19 interactions
  // where a reader wants a handful). `showInfra` (URL `?showInfra=1`) reveals it.
  //
  // Filtering runs on INTERACTIONS, before the per-leg flatMap below, so the
  // Flat view drops both legs of a hidden interaction together and its surviving
  // connector brackets stay intact. A hidden row whose descendant is visible is
  // KEPT — otherwise the tree would render a child with no parent. Lifecycle
  // hops are leaves, so that should not arise; it is guarded rather than assumed.
  // `infraTotal` is how many rows the filter WOULD hide, computed whether or not
  // it is currently hiding them — the affordance names that count in both states
  // ("2 hidden — show" / "Hide 2"), so it cannot be derived from the filtered list.
  const { displayedInteractions, infraTotal } = useMemo(() => {
    const byId = new Map(interactions.map((ix) => [ix.id, ix]));
    // Infrastructure rows that a visible row parents through, and so must stay.
    const keepAsAncestor = new Set<string>();
    for (const ix of interactions) {
      if (isInfrastructure(ix)) continue;
      // `seen` makes the ancestry walk cycle-safe.
      const seen = new Set<string>();
      let pid = ix.parent_interaction_id;
      while (pid && !seen.has(pid)) {
        seen.add(pid);
        const parent = byId.get(pid);
        if (!parent) break;
        if (isInfrastructure(parent)) keepAsAncestor.add(parent.id);
        pid = parent.parent_interaction_id;
      }
    }

    const hide = (ix: Interaction) => isInfrastructure(ix) && !keepAsAncestor.has(ix.id);
    return {
      displayedInteractions: showInfra ? interactions : interactions.filter((ix) => !hide(ix)),
      infraTotal: interactions.filter(hide).length,
    };
  }, [interactions, showInfra]);

  // Depths come from the FULL list, not the displayed one: a visible row's
  // indentation must not shift because a sibling was hidden. `computeInteractionDepths`
  // over the filtered list would re-root orphaned subtrees at depth 0.
  const depthById = useMemo(() => computeInteractionDepths(interactions), [interactions]);
  const flatRows = useMemo(() => flatLegRows(displayedInteractions), [displayedInteractions]);
  const flatConnectors = useMemo(() => flatConnectorRoles(flatRows), [flatRows]);
  // Read the pin colors directly on render (NOT via useMemo keyed on `pins`):
  // `pins` is a stable mutable store reference, so a memo keyed on it would
  // never recompute after a pin toggle. The parent re-renders FlowTables on
  // every pin change (onPinsChange → bumpPins), so a plain read is fresh.
  const pinColor = new Map<string, string>();
  pins.getPins().forEach((p) => pinColor.set(p.key, p.color));

  const isLoading = interactionsQ.isLoading || entitiesQ.isLoading;
  const isEmpty = interactions.length === 0 && entities.length === 0;

  /**
   * `silent` (the risk layer's default selection and its steps): select without
   * writing the URL — the selection is the open policy event's, which `?violation=`
   * already names, so the panel opens on it the way the risk page's does, with
   * no `?iid=` to hold it against the param.
   */
  /**
   * `silent`: no URL write (the URL restore and the default). `fromDefault`: the
   * default's own select, the only one a dismissal made meanwhile may drop:
   * a `?iid=` restore is a selection the URL names and always lands.
   */
  async function selectInteraction(ix: Interaction, { silent = false, fromDefault = false } = {}) {
    const seq = ++clickSeq.current;
    if (!silent) userClickInFlight.current = seq;
    const evidence = await fetchJson<{ spans: SpanEvidence[] }>(
      `/traces/${traceId}/interactions/${ix.id}/spans`,
    )
      .then((r) => r.spans)
      .catch(() => []);
    if (userClickInFlight.current === seq) userClickInFlight.current = 0;
    if (seq !== clickSeq.current) return; // a newer click superseded this one
    if (fromDefault && dismissed.current) return; // dismissed while the default was still fetching
    // Timing/payload now live on the request/response legs (ADR-0025). The
    // request leg brackets the call start, the response leg its end; duration
    // is the API's computed value (null = response in flight).
    const reqLeg = legOfType(ix, 'request');
    const respLeg = legOfType(ix, 'response');
    setSelection({
      kind: 'interaction',
      id: ix.id,
      sectionTitle: 'Interaction',
      fields: [
        ['summary', ix.summary ?? '—'],
        ['interaction_id', ix.id],
        // The trace is the page's, not the row's — read it off the prop rather
        // than off `ix`, so no other constructor of an Interaction has to carry
        // a field only this panel prints.
        ['trace_id', traceId],
        ['anchor span(s)', evidence.filter((e) => e.role === 'anchor').map((e) => e.span_id).join(', ') || '—'],
        ['evidence spans', String(evidence.length)],
        ['request_at', reqLeg?.occurred_at ?? '—'],
        ['response_at', respLeg?.occurred_at ?? '—'],
        ...(ix.duration_seconds != null
          ? ([['duration', `${(ix.duration_seconds * 1000).toFixed(0)} ms`]] as Array<[string, string]>)
          : []),
        // The sidecar read-time derivations (issue #155, ADR-0030). Each row is
        // omitted rather than printed empty when its fact is absent — an
        // interaction with no sidecar facts shows the rows above and no more.
        ...(ix.kinds
          ? ([
              [
                'kind',
                [ix.kinds.protocol, ix.kinds.mcp_method, ix.kinds.request_content_kind]
                  .filter(Boolean)
                  .join(' · '),
              ],
            ] as Array<[string, string]>)
          : []),
        ...(ix.destination
          ? ([
              [
                'destination',
                (ix.destination.url ??
                  `${ix.destination.host ?? ''}${ix.destination.path ?? ''}`) +
                  (ix.destination.internal == null
                    ? ''
                    : ix.destination.internal
                      ? ' (internal)'
                      : ' (external)'),
              ],
            ] as Array<[string, string]>)
          : []),
        ...(httpSummary(ix.http)
          ? ([['http', httpSummary(ix.http) as string]] as Array<[string, string]>)
          : []),
        ...(ix.principal_sub ? ([['user', ix.principal_sub]] as Array<[string, string]>) : []),
        ...(ix.session_id ? ([['session', ix.session_id]] as Array<[string, string]>) : []),
      ],
      evidence,
      pinKey: `interaction:${ix.id}`,
      pinLabel: ix.summary || ix.id,
      requestPayloadHash: reqLeg?.payload_hash ?? null,
      responsePayloadHash: respLeg?.payload_hash ?? null,
      requestContentKind: reqLeg?.content_kind ?? null,
      responseContentKind: respLeg?.content_kind ?? null,
    });
    if (silent) return;
    // A pick: the default no longer owns the selection, and nothing the hold
    // left behind may pull a later step or click back to the held event.
    dismissed.current = false;
    defaultSelectedId.current = null;
    pendingViolation.current = null;
    // The risk layer's contract (see `FlowTablesProps.risk`): a policy event's
    // interaction is named by `?violation=` and `?iid=` is dropped; any other
    // interaction is `?iid=`, with the open event pinned so it stays where it was.
    const eventIndex = risk ? violationIndexByInteractionId.get(ix.id) : undefined;
    if (eventIndex != null) onSelectionChange?.({ violation: eventIndex });
    else if (risk && violationIndex != null) onSelectionChange?.({ iid: ix.id, violation: violationIndex });
    else onSelectionChange?.({ iid: ix.id });
  }

  async function selectEntity(e: Entity) {
    const seq = ++clickSeq.current;
    userClickInFlight.current = seq;
    const evidence = await fetchJson<{ spans: SpanEvidence[] }>(
      `/traces/${traceId}/entities/${e.id}/spans`,
    )
      .then((r) => r.spans)
      .catch(() => []);
    if (userClickInFlight.current === seq) userClickInFlight.current = 0;
    if (seq !== clickSeq.current) return; // a newer click superseded this one
    dismissed.current = false;
    defaultSelectedId.current = null;
    pendingViolation.current = null;
    setSelection({
      kind: 'entity',
      id: e.id,
      sectionTitle: 'Entity',
      fields: [
        ['display_name', e.display_name],
        ['namespace', e.namespace ?? ''],
        ['kind', e.kind],
        ['natural_key', e.natural_key],
        ['entity_id', e.id],
        ['detected_from', e.detected_from],
      ],
      evidence,
      pinKey: `entity:${e.id}`,
      pinLabel: e.display_name || e.id,
      requestPayloadHash: null, // entities carry no payload
      responsePayloadHash: null,
      requestContentKind: null,
      responseContentKind: null,
    });
    onSelectionChange?.({ eid: e.id });
  }

  /**
   * An edge in either graph tab was clicked: select its parent INTERACTION, or
   * deselect on a background click.
   *
   * A THIN ADAPTER over `selectInteraction`, deliberately containing no selection
   * logic of its own. The graph reports an interaction ID (it holds a derived
   * `GraphSpec` and has no interactions array to resolve against — see
   * `EntityGraphProps.onSelectInteraction`), and all this does is turn that id into
   * the `Interaction` object the existing function takes. So an edge click produces
   * byte-for-byte the same selection a Flat-table row click does: the same evidence
   * fetch behind the same click-token race guard, the same fields, the same pin key,
   * the same `?iid` write.
   *
   * A LEG'S EDGE SELECTS ITS PARENT INTERACTION, which is `FlatLegsTable`'s contract
   * followed exactly rather than re-decided: legs have no selection of their own.
   * That is also why both of the interaction's edges then carry the selected
   * treatment (see `EdgeData.isSelected`) — the same reason the Interaction diagram
   * lights both of a selected interaction's messages.
   *
   * DESELECT (`null`) CLEARS THE PANEL, matching the panel's own close button
   * exactly: same `setSelection(null)`, same `onSelectionChange?.(null)` that drops
   * `?iid` from the URL. It is NOT gated on the current selection being an
   * interaction — a background click in the graph is an unambiguous "nothing", and
   * leaving a selected ENTITY's panel open because the reader had picked it from the
   * table would make the same gesture mean two different things depending on
   * invisible history. No evidence fetch is involved, so the click token is left
   * alone; an in-flight fetch from a previous click is superseded by the next click
   * that bumps it, exactly as before.
   *
   * A STALE ID IS A NO-OP, not a crash: the graph is drawn from the same
   * `interactions` array this resolves against, so a miss is unreachable today —
   * but the graph's model can outlive a poll that removed an interaction, and
   * silently doing nothing is the honest response to "select something that is no
   * longer there".
   */
  function selectInteractionById(interactionId: string | null) {
    if (interactionId === null) {
      // A dismissal like the panel's close: with the layer on, the default must
      // not reopen the panel on the open event straight away. Layer state,
      // so it is set only while the layer is on.
      if (risk) dismissed.current = true;
      setSelection(null);
      onSelectionChange?.(null);
      return;
    }
    const ix = interactions.find((i) => i.id === interactionId);
    if (!ix) return;
    void selectInteraction(ix);
  }

  /**
   * The graph's NODE-click adapter: an entity id from the graph → the same
   * `selectEntity` an Entities-table row click calls.
   *
   * The exact counterpart of {@link selectInteractionById} and it exists for the same
   * reason: the graph holds ids, this component holds the entity array, the evidence
   * fetch, the pin state and the `?eid` mirroring. Resolving the id here is what
   * keeps ONE selection path — a node click and a row click are the same call with
   * the same side effects, rather than two implementations that could drift on which
   * of those five things they remember to do.
   *
   * NO `null` ARM, unlike the interaction adapter: deselect already arrives through
   * `selectInteractionById(null)` when the graph background is clicked, and PF fires
   * that one event for both element kinds. A second deselect route would be two ways
   * to say one thing.
   *
   * A STALE ID IS A NO-OP for the same reason stated there — the graph's model can
   * outlive a poll that removed an entity, and doing nothing is the honest response.
   */
  function selectEntityById(entityId: string) {
    const e = entities.find((x) => x.id === entityId);
    if (!e) return;
    void selectEntity(e);
  }

  function togglePin() {
    if (!selection) return;
    if (pins.isPinned(selection.pinKey)) {
      pins.removePin(selection.pinKey);
      onPinsChange();
    } else {
      const spanIds = selection.evidence.map((e) => e.span_id).filter(Boolean);
      pins.addPin({ key: selection.pinKey, label: selection.pinLabel, spanIds });
      onPinsChange();
      // Jump to the tree and reveal the just-highlighted spans (expand their
      // ancestors so the striped rows are visible). Add-only, not unpin.
      onRevealSpans?.(spanIds);
    }
  }

  // THE RISK LAYER'S DEFAULT SELECTION AND ITS HOLD (see `FlowTablesProps.risk`
  // and the refs beside `clickSeq`). `pendingViolation` is a position the hold
  // below has just asked the parent to write: the default effect reads it instead
  // of the still-stale param in the same commit, so the held event is never left
  // for the one at its old position (and no evidence fetch is wasted on it); it is
  // cleared the moment the param catches up, whatever the URL's selection.
  const shownViolation = useRef<{ byId: ReadonlyMap<string, number>; id: string | null } | null>(null);
  // THE LAYER TOGGLED, either way: a dismissal and a pending hold belong to the
  // layer state that just ended (a dismissal made with the layer off must not
  // keep the default from opening when it is switched on). Off, a selection the
  // URL does not name — the default, or a policy event picked by its edge, which
  // `?violation=` named and the toggle just deleted — goes with the layer, so the
  // page is as it was and the URL still tells the truth.
  // `initialSelection` is read through a ref so the effect runs on the toggle only.
  const urlSelectionRef = useRef(initialSelection);
  urlSelectionRef.current = initialSelection;
  useEffect(() => {
    dismissed.current = false;
    pendingViolation.current = null;
    if (risk) return;
    defaultSelectedId.current = null;
    if (!urlSelectionRef.current?.iid) {
      setSelection((prev) => (prev?.kind === 'interaction' ? null : prev));
    }
  }, [risk]);
  useLayoutEffect(() => {
    if (!risk) {
      shownViolation.current = null;
      return;
    }
    const shown = shownViolation.current;
    shownViolation.current = {
      byId: violationIndexByInteractionId,
      id: fellBack ? null : (openEvent?.interaction_id ?? null),
    };
    // Only a CHANGED list (a re-read) can move an event; a step or a click leaves
    // the list as it was and never fires this.
    if (shown == null || shown.byId === violationIndexByInteractionId || shown.id == null) return;
    const index = violationIndexByInteractionId.get(shown.id);
    if (index == null || index === paramViolationIndex) return;
    pendingViolation.current = index;
    onViolationChange?.(index);
  }, [risk, violationIndexByInteractionId, openEvent, paramViolationIndex, fellBack, onViolationChange]);
  useEffect(() => {
    if (pendingViolation.current != null && pendingViolation.current === paramViolationIndex) pendingViolation.current = null;
    if (!risk || urlHasSelection || violations.length === 0) return;
    const index = pendingViolation.current ?? paramViolationIndex ?? 1;
    const target = violations[index - 1];
    if (!target || dismissed.current) return;
    if (selection?.kind === 'interaction' && selection.id === target.interaction_id) return;
    // Only nothing, or a policy event (a default or a stepped-to one), is moved
    // onto the open event: an entity or a plain interaction the reader just picked
    // (the URL write lands a render later) is left alone — and so is a click still
    // fetching its evidence: the default never supersedes a reader's pick.
    if (selection != null && !(selection.kind === 'interaction' && violationIndexByInteractionId.has(selection.id))) return;
    if (userClickInFlight.current !== 0) return;
    const ix = interactions.find((i) => i.id === target.interaction_id);
    if (!ix) return; // the risk read named an interaction the flow read has not (yet)
    defaultSelectedId.current = ix.id;
    void selectInteraction(ix, { silent: true, fromDefault: true });
    // `selectInteraction` closes over the current reads; keyed on what decides the target.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [risk, urlHasSelection, violations, paramViolationIndex, violationIndexByInteractionId, interactions, selection]);

  function handleStep(delta: -1 | 1) {
    if (violationIndex == null) return;
    dismissed.current = false;
    pendingViolation.current = null;
    // An explicit step beats a click still fetching its evidence: the click is
    // superseded (its seq is stale) and no longer blocks the default's move.
    // Only then: a bump on every step would also drop the step's own silent
    // select when Next is pressed twice inside one fetch.
    if (userClickInFlight.current !== 0) {
      clickSeq.current += 1;
      userClickInFlight.current = 0;
    }
    onSelectionChange?.({ violation: stepViolation(violationIndex, delta, violations.length) });
  }

  // URL → flow: restore the selection named by ?iid / ?eid once the tables have
  // loaded. Fires once per distinct target id (tracked in appliedInitial) so it
  // seeds the deep-link/reload selection without fighting later user clicks or
  // re-firing on every render. A stale id matching no loaded row is a no-op.
  const appliedInitial = useRef<string | null>(null);
  useEffect(() => {
    const target = initialSelection?.iid
      ? `interaction:${initialSelection.iid}`
      : initialSelection?.eid
        ? `entity:${initialSelection.eid}`
        : null;
    if (target === null) {
      appliedInitial.current = null; // URL cleared → allow a future restore
      return;
    }
    if (appliedInitial.current === target) return; // already applied this target
    // A user row click already selected the row AND wrote the URL (?iid/?eid),
    // which re-runs this effect via the changed initialSelection. Detect that
    // the internal selection already matches the target and just record it —
    // re-selecting would fire a redundant duplicate evidence fetch.
    const alreadySelected =
      (initialSelection?.iid && selection?.kind === 'interaction' && selection.id === initialSelection.iid) ||
      (initialSelection?.eid && selection?.kind === 'entity' && selection.id === initialSelection.eid);
    if (alreadySelected) {
      appliedInitial.current = target;
      return;
    }
    if (initialSelection?.iid) {
      const ix = interactions.find((i) => i.id === initialSelection.iid);
      if (!ix) return; // not loaded yet (or gone) — retry when data arrives
      appliedInitial.current = target;
      // Silent: the URL already names this selection, and a restore must not
      // rewrite it (with the risk layer on, a `?iid=` naming a policy event
      // would otherwise be turned into `?violation=` on load).
      void selectInteraction(ix, { silent: true });
    } else if (initialSelection?.eid) {
      const e = entities.find((x) => x.id === initialSelection.eid);
      if (!e) return;
      appliedInitial.current = target;
      void selectEntity(e);
    }
    // selectInteraction/selectEntity are stable enough for this effect's intent
    // (they close over traceId + the query data); we key the effect on the ids,
    // the loaded rows, and the current selection so it runs when any changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSelection?.iid, initialSelection?.eid, interactions, entities, selection]);

  if (isLoading) return <Spinner aria-label="Loading interactions and entities" />;
  if (isEmpty) {
    return (
      <EmptyState>
        <EmptyStateHeader titleText="No interaction data" headingLevel="h4" />
        <EmptyStateBody>
          {'No interaction data for this trace yet. The interactions processor derives it from the spans table as spans arrive; this trace may still be draining.'}
        </EmptyStateBody>
      </EmptyState>
    );
  }

  return (
    <LabelTrackContext.Provider value={labelTrack}>
    <div>
      <TrackBanner
        tracked={[...tracked.values()]}
        foreignHops={trackHighlight?.foreignHops ?? 0}
        onRemove={(key) => labelTrack.set(key, null)}
        onClear={() => setTracked(new Map())}
        onChoose={
          selectedInteractionId != null
            ? (at) => setPicker({ interactionId: selectedInteractionId, legType: 'request', at })
            : undefined
        }
      />
      {picker && (
            <TrackPicker
              // Keyed by target: a right-click on another leg is a new picker (its own folded/open state), not a re-render of the old one.
              key={`${picker.interactionId}/${picker.legType}`}
              traceId={traceId}
              target={picker}
              onClose={closePicker}
            />
          )}
      {/* The trace-level lineage-coverage warning (issue #120), OUTSIDE the
          tables' gutter div: the detail panel floats over that gutter, and the
          truncation must stay readable precisely when someone is drilling into a
          payload's data sources. */}
      <LineageCoverageAlert
        status={lineageQ.data?.status ?? null}
        stoppedAtSeq={lineageQ.data?.stoppedAtSeq ?? null}
        // `isLoadingError`, not `isError`: a failed BACKGROUND re-read (the page is
        // live, ADR-0034) keeps the last answer, and the banner keeps quoting it.
        // Only a read that never produced an answer is "unknown".
        isError={lineageQ.isLoadingError}
        // The `isLoading` gate above covers the interactions/entities reads only,
        // so these tables are already on screen while the lineage read is in
        // flight. The banner needs to know that, or its silence claims complete
        // coverage before the answer exists.
        isLoading={lineageQ.isLoading}
      />
      {/* Tables container. While the detail panel is open it floats fixed on the
          right, so `dg-detail-gutter` reserves a right gutter derived from the
          panel's own width vars (global.css) — the tables shrink out from under
          the float instead of being covered. Closed → no class, tables reclaim
          full width. */}
      <div className={selection ? 'dg-detail-gutter' : undefined}>
        {/* THE RISK LAYER'S ALERTS, above whichever presentation is active — the
            same section the risk trace page renders. Inside the gutter div, so it
            shrinks out from under the floating detail panel like the views do. */}
        {risk && <TraceAlertsSection alerts={alerts} />}
        {/* THE ENTITIES TABLE, ON THE TABLES VIEW ONLY.
            It used to render on all five presentations, on the reasoning that the
            trace's entities are a fact of the flow VIEW rather than a part of the
            interactions table the three pictures replace. It is now scoped to
            Tree|Flat by request: on the three picture views the entities are already
            the thing being drawn, so the table restated on screen what the nodes and
            lifelines show.

            WHAT THIS COSTS, stated rather than glossed. On the Lineage view the table
            was the KEYBOARD-accessible way to select an entity — a table row is
            tabbable, an SVG circle is not — and that view's whole answer is driven by
            an entity selection. Node clicks still work (`onSelectEntity` is wired on
            both graphs), so a mouse user loses nothing, but a keyboard or
            screen-reader user now has no in-view control for it. `?eid` in the URL is
            the remaining route. See the note at the Lineage render below. */}
        {legView === 'tree' || legView === 'flat' ? (
          <>
            <Title headingLevel="h3" size="md">
              Entities
            </Title>
            <EntitiesTable
              entities={entities}
              selectedId={selectedEntityId}
              pinColor={pinColor}
              onSelect={selectEntity}
            />
          </>
        ) : null}

        {/* THE "Interactions" HEADING, on every presentation EXCEPT the two graphs.
            It labels the thing below it, which for Tree/Flat is literally a table of
            interactions and for the Interaction diagram is a sequence of them. On the
            Execution Flow and Lineage graphs it was removed by request: those views draw
            entities as nodes and interaction legs as edges, so a bare "Interactions"
            above the canvas named only half of what is on screen — and both views
            already carry their own labelling (the top-level tab name, and on Lineage the
            two pickers immediately below it).

            Gated rather than deleted so the tables and the diagram keep their section
            label; a heading that vanished everywhere would leave those three with an
            unlabelled block. */}
        {legView !== 'graph' && legView !== 'lineage' ? (
          <Title headingLevel="h3" size="md" style={{ marginTop: '1rem' }}>
            Interactions
          </Title>
        ) : null}
        {/* STILL FIVE PRESENTATIONS OF ONE DATASET, but they are no longer all reached
            from here. This component renders whichever one `legView` names — the
            depth-indented parent/child tree, one row per request/response leg ordered by
            the trace-wide `seq`, that leg sequence as a UML sequence diagram, the
            directed Execution Flow graph, or that graph with a chosen data source's
            reachability highlighted.

            The last three are now selected by the TOP-LEVEL tabs in `TraceDetailPage`
            (path segments), so only Tree|Flat are offered by the bar below. The long
            argument that used to sit here about why `Interaction diagram` belonged
            between Flat and Execution Flow, and `Lineage` last, has moved with those
            tabs — ordering them is that page's business now, and restating it here would
            be a second, driftable copy of the same reasoning.

            Kept inside the gutter div so the tab bar shrinks out from under the floating
            detail panel along with the tables, the diagram and the graph. */}
        {/* THE SUB-TAB BAR IS NOW TREE|FLAT ONLY, and only on the flow view.
            Interaction diagram / Execution Flow / Lineage were three more tabs here
            until they were promoted to top-level views beside Span tree
            (`TraceDetailPage`'s ViewKey). What is left is the two renderings of ONE row
            set — indented by parent, or flat by seq — which is a genuine sub-choice of
            "the tables" and not a peer of the pictures.
            Hidden entirely (`showLegTabs`) when this component is rendering one of the
            promoted views: there the top-level tabs already decide the presentation, and
            a second bar offering the same choice would be two controls for one thing. */}
        {showLegTabs && (
          <Tabs
            activeKey={legView}
            onSelect={(_e, key) => onLegViewChange?.(parseLegViewKey(String(key)))}
            aria-label="Interaction list views"
          >
            <Tab eventKey="tree" title={<TabTitleText>Tree</TabTitleText>} />
            <Tab eventKey="flat" title={<TabTitleText>Flat</TabTitleText>} />
          </Tabs>
        )}
        {/* The infrastructure affordance. Present only when the trace HAS
            infrastructure rows — on a trace with none, a control that reveals
            nothing is noise. Sits above the row views rather than inside one,
            because it governs all three of Tree / Flat / Interaction diagram,
            which render the same row set. It names the count, so the reader
            knows what the default is keeping from them. */}
        {/* THE EDGE MODE, on the two graph views only: per leg or per interaction
            (`edgesPerInteraction`). A control of the picture, so it sits right
            above it, like the infrastructure affordance above the rows. */}
        {(legView === 'graph' || legView === 'lineage') && (
          <ToggleGroup aria-label="Graph edges" isCompact className="pf-v5-u-mb-sm">
            <ToggleGroupItem
              text="Edges per leg"
              buttonId="edges-leg"
              isSelected={!edgesPerInteraction}
              onChange={() => onEdgesPerInteractionChange?.(false)}
            />
            <ToggleGroupItem
              text="Edges per interaction"
              buttonId="edges-interaction"
              isSelected={edgesPerInteraction}
              onChange={() => onEdgesPerInteractionChange?.(true)}
            />
          </ToggleGroup>
        )}
        {infraTotal > 0 && legView !== 'graph' && legView !== 'lineage' && (
          <Button
            variant="link"
            isInline
            style={{ marginTop: '0.25rem' }}
            onClick={() => onShowInfraChange?.(!showInfra)}
          >
            {showInfra
              ? `Hide ${infraTotal} infrastructure interaction${infraTotal === 1 ? '' : 's'}`
              : `${infraTotal} infrastructure interaction${infraTotal === 1 ? '' : 's'} hidden — show`}
          </Button>
        )}
        {legView === 'lineage' ? (
          /* The Lineage graph, in the interactions TABLE's place inside the same
             gutter div — the same reasoning as the graph and the diagram: a selected
             row floats the detail panel over the right, and this must shrink out from
             under it rather than be overlapped.

             THE ENTITIES TABLE IS NO LONGER RENDERED HERE (see the gate above it), which
             reverses what this comment used to say — it called the table "load-bearing,
             not merely retained: the ONLY way to select an entity". That was true when
             the graph's nodes were drag surfaces only; they became click targets when
             `withSelection` was applied to them, and `onSelectEntity` below routes a node
             click into the same `selectEntity` the table row called. So the mouse control
             this view's answer is driven from is now the graph itself.

             THE GAP THAT LEAVES is keyboard access: an SVG circle is not tabbable, so
             with the table gone there is no keyboard-reachable entity control on this
             view. `?eid` (a deep link, a reload, a shared URL) still selects one. Worth
             fixing properly with a focusable node or a compact picker, rather than
             leaving the reader to discover it.

             Fed the ALREADY-DERIVED `entities` / `interactions` / `lineageQ` and the
             existing `selectedEntityId` — no new query and, crucially, no second
             notion of "the selected entity". The highlight follows the same `?eid`
             selection that highlights the Entities table row and opens the detail
             panel, so the three cannot disagree about what the reader picked.

             Same lazy chunk as the graph (see the two `lazy` calls above) and the
             same Suspense fallback wording, so a chunk fetch is not a new loading
             treatment for a reader who has already seen the graph tab. */
          <Suspense fallback={<Spinner aria-label="Loading execution flow graph" />}>
            <LineageGraph
              traceId={traceId}
              entities={entities}
              interactions={interactions}
              // `byLeg` is deliberately NOT passed any more. This tab's highlight now
              // comes from the SERVED reachability reads (ADR-0028 D14/D15), which the
              // tab queries itself — the per-leg map answered a strictly weaker
              // question (one hop, composed client-side) and keeping it here would
              // leave two suppliers of one answer. The trace's `status` still comes
              // from this read, so the coverage banner and the tab quote one value.
              status={lineageQ.data?.status ?? null}
              isLineageError={lineageQ.isLoadingError}
              selectedEntityId={selectedEntityId}
              // THE OTHER HALF OF THE QUESTION. The reachability read is
              // `fanin(entity, source)` / `fanout(entity, source)` with `source`
              // REQUIRED (docs/data_lineage_alg.md's `## API`), so the tab needs a
              // chosen source before it can ask anything — an entity selection alone is
              // no longer a complete question. It arrives from `?src` through the parent
              // for the same reason `?eid` does: one owner of the URL, one notion of
              // what the reader picked. Exactly ONE source is traced at a time;
              // multi-source semantics are deferred upstream, so there is deliberately
              // no array here.
              lineageSource={lineageSource}
              onLineageSourceChange={onLineageSourceChange}
              riskLevelByInteraction={riskColours}
              edgesPerInteraction={edgesPerInteraction}
              // NODE clicks select an entity, through the very same `selectEntity`
              // the Entities table row uses — so `?eid`, the detail panel and the
              // highlight all follow one path and there is no second notion of
              // "selected entity". See LineageGraph's prop note and
              // `DraggableKindColouredNode` for the drag-vs-click evidence.
              onSelectEntity={selectEntityById}
              // Edges are click targets on THIS tab too, not only on Execution Flow:
              // the two tabs are one graph, so an arrow that opened a panel on one
              // and did nothing on the other would be the fork the shared renderer
              // exists to prevent. Note the flow view holds ONE selection, so an
              // edge click here replaces the selected ENTITY and therefore clears
              // this tab's own highlight — see LineageGraph's prop note.
              selectedInteractionId={selectedInteractionId}
              onSelectInteraction={selectInteractionById}
            />
          </Suspense>
        ) : legView === 'graph' ? (
          /* The graph stands in for the interactions TABLE, inside the same
             gutter div — so when a row is selected it shrinks out from under the
             floating detail panel exactly as the tables do, rather than being
             overlapped by it.

             The `Entities` table is NOT rendered on this view (see the gate above it).
             This comment used to argue the opposite — that the table was a fact of the
             flow VIEW rather than part of the interactions table this tab replaces, and
             that it supplied the kind/natural-key/detected-from columns plus the entity
             click target the graph lacked. The requirement scoped it to Tree|Flat, and
             the click-target half of that argument had already expired: the nodes are
             click targets now. The columns genuinely are gone from this view; the node's
             `<title>` carries its kind and natural key.

             The Suspense fallback is the same PF `Spinner` + `aria-label` pairing
             every loading state in this view uses (the `isLoading` return above,
             `LegTabs`' per-leg payload read), so a chunk fetch is not a new,
             fourth loading treatment a reader has to learn. */
          <Suspense fallback={<Spinner aria-label="Loading execution flow graph" />}>
            <ExecutionFlowGraph
              traceId={traceId}
              highlight={graphHighlight}
              onContextMenuInteraction={trackDefaultOf}
              edgesPerInteraction={edgesPerInteraction}
              // The risk layer: edge colours by risk level and the classification
              // tags in the edges' tag slot, both `undefined` with the layer off so
              // this view is byte-for-byte as before then. The seams are the risk
              // page's (`EntityGraphProps.riskLevelByInteraction`).
              riskLevelByInteraction={riskColours}
              classificationByInteraction={classifications}
              // The graph's EDGES are the interaction click target this tab used to
              // lack — one edge is one leg, and clicking it opens the same detail
              // panel a Flat-table row click opens, through the same
              // `selectInteraction`.
              //
              // NO `onSelectEntity` HERE, deliberately, and this is the one place the two
              // graph views differ in their wiring. The nodes ARE selectable in the
              // renderer (it is one shared component), but this view has no use for an
              // entity selection: nothing on it is scoped to one entity, whereas Lineage
              // traces a chosen source THROUGH a selected entity. `EntityGraph` treats an
              // absent handler as "node clicks fire PF's event and are simply not acted
              // on", so leaving it off is the supported way to say that — see its
              // `onSelectEntity` note. (This comment previously claimed the nodes were
              // drag surfaces rather than click targets and pointed at the Entities table
              // as the place an entity is selected; both halves are now out of date —
              // `withSelection` is applied to nodes, and the table no longer renders on
              // this view.)
              selectedInteractionId={selectedInteractionId}
              onSelectInteraction={selectInteractionById}
            />
          </Suspense>
        ) : legView === 'diagram' ? (
          /* The sequence diagram stands in for the interactions TABLE, inside the
             same gutter div, for the same reason the graph does — a selected row
             floats the detail panel over the right, and the diagram must shrink out
             from under it rather than be overlapped.

             NOT lazy, unlike the graph: this is hand-rolled SVG with no dependency
             beyond what the bundle already carries, so there is no ~388kB chunk to
             defer and a Suspense boundary would buy a spinner and nothing else.

             Fed the ALREADY-DERIVED `entities` / `interactions` this component
             holds — no new query. It re-derives its own lifelines/messages from
             them (via lib/sequenceDiagram, which consumes the same
             `flow.flatLegRows` `flatRows` above does), so the diagram's arrows and
             the Flat tab's rows are the same list in the same order. */
          <InteractionDiagram
            entities={entities}
            interactions={displayedInteractions}
            selectedId={selectedInteractionId}
            hoveredId={hoveredInteractionId}
            litEdgeIds={trackHighlight?.edgeIds ?? null}
            onContextMenuInteraction={trackDefaultOf}
            riskLevelByInteraction={riskColours}
            onSelect={selectInteraction}
          />
        ) : legView === 'flat' ? (
          <FlatLegsTable
            rows={flatRows}
            connectors={flatConnectors}
            entById={entById}
            selectedId={selectedInteractionId}
            pinColor={pinColor}
            litEdgeIds={trackHighlight?.edgeIds ?? null}
            onContextMenuInteraction={trackDefaultOf}
            onSelect={selectInteraction}
          />
        ) : (
          <InteractionsTable
            interactions={displayedInteractions}
            entById={entById}
            depthById={depthById}
            selectedId={selectedInteractionId}
            pinColor={pinColor}
            litEdgeIds={trackHighlight?.edgeIds ?? null}
            onContextMenuInteraction={trackDefaultOf}
            onSelect={selectInteraction}
          />
        )}
      </div>

      {selection && (
        <HopHoverContext.Provider value={setHoveredInteractionId}>
        <FlowDetailPanel
          traceId={traceId}
          selection={selection}
          pins={pins}
          lineageQ={lineageQ}
          onTogglePin={togglePin}
          onClose={() => {
            // With the layer on, a close is a dismissal: the default does not
            // reopen the panel on the open event until a step or a pick.
            if (risk) dismissed.current = true;
            setSelection(null);
            onSelectionChange?.(null);
          }}
          onNavigateToSpan={onNavigateToSpan}
          layer={
            risk && selection.kind === 'interaction' ? (
              /* THE RISK LAYER IN THE PANEL: the same cards the risk trace page
                 renders, never copies. The interaction's risk for ANY selected
                 interaction (a stand-in with `risk: null` for one the risk read has
                 no record of, so it reads "not yet computed"); for a policy event,
                 the stepper and the decision card, whose hop links select by `?iid=`
                 here. The stepper is in the panel because the card is: the event is
                 the selection. */
              <div className="dg-detail-card dg-risk-layer" data-testid="risk-layer">
                {riskDetail.isLoading ? (
                  <Spinner size="md" aria-label="Loading risk" />
                ) : riskDetail.isLoadingError ? (
                  <p style={{ color: 'var(--dg-color-muted)' }}>The risk read failed.</p>
                ) : (
                  (() => {
                    const selectedIx = interactions.find((i) => i.id === selection.id);
                    const record =
                      forest.find((i) => i.interaction_id === selection.id) ??
                      (selectedIx ? forestStandIn(selectedIx, traceId) : null);
                    return record ? <InteractionPanel interaction={record} showTraceLink={false} /> : null;
                  })()
                )}
                {openEvent && selectedViolationIndex != null && violationIndex != null && (
                  <>
                    <div style={{ marginTop: '0.75rem' }}>
                      <ViolationStepper position={violationIndex} count={violations.length} onStep={handleStep} />
                    </div>
                    <PolicyDecisionPanel
                      violation={openEvent}
                      ruleIndex={catalog.data}
                      entities={entities}
                      selectionParam="iid"
                    />
                  </>
                )}
              </div>
            ) : undefined
          }
        />
        </HopHoverContext.Provider>
      )}
    </div>
    </LabelTrackContext.Provider>
  );
}
