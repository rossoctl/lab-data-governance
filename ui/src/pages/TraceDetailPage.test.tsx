import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route } from 'react-router-dom';
import { renderWithProviders } from '../test/renderWithProviders';
import { TraceDetailPage } from './TraceDetailPage';

// The trace-detail view hosts a two-way switcher: Tree | Flow. Default is
// Tree; clicking a tab swaps the active view. The trace id comes from the
// route (:traceId), proving the deep-link contract.

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

// A mock with one interaction + evidence span, so Add-to-highlights has
// something to pin and reveal.
function mockFetchWithFlow() {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url === '/api/traces/T1') {
      return {
        ok: true, status: 200,
        json: async () => ({
          trace_id: 'T1',
          listing_root: {
            seq: 1, trace_id: 'T1', span_id: 'root', parent_id: null,
            name: 'root-span', started_at: '2026-05-01T12:00:00Z',
            service_name: 'svc', kind: 'SERVER', error: null, attributes: {},
          },
          counts: { total: 2, in_window: 2, error_count: 0 }, in_time_window: true,
        }),
      };
    }
    if (url.endsWith('/interactions')) {
      return {
        ok: true, status: 200,
        json: async () => ({ interactions: [{
          id: 'i1', caller_entity_id: null, callee_entity_id: null,
          started_at: '2026-05-01T12:00:00Z', ended_at: '2026-05-01T12:00:01Z',
          error: false, request_payload_hash: null, response_payload_hash: null,
          summary: 'the interaction', parent_interaction_id: null,
          span_count: 1, anchor_count: 1,
        }] }),
      };
    }
    if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: [] }) };
    if (url.includes('/interactions/i1/spans')) {
      return { ok: true, status: 200, json: async () => ({ spans: [
        { span_id: 'ev-span', role: 'anchor', parent_id: 'root', kind: 'CLIENT', service_name: 'svc' },
      ] }) };
    }
    return { ok: true, status: 200, json: async () => ({ spans: [] }) };
  });
}

describe('TraceDetailPage', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('shows the trace id and a two-way view switcher, Tree active by default', async () => {
    mockFetch();
    renderWithProviders(
      <Routes>
        <Route path="/traces/:traceId" element={<TraceDetailPage />} />
      </Routes>,
      { route: '/traces/T1' },
    );

    // Both switcher tabs are present, and no Graph tab remains.
    await waitFor(() => expect(screen.getByRole('tab', { name: /Span tree/i })).toBeInTheDocument());
    expect(screen.getByRole('tab', { name: /Interaction flow/i })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /Graph/i })).not.toBeInTheDocument();

    // Tree tab is selected by default.
    expect(screen.getByRole('tab', { name: /Span tree/i })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('renders a breadcrumb back to the recent-traces list and shows the trace id', async () => {
    mockFetch();
    renderWithProviders(
      <Routes>
        <Route path="/traces/:traceId" element={<TraceDetailPage />} />
      </Routes>,
      { route: '/traces/T1' },
    );

    // Crumb 1 is a real link back to the list root.
    const backLink = await screen.findByRole('link', { name: /Recent traces/i });
    expect(backLink).toHaveAttribute('href', '/');
    // Crumb 2 (the "you are here" crumb) carries the full trace id.
    expect(screen.getByText('T1')).toBeInTheDocument();
  });

  it('switches to the Span tree when Add-to-highlights is clicked in the flow view', async () => {
    mockFetchWithFlow();
    renderWithProviders(
      <Routes>
        <Route path="/traces/:traceId" element={<TraceDetailPage />} />
      </Routes>,
      { route: '/traces/T1' },
    );
    // Go to the flow view, select the interaction, add it to highlights.
    const flowTab = await screen.findByRole('tab', { name: /Interaction flow/i });
    await userEvent.click(flowTab);
    await userEvent.click(await screen.findByText(/1 \(1 anchor\)/));
    await userEvent.click(await screen.findByRole('button', { name: /Add to highlights/i }));
    // The view flips back to the Span tree so the highlighted spans are revealed.
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /Span tree/i })).toHaveAttribute('aria-selected', 'true'),
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
