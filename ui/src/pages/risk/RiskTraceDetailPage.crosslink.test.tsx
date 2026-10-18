import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen } from '@testing-library/react';
import { Routes, Route } from 'react-router-dom';
import { renderWithProviders } from '../../test/renderWithProviders';
import { RiskTraceDetailPage } from './RiskTraceDetailPage';

/**
 * The cross link from the risk trace page to the trace page with the risk layer
 * on (risk is a layer on the trace page; this page stays for a
 * transition period). The page's own tests in `RiskTraceDetailPage.test.tsx` are
 * untouched — this file only pins the link and its translation of the params.
 */
function harness() {
  return (
    <Routes>
      <Route path="/risk/traces/:traceId" element={<RiskTraceDetailPage />} />
    </Routes>
  );
}

describe('RiskTraceDetailPage — the cross link to the trace page', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.startsWith('/risk/alerts')) return { ok: true, status: 200, json: async () => ({ items: [], next_cursor: null }) };
      if (url.includes('/risk/rules')) return { ok: true, status: 200, json: async () => ({ items: [], next_cursor: null }) };
      if (url.includes('/risk/traces/')) return { ok: true, status: 200, json: async () => ({ trace_risk: { trace_id: 't1' }, interactions: [] }) };
      return { ok: true, status: 200, json: async () => ({ entities: [] }) };
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('links to the execution flow with the layer on, carrying ?violation= and ?interaction= as ?iid=', async () => {
    renderWithProviders(harness(), { route: '/risk/traces/t1?violation=2&interaction=i7' });
    const link = await screen.findByRole('link', { name: /Open in trace view with the risk layer/ });
    expect(link).toHaveAttribute('href', '/traces/t1/graph?risk=1&violation=2&iid=i7');
  });

  it('links with ?risk=1 alone when nothing is open or selected', async () => {
    renderWithProviders(harness(), { route: '/risk/traces/t1' });
    const link = await screen.findByRole('link', { name: /Open in trace view with the risk layer/ });
    expect(link).toHaveAttribute('href', '/traces/t1/graph?risk=1');
  });
});
