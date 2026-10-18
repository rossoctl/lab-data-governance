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
 * ONE HOOK, ONE OWNER. `useTraceLive` is mounted once by the page showing the
 * trace; the views underneath keep reading through their existing query hooks
 * and know nothing about liveness. That is the whole seam: liveness is a
 * property of the page being open, not of any one tab. Two pages own it: the
 * trace page (the default keys, {@link traceLiveKeys}) and the risk trace page,
 * whose reads — the trace's risk detail and alerts — are whole-trace reads too
 * and are passed as its own {@link LiveKeys}. Same stream, same token.
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
 *   event arrives the interval is cleared. A source the browser has CLOSED
 *   (it does so on any non-200 reply, e.g. a gateway's 503 during a restart,
 *   and never retries) is reopened on the same cadence.
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
import { useEffect, useMemo, useState } from 'react';
import { useQueryClient, type QueryKey } from '@tanstack/react-query';
import { apiPath } from './client';
import { useEntities, useInteractions } from './hooks';

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

/**
 * Which queries a change re-reads, given the trace: key PREFIXES, each handed to
 * `invalidateQueries` as it is. Called on every invalidation and listed in the
 * stream effect's dependencies, so it must be a module-level function — a fresh
 * one each render would reopen the stream on every render.
 */
export type LiveKeys = (traceId: string) => readonly QueryKey[];

/** The trace page's live keys: every head in {@link TRACE_LIVE_KEYS}, scoped by trace. */
export function traceLiveKeys(traceId: string): QueryKey[] {
  return TRACE_LIVE_KEYS.map((head) => [head, traceId]);
}

/** The SSE event name the server emits — one, by design. */
export const CHANGE_EVENT = 'change';

export function traceEventsPath(traceId: string): string {
  return apiPath(`/traces/${traceId}/events`);
}

export function useTraceLive(traceId: string, liveKeys: LiveKeys = traceLiveKeys): LiveMode {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<LiveMode>('off');

  useEffect(() => {
    if (!traceId || typeof EventSource !== 'function') {
      setMode('off');
      return;
    }
    const invalidate = () => {
      for (const queryKey of liveKeys(traceId)) {
        void queryClient.invalidateQueries({ queryKey });
      }
    };

    let lastId: string | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let reopenTimer: ReturnType<typeof setTimeout> | null = null;
    let source: EventSource | null = null;
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

    const open = () => {
      reopenTimer = null;
      const es = new EventSource(traceEventsPath(traceId));
      source = es;
      es.addEventListener(CHANGE_EVENT, (event) => {
        const id = (event as MessageEvent).lastEventId;
        stopPolling();
        setMode('stream');
        if (id !== lastId) invalidate();
        lastId = id;
      });
      es.onerror = () => {
        // Cover the gap by polling. A dropped stream the browser reconnects to
        // by itself (CONNECTING); one it gave up on (CLOSED) it never retries —
        // a proxy answering 503 while the API is restarting is enough for that,
        // and the EventSource spec says a non-200 reply closes the source for
        // good. So a closed source is reopened on the poll cadence; polling
        // continues until the new one delivers its first token.
        startPolling();
        if (es.readyState === EventSource.CLOSED && reopenTimer === null) {
          reopenTimer = setTimeout(open, LIVE_POLL_MS);
        }
      };
    };
    open();

    return () => {
      source?.close();
      if (reopenTimer !== null) clearTimeout(reopenTimer);
      stopPolling();
    };
  }, [queryClient, traceId, liveKeys]);

  return mode;
}

/**
 * The trace's entities and interactions as ONE reading, for the views that draw
 * the two together (the graph tabs and the flow tables).
 *
 * A change event re-reads both, as two requests that land at different moments.
 * Drawn as they land, the picture passes through a state the trace was never in:
 * the new entity is there but the interaction that reaches it is not yet, so the
 * layout parks it in the unconnected column, and a moment later it moves to its
 * real cell — a node that appears in the wrong place and then travels. So while
 * either read is in flight the previous pair is kept, and the two are swapped in
 * together once both have landed.
 *
 * Only for a re-read of the SAME trace. A different trace is a different picture,
 * and its reads are passed through as they are (the loading state covers the gap).
 *
 * The queries are returned as well, for their loading/error flags.
 */
export function useEntitiesAndInteractions(traceId: string) {
  const entitiesQ = useEntities(traceId);
  const interactionsQ = useInteractions(traceId);
  const latest = { traceId, entities: entitiesQ.data, interactions: interactionsQ.data };
  const [held, setHeld] = useState(latest);
  const take =
    held.traceId !== traceId || (!entitiesQ.isFetching && !interactionsQ.isFetching);
  if (
    take &&
    (held.traceId !== latest.traceId ||
      held.entities !== latest.entities ||
      held.interactions !== latest.interactions)
  ) {
    // React's "adjust state while rendering": re-renders before anything commits.
    setHeld(latest);
  }
  const reading = take ? latest : held;
  const entities = useMemo(() => reading.entities ?? [], [reading.entities]);
  const interactions = useMemo(() => reading.interactions ?? [], [reading.interactions]);
  return { entities, interactions, entitiesQ, interactionsQ };
}
