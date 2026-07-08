import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  PageSection,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
  Checkbox,
  FormSelect,
  FormSelectOption,
  Label,
  Spinner,
  Button,
  EmptyState,
  EmptyStateBody,
  EmptyStateHeader,
} from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';

import { useTracesInfinite } from '../api/hooks';
import {
  dedupeByTraceId,
  applyMissingParentFilter,
  formatTime24Utc,
  type TraceRow,
} from '../lib/recentTraces';
import type { TraceListingEntry } from '../types';

type WindowKey = '15m' | '1h' | '6h' | '24h' | 'all';
const WINDOW_MINUTES: Record<Exclude<WindowKey, 'all'>, number> = {
  '15m': 15,
  '1h': 60,
  '6h': 360,
  '24h': 1440,
};

/** ISO window bounds for the selected key, or {} for "all time". */
function windowParams(key: WindowKey): { time_from?: string; time_to?: string } {
  if (key === 'all') return {};
  const now = Date.now();
  const from = now - WINDOW_MINUTES[key] * 60_000;
  const iso = (ms: number) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
  return { time_from: iso(from), time_to: iso(now) };
}

/** Flatten a TraceListingEntry to the row shape the ported helpers key on. */
interface Row extends TraceRow {
  name: string;
  service_name: string | null;
  in_time_window: boolean;
  counts: TraceListingEntry['counts'];
}

function toRow(e: TraceListingEntry): Row {
  return {
    trace_id: e.trace_id,
    seq: e.listing_root.seq,
    parent_id: e.listing_root.parent_id,
    started_at: e.listing_root.started_at,
    name: e.listing_root.name,
    service_name: e.listing_root.service_name,
    in_time_window: e.in_time_window,
    counts: e.counts,
  };
}

export function RecentTracesPage() {
  const navigate = useNavigate();
  const [windowKey, setWindowKey] = useState<WindowKey>('1h');
  const [hideMissingParent, setHideMissingParent] = useState(false);

  // Freeze the window bounds per windowKey (NOT per render) so the query key is
  // stable across time and doesn't refetch/re-key on every render.
  const window = useMemo(() => windowParams(windowKey), [windowKey]);

  const {
    data,
    isLoading,
    isError,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useTracesInfinite(window);

  const rows = useMemo(() => {
    // Accumulate every fetched page, then dedupe over the whole accumulator so
    // a flipped anchor (orphan → real root) arriving on a later page collapses
    // to one row (PROJECT.md §7 / the vanilla accumulate-then-dedupe design).
    const entries: TraceListingEntry[] = data ? data.pages.flat() : [];
    const deduped = dedupeByTraceId(entries.map(toRow));
    return applyMissingParentFilter(deduped, hideMissingParent);
  }, [data, hideMissingParent]);

  return (
    <PageSection>
      <Title headingLevel="h2" size="xl">
        Recent traces
      </Title>

      <Toolbar>
        <ToolbarContent>
          <ToolbarItem>
            <FormSelect
              aria-label="Time window"
              value={windowKey}
              onChange={(_e, v) => setWindowKey(v as WindowKey)}
              style={{ width: 160 }}
            >
              <FormSelectOption value="15m" label="Last 15 min" />
              <FormSelectOption value="1h" label="Last hour" />
              <FormSelectOption value="6h" label="Last 6 hours" />
              <FormSelectOption value="24h" label="Last 24 hours" />
              <FormSelectOption value="all" label="All time" />
            </FormSelect>
          </ToolbarItem>
          <ToolbarItem>
            <Checkbox
              id="hide-missing-parent"
              label="Hide missing-parent traces"
              isChecked={hideMissingParent}
              onChange={(_e, checked) => setHideMissingParent(checked)}
            />
          </ToolbarItem>
        </ToolbarContent>
      </Toolbar>

      {isLoading ? (
        <Spinner aria-label="Loading traces" />
      ) : isError ? (
        <EmptyState>
          <EmptyStateHeader titleText="Failed to load traces" headingLevel="h4" />
          <EmptyStateBody>The recent-traces feed could not be loaded.</EmptyStateBody>
        </EmptyState>
      ) : rows.length === 0 ? (
        <EmptyState>
          <EmptyStateHeader titleText="No traces" headingLevel="h4" />
          <EmptyStateBody>No traces in the selected window.</EmptyStateBody>
        </EmptyState>
      ) : (
        <Table aria-label="Recent traces" variant="compact">
          <Thead>
            <Tr>
              <Th>Service</Th>
              <Th>Name</Th>
              <Th>Started</Th>
              <Th>Spans</Th>
              <Th>Status</Th>
            </Tr>
          </Thead>
          <Tbody>
            {rows.map((row) => {
              const c = row.counts ?? { total: 0, in_window: 0, error_count: 0 };
              const orphan = row.parent_id != null;
              return (
                <Tr
                  key={row.trace_id}
                  isClickable
                  onRowClick={() => navigate(`/traces/${encodeURIComponent(row.trace_id)}`)}
                  style={row.in_time_window ? undefined : { opacity: 0.45 }}
                >
                  <Td dataLabel="Service">{row.service_name || '—'}</Td>
                  <Td dataLabel="Name">{row.name || '(unnamed)'}</Td>
                  <Td dataLabel="Started" title={row.started_at}>
                    {formatTime24Utc(row.started_at)}
                  </Td>
                  <Td dataLabel="Spans">
                    {c.in_window} / {c.total}
                  </Td>
                  <Td dataLabel="Status">
                    {c.error_count > 0 && (
                      <Label color="red" isCompact>
                        {c.error_count} {c.error_count === 1 ? 'error' : 'errors'}
                      </Label>
                    )}{' '}
                    {orphan ? (
                      <Label color="gold" isCompact>
                        Missing parent
                      </Label>
                    ) : (
                      <Label color="grey" isCompact>
                        Real root
                      </Label>
                    )}
                  </Td>
                </Tr>
              );
            })}
          </Tbody>
        </Table>
      )}

      {hasNextPage && (
        <Button
          variant="secondary"
          isBlock
          isLoading={isFetchingNextPage}
          onClick={() => fetchNextPage()}
          style={{ marginTop: '1rem' }}
        >
          {isFetchingNextPage ? 'Loading…' : 'Load more'}
        </Button>
      )}
    </PageSection>
  );
}
