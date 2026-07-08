import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route } from 'react-router-dom';
import { renderWithProviders } from '../test/renderWithProviders';
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

  it('navigates to /traces/:tid when a row is clicked', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ traces: [entry()] }),
    });

    renderWithProviders(
      <Routes>
        <Route path="/" element={<RecentTracesPage />} />
        <Route path="/traces/:traceId" element={<div>trace detail T1</div>} />
      </Routes>,
    );

    await waitFor(() => expect(screen.getByText('api-handler')).toBeInTheDocument());
    await userEvent.click(screen.getByText('api-handler'));
    await waitFor(() =>
      expect(screen.getByText('trace detail T1')).toBeInTheDocument(),
    );
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
