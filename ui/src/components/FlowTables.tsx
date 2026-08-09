import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Title,
  Spinner,
  EmptyState,
  EmptyStateBody,
  EmptyStateHeader,
  Button,
  Checkbox,
  CodeBlock,
  CodeBlockCode,
} from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';

import { useInteractions, useEntities, usePayload } from '../api/hooks';
import { fetchJson } from '../api/client';
import {
  computeInteractionDepths,
  isInfrastructure,
  legOfType,
  requestOccurredAt,
} from '../lib/flow';
import { formatTime24Utc } from '../lib/recentTraces';
import type { PinStore } from '../lib/pins';
import { EntityPill } from './EntityPill';
import { DetailList } from './DetailList';
import { ClassificationView } from './ClassificationView';
import { RoleIcon } from './RoleIcon';
import type { Entity, Interaction, SpanEvidence } from '../types';

interface Selection {
  kind: 'interaction' | 'entity';
  id: string;
  /** The panel's promoted caption for this selection ('Entity' | 'Interaction'). */
  sectionTitle: 'Entity' | 'Interaction';
  fields: Array<[string, string]>;
  evidence: SpanEvidence[];
  pinKey: string;
  pinLabel: string;
  /** Payload content hashes (interactions only) so the panel can lazily fetch
   *  and show the request/response bodies — ported from the vanilla flow view's
   *  Req/Resp cells + showPayload(). Null when the interaction carried none. */
  requestPayloadHash: string | null;
  responsePayloadHash: string | null;
}

/**
 * A deterministic muted color for an interaction's request↔response connector
 * line in the flat view. Hashing `ix.id` to a hue (NOT Math.random) keeps a
 * given interaction's bracket a stable color across renders and lets several
 * overlapping brackets be told apart. Kept dim (low saturation / mid lightness)
 * to sit alongside the file's #555/#888 grays without shouting.
 */
function connectorColor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) | 0;
  const hue = Math.abs(hash) % 360;
  return `hsl(${hue}, 45%, 60%)`;
}

/**
 * The flat view's request↔response connector cell. Given the drawing roles for
 * this row (one per interaction bracket covering it — see `flatConnectors`),
 * paints a small SVG: a vertical line down from center for a request `top`
 * edge (capped with a ▾ marker), up to center for a response `bottom` edge
 * (capped with ▴), and a full pass-through line for an in-between `through`
 * row. Overlapping brackets are laid out in adjacent lanes so their lines never
 * coincide. `pointer-events: none` on the SVG keeps the row click intact.
 *
 * The SVG is absolutely positioned to fill the cell's TRUE height (`inset: 0`
 * in a `position: relative` cell) and drawn with a fixed-height viewBox scaled
 * via `preserveAspectRatio="none"`. So each row's segment always spans the full
 * row — whatever a compact row actually measures, including cell padding — and
 * butts seamlessly against the adjacent rows' segments. That is what makes a
 * request→response bracket read as ONE unbroken line rather than the chopped
 * per-row pieces the old fixed 28px SVG produced. The end caps (▾/▴) and the
 * through pass-through keep their meaning; lanes keep overlapping brackets apart.
 */
function ConnectorCell({
  roles,
}: {
  roles: Array<{ id: string; role: 'top' | 'bottom' | 'through' }>;
}) {
  const laneW = 8; // horizontal spacing between overlapping brackets
  const width = Math.max(laneW, roles.length * laneW);
  // A nominal viewBox height the lines are drawn in; `preserveAspectRatio="none"`
  // stretches it to the cell's real pixel height, so the value is arbitrary —
  // only the ratios (mid = center) matter. Vertical lines don't distort under
  // that stretch, but glyphs would, so the ▾/▴ caps are drawn as separately-
  // positioned HTML markers (see below) rather than SVG <text>.
  const vbH = 100;
  const mid = vbH / 2;
  return (
    <>
      <svg
        width={width}
        height="100%"
        viewBox={`0 0 ${width} ${vbH}`}
        preserveAspectRatio="none"
        style={{
          position: 'absolute',
          inset: 0,
          display: 'block',
          pointerEvents: 'none',
          overflow: 'visible',
        }}
        aria-hidden="true"
        data-testid="flat-connector"
      >
        {roles.map(({ id, role }, lane) => {
          const x = lane * laneW + laneW / 2;
          const color = connectorColor(id);
          // top: line from center downward; bottom: from top edge to center;
          // through: full height. A non-scaling stroke keeps the line 2px wide
          // regardless of how tall the row (and thus the stretched viewBox) is.
          const y1 = role === 'bottom' ? 0 : mid;
          const y2 = role === 'top' ? vbH : mid;
          return (
            <g key={id} data-connector-id={id} data-connector-role={role} stroke={color} fill={color}>
              <line
                x1={x}
                y1={role === 'through' ? 0 : y1}
                x2={x}
                y2={role === 'through' ? vbH : y2}
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
            </g>
          );
        })}
      </svg>
      {/* The ▾/▴ end caps as HTML markers centered on the cell, so they keep a
          fixed size and shape while the SVG lines stretch to the row height. */}
      {roles.map(({ id, role }, lane) =>
        role === 'through' ? null : (
          <span
            key={`${id}-cap`}
            aria-hidden="true"
            style={{
              position: 'absolute',
              top: '50%',
              left: lane * laneW + laneW / 2,
              transform: 'translate(-50%, -50%)',
              fontSize: 9,
              lineHeight: 1,
              color: connectorColor(id),
              pointerEvents: 'none',
            }}
          >
            {role === 'top' ? '▾' : '▴'}
          </span>
        ),
      )}
    </>
  );
}

/** Truncated, clickable span-id cell (Span + Parent columns share this). */
function SpanLink({
  spanId,
  onNavigate,
}: {
  spanId: string | null;
  onNavigate?: (spanId: string) => void;
}) {
  if (!spanId) return <>—</>;
  return (
    <Button
      variant="link"
      isInline
      onClick={() => onNavigate?.(spanId)}
      className="dg-mono"
    >
      {spanId.length > 16 ? `${spanId.slice(0, 16)}…` : spanId}
    </Button>
  );
}

/**
 * A collapsible request/response payload. Ported from the vanilla flow view's
 * Req/Resp cells + showPayload(): a link shows the hash's first 8 chars, and
 * expanding it lazily fetches `GET /api/payloads/{hash}` and renders the
 * decoded content plus kind/hash/bytes. Fetch is gated on `open` (usePayload
 * enabled only once expanded), so an unopened payload costs nothing.
 */
function PayloadView({ label, hash }: { label: string; hash: string }) {
  const [open, setOpen] = useState(false);
  const { data, isLoading, isError } = usePayload(open ? hash : null);
  return (
    <div style={{ marginTop: '0.25rem' }}>
      <Button
        variant="link"
        isInline
        onClick={() => setOpen((o) => !o)}
        className="dg-mono"
      >
        {open ? '▼' : '▶'} {label}: {hash.slice(0, 8)}
      </Button>
      {open && (
        <div style={{ marginTop: '0.25rem' }}>
          {isLoading ? (
            <Spinner size="md" aria-label={`Loading ${label} payload`} />
          ) : isError || !data ? (
            <div style={{ color: '#f85149', fontSize: '0.85rem' }}>
              Failed to load payload.
            </div>
          ) : (
            <>
              <DetailList
                pairs={[
                  ['kind', data.content_kind],
                  ['hash', data.content_hash],
                  ['bytes', String(data.byte_size)],
                ]}
              />
              <CodeBlock>
                <CodeBlockCode>
                  {data.content == null ? '(none)' : JSON.stringify(data.content, null, 2)}
                </CodeBlockCode>
              </CodeBlock>
              {/* The P-classification Classification verdict for this payload
                  (issue #80): sensitivity level, regulatory tags, identity
                  bundle, and the Findings. `null` renders as "not yet
                  classified" (the eventual-consistency window, ADR-0024),
                  distinct from a real PUBLIC / zero-Findings verdict. */}
              <div style={{ marginTop: '0.5rem' }}>
                <div style={{ fontWeight: 700, fontSize: '0.85rem' }}>Classification</div>
                <ClassificationView classification={data.classification} />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** Which flow row is selected, mirrored to/from the URL (?iid | ?eid). */
export interface FlowSelection {
  iid?: string;
  eid?: string;
}

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
   * Show MCP infrastructure interactions (lifecycle / tool discovery)? Default
   * false = hidden. The page owns the URL mirror (ADR-0021 `?showInfra=1`, the
   * non-default state) and passes the resolved flag down.
   */
  showInfra?: boolean;
  /** Fired when the inline show/hide-infrastructure affordance is clicked. */
  onShowInfraChange?: (show: boolean) => void;
  /**
   * Flat view (one row per leg) on? When the parent supplies the pair it owns
   * the state — the page mirrors it to the URL (ADR-0021 `?flat=1`, the
   * non-default state); without a handler the checkbox falls back to local
   * state (uncontrolled), which is what direct component tests use.
   */
  flatView?: boolean;
  /** Fired when the Flat view checkbox is toggled. */
  onFlatViewChange?: (flat: boolean) => void;
}

/**
 * The interaction-flow view: an Entities table and an Interactions table
 * derived from spans by the in-cluster processor (ADR-0013). Ports the vanilla
 * execution_flow_logic.js — depth indentation via the parent walk, pin dots
 * mirroring the tree's highlight store, and lazy span-evidence fetch + a detail
 * panel on row click.
 */
export function FlowTables({
  traceId,
  pins,
  onPinsChange,
  onNavigateToSpan,
  onRevealSpans,
  initialSelection,
  onSelectionChange,
  showInfra = false,
  onShowInfraChange,
  flatView: flatViewProp,
  onFlatViewChange,
}: FlowTablesProps) {
  const interactionsQ = useInteractions(traceId);
  const entitiesQ = useEntities(traceId);
  const [selection, setSelection] = useState<Selection | null>(null);
  // Flat view: ignore the parent/child tree and list each request/response leg
  // as its own row, ordered by the leg `seq` (the trace-wide sequence number).
  // Controlled by the page (URL-mirrored) when the prop pair is supplied, else
  // local state.
  const [flatViewLocal, setFlatViewLocal] = useState(false);
  const flatView = onFlatViewChange ? (flatViewProp ?? false) : flatViewLocal;
  const setFlatView = onFlatViewChange ?? setFlatViewLocal;
  // Monotonic click token: each row click bumps it, and a click's async
  // evidence fetch only commits its setState if it is still the latest click.
  // Guards the out-of-order race where a slow fetch resolves after a later
  // click and would otherwise overwrite the selection/highlight.
  const clickSeq = useRef(0);

  // Highlight state for a row: 'active' if it is the current selection, else
  // null. Only the latest-selected row (entity or interaction) is highlighted;
  // `selection` already tracks that single row across both kinds.
  const rowState = (kind: 'entity' | 'interaction', id: string): 'active' | null =>
    selection?.kind === kind && selection.id === id ? 'active' : null;

  const interactions = useMemo(() => interactionsQ.data ?? [], [interactionsQ.data]);
  const entities = useMemo(() => entitiesQ.data ?? [], [entitiesQ.data]);
  const entById = useMemo(() => {
    const m = new Map<string, Entity>();
    entities.forEach((e) => m.set(e.id, e));
    return m;
  }, [entities]);
  // Depths come from the FULL interaction list — filtering is display-only, so
  // the indentation of the rows that stay visible never shifts.
  const depthById = useMemo(() => computeInteractionDepths(interactions), [interactions]);
  // The rows the default filter hides: MCP infrastructure exchanges (lifecycle /
  // tool discovery), minus any that a visible row parents through — lifecycle
  // hops are leaves so that shouldn't happen, but a visible row must never
  // dangle from a hidden parent, so the walk unhides full ancestor chains.
  const infraHidden = useMemo(() => {
    const hidden = new Set(interactions.filter((ix) => isInfrastructure(ix)).map((ix) => ix.id));
    const ixById = new Map(interactions.map((ix) => [ix.id, ix]));
    for (const ix of interactions) {
      if (hidden.has(ix.id)) continue;
      const seen = new Set<string>();
      for (let pid = ix.parent_interaction_id; pid && hidden.has(pid) && !seen.has(pid); ) {
        hidden.delete(pid);
        seen.add(pid);
        pid = ixById.get(pid)?.parent_interaction_id ?? null;
      }
    }
    return hidden;
  }, [interactions]);
  const displayedInteractions = useMemo(
    () => (showInfra ? interactions : interactions.filter((ix) => !infraHidden.has(ix.id))),
    [interactions, infraHidden, showInfra],
  );
  // Flat rows: one entry per leg across the DISPLAYED interactions, ordered by
  // `seq`. Filtering whole interactions before the flatMap keeps the connector
  // pairing consistent — both legs of a hidden interaction vanish together, so
  // `flatConnectors` row indices always cover intact brackets. Each row carries
  // its parent interaction so a click still opens that interaction's detail
  // panel (legs have no selection of their own).
  const flatRows = useMemo(
    () =>
      displayedInteractions
        .flatMap((ix) => ix.legs.map((leg) => ({ ix, leg })))
        .sort((a, b) => a.leg.seq - b.leg.seq),
    [displayedInteractions],
  );
  // Request↔response pairing for the flat view's connector column. A request
  // leg and its response leg share the same `ix.id` (that is the pairing key),
  // but they sort by `seq` so they are frequently NOT adjacent — other
  // interactions' legs interleave between them. We map `ix.id` → the row
  // indices of its request and response within `flatRows`, then derive each
  // interaction's [top, bottom] index span. A row then knows, for every
  // interaction whose span covers it, whether it is that span's top edge
  // (request → half-line down + ↓), its bottom edge (response → half-line up +
  // ↑), or an in-between pass-through (full vertical line). Single-leg
  // interactions (response in flight) have only one index, so their span is a
  // single row with no partner and thus no line is drawn. `undefined` values
  // guard the (theoretical) all-response case where a request row is absent.
  const flatConnectors = useMemo(() => {
    const spans = new Map<string, { top: number; bottom: number }>();
    flatRows.forEach(({ ix }, i) => {
      const s = spans.get(ix.id);
      if (!s) spans.set(ix.id, { top: i, bottom: i });
      else s.bottom = i; // later index (legs already sorted by seq)
    });
    // Per row, the drawing role for each interaction whose span covers it.
    return flatRows.map((_row, i) =>
      [...spans.entries()]
        .filter(([, s]) => s.top !== s.bottom && i >= s.top && i <= s.bottom)
        .map(([id, s]) => ({
          id,
          role: i === s.top ? ('top' as const) : i === s.bottom ? ('bottom' as const) : ('through' as const),
        })),
    );
  }, [flatRows]);
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
        ...(ix.destination
          ? ([[
              'destination',
              (ix.destination.url ?? `${ix.destination.host ?? ''}${ix.destination.path ?? ''}`) +
                (ix.destination.internal == null ? '' : ix.destination.internal ? ' (internal)' : ' (external)'),
            ]] as Array<[string, string]>)
          : []),
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

  // Row highlight: `data-dg-selected="active"` drives the background tint via
  // global.css for the single selected row. The attribute is omitted when the
  // row isn't selected, so unselected rows keep the default table styling.
  function rowProps(kind: 'entity' | 'interaction', id: string) {
    return { 'data-dg-selected': rowState(kind, id) ?? undefined };
  }

  function pinDot(key: string) {
    const color = pinColor.get(key);
    if (!color) return null;
    return (
      <span
        aria-label="pinned"
        style={{ display: 'inline-block', width: 9, height: 9, borderRadius: '50%', background: color }}
      />
    );
  }

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
      {/* Tables container. When the detail panel is open it floats fixed on the
          right (see below), so reserve a right gutter here equal to the panel's
          width + a gap — the tables shrink out from under the float instead of
          being covered. Closed → no padding, tables reclaim full width. The
          panel is `width:30%, minWidth:320` at `right:1rem`, so the gutter uses
          the same max() and adds ~1rem gap on each side. */}
      <div
        style={
          selection
            ? { paddingRight: 'max(30%, 320px)', marginRight: '2rem' }
            : undefined
        }
      >
        <Title headingLevel="h3" size="md">
          Entities
        </Title>
        <Table aria-label="Entities" variant="compact">
          <Thead>
            <Tr>
              <Th>Kind</Th>
              <Th screenReaderText="Pinned" />
              <Th>Display name</Th>
              <Th>Detected from</Th>
            </Tr>
          </Thead>
          <Tbody>
            {entities.map((e) => (
              <Tr key={e.id} isClickable onRowClick={() => selectEntity(e)} {...rowProps('entity', e.id)}>
                <Td dataLabel="Kind">
                  <EntityPill entity={e} />
                </Td>
                <Td>{pinDot(`entity:${e.id}`)}</Td>
                <Td dataLabel="Display name">{e.display_name}</Td>
                <Td dataLabel="Detected from" style={{ color: '#888' }}>
                  {e.detected_from}
                </Td>
              </Tr>
            ))}
          </Tbody>
        </Table>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginTop: '1rem',
          }}
        >
          <Title headingLevel="h3" size="md">
            Interactions
          </Title>
          <Checkbox
            id="flow-flat-view"
            label="Flat view"
            isChecked={flatView}
            onChange={(_e, checked) => setFlatView(checked)}
          />
        </div>
        {/* Infrastructure filter affordance: default-hidden MCP plumbing rows,
            with the count and a one-click toggle (mirrored to ?showInfra=1 by
            the parent). Absent entirely when the trace has no infra rows.
            Applies to both views — the flat rows are built from the same
            displayedInteractions. */}
        {infraHidden.size > 0 && (
          <div style={{ margin: '0.25rem 0' }}>
            <Button variant="link" isInline onClick={() => onShowInfraChange?.(!showInfra)}>
              {showInfra
                ? `Hide ${infraHidden.size} infrastructure ${infraHidden.size === 1 ? 'interaction' : 'interactions'}`
                : `${infraHidden.size} infrastructure ${infraHidden.size === 1 ? 'interaction' : 'interactions'} hidden — show`}
            </Button>
          </div>
        )}
        {flatView ? (
          // Flat view: one row per request/response leg, ordered by `seq`,
          // ignoring the parent/child tree (no depth indentation).
          <Table aria-label="Interactions (flat)" variant="compact">
            <Thead>
              <Tr>
                <Th>Seq</Th>
                <Th>Time</Th>
                <Th screenReaderText="Pinned" />
                <Th screenReaderText="Request/response link" />
                <Th>Leg</Th>
                <Th>Caller</Th>
                <Th>Callee</Th>
                <Th>Status</Th>
              </Tr>
            </Thead>
            <Tbody>
              {flatRows.map(({ ix, leg }, i) => {
                const from = ix.caller_entity_id ? entById.get(ix.caller_entity_id) : undefined;
                const to = ix.callee_entity_id ? entById.get(ix.callee_entity_id) : undefined;
                // A response flows callee → caller, so swap for the response leg (ADR-0025).
                const caller = leg.leg_type === 'response' ? to : from;
                const callee = leg.leg_type === 'response' ? from : to;
                return (
                  <Tr
                    key={`${ix.id}-${leg.leg_type}`}
                    isClickable
                    onRowClick={() => selectInteraction(ix)}
                    {...rowProps('interaction', ix.id)}
                  >
                    <Td dataLabel="Seq" className="dg-mono">
                      {leg.seq}
                    </Td>
                    <Td dataLabel="Time" className="dg-mono">
                      {leg.occurred_at ? formatTime24Utc(leg.occurred_at) : ''}
                    </Td>
                    <Td>{pinDot(`interaction:${ix.id}`)}</Td>
                    <Td
                      // The request↔response connector for this row, sitting just
                      // left of the Leg column (empty when its interaction has no
                      // partner leg present). `position: relative` lets the
                      // connector's full-height SVG fill the row's TRUE height via
                      // `inset: 0`, so the line is continuous across rows.
                      style={{ padding: 0, width: 1, position: 'relative' }}
                    >
                      <ConnectorCell roles={flatConnectors[i]} />
                    </Td>
                    <Td dataLabel="Leg">{leg.leg_type}</Td>
                    <Td dataLabel="Caller">
                      {caller ? (
                        <>
                          <EntityPill entity={caller} /> {caller.display_name}
                        </>
                      ) : (
                        '?'
                      )}
                    </Td>
                    <Td dataLabel="Callee">
                      {callee ? (
                        <>
                          <EntityPill entity={callee} /> {callee.display_name}
                        </>
                      ) : (
                        '?'
                      )}
                    </Td>
                    <Td dataLabel="Status">
                      {leg.error === true ? (
                        <span style={{ color: '#f85149' }}>ERROR</span>
                      ) : leg.error === false ? (
                        <span style={{ color: '#6acf6a' }}>ok</span>
                      ) : (
                        '—'
                      )}
                    </Td>
                  </Tr>
                );
              })}
            </Tbody>
          </Table>
        ) : (
        <Table aria-label="Interactions" variant="compact">
          <Thead>
            <Tr>
              <Th>Started</Th>
              <Th screenReaderText="Pinned" />
              <Th>Caller</Th>
              <Th>Callee</Th>
              <Th>Status</Th>
              <Th>Spans</Th>
            </Tr>
          </Thead>
          <Tbody>
            {displayedInteractions.map((ix) => {
              const depth = depthById.get(ix.id) ?? 0;
              const caller = ix.caller_entity_id ? entById.get(ix.caller_entity_id) : undefined;
              const callee = ix.callee_entity_id ? entById.get(ix.callee_entity_id) : undefined;
              return (
                <Tr key={ix.id} isClickable onRowClick={() => selectInteraction(ix)} {...rowProps('interaction', ix.id)}>
                  <Td dataLabel="Started" className="dg-mono">
                    {requestOccurredAt(ix) ? formatTime24Utc(requestOccurredAt(ix)!) : ''}
                  </Td>
                  <Td>{pinDot(`interaction:${ix.id}`)}</Td>
                  <Td dataLabel="Caller">
                    {depth > 0 && (
                      <span className="dg-mono" style={{ color: '#555' }}>
                        {'│ '.repeat(depth - 1)}
                        └─{' '}
                      </span>
                    )}
                    {caller ? (
                      <>
                        <EntityPill entity={caller} /> {caller.display_name}
                      </>
                    ) : (
                      '?'
                    )}
                  </Td>
                  <Td dataLabel="Callee">
                    {callee ? (
                      <>
                        <EntityPill entity={callee} /> {callee.display_name}
                      </>
                    ) : (
                      '?'
                    )}
                  </Td>
                  <Td dataLabel="Status">
                    {ix.any_error === true ? (
                      <span style={{ color: '#f85149' }}>ERROR</span>
                    ) : ix.any_error === false ? (
                      <span style={{ color: '#6acf6a' }}>ok</span>
                    ) : (
                      '—'
                    )}
                  </Td>
                  <Td dataLabel="Spans" style={{ color: '#888' }}>
                    {ix.span_count} ({ix.anchor_count} anchor)
                  </Td>
                </Tr>
              );
            })}
          </Tbody>
        </Table>
        )}
      </div>

      {/* The detail panel floats as a fixed overlay on the right of the
          viewport instead of occupying a layout column, so the tables use the
          full width. Only rendered when something is selected — an empty float
          is just clutter — and dismissable via the caption's × close button. */}
      {selection && (
        <div
          style={{
            position: 'fixed',
            // Sit just below the app masthead rather than the viewport top so
            // the panel doesn't tuck under the header. Tracks the real header
            // height via PatternFly's CSS var, with a sensible fallback.
            top: 'calc(var(--pf-v5-c-page__header--MinHeight, 4.75rem) + 1rem)',
            right: '1rem',
            width: '30%',
            minWidth: 320,
            maxHeight: 'calc(100vh - var(--pf-v5-c-page__header--MinHeight, 4.75rem) - 2rem)',
            overflowY: 'auto',
            background: '#1b1b1b',
            border: '1px solid #444',
            borderRadius: 4,
            boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5)',
            padding: '1rem',
            zIndex: 100,
          }}
        >
            {/* Caption row: the selection's own name ('Entity'/'Interaction')
                on the left — folding in what used to be a separate leading
                section header — with the pin toggle glued to the right, matching
                SpanDetailPanel's Refresh layout. */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                // Keep a gap between caption and button so they never butt
                // together when the narrow (30%) detail column squeezes the row.
                gap: '0.5rem',
                // A little breathing room between the caption and the first
                // field below (e.g. 'Interaction' → 'summary').
                marginBottom: '0.5rem',
              }}
            >
              <Title headingLevel="h3" size="md">
                {selection.sectionTitle}
              </Title>
              {/* Pin toggle + a × to dismiss the floating panel, kept together
                  on the right; both refuse to shrink below their labels. */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexShrink: 0 }}>
                <Button
                  variant="secondary"
                  isInline
                  onClick={togglePin}
                  // A swatch of the highlight color: the current color once
                  // pinned, else a preview of the next-free color the pin would
                  // take.
                  icon={
                    <span
                      data-testid="highlight-swatch"
                      aria-hidden="true"
                      style={{
                        display: 'inline-block',
                        width: 10,
                        height: 10,
                        borderRadius: 2,
                        border: '1px solid rgba(0, 0, 0, 0.35)',
                        // Extra gap beyond PF's default icon spacing so the color
                        // chip doesn't crowd the label text.
                        marginRight: '0.375rem',
                        background:
                          pins.slotColorFor(selection.pinKey) ?? pins.nextFreeColor(),
                      }}
                    />
                  }
                >
                  {pins.isPinned(selection.pinKey) ? 'Unpin' : 'Add to highlights'}
                </Button>
                <Button
                  variant="plain"
                  aria-label="Close details"
                  onClick={() => {
                    setSelection(null);
                    onSelectionChange?.(null);
                  }}
                  style={{ color: '#888', fontSize: '1.1rem', lineHeight: 1, padding: 0 }}
                >
                  ×
                </Button>
              </div>
            </div>
            <DetailList pairs={selection.fields} />

            {(selection.requestPayloadHash || selection.responsePayloadHash) && (
              <>
                <Title headingLevel="h4" size="md" style={{ marginTop: '0.75rem' }}>
                  Payloads
                </Title>
                {selection.requestPayloadHash && (
                  <PayloadView label="Request" hash={selection.requestPayloadHash} />
                )}
                {selection.responsePayloadHash && (
                  <PayloadView label="Response" hash={selection.responsePayloadHash} />
                )}
              </>
            )}

            <Title headingLevel="h4" size="md" style={{ marginTop: '0.75rem' }}>
              Spans
            </Title>
            <Table aria-label="Span evidence" variant="compact">
              <Thead>
                <Tr>
                  <Th>Role</Th>
                  <Th>Span</Th>
                  <Th>Parent</Th>
                  <Th>Kind</Th>
                  <Th>Service</Th>
                </Tr>
              </Thead>
              <Tbody>
                {selection.evidence.map((ev, i) => (
                  <Tr key={`${ev.span_id}-${i}`}>
                    <Td dataLabel="Role"><RoleIcon role={ev.role} /></Td>
                    <Td dataLabel="Span">
                      <SpanLink spanId={ev.span_id} onNavigate={onNavigateToSpan} />
                    </Td>
                    <Td dataLabel="Parent">
                      <SpanLink spanId={ev.parent_id} onNavigate={onNavigateToSpan} />
                    </Td>
                    <Td dataLabel="Kind">{ev.kind ?? '—'}</Td>
                    <Td dataLabel="Service">{ev.service_name ?? '—'}</Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
        </div>
      )}
    </div>
  );
}
