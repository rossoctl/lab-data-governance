import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { LIVE_POLL_MS, TRACE_LIVE_KEYS, useTraceLive } from './live';

/**
 * A stand-in for the browser's EventSource: records the URL it was opened on,
 * lets a test deliver `change` events and errors, and reports `close()`.
 * jsdom has no EventSource of its own, which is also what the `off` test relies on.
 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  closed = false;
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
    const ev = new MessageEvent('change', { lastEventId: id, data: id });
    this.listeners.get('change')?.forEach((fn) => fn(ev));
  }
  fail() {
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

  it('closes the stream and clears the poll timer on unmount and on trace change', () => {
    const { invalidate, wrapper } = setup();
    const { rerender, unmount } = renderHook(({ id }) => useTraceLive(id), {
      wrapper,
      initialProps: { id: 'T1' },
    });
    const first = FakeEventSource.instances[0];
    act(() => first.fail());

    rerender({ id: 'T2' });
    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[1].url).toBe('/api/traces/T2/events');
    // T1's poll timer is gone: advancing time invalidates nothing for T1.
    invalidate.mockClear();
    act(() => vi.advanceTimersByTime(LIVE_POLL_MS * 2));
    expect(invalidatedKeys(invalidate).some((k) => k.includes('"T1"'))).toBe(false);

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
