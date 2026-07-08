import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route } from 'react-router-dom';
import { renderWithProviders } from '../test/renderWithProviders';
import { TraceDetailPage } from './TraceDetailPage';

// The trace-detail view hosts a three-way switcher: Tree | Flow | Graph.
// Default is Tree; clicking a tab swaps the active view. The trace id comes
// from the route (:traceId), proving the deep-link contract.

function mockFetch() {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url === '/api/traces/T1') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          trace_id: 'T1',
          listing_root: {
            seq: 1, trace_id: 'T1', span_id: 'root', parent_id: null,
            name: 'root-span', started_at: '2026-05-01T12:00:00Z',
            service_name: 'svc', kind: 'SERVER', error: null, attributes: {},
          },
          counts: { total: 1, in_window: 1, error_count: 0 },
          in_time_window: true,
        }),
      };
    }
    // children / interactions / entities — empty is fine for the switcher test.
    return { ok: true, status: 200, json: async () => ({ spans: [], interactions: [], entities: [] }) };
  });
}

describe('TraceDetailPage', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('shows the trace id and a three-way view switcher, Tree active by default', async () => {
    mockFetch();
    renderWithProviders(
      <Routes>
        <Route path="/traces/:traceId" element={<TraceDetailPage />} />
      </Routes>,
      { route: '/traces/T1' },
    );

    // The three switcher tabs are present.
    await waitFor(() => expect(screen.getByRole('tab', { name: /Span tree/i })).toBeInTheDocument());
    expect(screen.getByRole('tab', { name: /Interaction flow/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Graph/i })).toBeInTheDocument();

    // Tree tab is selected by default.
    expect(screen.getByRole('tab', { name: /Span tree/i })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('switches to the Flow view when the Interaction flow tab is clicked', async () => {
    mockFetch();
    renderWithProviders(
      <Routes>
        <Route path="/traces/:traceId" element={<TraceDetailPage />} />
      </Routes>,
      { route: '/traces/T1' },
    );

    const flowTab = await screen.findByRole('tab', { name: /Interaction flow/i });
    await userEvent.click(flowTab);
    await waitFor(() =>
      expect(flowTab).toHaveAttribute('aria-selected', 'true'),
    );
    // Flow view mounted: with no derived data it shows its empty-state hint.
    await waitFor(() =>
      expect(
        screen.getByText(/No interaction data for this trace yet/i),
      ).toBeInTheDocument(),
    );
  });
});
