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
 *
 * Only traces with a non-none risk level are listed (issue #214) — an alerts
 * table is a worklist, and a trace evaluated as clean is not work. The filter
 * lives in `groupTraceRisksByWorkflow`, and `useRiskTracesInfinite` asks the
 * server for the same subset so none-risk rows do not consume page budget.
 * The client-side half is kept even so: `items` is a plain prop, and a
 * none-risk record could still arrive from a cached page or a future
 * `/risk/alerts` swap.
 *
 * Because filtering can empty a page the server still has successors for, the
 * "no incidents" empty state replaces only the TABLE — the pagination control
 * renders whenever `hasNextPage`, regardless of how many groups survived.
 * Gating it on `groups.length` instead would strand the user on an all-none
 * page with no way to reach the alerts behind it.
 *
 * There is no dedicated "Open" column: clicking anywhere on the summary row
 * (other than the expand chevron) navigates straight to the trace, via
 * `navigate()` on `onRowClick` — a full row is a bigger, easier target than a
 * single cell's worth of column. Only the chevron button expands/collapses
 * the detail row in place; it calls `event.stopPropagation()` so the row's
 * own click handler doesn't also fire a navigation underneath it. The Open
 * link in the expanded detail row is kept too, alongside the triggered
 * rules, as a visible affordance once a group is already open.
 */
import { Fragment, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
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
  const navigate = useNavigate();

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
                </Tr>
              </Thead>
              <Tbody>
                {groups.map((group) => {
                  const isExpanded = expanded.has(group.traceId);
                  return (
                    <Fragment key={group.traceId}>
                      <Tr
                        data-testid={`alert-row-${group.traceId}`}
                        isClickable
                        onRowClick={() => navigate(`/risk/traces/${group.traceId}`)}
                      >
                        <Td dataLabel="Expand">
                          <Button
                            variant="plain"
                            aria-label={`${isExpanded ? 'Collapse' : 'Expand'} ${group.traceId}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              toggle(group.traceId);
                            }}
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
                      </Tr>
                      {isExpanded && (
                        <Tr>
                          <Td dataLabel="Details" colSpan={6}>
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
                                      onClick={(event) => event.stopPropagation()}
                                    >
                                      {ruleId}
                                    </Link>
                                  ))}
                            </div>
                            <div className="pf-v5-u-mt-sm">
                              <Link
                                to={`/risk/traces/${group.traceId}`}
                                onClick={(event) => event.stopPropagation()}
                              >
                                Open
                              </Link>
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
        )}
        <CursorPagination
          hasNextPage={hasNextPage}
          isFetchingNextPage={isFetchingNextPage}
          onNextPage={onNextPage}
        />
      </CardBody>
    </Card>
  );
}
