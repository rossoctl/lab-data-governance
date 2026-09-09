import { Link } from 'react-router-dom';
import { Card, CardTitle, CardHeader, CardBody, EmptyState, EmptyStateBody } from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';
import { RiskBadge } from '../../../risk-components/RiskBadge';
import type { TopRuleItem } from '../../../risk-api/types';

interface TopRulesCardProps {
  items: TopRuleItem[];
}

/**
 * FR-DAS-051 top-rules card (issue #169). `rule_name` and `risk_level` are
 * nullable on the wire (`aggregate.get_top_rules`'s docstring: a rule id
 * absent from the catalog is still counted) — a null name falls back to the
 * rule id so the row stays identifiable, and a null risk_level still
 * renders a row with `RiskBadge`'s own 'unknown' fallback colour.
 */
export function TopRulesCard({ items }: TopRulesCardProps) {
  return (
    <Card isCompact>
      <CardHeader
        actions={{
          actions: (
            <Link to="/risk/rules" className="pf-v5-c-button pf-m-link pf-m-inline">
              All Rules
            </Link>
          ),
        }}
      >
        <CardTitle>Top rules</CardTitle>
      </CardHeader>
      <CardBody>
        {items.length === 0 ? (
          <EmptyState variant="xs">
            <EmptyStateBody>No rules triggered in this window.</EmptyStateBody>
          </EmptyState>
        ) : (
          <Table aria-label="Top rules" variant="compact">
            <Thead>
              <Tr>
                <Th>Rule</Th>
                <Th>Traces</Th>
                <Th>Risk level</Th>
              </Tr>
            </Thead>
            <Tbody>
              {items.map((item) => (
                <Tr key={item.rule_id}>
                  <Td dataLabel="Rule">
                    <Link to={`/risk/rules/${item.rule_id}`}>{item.rule_name ?? item.rule_id}</Link>
                  </Td>
                  <Td dataLabel="Traces">{item.trace_count}</Td>
                  <Td dataLabel="Risk level">
                    <RiskBadge level={item.risk_level ?? 'unknown'} />
                  </Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        )}
      </CardBody>
    </Card>
  );
}
