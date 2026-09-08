import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { AlertsCard } from './AlertsCard';
import type { TraceRiskRecord } from '../../../risk-api/types';

function record(overrides: Partial<TraceRiskRecord> = {}): TraceRiskRecord {
  return {
    trace_risk_id: 'trr-1',
    trace_id: 'trace-1',
    version: 1,
    computed_at: '2026-08-02T00:00:00Z',
    trace_risk_level: 'high',
    trace_enforcement_type: 'block',
    risk_compounding_mode: 'max',
    enforcement_aggregation_mode: 'strictest',
    interaction_count: 3,
    policy_event_count: 2,
    all_entity_ids: [],
    triggered_rule_ids: ['rule-1'],
    overall_confidence: 0.9,
    contributing_interaction_risk_ids: [],
    ...overrides,
  };
}

function renderCard(props: Partial<Parameters<typeof AlertsCard>[0]> = {}) {
  return render(
    <MemoryRouter>
      <AlertsCard
        items={[record()]}
        hasNextPage={false}
        isFetchingNextPage={false}
        onNextPage={vi.fn()}
        {...props}
      />
    </MemoryRouter>,
  );
}

describe('AlertsCard', () => {
  it('renders groups collapsed by default, with no separate Open column', () => {
    renderCard();
    expect(screen.queryByText('rule-1')).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Open' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /^open$/i })).not.toBeInTheDocument();
  });

  it('renders every remaining column header, and truncates the trace id with its full value available on hover', () => {
    const items = [
      record({ trace_id: 'trace-with-a-very-long-identifier-that-would-overflow-the-column' }),
    ];
    renderCard({ items });

    for (const header of ['Trace', 'Risk level', 'Enforcement', 'Interactions', 'Policy events']) {
      expect(screen.getByRole('columnheader', { name: header })).toBeInTheDocument();
    }

    const traceCell = screen.getByTitle('trace-with-a-very-long-identifier-that-would-overflow-the-column');
    expect(traceCell).toBeInTheDocument();
  });

  it("shows the full trace id in the expanded group's detail row, alongside an Open link", () => {
    const items = [record({ trace_id: 'trace-with-a-very-long-identifier' })];
    renderCard({ items });

    fireEvent.click(screen.getByRole('button', { name: /trace-with-a-very-long-identifier/i }));
    expect(screen.getByText(/Trace:/).parentElement).toHaveTextContent(
      'Trace: trace-with-a-very-long-identifier',
    );
    const link = screen.getByRole('link', { name: /^open$/i });
    expect(link).toHaveAttribute('href', '/risk/traces/trace-with-a-very-long-identifier');
  });

  it('has no Open link while a group is collapsed', () => {
    renderCard();
    expect(screen.queryByRole('link', { name: /^open$/i })).not.toBeInTheDocument();
  });

  it('expands a group on chevron click without navigating, and collapses again on a second click', () => {
    renderCard();
    const toggle = screen.getByRole('button', { name: /trace-1/i });
    fireEvent.click(toggle);
    expect(screen.getByText('rule-1')).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.queryByText('rule-1')).not.toBeInTheDocument();
  });

  it('navigates to the trace when its row is clicked', () => {
    render(
      <MemoryRouter initialEntries={['/risk']}>
        <Routes>
          <Route
            path="/risk"
            element={
              <AlertsCard items={[record()]} hasNextPage={false} isFetchingNextPage={false} onNextPage={vi.fn()} />
            }
          />
          <Route path="/risk/traces/:traceId" element={<div>Trace detail page</div>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTestId('alert-row-trace-1'));
    expect(screen.getByText('Trace detail page')).toBeInTheDocument();
  });

  it('expands a group on row click without navigating when only the chevron is clicked', () => {
    render(
      <MemoryRouter initialEntries={['/risk']}>
        <Routes>
          <Route
            path="/risk"
            element={
              <AlertsCard items={[record()]} hasNextPage={false} isFetchingNextPage={false} onNextPage={vi.fn()} />
            }
          />
          <Route path="/risk/traces/:traceId" element={<div>Trace detail page</div>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: /trace-1/i }));
    expect(screen.getByText('rule-1')).toBeInTheDocument();
    expect(screen.queryByText('Trace detail page')).not.toBeInTheDocument();
  });

  it('leaves other groups collapsed when one group is expanded', () => {
    const items = [
      record({ trace_risk_id: 'trr-1', trace_id: 'trace-1', triggered_rule_ids: ['rule-1'] }),
      record({ trace_risk_id: 'trr-2', trace_id: 'trace-2', triggered_rule_ids: ['rule-2'] }),
    ];
    renderCard({ items });

    fireEvent.click(screen.getByRole('button', { name: /trace-1/i }));
    expect(screen.getByText('rule-1')).toBeInTheDocument();
    expect(screen.queryByText('rule-2')).not.toBeInTheDocument();
  });

  it('collapses multiple versions of one trace into a single group summarised by the highest version', () => {
    const items = [
      record({ trace_risk_id: 'trr-1', version: 1, trace_risk_level: 'low' }),
      record({ trace_risk_id: 'trr-2', version: 2, trace_risk_level: 'critical' }),
    ];
    renderCard({ items });
    expect(screen.getByText('critical')).toBeInTheDocument();
    expect(screen.getAllByTestId('alert-row-trace-1')).toHaveLength(1);
  });

  it('shows "Load more" only when hasNextPage is true', () => {
    const { rerender } = render(
      <MemoryRouter>
        <AlertsCard items={[record()]} hasNextPage={false} isFetchingNextPage={false} onNextPage={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument();

    rerender(
      <MemoryRouter>
        <AlertsCard items={[record()]} hasNextPage={true} isFetchingNextPage={false} onNextPage={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.getByRole('button', { name: /load more/i })).toBeInTheDocument();
  });

  it('renders an empty state when items is empty', () => {
    renderCard({ items: [] });
    expect(screen.getByText(/no incidents/i)).toBeInTheDocument();
  });

  // Issue #214: only traces with a non-none risk level belong in this table.
  // The hook filters server-side; the card filters too, so a none-risk record
  // arriving from any other source (a cached page, a hand-passed `items`, a
  // future `/risk/alerts` swap) still cannot render a row.

  it('does not render a row for a none-risk trace', () => {
    const items = [
      record({ trace_risk_id: 'trr-1', trace_id: 'trace-risky', trace_risk_level: 'low' }),
      record({ trace_risk_id: 'trr-2', trace_id: 'trace-quiet', trace_risk_level: 'none' }),
    ];
    renderCard({ items });

    expect(screen.getByTestId('alert-row-trace-risky')).toBeInTheDocument();
    expect(screen.queryByTestId('alert-row-trace-quiet')).not.toBeInTheDocument();
  });

  it('renders the empty state when every trace is none-risk', () => {
    const items = [
      record({ trace_risk_id: 'trr-1', trace_id: 'trace-1', trace_risk_level: 'none' }),
      record({ trace_risk_id: 'trr-2', trace_id: 'trace-2', trace_risk_level: 'none' }),
    ];
    renderCard({ items });

    expect(screen.getByText(/no incidents/i)).toBeInTheDocument();
    expect(screen.queryByRole('row', { name: /trace-1/i })).not.toBeInTheDocument();
  });

  it('still offers Load more when a page filters down to nothing, so paging can continue', () => {
    // A page can legitimately contain only none-risk records; hiding the
    // pagination control would strand the user with no way to reach the
    // alerts on later pages.
    renderCard({
      items: [record({ trace_id: 'trace-quiet', trace_risk_level: 'none' })],
      hasNextPage: true,
    });

    expect(screen.getByRole('button', { name: /load more/i })).toBeInTheDocument();
  });
});
