import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';

import type { FlatRow } from '../../lib/flow';
import { formatTime24Utc } from '../../lib/recentTraces';
import type { Entity, Interaction } from '../../types';
import { ConnectorCell } from './ConnectorCell';
import type { ConnectorRole } from './connectorColor';
import { EntityCell } from './EntityCell';
import { PinDot } from './PinDot';
import { rowProps } from './rowChrome';
import { StatusText } from './StatusText';

/**
 * The **Interactions** table's flat variant: one row per request/response leg,
 * ordered by the trace-wide leg `seq`, ignoring the parent/child tree (so no
 * depth indentation). A row click still selects the leg's parent *interaction* —
 * legs have no selection of their own — and the connector column draws the
 * bracket tying a request row to its (usually non-adjacent) response row.
 */
export function FlatLegsTable({
  rows,
  connectors,
  entById,
  selectedId,
  pinColor,
  onSelect,
}: {
  rows: readonly FlatRow[];
  /** Per-row connector drawing roles, index-aligned with `rows`. */
  connectors: ConnectorRole[][];
  entById: Map<string, Entity>;
  selectedId: string | null;
  pinColor: Map<string, string>;
  onSelect: (ix: Interaction) => void;
}) {
  return (
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
        {rows.map(({ ix, leg }, i) => {
          const from = ix.caller_entity_id ? entById.get(ix.caller_entity_id) : undefined;
          const to = ix.callee_entity_id ? entById.get(ix.callee_entity_id) : undefined;
          // A response flows callee → caller, so swap for the response leg (ADR-0025).
          const caller = leg.leg_type === 'response' ? to : from;
          const callee = leg.leg_type === 'response' ? from : to;
          return (
            <Tr
              key={`${ix.id}-${leg.leg_type}`}
              isClickable
              onRowClick={() => onSelect(ix)}
              {...rowProps(selectedId === ix.id)}
            >
              <Td dataLabel="Seq" className="dg-mono">
                {leg.seq}
              </Td>
              <Td dataLabel="Time" className="dg-mono">
                {leg.occurred_at ? formatTime24Utc(leg.occurred_at) : ''}
              </Td>
              <Td>
                <PinDot color={pinColor.get(`interaction:${ix.id}`)} />
              </Td>
              <Td
                // The request↔response connector for this row, sitting just
                // left of the Leg column (empty when its interaction has no
                // partner leg present). `position: relative` lets the
                // connector's full-height SVG fill the row's TRUE height via
                // `inset: 0`, so the line is continuous across rows.
                style={{ padding: 0, width: 1, position: 'relative' }}
              >
                <ConnectorCell roles={connectors[i]} />
              </Td>
              <Td dataLabel="Leg">{leg.leg_type}</Td>
              <Td dataLabel="Caller">
                <EntityCell entity={caller} />
              </Td>
              <Td dataLabel="Callee">
                <EntityCell entity={callee} />
              </Td>
              <Td dataLabel="Status">
                <StatusText error={leg.error} />
              </Td>
            </Tr>
          );
        })}
      </Tbody>
    </Table>
  );
}
