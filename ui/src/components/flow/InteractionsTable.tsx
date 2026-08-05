import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';

import { requestOccurredAt } from '../../lib/flow';
import { formatTime24Utc } from '../../lib/recentTraces';
import type { Entity, Interaction } from '../../types';
import { EntityCell } from './EntityCell';
import { PinDot } from './PinDot';
import { rowProps } from './rowChrome';
import { StatusText } from './StatusText';

/**
 * The **Interactions** table's tree variant: one row per interaction, indented
 * by its depth in the `parent_interaction_id` tree (`computeInteractionDepths`,
 * which the caller supplies). Ports the vanilla execution_flow_logic.js's
 * `│ `/`└─ ` guide glyphs.
 */
export function InteractionsTable({
  interactions,
  entById,
  depthById,
  selectedId,
  pinColor,
  onSelect,
}: {
  interactions: readonly Interaction[];
  entById: Map<string, Entity>;
  depthById: Map<string, number>;
  /** The selected interaction's id, or null when the selection is elsewhere. */
  selectedId: string | null;
  pinColor: Map<string, string>;
  onSelect: (ix: Interaction) => void;
}) {
  return (
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
            <Tr key={ix.id} isClickable onRowClick={() => onSelect(ix)} {...rowProps(selectedId === ix.id)}>
              <Td dataLabel="Started" className="dg-mono">
                {requestOccurredAt(ix) ? formatTime24Utc(requestOccurredAt(ix)!) : ''}
              </Td>
              <Td>
                <PinDot color={pinColor.get(`interaction:${ix.id}`)} />
              </Td>
              <Td dataLabel="Caller">
                {depth > 0 && (
                  <span className="dg-mono" style={{ color: 'var(--dg-tree-guide)' }}>
                    {'│ '.repeat(depth - 1)}
                    └─{' '}
                  </span>
                )}
                <EntityCell entity={caller} />
              </Td>
              <Td dataLabel="Callee">
                <EntityCell entity={callee} />
              </Td>
              <Td dataLabel="Status">
                <StatusText error={ix.any_error} />
              </Td>
              <Td dataLabel="Spans" style={{ color: 'var(--dg-color-muted)' }}>
                {ix.span_count} ({ix.anchor_count} anchor)
              </Td>
            </Tr>
          );
        })}
      </Tbody>
    </Table>
  );
}
