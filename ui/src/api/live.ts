/**
 * Live trace updates — the SPA side of the trace change stream (ADR-0034).
 *
 * `GET /api/traces/{tid}/events` is a Server-Sent Events stream of `change`
 * events whose `id` is the trace's **version token**. The token says nothing
 * about *what* changed and the page never parses it: every derived read the
 * trace page makes is a whole-trace read, so "the token differs from the one I
 * last saw" is the complete instruction — invalidate the trace's queries and
 * let TanStack refetch the ones that are mounted. Structural sharing then keeps
 * every unchanged array's identity, so a refetch that changed nothing re-renders
 * nothing, and one that added an interaction re-renders exactly the views
 * deriving from it.
 *
 * ONE HOOK, ONE OWNER. `useTraceLive` is mounted once by the trace page; the
 * views underneath keep reading through their existing query hooks and know
 * nothing about liveness. That is the whole seam: liveness is a property of the
 * page being open, not of any one tab.
 *
 * TWO MODES, decided by the browser's `EventSource`:
 *
 * - `stream` — the normal case. The browser reconnects by itself after a
 *   drop (the server's `retry:` hint sets the cadence) and replays nothing;
 *   the server's first event on every connection is the current token, and
 *   comparing it with the last one seen is what makes a reconnect correct —
 *   a change that happened while disconnected shows as a differing token.
 * - `poll` — while the stream is in error (a proxy that will not pass SSE, a
 *   server restart in progress). The hook invalidates on a fixed interval
 *   until the stream comes back, so liveness degrades to a few seconds of lag
 *   rather than to nothing. Polling is never the steady state: the moment an
 *   event arrives the interval is cleared.
 *
 * `off` is the third value and means no `EventSource` at all, which is not a
 * browser this SPA supports — it is jsdom. The hook then does nothing, so the
 * page behaves as it did before this stream existed and no test that counts
 * fetches sees a timer it did not start.
 *
 * THE FIRST EVENT ALSO INVALIDATES. The page's queries fire on mount, the
 * stream's first token arrives a little later, and a derived write that lands
 * between the two would be baked into that baseline token and never signalled.
 * Re-reading once at that point closes the window at the cost of one extra
 * round of the page's small reads per open — cheaper than being wrong once.
 */
import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { apiPath } from './client';

export type LiveMode = 'stream' | 'poll' | 'off';

/** Invalidation cadence while the stream is down. */
export const LIVE_POLL_MS = 2000;

/**
 * The query-key heads this page keeps live, each scoped by `traceId` as its
 * second element (see `hooks.ts`). A prefix match, so `['lineage-graph', tid]`
 * covers every direction/entity/source variant under it.
 *
 * NOT the span tree (`span`, `span-children`): it is keyset-paginated with its
 * own expand/reveal state and a Refresh button, and re-fetching its pages
 * underneath an open reveal is a different problem than re-reading a
 * whole-trace derivation. Left as it is, deliberately.
 */
export const TRACE_LIVE_KEYS = [
  'trace',
  'interactions',
  'entities',
  'data-lineage',
  'lineage-summary',
  'lineage-graph',
] as const;

/** The SSE event name the server emits — one, by design. */
export const CHANGE_EVENT = 'change';

export function traceEventsPath(traceId: string): string {
  return apiPath(`/traces/${traceId}/events`);
}

export function useTraceLive(traceId: string): LiveMode {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<LiveMode>('off');

  useEffect(() => {
    if (!traceId || typeof EventSource !== 'function') {
      setMode('off');
      return;
    }
    const invalidate = () => {
      for (const head of TRACE_LIVE_KEYS) {
        void queryClient.invalidateQueries({ queryKey: [head, traceId] });
      }
    };

    let lastId: string | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    const startPolling = () => {
      if (pollTimer === null) pollTimer = setInterval(invalidate, LIVE_POLL_MS);
      setMode('poll');
    };
    const stopPolling = () => {
      if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const source = new EventSource(traceEventsPath(traceId));
    source.addEventListener(CHANGE_EVENT, (event) => {
      const id = (event as MessageEvent).lastEventId;
      stopPolling();
      setMode('stream');
      if (id !== lastId) invalidate();
      lastId = id;
    });
    source.onerror = () => {
      // EventSource reconnects on its own; cover the gap. A CLOSED source
      // (the browser gave up) is not expected from a `retry:`-hinted stream,
      // and polling is the right answer to it too.
      startPolling();
    };

    return () => {
      source.close();
      stopPolling();
    };
  }, [queryClient, traceId]);

  return mode;
}
