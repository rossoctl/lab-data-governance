import { Label } from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';
import type { RiskAlert } from '../risk-api/types';
import { RiskBadge } from './RiskBadge';

/**
 * A trace's alerts, oldest first: the supersession chain the alerts processor
 * wrote (issue #104), read-only. The one alert not superseded is the trace's
 * current alert ("current"); every other row names the alert that superseded
 * it by its position in this list. `duplicate_count` counts the
 * recomputations that did not raise the level. `truncated` says the list is
 * the newest page only, so the oldest alerts are not shown.
 */
export function TraceAlertsPanel({ alerts, truncated = false }: { alerts: RiskAlert[]; truncated?: boolean }) {
  const position = new Map(alerts.map((a, i) => [a.alert_id, i + 1]));
  return (
    <>
      {truncated && <p className="pf-v5-u-mb-sm">The oldest alerts of this trace are not shown.</p>}
      <Table aria-label="Trace alerts" variant="compact">
        <Thead>
          <Tr>
            <Th>#</Th>
            <Th>Level</Th>
            <Th>Title</Th>
            <Th>Description</Th>
            <Th>Status</Th>
            <Th>Duplicates</Th>
            <Th>Superseded by</Th>
            <Th>Raised</Th>
          </Tr>
        </Thead>
        <Tbody>
          {alerts.map((a, i) => (
            <Tr key={a.alert_id}>
              <Td dataLabel="#">{i + 1}</Td>
              <Td dataLabel="Level">
                <RiskBadge level={a.risk_level} />
              </Td>
              <Td dataLabel="Title">{a.title}</Td>
              <Td dataLabel="Description">{a.description}</Td>
              <Td dataLabel="Status">{a.status}</Td>
              <Td dataLabel="Duplicates">{a.duplicate_count}</Td>
              <Td dataLabel="Superseded by">
                {a.superseded_by == null ? (
                  <Label isCompact color="blue">
                    current
                  </Label>
                ) : (
                  `#${position.get(a.superseded_by) ?? '?'}`
                )}
              </Td>
              <Td dataLabel="Raised" className="dg-mono">
                {new Date(a.timestamp).toLocaleString()}
              </Td>
            </Tr>
          ))}
        </Tbody>
      </Table>
    </>
  );
}
