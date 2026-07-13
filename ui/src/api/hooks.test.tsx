import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useTraces, useSpanChildren } from './hooks';

// The hooks wrap fetchJson in TanStack Query. We prove one list hook and one
// keyset-paginated hook resolve data through a QueryClientProvider against a
// mocked fetch — the hooks own URL construction + unwrapping, not policy.

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('useTraces', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('fetches GET /api/traces and returns the traces array', async () => {
    const entry = { trace_id: 'T', listing_root: { span_id: 'r' }, counts: null, in_time_window: true };
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ traces: [entry] }),
    });

    const { result } = renderHook(() => useTraces({ limit: 20 }), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([entry]);

    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toBe('/api/traces?limit=20');
  });
});

describe('useSpanChildren', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('fetches the children sub-resource with cursor + limit', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ spans: [{ span_id: 'c1' }] }),
    });

    const { result } = renderHook(
      () => useSpanChildren('T', 'root', { cursor: 5, limit: 50 }),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([{ span_id: 'c1' }]);

    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toBe('/api/traces/T/spans/root/children?cursor=5&limit=50');
  });
});
