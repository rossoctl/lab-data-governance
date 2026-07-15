import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Title,
  Spinner,
  EmptyState,
  EmptyStateBody,
  EmptyStateHeader,
  Split,
  SplitItem,
  Button,
  CodeBlock,
  CodeBlockCode,
} from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';

import { useInteractions, useEntities, usePayload } from '../api/hooks';
import { fetchJson } from '../api/client';
import { computeInteractionDepths, durationMs } from '../lib/flow';
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
  /** Leading section header inside the panel ('Entity' | 'Interaction'). */
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
}: FlowTablesProps) {
  const interactionsQ = useInteractions(traceId);
  const entitiesQ = useEntities(traceId);
  const [selection, setSelection] = useState<Selection | null>(null);
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
  const depthById = useMemo(() => computeInteractionDepths(interactions), [interactions]);
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
    const dur = durationMs(ix.started_at, ix.ended_at);
    setSelection({
      kind: 'interaction',
      id: ix.id,
      sectionTitle: 'Interaction',
      fields: [
        ['summary', ix.summary ?? '—'],
        ['interaction_id', ix.id],
        ['anchor span(s)', evidence.filter((e) => e.role === 'anchor').map((e) => e.span_id).join(', ') || '—'],
        ['evidence spans', String(evidence.length)],
        ['started_at', ix.started_at ?? '—'],
        ['ended_at', ix.ended_at ?? '—'],
        ...(dur ? ([['duration', `${dur} ms`]] as Array<[string, string]>) : []),
      ],
      evidence,
      pinKey: `interaction:${ix.id}`,
      pinLabel: ix.summary || ix.id,
      requestPayloadHash: ix.request_payload_hash,
      responsePayloadHash: ix.response_payload_hash,
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
    <Split hasGutter>
      <SplitItem isFilled>
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

        <Title headingLevel="h3" size="md" style={{ marginTop: '1rem' }}>
          Interactions
        </Title>
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
            {interactions.map((ix) => {
              const depth = depthById.get(ix.id) ?? 0;
              const caller = ix.caller_entity_id ? entById.get(ix.caller_entity_id) : undefined;
              const callee = ix.callee_entity_id ? entById.get(ix.callee_entity_id) : undefined;
              return (
                <Tr key={ix.id} isClickable onRowClick={() => selectInteraction(ix)} {...rowProps('interaction', ix.id)}>
                  <Td dataLabel="Started" className="dg-mono">
                    {ix.started_at ? formatTime24Utc(ix.started_at) : ''}
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
                    {ix.error === true ? (
                      <span style={{ color: '#f85149' }}>ERROR</span>
                    ) : ix.error === false ? (
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
      </SplitItem>

      {/* The detail panel is always present (fixed column); a placeholder
          stands in before any row is selected. */}
      <SplitItem style={{ flex: '0 0 30%', minWidth: 0 }}>
        {!selection ? (
          // Nothing selected yet: the generic 'Details' caption stands in — no
          // target to name, and no pin action to offer.
          <>
            <Title headingLevel="h3" size="md">
              Details
            </Title>
            <div style={{ color: '#888', fontStyle: 'italic', marginTop: '0.75rem' }}>
              Select an entity or interaction to view its details.
            </div>
          </>
        ) : (
          <>
            {/* Caption row: the selection's own name ('Entity'/'Interaction')
                on the left — folding in what used to be a separate leading
                section header — with the pin toggle glued to the right, matching
                SpanDetailPanel's Refresh layout. */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                // A little breathing room between the caption and the first
                // field below (e.g. 'Interaction' → 'summary').
                marginBottom: '0.5rem',
              }}
            >
              <Title headingLevel="h3" size="md">
                {selection.sectionTitle}
              </Title>
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
          </>
        )}
      </SplitItem>
    </Split>
  );
}
