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
 * of JS and ~130kB of CSS serving this ONE tab, so a static import made every
 * reader of the trace list and the span tree pay for a view most never open.
 * `React.lazy` puts it in its own async chunk that is fetched the first time
 * `?legs=graph` is active — see the Suspense boundary at the render site, and
 * ExecutionFlowGraph.tsx's note on why its stylesheets moved in there too.
 *
 * `ExecutionFlowGraph` has a default export purely so this needs no
 * `.then(m => ({ default: m.X }))` unwrap.
 */
const ExecutionFlowGraph = lazy(() => import('./ExecutionFlowGraph'));

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
   * `graph`, the Execution Flow. The parent owns every URL param in this view
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
 * The Interactions section has three peer presentations behind the `?legs` tabs
 * (`LegViewKey`): `tree`, `flat`, and the `graph` (Execution Flow) — all reading
 * the same two queries, so switching between them costs no fetch.
 *
 * This module owns only the composition and the selection/URL state; the tables,
 * the floating detail panel and the coverage banner live in ./flow, and the graph
 * in ../ExecutionFlowGraph (lazy — see the import above).
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
  // Persisted **Data lineage** for the whole trace (ADR-0027), read ONCE per
  // trace and keyed per leg — not per payload expansion. TanStack caches on
  // `traceId`, so the panel switching between rows/legs never re-fetches.
  //
  // NOT gated on a selection (as #119 had it): since #120 this read also carries
  // the trace's complete/partial coverage, and that warning belongs on the first
  // paint. A truncation a reader must select a row to discover cannot stop them
  // reading the visible rows as the whole picture — which is the entire point of
  // the flag (ADR-0027 D6).
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
        {/* Three ways to present the same interactions — the default
            depth-indented parent/child tree, one row per request/response leg
            ordered by the trace-wide `seq`, or the directed Execution Flow graph.
            Tabs (not the old checkbox) because these are peer presentations of
            one dataset, which is what a tab bar says; the active one is a URL
            param so it survives reload. Kept inside the gutter div so the tab bar
            shrinks out from under the floating detail panel along with the tables
            and the graph. */}
        <Tabs
          activeKey={legView}
          onSelect={(_e, key) => onLegViewChange?.(parseLegViewKey(String(key)))}
          aria-label="Interaction list views"
        >
          <Tab eventKey="tree" title={<TabTitleText>Tree</TabTitleText>} />
          <Tab eventKey="flat" title={<TabTitleText>Flat</TabTitleText>} />
          <Tab eventKey="graph" title={<TabTitleText>Execution Flow</TabTitleText>} />
        </Tabs>
        {legView === 'graph' ? (
          /* The graph stands in for the interactions TABLE, inside the same
             gutter div — so when a row is selected it shrinks out from under the
             floating detail panel exactly as the tables do, rather than being
             overlapped by it.

             The `Entities` table above deliberately stays visible: it is a fact
             of the flow VIEW, not a part of the interactions table this tab
             replaces, and the graph's nodes ARE those entities — keeping the
             table gives the reader the kind/natural-key/detected-from columns the
             nodes can only hint at, and a click target for the entity detail
             panel the graph does not offer.

             The Suspense fallback is the same PF `Spinner` + `aria-label` pairing
             every loading state in this view uses (the `isLoading` return above,
             `LegTabs`' per-leg payload read), so a chunk fetch is not a new,
             fourth loading treatment a reader has to learn. */
          <Suspense fallback={<Spinner aria-label="Loading execution flow graph" />}>
            <ExecutionFlowGraph traceId={traceId} />
          </Suspense>
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
