import { useMemo, useState } from 'react';
import {
  Title,
  Spinner,
  EmptyState,
  EmptyStateBody,
  EmptyStateHeader,
  Split,
  SplitItem,
  DescriptionList,
  DescriptionListGroup,
  DescriptionListTerm,
  DescriptionListDescription,
  Button,
} from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';

import { useInteractions, useEntities } from '../api/hooks';
import { fetchJson } from '../api/client';
import { computeInteractionDepths, durationMs } from '../lib/flow';
import type { PinStore } from '../lib/pins';
import { EntityPill } from './EntityPill';
import type { Entity, Interaction, SpanEvidence } from '../types';

interface Selection {
  kind: 'interaction' | 'entity';
  id: string;
  title: string;
  fields: Array<[string, string]>;
  evidence: SpanEvidence[];
  pinKey: string;
  pinLabel: string;
}

export interface FlowTablesProps {
  traceId: string;
  pins: PinStore;
  onPinsChange: () => void;
  /** Navigate to a span in the tree view (row's span-link). */
  onNavigateToSpan?: (spanId: string) => void;
}

/**
 * The interaction-flow view: an Entities table and an Interactions table
 * derived from spans by the in-cluster processor (ADR-0013). Ports the vanilla
 * execution_flow_logic.js — depth indentation via the parent walk, pin dots
 * mirroring the tree's highlight store, and lazy span-evidence fetch + a detail
 * panel on row click.
 */
export function FlowTables({ traceId, pins, onPinsChange, onNavigateToSpan }: FlowTablesProps) {
  const interactionsQ = useInteractions(traceId);
  const entitiesQ = useEntities(traceId);
  const [selection, setSelection] = useState<Selection | null>(null);

  const interactions = useMemo(() => interactionsQ.data ?? [], [interactionsQ.data]);
  const entities = useMemo(() => entitiesQ.data ?? [], [entitiesQ.data]);
  const entById = useMemo(() => {
    const m = new Map<string, Entity>();
    entities.forEach((e) => m.set(e.id, e));
    return m;
  }, [entities]);
  const depthById = useMemo(() => computeInteractionDepths(interactions), [interactions]);
  const pinColor = useMemo(() => {
    const m = new Map<string, string>();
    pins.getPins().forEach((p) => m.set(p.key, p.color));
    return m;
  }, [pins]);

  const isLoading = interactionsQ.isLoading || entitiesQ.isLoading;
  const isEmpty = interactions.length === 0 && entities.length === 0;

  async function selectInteraction(ix: Interaction) {
    const evidence = await fetchJson<{ spans: SpanEvidence[] }>(
      `/traces/${traceId}/interactions/${ix.id}/spans`,
    )
      .then((r) => r.spans)
      .catch(() => []);
    const dur = durationMs(ix.started_at, ix.ended_at);
    setSelection({
      kind: 'interaction',
      id: ix.id,
      title: 'Interaction details',
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
    });
  }

  async function selectEntity(e: Entity) {
    const evidence = await fetchJson<{ spans: SpanEvidence[] }>(
      `/traces/${traceId}/entities/${e.id}/spans`,
    )
      .then((r) => r.spans)
      .catch(() => []);
    setSelection({
      kind: 'entity',
      id: e.id,
      title: 'Entity details',
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
    });
  }

  function togglePin() {
    if (!selection) return;
    if (pins.isPinned(selection.pinKey)) {
      pins.removePin(selection.pinKey);
    } else {
      pins.addPin({
        key: selection.pinKey,
        label: selection.pinLabel,
        spanIds: selection.evidence.map((e) => e.span_id).filter(Boolean),
      });
    }
    onPinsChange();
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
              <Tr key={e.id} isClickable onRowClick={() => selectEntity(e)}>
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
                <Tr key={ix.id} isClickable onRowClick={() => selectInteraction(ix)}>
                  <Td dataLabel="Started" className="dg-mono">
                    {ix.started_at ? new Date(ix.started_at).toISOString().slice(11, 19) : ''}
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

      {selection && (
        <SplitItem style={{ minWidth: 320 }}>
          <Title headingLevel="h3" size="md">
            {selection.title}
          </Title>
          <DescriptionList isCompact isHorizontal>
            {selection.fields.map(([k, v]) => (
              <DescriptionListGroup key={k}>
                <DescriptionListTerm>{k}</DescriptionListTerm>
                <DescriptionListDescription className="dg-mono">{v}</DescriptionListDescription>
              </DescriptionListGroup>
            ))}
          </DescriptionList>

          <Button variant="secondary" isInline onClick={togglePin} style={{ marginTop: '0.5rem' }}>
            {pins.isPinned(selection.pinKey) ? 'Unpin' : 'Add to highlights'}
          </Button>

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
                  <Td dataLabel="Role">{ev.role}</Td>
                  <Td dataLabel="Span">
                    {ev.span_id ? (
                      <Button
                        variant="link"
                        isInline
                        onClick={() => onNavigateToSpan?.(ev.span_id)}
                        className="dg-mono"
                      >
                        {ev.span_id.length > 16 ? `${ev.span_id.slice(0, 16)}…` : ev.span_id}
                      </Button>
                    ) : (
                      '—'
                    )}
                  </Td>
                  <Td dataLabel="Parent" className="dg-mono">
                    {ev.parent_id ?? '—'}
                  </Td>
                  <Td dataLabel="Kind">{ev.kind ?? '—'}</Td>
                  <Td dataLabel="Service">{ev.service_name ?? '—'}</Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        </SplitItem>
      )}
    </Split>
  );
}
