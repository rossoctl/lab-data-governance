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
  useRulesInfinite,
  useRule,
  useRuleCategories,
  useTraceRiskDetail,
  useRuleCatalogIndex,
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

  // Issue #214: the alerts table shows only traces with a non-none risk level.
  // Filtering server-side keeps keyset pagination honest — a page of
  // RISK_PAGE_SIZE is RISK_PAGE_SIZE alerts, not a page mostly of none-risk
  // rows the client then throws away.

  it('requests only non-none risk levels, so none-risk traces never reach the client', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [], next_cursor: null }),
    });

    renderHook(() => useRiskTracesInfinite('24h'), { wrapper: wrapper() });
    await waitFor(() => expect(fetch).toHaveBeenCalled());

    const url = new URL(
      (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string,
      'http://localhost',
    );
    const levels = url.searchParams.get('risk_level')?.split(',') ?? [];
    expect(new Set(levels)).toEqual(new Set(['critical', 'high', 'medium', 'low']));
    expect(levels).not.toContain('none');
  });

  it('keeps the risk_level filter on every subsequent page, not just the first', async () => {
    const fetchMock = fetch as ReturnType<typeof vi.fn>;
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ items: [{ trace_id: 't1' }], next_cursor: 'CURSOR1' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ items: [{ trace_id: 't2' }], next_cursor: null }),
      });

    const { result } = renderHook(() => useRiskTracesInfinite('24h'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    await result.current.fetchNextPage();
    await waitFor(() => expect(fetchMock.mock.calls).toHaveLength(2));

    const secondUrl = fetchMock.mock.calls[1][0] as string;
    expect(secondUrl).toContain('risk_level=');
    expect(secondUrl).not.toContain('none');
  });
});

// Rules catalog hooks (issue #171).

describe('useRulesInfinite', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('fetches GET /risk/rules with no filters and follows next_cursor', async () => {
    const page1 = { items: [{ rule_id: 'DG-001' }], next_cursor: 'CURSOR1' };
    const page2 = { items: [{ rule_id: 'DG-002' }], next_cursor: null };
    const fetchMock = fetch as ReturnType<typeof vi.fn>;
    fetchMock
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => page1 })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => page2 });

    const { result } = renderHook(() => useRulesInfinite({}), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const firstUrl = fetchMock.mock.calls[0][0] as string;
    expect(firstUrl).toBe('/risk/rules');

    expect(result.current.hasNextPage).toBe(true);
    await result.current.fetchNextPage();
    await waitFor(() => expect(fetchMock.mock.calls).toHaveLength(2));

    const secondUrl = fetchMock.mock.calls[1][0] as string;
    expect(secondUrl).toBe('/risk/rules?cursor=CURSOR1');
    expect(result.current.data?.pages).toEqual([page1, page2]);
  });

  it('includes category and risk_level filters in the query when given', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [], next_cursor: null }),
    });

    renderHook(() => useRulesInfinite({ category: 'pii_exposure', riskLevel: 'critical' }), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(fetch).toHaveBeenCalled());

    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toContain('category=pii_exposure');
    expect(calledUrl).toContain('risk_level=critical');
  });

  it('gives differing filters different query keys, so switching a filter refetches', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [], next_cursor: null }),
    });

    const { result: a } = renderHook(() => useRulesInfinite({ category: 'pii_exposure' }), {
      wrapper: wrapper(),
    });
    const { result: b } = renderHook(() => useRulesInfinite({ category: 'data_exfiltration' }), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(a.current.isSuccess).toBe(true));
    await waitFor(() => expect(b.current.isSuccess).toBe(true));

    const urls = (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string);
    expect(urls.some((u) => u.includes('category=pii_exposure'))).toBe(true);
    expect(urls.some((u) => u.includes('category=data_exfiltration'))).toBe(true);
  });
});

describe('useRule', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('fetches GET /risk/rules/{rule_id} and unwraps the body', async () => {
    const body = { rule_id: 'DG-001', rule_name: 'pii_to_untrusted_external' };
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => body,
    });

    const { result } = renderHook(() => useRule('DG-001'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(body);

    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toBe('/risk/rules/DG-001');
  });

  it('surfaces a 404 as an error rather than throwing past react-query', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({
        error: 'not found',
        detail: "no rule with id 'nope'",
        timestamp: '2026-08-02T00:00:00Z',
      }),
    });

    const { result } = renderHook(() => useRule('nope'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as { status?: number })?.status).toBe(404);
  });

  it('does not fetch when ruleId is undefined', async () => {
    renderHook(() => useRule(undefined), { wrapper: wrapper() });
    await new Promise((r) => setTimeout(r, 0));
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe('useRuleCategories', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('fetches GET /risk/rules/categories and unwraps the body', async () => {
    const body = { items: [{ category: 'pii_exposure', rule_count: 2 }] };
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => body,
    });

    const { result } = renderHook(() => useRuleCategories(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(body);

    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toBe('/risk/rules/categories');
  });
});

// Trace detail forest + rule catalog index (issue #170).

describe('useTraceRiskDetail', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('fetches GET /risk/traces/{trace_id} and unwraps the body', async () => {
    const body = {
      trace_risk: { trace_risk_id: 'tr1', trace_id: 't1' },
      interactions: [],
    };
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => body,
    });

    const { result } = renderHook(() => useTraceRiskDetail('t1'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(body);

    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toBe('/risk/traces/t1');
  });

  it('surfaces a 404 as an error rather than throwing past react-query', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({
        error: 'not found',
        detail: "no trace risk record for 'nope'",
        timestamp: '2026-08-02T00:00:00Z',
      }),
    });

    const { result } = renderHook(() => useTraceRiskDetail('nope'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as { status?: number })?.status).toBe(404);
  });

  it('does not fetch when traceId is undefined', async () => {
    renderHook(() => useTraceRiskDetail(undefined), { wrapper: wrapper() });
    await new Promise((r) => setTimeout(r, 0));
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe('useRuleCatalogIndex', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('fetches GET /risk/rules once with a high limit and indexes by rule_id', async () => {
    const body = {
      items: [
        { rule_id: 'DG-001', rule_name: 'pii_to_untrusted_external' },
        { rule_id: 'DG-002', rule_name: 'excess_data_volume' },
      ],
      next_cursor: null,
    };
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => body,
    });

    const { result } = renderHook(() => useRuleCatalogIndex(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(fetch).toHaveBeenCalledTimes(1);
    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toContain('/risk/rules?limit=');

    expect(result.current.data?.get('DG-001')?.rule_name).toBe('pii_to_untrusted_external');
    expect(result.current.data?.get('DG-002')?.rule_name).toBe('excess_data_volume');
    expect(result.current.data?.size).toBe(2);
  });
});
