import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';

import type { Entity } from '../../types';
import { EntityPill } from '../EntityPill';
import { PinDot } from './PinDot';
import { rowProps } from './rowChrome';

/** The derived **Entities** of the trace (ADR-0013), one row each. */
export function EntitiesTable({
  entities,
  selectedId,
  pinColor,
  onSelect,
}: {
  entities: readonly Entity[];
  /** The selected entity's id, or null when the selection is elsewhere. */
  selectedId: string | null;
  /** pinKey → highlight-set color, for the pin dot. */
  pinColor: Map<string, string>;
  onSelect: (entity: Entity) => void;
}) {
  return (
    <Table aria-label="Entities" variant="compact">
      <Thead>
        <Tr>
          <Th>Kind</Th>
          <Th screenReaderText="Pinned" />
          <Th>Display name</Th>
          <Th>Namespace</Th>
          <Th>Detected from</Th>
        </Tr>
      </Thead>
      <Tbody>
        {entities.map((e) => (
          <Tr key={e.id} isClickable onRowClick={() => onSelect(e)} {...rowProps(selectedId === e.id)}>
            <Td dataLabel="Kind">
              <EntityPill entity={e} />
            </Td>
            <Td>
              <PinDot color={pinColor.get(`entity:${e.id}`)} />
            </Td>
            <Td dataLabel="Display name">{e.display_name}</Td>
            <Td dataLabel="Namespace">{e.namespace ?? ''}</Td>
            <Td dataLabel="Detected from" style={{ color: 'var(--dg-color-muted)' }}>
              {e.detected_from}
            </Td>
          </Tr>
        ))}
      </Tbody>
    </Table>
  );
}
