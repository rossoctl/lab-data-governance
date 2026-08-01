import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import {
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
 * `@patternfly/react-topology` (plus the d3 / dagre / mobx it drags in) is ~388kB
 * of JS and ~130kB of CSS serving these TWO tabs, so a static import made every
 * reader of the trace list and the span tree pay for a view most never open.
 * `React.lazy` puts it in its own async chunk that is fetched the first time
 * `?legs=graph` or `?legs=lineage` is active — see the Suspense boundary at the
 * render site, and ExecutionFlowGraph.tsx's note on why its stylesheets moved in
 * there too.
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
   * Which Interactions tab is active, from the URL (`?legs`) — including
   * `graph` (Execution Flow) and `lineage`. The parent owns every URL param in this view
   * (same as `initialSelection`), so this is a controlled prop rather than
   * internal state: this component never reaches for `useSearchParams` itself,
   * and reload / bookmark / back restore the tab. Defaults to `tree` when
   * omitted.
   */
  legView?: LegViewKey;
  /** Fired when the Interactions tab changes so the parent can mirror `?legs`. */
  onLegViewChange?: (key: LegViewKey) => void;
}

/**
 * The interaction-flow view: an Entities table plus a presentation of the trace's
 * Interactions, both derived from spans by the in-cluster processor (ADR-0013).
 * Ports the vanilla execution_flow_logic.js — depth indentation via the parent
 * walk, pin dots mirroring the tree's highlight store, and lazy span-evidence
 * fetch + a detail panel on row click.
 *
 * The Interactions section has five peer presentations behind the `?legs` tabs
 * (`LegViewKey`): `tree`, `flat`, `diagram` (the Interaction diagram — a sequence
 * diagram of the flat leg list), `graph` (Execution Flow) and `lineage` (that same
 * graph with the selected entity's **Data lineage** sources highlighted). The first
 * four read the same two queries, so switching between them costs no fetch;
 * `lineage` additionally reads the trace's lineage — which this component already
 * holds for the coverage banner and the per-leg detail blocks, so it costs no fetch
 * either.
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
  const depthById = useMemo(() => computeInteractionDepths(interactions), [interactions]);
  const flatRows = useMemo(() => flatLegRows(interactions), [interactions]);
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
        ['anchor span(s)', evidence.filter((e) => e.role === 'anchor').map((e) => e.span_id).join(', ') || '—'],
        ['evidence spans', String(evidence.length)],
        ['request_at', reqLeg?.occurred_at ?? '—'],
        ['response_at', respLeg?.occurred_at ?? '—'],
        ...(ix.duration_seconds != null
          ? ([['duration', `${(ix.duration_seconds * 1000).toFixed(0)} ms`]] as Array<[string, string]>)
          : []),
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
        <Title headingLevel="h3" size="md">
          Entities
        </Title>
        <EntitiesTable
          entities={entities}
          selectedId={selectedEntityId}
          pinColor={pinColor}
          onSelect={selectEntity}
        />

        <Title headingLevel="h3" size="md" style={{ marginTop: '1rem' }}>
          Interactions
        </Title>
        {/* Five ways to present the same interactions — the default
            depth-indented parent/child tree, one row per request/response leg
            ordered by the trace-wide `seq`, that same leg sequence as a UML
            sequence diagram, the directed Execution Flow graph, or that graph with
            the selected entity's data sources highlighted. Tabs (not the
            old checkbox) because these are peer presentations of one dataset,
            which is what a tab bar says; the active one is a URL param so it
            survives reload. Kept inside the gutter div so the tab bar shrinks out
            from under the floating detail panel along with the tables, the diagram
            and the graph.

            `Interaction diagram` sits between Flat and Execution Flow because it
            is the Flat list read down the page (it renders that list's rows, in
            that order, from the same `flatRows` derivation) with the graph's
            who-called-whom axis laid out horizontally — the two neighbours' shared
            middle rather than an unrelated sixth thing.

            `Lineage` sits LAST, immediately after Execution Flow, because it IS the
            Execution Flow picture with one more question asked of it: identical
            nodes and edges (one component, one `deriveGraph` — see
            ExecutionFlowGraph's `EntityGraph`), plus a highlight of where the
            selected entity's data came from. Anywhere earlier would separate it
            from the view it is a reading of. */}
        <Tabs
          activeKey={legView}
          onSelect={(_e, key) => onLegViewChange?.(parseLegViewKey(String(key)))}
          aria-label="Interaction list views"
        >
          <Tab eventKey="tree" title={<TabTitleText>Tree</TabTitleText>} />
          <Tab eventKey="flat" title={<TabTitleText>Flat</TabTitleText>} />
          <Tab eventKey="diagram" title={<TabTitleText>Interaction diagram</TabTitleText>} />
          <Tab eventKey="graph" title={<TabTitleText>Execution Flow</TabTitleText>} />
          <Tab eventKey="lineage" title={<TabTitleText>Lineage</TabTitleText>} />
        </Tabs>
        {legView === 'lineage' ? (
          /* The Lineage graph, in the interactions TABLE's place inside the same
             gutter div — the same reasoning as the graph and the diagram: a selected
             row floats the detail panel over the right, and this must shrink out from
             under it rather than be overlapped.

             THE ENTITIES TABLE ABOVE IS LOAD-BEARING HERE, not merely retained: it is
             the ONLY way to select an entity (the graph's nodes are drag targets, not
             click targets — see ExecutionFlowGraph's note on why `withSelection` is
             not applied), so it is the control this tab's whole answer is driven from.

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

             The `Entities` table above deliberately stays visible: it is a fact
             of the flow VIEW, not a part of the interactions table this tab
             replaces, and the graph's nodes ARE those entities — keeping the
             table gives the reader the kind/natural-key/detected-from columns the
             nodes can only hint at, and a click target for the ENTITY detail
             panel the graph still does not offer (its nodes are drag surfaces;
             its edges select an INTERACTION, which is a different thing).

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
              // `selectInteraction`. The nodes remain drag surfaces rather than click
              // targets (the Entities table above is still where an entity is
              // selected), which is why the comment above says the graph offers no
              // entity click target rather than no click target at all.
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
            interactions={interactions}
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
            interactions={interactions}
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
