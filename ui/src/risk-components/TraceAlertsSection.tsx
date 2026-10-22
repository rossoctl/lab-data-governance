import { EmptyState, EmptyStateBody, EmptyStateHeader, Spinner, Title } from '@patternfly/react-core';
import type { UseQueryResult } from '@tanstack/react-query';
import { TraceAlertsPanel } from './TraceAlertsPanel';
import type { AlertListResponse } from '../risk-api/types';

/**
 * The "Alerts" section of a trace: the heading and the four states of the
 * `useTraceAlerts` read — loading, a read that never produced an answer, the
 * chain (`TraceAlertsPanel`), or "No alerts". Rendered by the trace page's
 * risk layer, above the flow views and above the span tree.
 *
 * `isLoadingError`, not `isError`: a failed BACKGROUND re-read (both pages are
 * live, ADR-0034) keeps the chain it has rather than swapping it for an error.
 */
export function TraceAlertsSection({ alerts }: { alerts: UseQueryResult<AlertListResponse> }) {
  return (
    <>
      <Title headingLevel="h3" size="lg" className="pf-v5-u-mt-lg pf-v5-u-mb-sm">
        Alerts
      </Title>
      {alerts.isLoading ? (
        <Spinner size="md" aria-label="Loading alerts" />
      ) : alerts.isLoadingError ? (
        <EmptyState>
          <EmptyStateHeader titleText="Failed to load alerts" headingLevel="h4" />
        </EmptyState>
      ) : alerts.data && alerts.data.items.length > 0 ? (
        <TraceAlertsPanel alerts={alerts.data.items} truncated={alerts.data.next_cursor != null} />
      ) : (
        <EmptyState>
          <EmptyStateHeader titleText="No alerts" headingLevel="h4" />
          <EmptyStateBody>No alert has been raised for this trace.</EmptyStateBody>
        </EmptyState>
      )}
    </>
  );
}

export default TraceAlertsSection;
