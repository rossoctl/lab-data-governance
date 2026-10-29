import { Card, CardTitle, CardBody, EmptyState, EmptyStateBody } from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';
import type { CategoryCount } from '../../../risk-api/types';

interface RiskByCategoryCardProps {
  items: CategoryCount[];
}

/**
 * FR-DAS-054 risk-by-category card (issue #169).
 *
 * Labelled **"Policy events"**, not "Affected workflows": the shipped
 * `aggregate.get_risk_by_category` counts rule-firing occurrences (one
 * increment per triggered rule per category), not distinct affected
 * workflows — its own `CategoryCount` docstring says "policy event count
 * for one rule category" (FR-DAS-054). #169 asked for an affected-workflow
 * count (FR-DAS-054a), which the backend does not currently compute; rather
 * than fake that number client-side, this card states what the API actually
 * returns. See the PR for this issue's divergence note.
 */
export function RiskByCategoryCard({ items }: RiskByCategoryCardProps) {
  return (
    <Card isCompact>
      <CardTitle>Risk by category</CardTitle>
      <CardBody>
        {items.length === 0 ? (
          <EmptyState variant="xs">
            <EmptyStateBody>No policy events in this window.</EmptyStateBody>
          </EmptyState>
        ) : (
          <Table aria-label="Risk by category" variant="compact">
            <Thead>
              <Tr>
                <Th>Category</Th>
                <Th>Policy events</Th>
              </Tr>
            </Thead>
            <Tbody>
              {items.map((item) => (
                <Tr key={item.category}>
                  <Td dataLabel="Category">{item.category}</Td>
                  <Td dataLabel="Policy events">{item.count}</Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        )}
      </CardBody>
    </Card>
  );
}
