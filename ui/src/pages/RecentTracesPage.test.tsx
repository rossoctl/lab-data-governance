import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route } from 'react-router-dom';
import { renderWithProviders } from '../test/renderWithProviders';
import { LocationProbe } from '../test/LocationProbe';
import { RecentTracesPage } from './RecentTracesPage';

// The recent-traces landing view: renders a row per TraceListingEntry with
// service / name / started / spans-count / status badges, and navigates to
// /traces/:tid on row click (the SPA equivalent of the vanilla openTrace).

function entry(over: Record<string, unknown> = {}) {
  return {
    trace_id: 'T1',
    listing_root: {
      seq: 1,
      trace_id: 'T1',
      span_id: 'root',
      parent_id: null,
      name: 'api-handler',
      started_at: '2026-05-01T12:00:00Z',
      service_name: 'svc-a',
      kind: 'SERVER',
      error: null,
    },
    counts: { total: 3, in_window: 3, error_count: 1 },
    in_time_window: true,
    ...over,
  };
}

describe('RecentTracesPage', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('renders a row per trace with service, name, count and error badge', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ traces: [entry()] }),
    });

    renderWithProviders(<RecentTracesPage />);

    await waitFor(() => expect(screen.getByText('api-handler')).toBeInTheDocument());
    expect(screen.getByText('svc-a')).toBeInTheDocument();
    // in_window / total.
    expect(screen.getByText('3 / 3')).toBeInTheDocument();
    // error-count badge.
    expect(screen.getByText(/1 error/)).toBeInTheDocument();
    // real-root badge (parent_id null).
    expect(screen.getByText(/Real root/)).toBeInTheDocument();
  });

  it('navigates to the canonical /traces/:tid/spans URL when a row is clicked', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ traces: [entry()] }),
    });

    renderWithProviders(
      <Routes>
        <Route path="/traces" element={<RecentTracesPage />} />
        <Route path="/traces/:traceId/spans" element={<div>trace detail T1</div>} />
      </Routes>,
      { route: '/traces' },
    );

    await waitFor(() => expect(screen.getByText('api-handler')).toBeInTheDocument());
    await userEvent.click(screen.getByText('api-handler'));
    await waitFor(() =>
      expect(screen.getByText('trace detail T1')).toBeInTheDocument(),
    );
  });

  // --- URL-as-state: the time window + hide-orphans checkbox round-trip
  // through the query string, so reload/bookmark/back restore the list view.

  it('reads the initial time window from ?window and queries all-time (no bounds)', async () => {
    const calls: string[] = [];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      calls.push(url);
      return { ok: true, status: 200, json: async () => ({ traces: [entry()] }) };
    });

    renderWithProviders(<RecentTracesPage />, { route: '/traces?window=all' });

    await waitFor(() => expect(screen.getByText('api-handler')).toBeInTheDocument());
    // The select reflects the URL.
    expect((screen.getByLabelText('Time window') as HTMLSelectElement).value).toBe('all');
    // "all" means no time bounds on the API call.
    expect(calls.some((u) => u.includes('/api/traces'))).toBe(true);
    expect(calls.every((u) => !u.includes('time_from') && !u.includes('time_to'))).toBe(true);
  });

  it('reads a bounded window from ?window and sends time_from to the API', async () => {
    const calls: string[] = [];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      calls.push(url);
      return { ok: true, status: 200, json: async () => ({ traces: [entry()] }) };
    });

    renderWithProviders(<RecentTracesPage />, { route: '/traces?window=6h' });

    await waitFor(() => expect(screen.getByText('api-handler')).toBeInTheDocument());
    expect((screen.getByLabelText('Time window') as HTMLSelectElement).value).toBe('6h');
    expect(calls.some((u) => u.includes('time_from'))).toBe(true);
  });

  it('defaults to the 1h window when ?window is absent or garbage', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ traces: [entry()] }),
    });

    renderWithProviders(<RecentTracesPage />, { route: '/traces?window=bogus' });
    await waitFor(() => expect(screen.getByText('api-handler')).toBeInTheDocument());
    expect((screen.getByLabelText('Time window') as HTMLSelectElement).value).toBe('1h');
  });

  it('writes the chosen window into ?window when the select changes', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ traces: [entry()] }),
    });

    renderWithProviders(
      <>
        <RecentTracesPage />
        <LocationProbe />
      </>,
      { route: '/traces' },
    );

    await waitFor(() => expect(screen.getByText('api-handler')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText('Time window'), 'all');
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces?window=all'),
    );
  });

  it('does not write ?window=1h for the default (keeps canonical URLs clean)', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ traces: [entry()] }),
    });

    renderWithProviders(
      <>
        <RecentTracesPage />
        <LocationProbe />
      </>,
      { route: '/traces?window=all' },
    );

    await waitFor(() => expect(screen.getByText('api-handler')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText('Time window'), '1h');
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/traces'),
    );
    expect(screen.getByTestId('location')).not.toHaveTextContent('window=1h');
  });

  it('reads ?hideOrphans=1 to filter missing-parent traces on load', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        traces: [
          entry(),
          entry({
            trace_id: 'T2',
            listing_root: {
              seq: 2, trace_id: 'T2', span_id: 'orphan', parent_id: 'missing',
              name: 'orphan-root', started_at: '2026-05-01T13:00:00Z',
              service_name: 'svc-b', kind: 'INTERNAL', error: null,
            },
            counts: { total: 1, in_window: 1, error_count: 0 },
          }),
        ],
      }),
    });

    renderWithProviders(<RecentTracesPage />, { route: '/traces?hideOrphans=1' });

    await waitFor(() => expect(screen.getByText('api-handler')).toBeInTheDocument());
    // Checkbox reflects the URL, and the orphan row is filtered out.
    expect((screen.getByLabelText(/Hide missing-parent/i) as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByText('orphan-root')).toBeNull();
  });

  it('writes ?hideOrphans=1 when the checkbox is toggled on', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ traces: [entry()] }),
    });

    renderWithProviders(
      <>
        <RecentTracesPage />
        <LocationProbe />
      </>,
      { route: '/traces' },
    );

    await waitFor(() => expect(screen.getByText('api-handler')).toBeInTheDocument());
    await userEvent.click(screen.getByLabelText(/Hide missing-parent/i));
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('hideOrphans=1'),
    );
  });

  it('paginates via Load more: a full first page offers the button, a short second page ends it', async () => {
    // 20-entry first page (== page size) → hasNextPage; then a 1-entry page → end.
    const page1 = Array.from({ length: 20 }, (_, i) =>
      entry({
        trace_id: `T${i}`,
        listing_root: {
          seq: i + 1, trace_id: `T${i}`, span_id: `r${i}`, parent_id: null,
          name: `trace-${i}`, started_at: `2026-05-01T12:00:${String(i).padStart(2, '0')}Z`,
          service_name: 'svc', kind: 'SERVER', error: null,
        },
        counts: { total: 1, in_window: 1, error_count: 0 },
      }),
    );
    const page2 = [entry({ trace_id: 'LAST', listing_root: {
      seq: 999, trace_id: 'LAST', span_id: 'rlast', parent_id: null,
      name: 'last-trace', started_at: '2026-05-01T11:00:00Z',
      service_name: 'svc', kind: 'SERVER', error: null,
    }, counts: { total: 1, in_window: 1, error_count: 0 } })];
    let call = 0;
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      call += 1;
      return { ok: true, status: 200, json: async () => ({ traces: call === 1 ? page1 : page2 }) };
    });

    renderWithProviders(<RecentTracesPage />);
    await waitFor(() => expect(screen.getByText('trace-0')).toBeInTheDocument());
    const more = await screen.findByRole('button', { name: /Load more/i });
    await userEvent.click(more);
    await waitFor(() => expect(screen.getByText('last-trace')).toBeInTheDocument());
    // Short second page → the button is gone.
    expect(screen.queryByRole('button', { name: /Load more/i })).toBeNull();
  });

  it('renders a missing-parent badge for an orphan listing root', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        traces: [
          entry({
            trace_id: 'T2',
            listing_root: {
              seq: 2,
              trace_id: 'T2',
              span_id: 'orphan',
              parent_id: 'missing',
              name: 'orphan-root',
              started_at: '2026-05-01T13:00:00Z',
              service_name: 'svc-b',
              kind: 'INTERNAL',
              error: null,
            },
            counts: { total: 1, in_window: 1, error_count: 0 },
          }),
        ],
      }),
    });

    renderWithProviders(<RecentTracesPage />);
    await waitFor(() => expect(screen.getByText('orphan-root')).toBeInTheDocument());
    expect(screen.getByText(/Missing parent/)).toBeInTheDocument();
  });
});
