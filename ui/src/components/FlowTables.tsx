import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
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
} from '@patternfly/react-core';

import { useInteractions, useEntities, useDataLineage } from '../api/hooks';
import { fetchJson } from '../api/client';
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
}: FlowTablesProps) {
  const interactionsQ = useInteractions(traceId);
  const entitiesQ = useEntities(traceId);
  const [selection, setSelection] = useState<Selection | null>(null);
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

  // The selected id per table kind. Only the latest-selected row (entity or
  // interaction) is highlighted; `selection` already tracks that single row
  // across both kinds, so the other table's id is null.
  const selectedEntityId = selection?.kind === 'entity' ? selection.id : null;
  const selectedInteractionId = selection?.kind === 'interaction' ? selection.id : null;

  const interactions = useMemo(() => interactionsQ.data ?? [], [interactionsQ.data]);
  const entities = useMemo(() => entitiesQ.data ?? [], [entitiesQ.data]);
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

  async function selectInteraction(ix: Interaction) {
    const seq = ++clickSeq.current;
    const evidence = await fetchJson<{ spans: SpanEvidence[] }>(
      `/traces/${traceId}/interactions/${ix.id}/spans`,
    )
      .then((r) => r.spans)
      .catch(() => []);
    if (seq !== clickSeq.current) return; // a newer click superseded this one
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
    });
    onSelectionChange?.({ iid: ix.id });
  }

  async function selectEntity(e: Entity) {
    const seq = ++clickSeq.current;
    const evidence = await fetchJson<{ spans: SpanEvidence[] }>(
      `/traces/${traceId}/entities/${e.id}/spans`,
    )
      .then((r) => r.spans)
      .catch(() => []);
    if (seq !== clickSeq.current) return; // a newer click superseded this one
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
      void selectInteraction(ix);
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

  if (isLoading) return <Spinner aria-label="Loading interaction flow" />;
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
    <div>
      {/* The trace-level lineage-coverage warning (issue #120), OUTSIDE the
          tables' gutter div: the detail panel floats over that gutter, and the
          truncation must stay readable precisely when someone is drilling into a
          payload's data sources. */}
      <LineageCoverageAlert
        status={lineageQ.data?.status ?? null}
        stoppedAtSeq={lineageQ.data?.stoppedAtSeq ?? null}
        isError={lineageQ.isError}
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
              isLineageError={lineageQ.isError}
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
            onSelect={selectInteraction}
          />
        ) : legView === 'flat' ? (
          <FlatLegsTable
            rows={flatRows}
            connectors={flatConnectors}
            entById={entById}
            selectedId={selectedInteractionId}
            pinColor={pinColor}
            onSelect={selectInteraction}
          />
        ) : (
          <InteractionsTable
            interactions={displayedInteractions}
            entById={entById}
            depthById={depthById}
            selectedId={selectedInteractionId}
            pinColor={pinColor}
            onSelect={selectInteraction}
          />
        )}
      </div>

      {selection && (
        <FlowDetailPanel
          selection={selection}
          pins={pins}
          lineageQ={lineageQ}
          onTogglePin={togglePin}
          onClose={() => {
            setSelection(null);
            onSelectionChange?.(null);
          }}
          onNavigateToSpan={onNavigateToSpan}
        />
      )}
    </div>
  );
}
