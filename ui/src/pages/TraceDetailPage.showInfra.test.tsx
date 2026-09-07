import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route } from 'react-router-dom';
import { renderWithProviders } from '../test/renderWithProviders';
import { LocationProbe } from '../test/LocationProbe';
import { TraceDetailPage } from './TraceDetailPage';

/**
 * `?showInfra=1` — the URL half of the flow view's infrastructure filter.
 *
 * ADR-0021 requires every view state to be URL-addressable, and its own rule that
 * DEFAULTS ARE OMITTED, NOT WRITTEN: hidden is the default, so a plain `/flow`
 * link is the plain view and only the revealed state puts a param in the URL.
 * That is the same convention `?hideOrphans` follows on the trace list.
 *
 * Kept out of `TraceDetailPage.test.tsx` because it needs a trace that HAS
 * infrastructure rows, which that file's fixtures deliberately do not.
 */

function harness() {
  return (
    <>
      <Routes>
        <Route path="/traces/:traceId/:view" element={<TraceDetailPage />} />
      </Routes>
      <LocationProbe />
    </>
  );
}

function ix(id: string, second: number, requestContentKind: string) {
  const at = (s: number) => `2026-05-01T12:00:${String(s).padStart(2, '0')}Z`;
  return {
    id,
    caller_entity_id: null,
    callee_entity_id: null,
    summary: `summary-${id}`,
    parent_interaction_id: null,
    legs: [
      { leg_type: 'request', occurred_at: at(second), payload_hash: null, error: false, seq: second * 2 },
      { leg_type: 'response', occurred_at: at(second + 30), payload_hash: null, error: false, seq: second * 2 + 1 },
    ],
    duration_seconds: 1,
    any_error: false,
    span_count: 1,
    anchor_count: 1,
    kinds: {
      protocol: 'mcp',
      mcp_method: null,
      request_content_kind: requestContentKind,
      response_content_kind: null,
    },
    destination: null,
    http: null,
    principal_sub: null,
    session_id: null,
  };
}

function mockFetchWithInfra() {
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
          counts: { total: 2, in_window: 2, error_count: 0 },
          in_time_window: true,
        }),
      };
    }
    if (url.endsWith('/interactions')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          interactions: [ix('i-real', 1, 'mcp_request'), ix('i-life', 2, 'mcp_lifecycle_request')],
        }),
      };
    }
    if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: [] }) };
    return { ok: true, status: 200, json: async () => ({ spans: [] }) };
  });
}

describe('TraceDetailPage · ?showInfra', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('hides infrastructure by default and writes no param', async () => {
    mockFetchWithInfra();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    expect(
      await screen.findByRole('button', { name: /1 infrastructure interaction hidden — show/i }),
    ).toBeTruthy();
    expect(screen.getByTestId('location')).not.toHaveTextContent('showInfra');
  });

  it('writes ?showInfra=1 when the affordance is used', async () => {
    mockFetchWithInfra();
    renderWithProviders(harness(), { route: '/traces/T1/flow' });

    await userEvent.click(await screen.findByRole('button', { name: /hidden — show/i }));

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('showInfra=1'));
    expect(screen.getByRole('button', { name: /Hide 1 infrastructure interaction/i })).toBeTruthy();
  });

  it('restores the revealed state from ?showInfra=1 on load', async () => {
    mockFetchWithInfra();
    renderWithProviders(harness(), { route: '/traces/T1/flow?showInfra=1' });

    expect(
      await screen.findByRole('button', { name: /Hide 1 infrastructure interaction/i }),
    ).toBeTruthy();
  });

  it('drops the param when the filter goes back to its default, keeping the URL canonical', async () => {
    mockFetchWithInfra();
    renderWithProviders(harness(), { route: '/traces/T1/flow?showInfra=1' });

    await userEvent.click(await screen.findByRole('button', { name: /^Hide 1 infrastructure/i }));

    await waitFor(() => expect(screen.getByTestId('location')).not.toHaveTextContent('showInfra'));
  });
});
