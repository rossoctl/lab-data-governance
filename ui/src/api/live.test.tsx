import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { LIVE_POLL_MS, TRACE_LIVE_KEYS, useEntitiesAndInteractions, useTraceLive } from './live';

/**
 * A stand-in for the browser's EventSource: records the URL it was opened on,
 * lets a test deliver `change` events and errors, and reports `close()`.
 * jsdom has no EventSource of its own, which is also what the `off` test relies on.
 */
class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readonly url: string;
  closed = false;
  readyState = FakeEventSource.CONNECTING;
  onerror: ((e: Event) => void) | null = null;
  private listeners = new Map<string, Set<(e: Event) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: Event) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn);
  }
  removeEventListener(type: string, fn: (e: Event) => void) {
    this.listeners.get(type)?.delete(fn);
  }
  close() {
    this.closed = true;
  }
  /** Deliver a `change` event carrying `id` as the token. */
  change(id: string) {
    this.readyState = FakeEventSource.OPEN;
    const ev = new MessageEvent('change', { lastEventId: id, data: id });
    this.listeners.get('change')?.forEach((fn) => fn(ev));
  }
  /** A drop the browser will retry by itself (readyState CONNECTING). */
  fail() {
    this.readyState = FakeEventSource.CONNECTING;
    this.onerror?.(new Event('error'));
  }
  /** A non-200 reply: the browser closes the source for good and never retries. */
  reject() {
    this.readyState = FakeEventSource.CLOSED;
    this.onerror?.(new Event('error'));
  }
}

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, invalidate, wrapper };
}

function invalidatedKeys(invalidate: { mock: { calls: unknown[][] } }): string[] {
  return invalidate.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
}

describe('useTraceLive', () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal('EventSource', FakeEventSource);
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('opens the trace events stream and starts in no mode until the first event', () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useTraceLive('T1'), { wrapper });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toBe('/api/traces/T1/events');
    expect(result.current).toBe('off');
  });

  it('invalidates every live key on the first token, then only when the token changes', () => {
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useTraceLive('T1'), { wrapper });
    const es = FakeEventSource.instances[0];

    act(() => es.change('v1'));
    expect(result.current).toBe('stream');
    // The first token re-reads once (closes the mount-to-first-event window).
    expect(invalidatedKeys(invalidate)).toEqual(
      TRACE_LIVE_KEYS.map((k) => JSON.stringify([k, 'T1'])),
    );

    invalidate.mockClear();
    act(() => es.change('v1'));
    expect(invalidate).not.toHaveBeenCalled();

    act(() => es.change('v2'));
    expect(invalidate).toHaveBeenCalledTimes(TRACE_LIVE_KEYS.length);
  });

  it('polls while the stream is in error and stops the moment it recovers', () => {
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useTraceLive('T1'), { wrapper });
    const es = FakeEventSource.instances[0];
    act(() => es.change('v1'));
    invalidate.mockClear();

    act(() => es.fail());
    expect(result.current).toBe('poll');
    act(() => vi.advanceTimersByTime(LIVE_POLL_MS * 2 + 10));
    expect(invalidate).toHaveBeenCalledTimes(TRACE_LIVE_KEYS.length * 2);

    // Recovery: the browser reconnected and the server sent its first token.
    // Same token as before → no extra invalidation, but polling must stop.
    invalidate.mockClear();
    act(() => es.change('v1'));
    expect(result.current).toBe('stream');
    act(() => vi.advanceTimersByTime(LIVE_POLL_MS * 3));
    expect(invalidate).not.toHaveBeenCalled();

    // A reconnect that reveals a change made while disconnected does re-read.
    act(() => es.fail());
    act(() => es.change('v2'));
    expect(invalidate).toHaveBeenCalledTimes(TRACE_LIVE_KEYS.length);
    expect(result.current).toBe('stream');
  });

  it('reopens a source the browser closed (non-200 reply) on the poll cadence, polling meanwhile', () => {
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useTraceLive('T1'), { wrapper });
    const first = FakeEventSource.instances[0];
    act(() => first.change('v1'));
    invalidate.mockClear();

    // The pod went away; the gateway answered the reconnect with 503.
    act(() => first.reject());
    expect(result.current).toBe('poll');
    expect(FakeEventSource.instances).toHaveLength(1);

    act(() => vi.advanceTimersByTime(LIVE_POLL_MS + 10));
    expect(FakeEventSource.instances).toHaveLength(2);
    const second = FakeEventSource.instances[1];
    expect(second.url).toBe('/api/traces/T1/events');
    expect(result.current).toBe('poll'); // still polling until the new one speaks

    // Still 503: closed again → one more reopen, not a storm.
    act(() => second.reject());
    act(() => vi.advanceTimersByTime(LIVE_POLL_MS + 10));
    expect(FakeEventSource.instances).toHaveLength(3);
    expect(invalidate.mock.calls.length).toBeGreaterThan(0); // polling carried the page meanwhile

    // The new pod is up: first token → stream, polling stops, token compared across reopens.
    invalidate.mockClear();
    act(() => FakeEventSource.instances[2].change('v1'));
    expect(result.current).toBe('stream');
    expect(invalidate).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(LIVE_POLL_MS * 3));
    expect(invalidate).not.toHaveBeenCalled();
    expect(FakeEventSource.instances).toHaveLength(3);
  });

  it('closes the stream and clears the poll timer on unmount and on trace change', () => {
    const { invalidate, wrapper } = setup();
    const { rerender, unmount } = renderHook(({ id }) => useTraceLive(id), {
      wrapper,
      initialProps: { id: 'T1' },
    });
    const first = FakeEventSource.instances[0];
    act(() => first.reject()); // closed by the browser: a reopen is pending too

    rerender({ id: 'T2' });
    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[1].url).toBe('/api/traces/T2/events');
    // T1's poll timer and its pending reopen are gone: advancing time
    // invalidates nothing for T1 and opens nothing on T1.
    invalidate.mockClear();
    act(() => vi.advanceTimersByTime(LIVE_POLL_MS * 2));
    expect(invalidatedKeys(invalidate).some((k) => k.includes('"T1"'))).toBe(false);
    expect(FakeEventSource.instances).toHaveLength(2);

    unmount();
    expect(FakeEventSource.instances[1].closed).toBe(true);
  });

  it('is off, and opens nothing, without a browser EventSource', () => {
    vi.unstubAllGlobals();
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useTraceLive('T1'), { wrapper });
    expect(result.current).toBe('off');
    act(() => vi.advanceTimersByTime(LIVE_POLL_MS * 3));
    expect(invalidate).not.toHaveBeenCalled();
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});

describe('useEntitiesAndInteractions — one reading of the two (ADR-0034)', () => {
  type Reply = { entities: string[]; interactions: string[] };
  /** Per-trace replies; `hold` keeps that trace's interactions read open until released. */
  let replies: Record<string, Reply>;
  let hold: Promise<void> | null;

  beforeEach(() => {
    hold = null;
    replies = {};
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const tid = /traces\/([^/]+)\//.exec(url)?.[1] ?? '';
        const r = replies[tid];
        if (url.endsWith('/entities')) {
          return { ok: true, status: 200, json: async () => ({ entities: r.entities.map((id) => ({ id })) }) };
        }
        if (hold) await hold;
        return { ok: true, status: 200, json: async () => ({ interactions: r.interactions.map((id) => ({ id })) }) };
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  const ids = (xs: ReadonlyArray<{ id: string }>) => xs.map((x) => x.id);

  function setup(initial: string) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const hook = renderHook(({ tid }) => useEntitiesAndInteractions(tid), {
      wrapper,
      initialProps: { tid: initial },
    });
    return { client, ...hook };
  }

  it('keeps the previous pair while either read is in flight, then swaps both together', async () => {
    replies.T1 = { entities: ['e1'], interactions: ['i1'] };
    const { client, result } = setup('T1');
    await waitFor(() => expect(ids(result.current.interactions)).toEqual(['i1']));

    replies.T1 = { entities: ['e1', 'e2'], interactions: ['i1', 'i2'] };
    let release!: () => void;
    hold = new Promise<void>((r) => (release = r));
    act(() => void client.invalidateQueries());
    // The entities answer is in; the interactions answer is not.
    await waitFor(() => expect(result.current.entitiesQ.data?.length).toBe(2));
    expect(ids(result.current.entities)).toEqual(['e1']);
    expect(ids(result.current.interactions)).toEqual(['i1']);

    await act(async () => release());
    await waitFor(() => expect(ids(result.current.interactions)).toEqual(['i1', 'i2']));
    expect(ids(result.current.entities)).toEqual(['e1', 'e2']);
  });

  it('never shows one trace\'s pair under another trace', async () => {
    replies.T1 = { entities: ['a1'], interactions: ['ai'] };
    replies.T2 = { entities: ['b1'], interactions: ['bi'] };
    const { result, rerender } = setup('T1');
    await waitFor(() => expect(ids(result.current.entities)).toEqual(['a1']));
    rerender({ tid: 'T2' });
    // Never the T1 pair, however the T2 reads are going.
    expect(ids(result.current.entities)).not.toContain('a1');
    await waitFor(() => expect(ids(result.current.entities)).toEqual(['b1']));
    // Back to T1 (cached, re-read in the background): T1's own data, not T2's.
    rerender({ tid: 'T1' });
    expect(ids(result.current.entities)).toEqual(['a1']);
  });
});
