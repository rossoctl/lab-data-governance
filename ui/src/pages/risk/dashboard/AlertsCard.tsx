/**
 * Alerts/incidents card (issue #169's FR-DAS-050c). Sourced from
 * `GET /risk/traces` rather than a dedicated `/risk/alerts` endpoint:
 * `/risk/alerts` does not exist yet — backend #110 (alert endpoints) is
 * still open, as are #104/#105 (alert generation/supersession), so the
 * `alerts` table has no writer. #169 anticipated exactly this case
 * ("confirm against the live API; if neither cleanly supports
 * group-by-workflow server-side, group client-side"): this card groups
 * `/risk/traces` records by `trace_id` via `groupTraceRisksByWorkflow`
 * instead. Swapping the data source once #110 lands is a follow-up — see
 * the PR for this issue.
 *
 * The Trace column is visually truncated (this card lives in a half-width
 * pane, and trace ids are long, opaque strings not meant to be read at a
 * glance) — the full id is still available via the cell's `title` attribute
 * (native hover tooltip) and is spelled out in full in the expanded group's
 * detail row, so nothing is permanently hidden, only deferred to a click or
 * hover.
 */
import { Fragment, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Card,
  CardTitle,
  CardBody,
  EmptyState,
  EmptyStateBody,
  Button,
} from '@patternfly/react-core';
import { AngleRightIcon, AngleDownIcon } from '@patternfly/react-icons';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';
import { RiskBadge } from '../../../risk-components/RiskBadge';
import { EnforcementChip } from '../../../risk-components/EnforcementChip';
import { CursorPagination } from '../../../risk-components/CursorPagination';
import { groupTraceRisksByWorkflow } from '../../../lib/riskAlertGroups';
import type { TraceRiskRecord } from '../../../risk-api/types';

interface AlertsCardProps {
  items: TraceRiskRecord[];
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onNextPage: () => void;
}

export function AlertsCard({ items, hasNextPage, isFetchingNextPage, onNextPage }: AlertsCardProps) {
  const groups = groupTraceRisksByWorkflow(items);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function toggle(traceId: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(traceId)) next.delete(traceId);
      else next.add(traceId);
      return next;
    });
  }

  return (
    <Card isCompact>
      <CardTitle>Alerts</CardTitle>
      <CardBody>
        {groups.length === 0 ? (
          <EmptyState variant="xs">
            <EmptyStateBody>No incidents in this window.</EmptyStateBody>
          </EmptyState>
        ) : (
          <>
            <div style={{ overflowX: 'auto' }}>
              <Table aria-label="Alerts" variant="compact">
                <Thead>
                  <Tr>
                    <Th screenReaderText="Expand" />
                    <Th>Trace</Th>
                    <Th>Risk level</Th>
                    <Th>Enforcement</Th>
                    <Th>Interactions</Th>
                    <Th>Policy events</Th>
                    <Th>Open</Th>
                  </Tr>
                </Thead>
                <Tbody>
                  {groups.map((group) => {
                    const isExpanded = expanded.has(group.traceId);
                    return (
                      <Fragment key={group.traceId}>
                        <Tr>
                          <Td dataLabel="Expand">
                            <Button
                              variant="plain"
                              aria-label={`${isExpanded ? 'Collapse' : 'Expand'} ${group.traceId}`}
                              onClick={() => toggle(group.traceId)}
                            >
                              {isExpanded ? <AngleDownIcon /> : <AngleRightIcon />}
                            </Button>
                          </Td>
                          <Td dataLabel="Trace">
                            <span
                              title={group.traceId}
                              style={{
                                display: 'inline-block',
                                maxWidth: '10ch',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                                verticalAlign: 'bottom',
                              }}
                            >
                              {group.traceId}
                            </span>
                          </Td>
                          <Td dataLabel="Risk level">
                            <RiskBadge level={group.summary.trace_risk_level} />
                          </Td>
                          <Td dataLabel="Enforcement">
                            <EnforcementChip type={group.summary.trace_enforcement_type ?? 'none'} />
                          </Td>
                          <Td dataLabel="Interactions">{group.summary.interaction_count}</Td>
                          <Td dataLabel="Policy events">{group.summary.policy_event_count}</Td>
                          <Td dataLabel="Open">
                            <Link to={`/risk/traces/${group.traceId}`}>Open</Link>
                          </Td>
                        </Tr>
                        {isExpanded && (
                          <Tr>
                            <Td dataLabel="Details" colSpan={7}>
                              <div>
                                <strong>Trace:</strong> {group.traceId}
                              </div>
                              <div>
                                <strong>Triggered rules:</strong>{' '}
                                {group.summary.triggered_rule_ids.length === 0
                                  ? 'none'
                                  : group.summary.triggered_rule_ids.map((ruleId) => (
                                      <Link
                                        key={ruleId}
                                        to={`/risk/rules/${ruleId}`}
                                        style={{ marginRight: '0.5rem' }}
                                      >
                                        {ruleId}
                                      </Link>
                                    ))}
                              </div>
                            </Td>
                          </Tr>
                        )}
                      </Fragment>
                    );
                  })}
                </Tbody>
              </Table>
            </div>
            <CursorPagination
              hasNextPage={hasNextPage}
              isFetchingNextPage={isFetchingNextPage}
              onNextPage={onNextPage}
            />
          </>
        )}
      </CardBody>
    </Card>
  );
}
