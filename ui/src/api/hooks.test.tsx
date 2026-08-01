import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useTraces, useSpanChildren, useDataLineage } from './hooks';

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

describe('useDataLineage', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  const LEG = {
    interaction_id: 'i1',
    leg_type: 'request',
    payload_hash: 'h1',
    lineage: {
      data_sources: ['agent-one'],
      source_transformations: { 'agent-one': ['summarization'] },
      entities: ['agent-one', 'llm-x'],
      seq: 2,
    },
  };

  it('fetches the trace-scoped data-lineage resource and keys the legs by (interaction_id, leg_type)', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        legs: [LEG, { ...LEG, leg_type: 'response', lineage: null }],
        status: 'complete',
        stopped_at_seq: null,
      }),
    });

    const { result } = renderHook(() => useDataLineage('T'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toBe('/api/traces/T/data-lineage');

    // The hook unwraps `{legs:[...]}` into the per-leg map the flow view reads
    // (ADR-0028 D5: the leg is the key, never the payload hash).
    expect(result.current.data?.byLeg.get('i1:request')).toEqual(LEG.lineage);
    // A leg present with lineage=null is a real "not yet computed" entry, and
    // must stay distinguishable from an absent leg.
    expect(result.current.data?.byLeg.has('i1:response')).toBe(true);
    expect(result.current.data?.byLeg.get('i1:response')).toBeNull();
    expect(result.current.data?.byLeg.has('i2:request')).toBe(false);
  });

  it('yields an empty map for the not-yet-migrated / no-interactions empty shape', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ legs: [], status: null, stopped_at_seq: null }),
    });

    const { result } = renderHook(() => useDataLineage('T'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.byLeg.size).toBe(0);
  });

  it('does not fetch while disabled', async () => {
    renderHook(() => useDataLineage('T', false), { wrapper: wrapper() });
    expect(fetch as ReturnType<typeof vi.fn>).not.toHaveBeenCalled();
  });

  // --- trace-level coverage status (issue #120, ADR-0028 D6) -----------------

  it('carries the trace-level partial status and its stop position', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ legs: [LEG], status: 'partial', stopped_at_seq: 7 }),
    });

    const { result } = renderHook(() => useDataLineage('T'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.status).toBe('partial');
    expect(result.current.data?.stoppedAtSeq).toBe(7);
  });

  it('reads a missing status as null, never as complete', async () => {
    // An older server (or a DB without migration 0012) omits the fields
    // entirely. "Coverage unknown" and "coverage total" are opposite claims, so
    // the absent field must not default to `complete` — that would let a
    // truncated prefix read as the whole set of sources.
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ legs: [LEG] }),
    });

    const { result } = renderHook(() => useDataLineage('T'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.status).toBeNull();
    expect(result.current.data?.stoppedAtSeq).toBeNull();
  });
});
