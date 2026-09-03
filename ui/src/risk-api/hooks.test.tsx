import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import {
  useRiskSummary,
  useRiskDistribution,
  useRiskByCategory,
  useTopRules,
  useRiskTracesInfinite,
} from './hooks';

// JSX-wrapper tests for the dashboard hooks (issue #169). Kept in a NEW
// `.tsx` file rather than folding into the existing `hooks.test.ts`, which
// is deliberately hook-free (issue #165's scaffold) — extending it here
// instead of renaming it means that file is never modified.

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('useRiskSummary', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('fetches GET /risk/metrics/summary with the window and unwraps the body', async () => {
    const body = {
      window: '24h',
      from: '2026-08-01T00:00:00Z',
      to: '2026-08-02T00:00:00Z',
      agents: { total: 1, risky: 0, risky_pct: 0 },
      users: { total: 1, risky: 0, risky_pct: 0 },
      workflows: { total: 1, risky: 0, risky_pct: 0 },
      evaluated_interactions: { total: 1, risky: 0, risky_pct: 0 },
      rules_fired: { total: 0, critical: 0 },
      computed_at: '2026-08-02T00:00:00Z',
    };
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => body,
    });

    const { result } = renderHook(() => useRiskSummary('24h'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(body);

    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toBe('/risk/metrics/summary?window=24h');
  });
});

describe('useRiskDistribution', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('fetches GET /risk/metrics/risk-distribution with the window', async () => {
    const body = {
      window: '7d',
      distribution: { critical: 0, high: 0, medium: 0, low: 0, none: 0, total: 0 },
      computed_at: '2026-08-02T00:00:00Z',
    };
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => body,
    });

    const { result } = renderHook(() => useRiskDistribution('7d'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(body);

    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toBe('/risk/metrics/risk-distribution?window=7d');
  });
});

describe('useRiskByCategory', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('fetches GET /risk/metrics/risk-by-category with the window', async () => {
    const body = { window: '30d', items: [{ category: 'privacy', count: 3 }] };
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => body,
    });

    const { result } = renderHook(() => useRiskByCategory('30d'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(body);

    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toBe('/risk/metrics/risk-by-category?window=30d');
  });
});

describe('useTopRules', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('fetches GET /risk/metrics/top-rules with the window and a limit, no cursor', async () => {
    const body = { window: '24h', items: [] };
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => body,
    });

    const { result } = renderHook(() => useTopRules('24h', 5), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(body);

    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toBe('/risk/metrics/top-rules?window=24h&limit=5');
  });

  it('defaults limit when omitted', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ window: '24h', items: [] }),
    });

    renderHook(() => useTopRules('24h'), { wrapper: wrapper() });
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toBe('/risk/metrics/top-rules?window=24h');
  });
});

describe('useRiskTracesInfinite', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('fetches GET /risk/traces with the window, sorted by risk level, and follows next_cursor', async () => {
    const page1 = { items: [{ trace_id: 't1' }], next_cursor: 'CURSOR1' };
    const page2 = { items: [{ trace_id: 't2' }], next_cursor: null };
    const fetchMock = fetch as ReturnType<typeof vi.fn>;
    fetchMock
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => page1 })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => page2 });

    const { result } = renderHook(() => useRiskTracesInfinite('24h'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const firstUrl = fetchMock.mock.calls[0][0] as string;
    expect(firstUrl).toContain('/risk/traces?');
    expect(firstUrl).toContain('sort=risk_level_desc');
    expect(firstUrl).toContain('window=24h');
    expect(firstUrl).not.toContain('cursor=');

    expect(result.current.hasNextPage).toBe(true);
    await result.current.fetchNextPage();
    await waitFor(() => expect(fetchMock.mock.calls).toHaveLength(2));

    const secondUrl = fetchMock.mock.calls[1][0] as string;
    expect(secondUrl).toContain('cursor=CURSOR1');
    expect(result.current.data?.pages).toEqual([page1, page2]);
  });
});
